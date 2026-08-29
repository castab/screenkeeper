from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from signage_controller.control_plane.client import (
    ControlPlaneAuthError,
    ControlPlaneClient,
    ControlPlaneRejectedError,
    ControlPlaneUnavailableError,
    EnrollmentExpiredError,
)
from signage_controller.control_plane.enrollment import enroll, format_pairing_code
from signage_controller.control_plane.identity import DeviceStore
from signage_controller.http_client import URLError

from .conftest import FakeTransport


BASE_URL = "https://screenkeeper.example.com"
CREATED = (
    201,
    {
        "enrollment_id": "enr-1",
        "code": "Q7KM-4HF2",
        "expires_at": "2026-08-29T05:00:00Z",
        # Zero-ish so the poll loop does not slow the suite down.
        "poll_interval_seconds": 0,
    },
)
PENDING = (200, {"status": "pending"})
CLAIMED = (
    200,
    {
        "status": "claimed",
        "player_id": "player-1",
        "player_name": "Main Menu Wall",
        "organization": {"id": "org-1", "name": "Example Restaurant"},
        "location": {"id": "loc-1", "name": "Downtown"},
    },
)


def _client(transport: FakeTransport) -> ControlPlaneClient:
    return ControlPlaneClient(BASE_URL, transport=transport)


def test_creating_an_enrollment_posts_the_identity_to_the_v1_path(tmp_path: Path) -> None:
    transport = FakeTransport([CREATED])
    identity = DeviceStore(tmp_path).load_or_create()

    ticket = _client(transport).create_enrollment(identity, "menu-player-01")

    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"{BASE_URL}/api/v1/enrollments"
    assert request["payload"]["installation_id"] == identity.installation_id
    assert request["payload"]["device_token"] == identity.device_token
    assert request["payload"]["hostname"] == "menu-player-01"
    assert ticket.code == "Q7KM-4HF2"
    assert ticket.enrollment_id == "enr-1"


def test_the_displayed_code_block_shows_the_pairing_code(tmp_path: Path) -> None:
    transport = FakeTransport([CREATED])
    identity = DeviceStore(tmp_path).load_or_create()
    ticket = _client(transport).create_enrollment(identity, "menu-player-01")

    rendered = format_pairing_code(ticket)

    assert "Q7KM-4HF2" in rendered
    assert "Waiting for this player to be claimed" in rendered
    assert identity.device_token not in rendered


def test_polling_sends_the_token_as_a_bearer_credential(tmp_path: Path) -> None:
    transport = FakeTransport([PENDING])

    state = _client(transport).poll_enrollment("enr-1", "the-token")

    assert transport.requests[0]["method"] == "GET"
    assert transport.requests[0]["url"] == f"{BASE_URL}/api/v1/enrollments/enr-1"
    assert transport.requests[0]["token"] == "the-token"
    assert state.status == "pending"
    assert not state.is_claimed


def test_a_claimed_poll_carries_the_assigned_organization_and_location() -> None:
    state = _client(FakeTransport([CLAIMED])).poll_enrollment("enr-1", "the-token")

    assert state.is_claimed
    assert state.player_id == "player-1"
    assert state.player_name == "Main Menu Wall"
    assert state.organization_name == "Example Restaurant"
    assert state.location_name == "Downtown"


def test_unknown_response_fields_are_tolerated() -> None:
    """A newer control plane must not break an older appliance."""
    body = dict(CLAIMED[1], future_field="ignored", nested={"also": "ignored"})

    state = _client(FakeTransport([(200, body)])).poll_enrollment("enr-1", "token")

    assert state.is_claimed
    assert state.player_id == "player-1"


@pytest.mark.parametrize("status", [404, 410])
def test_an_expired_enrollment_is_reported_distinctly(status: int) -> None:
    client = _client(FakeTransport([(status, {"error": "enrollment_expired"})]))

    with pytest.raises(EnrollmentExpiredError):
        client.poll_enrollment("enr-1", "token")


def test_an_expired_status_body_is_treated_as_expiry() -> None:
    client = _client(FakeTransport([(200, {"status": "expired"})]))

    with pytest.raises(EnrollmentExpiredError):
        client.poll_enrollment("enr-1", "token")


@pytest.mark.parametrize("status", [401, 403])
def test_an_invalid_credential_is_reported_clearly(status: int) -> None:
    client = _client(FakeTransport([(status, {"message": "unknown device"})]))

    with pytest.raises(ControlPlaneAuthError) as err:
        client.poll_enrollment("enr-1", "token")

    assert "screenkeeper.example.com" in str(err.value)
    assert "token" not in str(err.value)


def test_a_transport_failure_is_reported_as_unavailable() -> None:
    client = _client(FakeTransport([URLError("connection refused")]))

    with pytest.raises(ControlPlaneUnavailableError, match="unreachable"):
        client.create_enrollment(DeviceStoreStub(), "host")


def test_a_server_error_is_reported_as_unavailable() -> None:
    client = _client(FakeTransport([(503, {"error": "maintenance"})]))

    with pytest.raises(ControlPlaneUnavailableError):
        client.create_enrollment(DeviceStoreStub(), "host")


def test_a_conflict_is_not_treated_as_retryable() -> None:
    client = _client(FakeTransport([(409, {"error": "already_enrolled"})]))

    with pytest.raises(ControlPlaneRejectedError):
        client.create_enrollment(DeviceStoreStub(), "host")


def test_an_enrollment_without_a_code_is_rejected() -> None:
    client = _client(FakeTransport([(201, {"enrollment_id": "enr-1"})]))

    with pytest.raises(ControlPlaneRejectedError, match="without an ID or code"):
        client.create_enrollment(DeviceStoreStub(), "host")


class DeviceStoreStub:
    """Minimal identity stand-in for client tests that never touch disk."""

    installation_id = "70b3f02f-6f6c-4f0d-8f4a-2f5f1b0c9d21"
    device_token = "test-token"


async def test_enrollment_persists_the_claim(tmp_path: Path) -> None:
    transport = FakeTransport([CREATED, PENDING, CLAIMED])
    store = DeviceStore(tmp_path)
    identity = store.load_or_create()

    claimed = await asyncio.wait_for(
        enroll(_client(transport), store, identity, asyncio.Event(), hostname="menu-player-01"),
        timeout=5,
    )

    assert claimed is not None
    assert claimed.player_id == "player-1"
    assert claimed.organization_name == "Example Restaurant"
    assert claimed.location_name == "Downtown"
    assert claimed.enrolled_at is not None

    reloaded = DeviceStore(tmp_path).load()
    assert reloaded is not None
    assert reloaded.player_id == "player-1"
    assert reloaded.location_name == "Downtown"


async def test_an_expired_code_starts_a_new_enrollment_with_the_same_identity(
    tmp_path: Path,
) -> None:
    """Identity must survive expiry: regenerating it would orphan a pending claim."""
    second_ticket = (201, dict(CREATED[1], enrollment_id="enr-2", code="ZZZZ-9999"))
    transport = FakeTransport(
        [CREATED, (410, {"error": "enrollment_expired"}), second_ticket, CLAIMED]
    )
    store = DeviceStore(tmp_path)
    identity = store.load_or_create()

    claimed = await asyncio.wait_for(
        enroll(_client(transport), store, identity, asyncio.Event()), timeout=5
    )

    assert claimed is not None
    assert claimed.installation_id == identity.installation_id
    assert claimed.device_token == identity.device_token

    creations = [
        request for request in transport.requests if request["url"].endswith("/enrollments")
    ]
    assert len(creations) == 2
    assert {request["payload"]["installation_id"] for request in creations} == {
        identity.installation_id
    }
    assert {request["payload"]["device_token"] for request in creations} == {
        identity.device_token
    }


async def test_enrollment_stops_cleanly_when_interrupted(tmp_path: Path) -> None:
    transport = FakeTransport([CREATED, PENDING, PENDING, PENDING])
    store = DeviceStore(tmp_path)
    identity = store.load_or_create()
    stop_event = asyncio.Event()

    task = asyncio.create_task(enroll(_client(transport), store, identity, stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()

    assert await asyncio.wait_for(task, timeout=5) is None
    # An interrupted enrollment leaves identity untouched and unclaimed.
    reloaded = DeviceStore(tmp_path).load()
    assert reloaded is not None
    assert not reloaded.is_enrolled
    assert reloaded.device_token == identity.device_token
