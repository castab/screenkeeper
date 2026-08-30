from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from ..control_plane.client import ControlPlaneRejectedError


@dataclass(frozen=True, slots=True)
class ContentAsset:
    asset_id: str
    asset_revision_id: str
    revision: int
    original_filename: str
    content_type: str
    byte_size: int
    sha256: str
    download_url: str = ""

    @classmethod
    def from_dict(cls, raw: Any) -> ContentAsset:
        if not isinstance(raw, dict) or raw.get("kind") != "asset":
            raise ControlPlaneRejectedError("Control plane returned an invalid content assignment.")
        strings = {}
        for name in ("asset_id", "asset_revision_id", "original_filename", "content_type", "sha256", "download_url"):
            value = raw.get(name)
            if not isinstance(value, str) or not value:
                raise ControlPlaneRejectedError(f"Content assignment has no valid {name}.")
            strings[name] = value
        revision = raw.get("revision")
        byte_size = raw.get("byte_size")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ControlPlaneRejectedError("Content assignment has no valid revision.")
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 1:
            raise ControlPlaneRejectedError("Content assignment has no valid byte size.")
        if not strings["content_type"].startswith("video/"):
            raise ControlPlaneRejectedError("Content assignment is not a video asset.")
        if len(strings["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in strings["sha256"]):
            raise ControlPlaneRejectedError("Content assignment has no valid SHA-256.")
        parsed = urlsplit(strings["download_url"])
        if parsed.scheme != "https" or not parsed.hostname:
            raise ControlPlaneRejectedError("Content download URL must use HTTPS.")
        return cls(revision=revision, byte_size=byte_size, **strings)

    def persisted(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("download_url", None)
        return data


@dataclass(frozen=True, slots=True)
class PlayerAssignment:
    player_id: str
    asset: ContentAsset


@dataclass(frozen=True, slots=True)
class ContentManifest:
    revision: int
    players: tuple[PlayerAssignment, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> ContentManifest:
        if not isinstance(raw, dict):
            raise ControlPlaneRejectedError("Control plane returned an invalid content manifest.")
        revision = raw.get("revision")
        players = raw.get("players")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ControlPlaneRejectedError("Content manifest has no valid revision.")
        if not isinstance(players, list):
            raise ControlPlaneRejectedError("Content manifest has no player assignments.")
        parsed: list[PlayerAssignment] = []
        seen: set[str] = set()
        for entry in players:
            if not isinstance(entry, dict) or not isinstance(entry.get("player_id"), str):
                raise ControlPlaneRejectedError("Content manifest has an invalid player assignment.")
            player_id = entry["player_id"]
            if not player_id or player_id in seen:
                raise ControlPlaneRejectedError("Content manifest has duplicate or empty player IDs.")
            seen.add(player_id)
            parsed.append(PlayerAssignment(player_id, ContentAsset.from_dict(entry.get("content"))))
        return cls(revision, tuple(parsed))
