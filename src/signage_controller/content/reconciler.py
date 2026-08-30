from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from pathlib import Path
from typing import Any

from ..config import ApplicationConfig, ContentConfig
from ..control_plane.client import (
    ControlPlaneAuthError,
    ControlPlaneClient,
    ControlPlaneError,
)
from ..control_plane.identity import DeviceIdentity
from ..control_plane.report import utc_now
from .downloader import AssetDownloader, DownloadError, HttpAssetDownloader
from .models import ContentAsset, ContentManifest
from .store import ContentStore


LOGGER = logging.getLogger(__name__)
CONTENT_BACKOFF_SECONDS = (30.0, 60.0, 120.0, 300.0)
AUTH_BACKOFF_SECONDS = 300.0
JITTER_FRACTION = 0.2


class ContentReconciler:
    def __init__(
        self,
        config: ApplicationConfig,
        content: ContentConfig,
        client: ControlPlaneClient,
        identity: DeviceIdentity,
        store: ContentStore,
        *,
        downloader: AssetDownloader | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.content = content
        self.client = client
        self.identity = identity
        self.store = store
        self.downloader = downloader or HttpAssetDownloader(content.download_timeout)
        self.logger = logger or LOGGER

    async def reconcile(self) -> bool:
        manifest = await asyncio.to_thread(
            self.client.fetch_content, self.identity.player_id or "", self.identity.device_token
        )
        state = self.store.load_state()
        current_revision = state.get("manifest_revision", 0)
        if isinstance(current_revision, int) and manifest.revision < current_revision:
            self.logger.warning(
                "Ignoring stale content manifest revision %s; local revision is %s.",
                manifest.revision,
                current_revision,
            )
            return True

        known_players = {
            player.id: player
            for player in (self.config.playback.players if self.config.playback else ())
        }
        assignments = {assignment.player_id: assignment.asset for assignment in manifest.players}
        players_state = state.setdefault("players", {})
        now = utc_now()
        pending: dict[str, ContentAsset] = {}

        for player_id, player in known_players.items():
            asset = assignments.get(player_id)
            record = players_state.setdefault(player_id, {})
            if asset is None:
                record["desired"] = None
                if player.media.exists():
                    record["active"] = None
                record["status"] = "active" if record.get("active") else "available"
                record["last_error"] = None
                continue
            record["desired"] = asset.persisted()
            record["status"] = "desired"
            record["last_error"] = None
            if not self.store.verify(asset):
                pending.setdefault(asset.sha256, asset)

        for unknown_id in sorted(set(assignments) - set(known_players)):
            self.logger.warning("Ignoring content assignment for unknown local player %s.", unknown_id)

        state["manifest_revision"] = manifest.revision
        self.store.save_manifest(manifest)
        self.store.save_state(state)

        success = True
        for asset in pending.values():
            affected = [pid for pid, desired in assignments.items() if desired.sha256 == asset.sha256 and pid in known_players]
            for player_id in affected:
                players_state[player_id]["status"] = "downloading"
            self.store.save_state(state)
            await self._report(state)
            try:
                await self.downloader.download(asset, self.store.media_path(asset.sha256))
            except DownloadError as err:
                success = False
                for player_id in affected:
                    players_state[player_id]["status"] = "failed"
                    players_state[player_id]["last_error"] = str(err)[:500]
                self.logger.warning("Content download failed; current signage is unchanged: %s", err)
                continue
            if not self.store.verify(asset):
                success = False
                self.store.media_path(asset.sha256).unlink(missing_ok=True)
                for player_id in affected:
                    players_state[player_id]["status"] = "failed"
                    players_state[player_id]["last_error"] = "Installed object failed SHA-256 validation."

        for player_id, asset in assignments.items():
            if player_id not in known_players:
                continue
            record = players_state[player_id]
            if self.store.verify(asset):
                record["status"] = "active"
                record["cached"] = asset.persisted()
                record["active"] = asset.persisted()
                record["last_error"] = None

        state["last_sync_at"] = now
        self.store.save_state(state)
        report_ok = await self._report(state)
        if success and report_ok:
            state["last_successful_sync_at"] = now
            self.store.save_state(state)
        return success and report_ok

    async def _report(self, state: dict[str, Any]) -> bool:
        report = build_content_status(self.store, state)
        try:
            await asyncio.to_thread(
                self.client.send_content_status,
                self.identity.player_id or "",
                self.identity.device_token,
                report,
            )
        except ControlPlaneError as err:
            self.logger.info("Could not report content status; local signage is unaffected: %s", err)
            return False
        return True


def build_content_status(store: ContentStore, state: dict[str, Any]) -> dict[str, Any]:
    players = []
    for player_id, record in sorted(state.get("players", {}).items()):
        if not isinstance(record, dict):
            continue
        desired = record.get("desired") if isinstance(record.get("desired"), dict) else {}
        cached = record.get("cached") if isinstance(record.get("cached"), dict) else {}
        active = record.get("active") if isinstance(record.get("active"), dict) else {}
        players.append(
            {
                "player_id": player_id,
                "status": record.get("status", "desired"),
                "desired_asset_revision_id": desired.get("asset_revision_id"),
                "cached_asset_revision_id": cached.get("asset_revision_id"),
                "active_asset_revision_id": active.get("asset_revision_id"),
                "playing_asset_revision_id": store.playing_asset_revision(player_id),
                "last_sync_at": state.get("last_sync_at"),
                "last_error": record.get("last_error"),
            }
        )
    return {
        "reported_at": utc_now(),
        "manifest_revision": state.get("manifest_revision", 0),
        "players": players,
    }


async def run_content(
    reconciler: ContentReconciler,
    content: ContentConfig,
    stop_event: asyncio.Event,
    *,
    backoff_delays: tuple[float, ...] = CONTENT_BACKOFF_SECONDS,
    rng: random.Random | None = None,
) -> None:
    failure_streak = 0
    random_source = rng or random.Random()
    reachable: bool | None = None
    while not stop_event.is_set():
        try:
            complete = await reconciler.reconcile()
            if reachable is False:
                reconciler.logger.info("Content synchronization restored.")
            reachable = True
            failure_streak = 0 if complete else failure_streak + 1
            delay = content.reconcile_interval if complete else _backoff(failure_streak, backoff_delays)
        except asyncio.CancelledError:
            raise
        except ControlPlaneAuthError as err:
            if reachable is not False:
                reconciler.logger.error("Content authentication rejected: %s", err)
            reachable = False
            delay = AUTH_BACKOFF_SECONDS
        except (ControlPlaneError, Exception) as err:
            if reachable is not False:
                reconciler.logger.info("Content control plane unavailable; signage is unaffected: %s", err)
            else:
                reconciler.logger.debug("Content synchronization still unavailable: %s", err)
            reachable = False
            failure_streak += 1
            delay = _backoff(failure_streak, backoff_delays)
        delay = max(0.0, delay + delay * random_source.uniform(-JITTER_FRACTION, JITTER_FRACTION))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)


def _backoff(streak: int, delays: tuple[float, ...]) -> float:
    if not delays:
        return 0.0
    return delays[min(max(streak - 1, 0), len(delays) - 1)]
