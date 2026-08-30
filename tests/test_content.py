from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from signage_controller.config import (
    ApplicationConfig,
    ContentConfig,
    ControlPlaneConfig,
    PlaybackConfig,
    PlayerConfig,
    TvConfig,
    load_config,
)
from signage_controller.content.downloader import DownloadError, HttpAssetDownloader
from signage_controller.content.models import ContentAsset
from signage_controller.content.reconciler import ContentReconciler, build_content_status
from signage_controller.content.resolver import MediaResolver
from signage_controller.content.store import ContentStore
from signage_controller.control_plane.client import ControlPlaneClient
from signage_controller.control_plane.identity import DeviceIdentity

from .conftest import FakeTransport


BYTES = b"screenkeeper-video"
SHA = hashlib.sha256(BYTES).hexdigest()
REVISION_ID = "019254c1-bbbb-7a13-8d4f-6e7f8a9b0c1d"


def _manifest(*player_ids: str) -> dict:
    return {
        "revision": 18,
        "players": [
            {
                "player_id": player_id,
                "content": {
                    "kind": "asset",
                    "asset_id": "019254c1-aaaa-7a13-8d4f-6e7f8a9b0c1d",
                    "asset_revision_id": REVISION_ID,
                    "revision": 3,
                    "original_filename": "menu-v2.mp4",
                    "content_type": "video/mp4",
                    "byte_size": len(BYTES),
                    "sha256": SHA,
                    "download_url": "https://bucket.example/object?signature=secret",
                },
            }
            for player_id in player_ids
        ],
    }


def _config(tmp_path: Path, *player_ids: str) -> ApplicationConfig:
    return ApplicationConfig(
        tvs=(TvConfig("tv", "TV", "127.0.0.1", "HDMI_1", 0),),
        playback=PlaybackConfig(
            players=tuple(
                PlayerConfig(player_id, player_id, tmp_path / f"{player_id}.mp4")
                for player_id in player_ids
            )
        ),
        control_plane=ControlPlaneConfig("https://control.example"),
        content=ContentConfig(reconcile_interval=300, download_timeout=30),
    )


class FakeDownloader:
    def __init__(self, *, fail: bool = False, corrupt: bool = False) -> None:
        self.fail = fail
        self.corrupt = corrupt
        self.calls = 0

    async def download(self, asset, destination: Path) -> None:
        self.calls += 1
        if self.fail:
            raise DownloadError("Object storage at bucket.example returned HTTP 503.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"corrupt" if self.corrupt else BYTES)


def _reconciler(tmp_path: Path, transport: FakeTransport, downloader, *players: str):
    config = _config(tmp_path, *players)
    store = ContentStore(tmp_path / "state")
    identity = DeviceIdentity("install", "token", player_id="device")
    client = ControlPlaneClient("https://control.example", transport=transport)
    return ContentReconciler(config, config.content, client, identity, store, downloader=downloader), store


def test_content_configuration_is_optional_and_parses_when_present(tmp_path: Path) -> None:
    base = """
tvs:
  - id: tv
    name: TV
    host: 127.0.0.1
    desired_input: HDMI_1
    desired_volume: 0
"""
    path = tmp_path / "config.yaml"
    path.write_text(base, encoding="utf-8")
    assert load_config(path).content is None

    path.write_text(base + "content:\n  reconcile_interval: 180\n", encoding="utf-8")
    content = load_config(path).content
    assert content is not None and content.enabled
    assert content.reconcile_interval == 180


@pytest.mark.asyncio
async def test_missing_asset_is_downloaded_verified_and_activated(tmp_path: Path) -> None:
    transport = FakeTransport([(200, _manifest("menu")), (204, {}), (204, {})])
    downloader = FakeDownloader()
    reconciler, store = _reconciler(tmp_path, transport, downloader, "menu")

    assert await reconciler.reconcile()
    assert downloader.calls == 1
    assert store.media_path(SHA).read_bytes() == BYTES
    assert store.active_asset("menu")["asset_revision_id"] == REVISION_ID
    assert "download_url" not in store.manifest_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_cached_asset_makes_reconcile_idempotent(tmp_path: Path) -> None:
    transport = FakeTransport([(200, _manifest("menu")), (204, {}), (200, _manifest("menu")), (204, {})])
    downloader = FakeDownloader()
    reconciler, store = _reconciler(tmp_path, transport, downloader, "menu")
    store.ensure_directories()
    store.media_path(SHA).write_bytes(BYTES)

    assert await reconciler.reconcile()
    assert await reconciler.reconcile()
    assert downloader.calls == 0


@pytest.mark.asyncio
async def test_two_players_share_one_download(tmp_path: Path) -> None:
    transport = FakeTransport([(200, _manifest("left", "right")), (204, {}), (204, {})])
    downloader = FakeDownloader()
    reconciler, store = _reconciler(tmp_path, transport, downloader, "left", "right")

    assert await reconciler.reconcile()
    assert downloader.calls == 1
    assert store.active_asset("left")["sha256"] == SHA
    assert store.active_asset("right")["sha256"] == SHA


@pytest.mark.asyncio
async def test_failed_download_preserves_previous_active_asset(tmp_path: Path) -> None:
    transport = FakeTransport([(200, _manifest("menu")), (204, {}), (204, {})])
    downloader = FakeDownloader(fail=True)
    reconciler, store = _reconciler(tmp_path, transport, downloader, "menu")
    state = store.load_state()
    state["players"]["menu"] = {
        "active": {"asset_revision_id": "old", "sha256": "0" * 64},
        "status": "active",
    }
    store.save_state(state)

    assert not await reconciler.reconcile()
    assert store.active_asset("menu")["asset_revision_id"] == "old"
    assert not store.media_path(SHA).exists()


def test_resolver_prefers_verified_selected_media_and_records_playing(tmp_path: Path) -> None:
    player = PlayerConfig("menu", "Menu", tmp_path / "fallback.mp4")
    player.media.write_bytes(b"fallback")
    store = ContentStore(tmp_path / "state")
    store.ensure_directories()
    store.media_path(SHA).write_bytes(BYTES)
    state = store.load_state()
    state["players"]["menu"] = {
        "active": {"asset_revision_id": REVISION_ID, "sha256": SHA},
        "status": "active",
    }
    store.save_state(state)
    resolver = MediaResolver(store)

    resolved = resolver.resolve(player)
    assert resolved.path == store.media_path(SHA)
    resolver.record_playing(player.id, resolved.asset_revision_id)
    assert build_content_status(store, store.load_state())["players"][0]["playing_asset_revision_id"] == REVISION_ID


def test_cache_path_is_hash_only_and_cannot_traverse(tmp_path: Path) -> None:
    store = ContentStore(tmp_path)
    with pytest.raises(Exception):
        store.media_path("../../etc/passwd")


class _Chunks:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int):
        yield self.body[:5]
        yield self.body[5:]


class _Response:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.content = _Chunks(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _Session:
    body = BYTES

    def __init__(self, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def get(self, *args, **kwargs):
        return _Response(self.body)


@pytest.mark.asyncio
async def test_http_downloader_installs_atomically_and_rejects_mismatch(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("signage_controller.content.downloader.aiohttp.ClientSession", _Session)
    asset = ContentAsset(
        "asset", REVISION_ID, 1, "menu.mp4", "video/mp4", len(BYTES), SHA,
        "https://bucket.example/object?signature=secret",
    )
    destination = tmp_path / SHA

    await HttpAssetDownloader(30).download(asset, destination)
    assert destination.read_bytes() == BYTES
    assert list(tmp_path.glob("*.part")) == []

    destination.unlink()
    _Session.body = b"wrong"
    with pytest.raises(DownloadError, match="SHA-256"):
        await HttpAssetDownloader(30).download(asset, destination)
    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []
