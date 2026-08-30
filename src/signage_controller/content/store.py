from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..state_store import StateStoreError, write_json_atomic
from .models import ContentAsset, ContentManifest


class ContentStore:
    def __init__(self, state_dir: Path, cache_dir: Path | None = None) -> None:
        self.state_dir = state_dir
        self.content_dir = state_dir / "content"
        self.cache_dir = cache_dir or state_dir / "media"
        self.state_path = self.content_dir / "state.json"
        self.manifest_path = self.content_dir / "manifest.json"
        self.playback_dir = self.content_dir / "playback"

    def ensure_directories(self) -> None:
        for directory in (self.content_dir, self.cache_dir, self.playback_dir):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "manifest_revision": 0,
                "last_sync_at": None,
                "last_successful_sync_at": None,
                "players": {},
            }
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise StateStoreError(f"Could not read content state {self.state_path}: {err}") from err
        if not isinstance(raw, dict) or not isinstance(raw.get("players"), dict):
            raise StateStoreError(f"Content state {self.state_path} is invalid.")
        return raw

    def save_state(self, state: dict[str, Any]) -> None:
        self.ensure_directories()
        write_json_atomic(self.content_dir, self.state_path, state)

    def save_manifest(self, manifest: ContentManifest) -> None:
        self.ensure_directories()
        data = {
            "revision": manifest.revision,
            "players": [
                {"player_id": assignment.player_id, "content": assignment.asset.persisted()}
                for assignment in manifest.players
            ],
        }
        write_json_atomic(self.content_dir, self.manifest_path, data)

    def media_path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise StateStoreError("Refusing an invalid content hash as a cache key.")
        return self.cache_dir / sha256

    def active_asset(self, player_id: str) -> dict[str, Any] | None:
        record = self.load_state().get("players", {}).get(player_id)
        active = record.get("active") if isinstance(record, dict) else None
        return active if isinstance(active, dict) else None

    def write_playing(self, player_id: str, asset_revision_id: str | None) -> None:
        self.ensure_directories()
        path = self.playback_dir / f"{hashlib.sha256(player_id.encode()).hexdigest()}.json"
        if asset_revision_id is None:
            path.unlink(missing_ok=True)
            return
        write_json_atomic(self.playback_dir, path, {"player_id": player_id, "asset_revision_id": asset_revision_id})

    def playing_asset_revision(self, player_id: str) -> str | None:
        path = self.playback_dir / f"{hashlib.sha256(player_id.encode()).hexdigest()}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        value = raw.get("asset_revision_id") if isinstance(raw, dict) else None
        return value if isinstance(value, str) and value else None

    def cached_files(self) -> list[Path]:
        if not self.cache_dir.exists():
            return []
        return sorted(
            path for path in self.cache_dir.iterdir()
            if path.is_file() and len(path.name) == 64 and all(c in "0123456789abcdef" for c in path.name)
        )

    def verify(self, asset: ContentAsset) -> bool:
        path = self.media_path(asset.sha256)
        if not path.is_file() or path.stat().st_size != asset.byte_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == asset.sha256
