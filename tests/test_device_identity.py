from __future__ import annotations

import base64
import json
import stat
import uuid
from pathlib import Path

import pytest

from signage_controller.control_plane.identity import (
    DEVICE_TOKEN_BYTES,
    DeviceIdentity,
    DeviceStore,
)
from signage_controller.state_store import StateStoreError


def test_generates_a_uuid4_installation_id(tmp_path: Path) -> None:
    identity = DeviceStore(tmp_path).load_or_create()

    parsed = uuid.UUID(identity.installation_id)
    assert parsed.version == 4


def test_generates_a_256_bit_device_token(tmp_path: Path) -> None:
    identity = DeviceStore(tmp_path).load_or_create()

    # token_urlsafe strips base64 padding, so restore it before decoding.
    padded = identity.device_token + "=" * (-len(identity.device_token) % 4)
    assert len(base64.urlsafe_b64decode(padded)) == DEVICE_TOKEN_BYTES


def test_two_installations_do_not_share_an_identity(tmp_path: Path) -> None:
    first = DeviceStore(tmp_path / "a").load_or_create()
    second = DeviceStore(tmp_path / "b").load_or_create()

    assert first.installation_id != second.installation_id
    assert first.device_token != second.device_token


def test_identity_persists_across_restart(tmp_path: Path) -> None:
    created = DeviceStore(tmp_path).load_or_create()

    # A separate store instance stands in for a restarted process.
    reloaded = DeviceStore(tmp_path).load_or_create()

    assert reloaded.installation_id == created.installation_id
    assert reloaded.device_token == created.device_token


def test_a_wiped_state_directory_is_a_new_installation(tmp_path: Path) -> None:
    first = DeviceStore(tmp_path).load_or_create()
    (tmp_path / "device.json").unlink()

    second = DeviceStore(tmp_path).load_or_create()

    assert second.installation_id != first.installation_id


def test_device_file_and_directory_permissions_are_restrictive(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    store = DeviceStore(state_dir)
    store.load_or_create()

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700


def test_repr_never_exposes_the_device_token(tmp_path: Path) -> None:
    identity = DeviceStore(tmp_path).load_or_create()

    assert identity.device_token not in repr(identity)
    assert identity.installation_id in repr(identity)


def test_recording_a_claim_keeps_the_same_token_and_installation_id(tmp_path: Path) -> None:
    store = DeviceStore(tmp_path)
    identity = store.load_or_create()

    claimed = store.record_claim(
        identity,
        player_id="player-1",
        player_name="Main Menu Wall",
        organization_id="org-1",
        organization_name="Example Restaurant",
        location_id="loc-1",
        location_name="Downtown",
        enrolled_at="2026-08-29T04:00:00Z",
    )

    assert claimed.device_token == identity.device_token
    assert claimed.installation_id == identity.installation_id
    assert claimed.is_enrolled

    reloaded = DeviceStore(tmp_path).load()
    assert reloaded is not None
    assert reloaded.player_id == "player-1"
    assert reloaded.location_name == "Downtown"


def test_recording_a_heartbeat_preserves_the_claim(tmp_path: Path) -> None:
    store = DeviceStore(tmp_path)
    identity = store.record_claim(
        store.load_or_create(),
        player_id="player-1",
        player_name="Main Menu Wall",
        organization_id=None,
        organization_name=None,
        location_id=None,
        location_name=None,
        enrolled_at="2026-08-29T04:00:00Z",
    )

    store.record_heartbeat(identity, "2026-08-29T04:12:00Z")

    reloaded = DeviceStore(tmp_path).load()
    assert reloaded is not None
    assert reloaded.last_heartbeat_at == "2026-08-29T04:12:00Z"
    assert reloaded.player_id == "player-1"


def test_load_returns_none_before_any_identity_exists(tmp_path: Path) -> None:
    assert DeviceStore(tmp_path).load() is None


def test_unknown_keys_survive_a_write(tmp_path: Path) -> None:
    """A newer version's field must not be dropped by an older one rewriting the file."""
    store = DeviceStore(tmp_path)
    identity = store.load_or_create()
    record = json.loads(store.path.read_text())
    record["future_field"] = "keep me"
    store.path.write_text(json.dumps(record))

    store.record_heartbeat(store.load() or identity, "2026-08-29T04:12:00Z")

    assert json.loads(store.path.read_text())["future_field"] == "keep me"


def test_a_corrupt_device_file_is_reported_not_silently_replaced(tmp_path: Path) -> None:
    store = DeviceStore(tmp_path)
    store.load_or_create()
    store.path.write_text("{not json")

    with pytest.raises(StateStoreError):
        store.load()


def test_a_device_file_without_a_token_is_rejected(tmp_path: Path) -> None:
    store = DeviceStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"installation_id": "abc"}))

    with pytest.raises(StateStoreError):
        store.load()


def test_identity_is_not_enrolled_without_a_player_id() -> None:
    identity = DeviceIdentity(installation_id="abc", device_token="secret")

    assert not identity.is_enrolled
