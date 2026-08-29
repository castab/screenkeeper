"""Persistent, local storage for per-television pairing keys."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class StateStoreError(RuntimeError):
    """Raised when pairing state cannot be loaded or saved safely."""


def default_state_dir() -> Path:
    """Return the XDG-compatible development state directory."""
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "signage-controller"
    return Path.home() / ".local" / "state" / "signage-controller"


def write_json_atomic(state_dir: Path, path: Path, data: Any) -> None:
    """Replace a state file in one step, leaving no partial or readable-by-all copy.

    Every file this writes may hold a credential — a TV pairing key, the device
    token — so the directory stays `0700` and the file `0600`, and the content
    is fsynced before the rename rather than after.
    """
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".state-", suffix=".json", dir=state_dir, text=True
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
                json.dump(data, state_file, indent=2, sort_keys=True)
                state_file.write("\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary_path, path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    except OSError as err:
        raise StateStoreError(f"Could not save state file {path}: {err}") from err


class StateStore:
    """Store client keys keyed by configured television ID."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or default_state_dir()
        self.path = self.state_dir / "state.json"

    def get_client_key(self, tv_id: str) -> str | None:
        """Return a persisted key for a TV, if one exists."""
        record = self._load().get(tv_id)
        if record is None:
            return None
        key = record.get("client_key")
        if not isinstance(key, str) or not key:
            raise StateStoreError(f"Invalid client key record for TV {tv_id!r} in {self.path}")
        return key

    def set_client_key(self, tv_id: str, client_key: str) -> None:
        """Persist a non-empty client key without exposing it in logs."""
        if not client_key:
            raise StateStoreError(f"Refusing to save an empty client key for TV {tv_id!r}")
        data = self._load()
        data[tv_id] = {"client_key": client_key}
        self._save(data)

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open(encoding="utf-8") as state_file:
                raw: Any = json.load(state_file)
        except (OSError, json.JSONDecodeError) as err:
            raise StateStoreError(f"Could not read state file {self.path}: {err}") from err

        if not isinstance(raw, dict):
            raise StateStoreError(f"State file {self.path} must contain a JSON object.")
        data: dict[str, dict[str, str]] = {}
        for tv_id, record in raw.items():
            if not isinstance(tv_id, str) or not isinstance(record, dict):
                raise StateStoreError(f"State file {self.path} has an invalid TV record.")
            key = record.get("client_key")
            if not isinstance(key, str) or not key:
                raise StateStoreError(f"State file {self.path} has an invalid client key record.")
            data[tv_id] = {"client_key": key}
        return data

    def _save(self, data: dict[str, dict[str, str]]) -> None:
        write_json_atomic(self.state_dir, self.path, data)
