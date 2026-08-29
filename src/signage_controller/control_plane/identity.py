"""Permanent, local device identity.

An appliance names itself. On first use it generates an `installation_id` (public)
and a `device_token` (a credential), stores them beside the TV pairing keys under
the state directory, and never regenerates them — not on an upgrade, not on a
rollback, and above all not to paper over an authentication failure. A host that
keeps its state directory keeps its identity; a wiped state directory is a new
installation and gets a new one.

The token is written `0600` inside a `0700` directory, is excluded from the
dataclass `repr`, and is never printed or logged.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..state_store import StateStoreError, write_json_atomic


DEVICE_FILE_NAME = "device.json"
# 32 bytes of urandom, URL-safe base64 encoded: a 256-bit secret.
DEVICE_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """This appliance's identity and whatever the control plane assigned to it."""

    installation_id: str
    # repr=False so the token cannot reach a log, a traceback, or a debugger
    # transcript through an accidental repr() of the surrounding object.
    device_token: str = field(repr=False)
    player_id: str | None = None
    player_name: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    location_id: str | None = None
    location_name: str | None = None
    enrolled_at: str | None = None
    last_heartbeat_at: str | None = None

    @property
    def is_enrolled(self) -> bool:
        """Report whether an administrator has claimed this appliance."""
        return bool(self.player_id)


class DeviceStore:
    """Persist device identity under the existing state directory."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / DEVICE_FILE_NAME

    def load(self) -> DeviceIdentity | None:
        """Return the stored identity, or None when this host has none yet."""
        raw = self._read()
        if raw is None:
            return None
        installation_id = raw.get("installation_id")
        device_token = raw.get("device_token")
        if not isinstance(installation_id, str) or not installation_id:
            raise StateStoreError(f"Device file {self.path} has no valid installation ID.")
        if not isinstance(device_token, str) or not device_token:
            raise StateStoreError(f"Device file {self.path} has no valid device token.")
        return DeviceIdentity(
            installation_id=installation_id,
            device_token=device_token,
            **{name: _optional_string(raw.get(name)) for name in _ASSIGNED_FIELDS},
        )

    def load_or_create(self) -> DeviceIdentity:
        """Return the stored identity, generating one on first use only."""
        existing = self.load()
        if existing is not None:
            return existing
        identity = DeviceIdentity(
            installation_id=str(uuid.uuid4()),
            device_token=secrets.token_urlsafe(DEVICE_TOKEN_BYTES),
        )
        self.save(identity)
        return identity

    def save(self, identity: DeviceIdentity) -> DeviceIdentity:
        """Write the identity, preserving any keys a newer version wrote."""
        existing = self._read() or {}
        record = {key: value for key, value in existing.items() if key not in _KNOWN_FIELDS}
        record["installation_id"] = identity.installation_id
        record["device_token"] = identity.device_token
        for name in _ASSIGNED_FIELDS:
            value = getattr(identity, name)
            if value is not None:
                record[name] = value
        write_json_atomic(self.state_dir, self.path, record)
        return identity

    def record_claim(
        self,
        identity: DeviceIdentity,
        *,
        player_id: str,
        player_name: str | None,
        organization_id: str | None,
        organization_name: str | None,
        location_id: str | None,
        location_name: str | None,
        enrolled_at: str,
    ) -> DeviceIdentity:
        """Persist the non-secret metadata assigned when the enrollment was claimed.

        The device token is unchanged: the same secret the appliance generated
        becomes its permanent API credential, so the control plane never has to
        send a secret back.
        """
        return self.save(
            replace(
                identity,
                player_id=player_id,
                player_name=player_name,
                organization_id=organization_id,
                organization_name=organization_name,
                location_id=location_id,
                location_name=location_name,
                enrolled_at=enrolled_at,
            )
        )

    def record_heartbeat(self, identity: DeviceIdentity, when: str) -> DeviceIdentity:
        """Persist the last successful heartbeat so `device status` can report it."""
        return self.save(replace(identity, last_heartbeat_at=when))

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            with self.path.open(encoding="utf-8") as device_file:
                raw: Any = json.load(device_file)
        except (OSError, json.JSONDecodeError) as err:
            raise StateStoreError(f"Could not read device file {self.path}: {err}") from err
        if not isinstance(raw, dict):
            raise StateStoreError(f"Device file {self.path} must contain a JSON object.")
        return raw


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


_ASSIGNED_FIELDS = (
    "player_id",
    "player_name",
    "organization_id",
    "organization_name",
    "location_id",
    "location_name",
    "enrolled_at",
    "last_heartbeat_at",
)
_KNOWN_FIELDS = frozenset({"installation_id", "device_token", *_ASSIGNED_FIELDS})
