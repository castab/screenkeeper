from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import PlayerConfig
from .store import ContentStore


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    path: Path
    asset_revision_id: str | None = None


class MediaResolver:
    def __init__(self, store: ContentStore | None = None) -> None:
        self.store = store

    def resolve(self, player: PlayerConfig) -> ResolvedMedia:
        if self.store is not None:
            active = self.store.active_asset(player.id)
            if active is not None:
                sha256 = active.get("sha256")
                revision_id = active.get("asset_revision_id")
                if isinstance(sha256, str) and isinstance(revision_id, str):
                    path = self.store.media_path(sha256)
                    if path.is_file():
                        return ResolvedMedia(path, revision_id)
        return ResolvedMedia(player.media)

    def record_playing(self, player_id: str, asset_revision_id: str | None) -> None:
        if self.store is not None:
            self.store.write_playing(player_id, asset_revision_id)
