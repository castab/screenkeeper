"""The edge implementation checked against the shared contract at the repo root.

`contracts/openapi.yaml` is the source of truth for both halves of the monorepo.
These tests fail if the appliance stops producing or accepting the shapes it
declares, which is what keeps the contract honest without either side importing
the other's code.

One test module also drives the real `urllib` request path against a loopback
HTTP server. Everything else injects a transport; this proves the actual wire
behaviour — headers, JSON encoding, status handling — works against a socket.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from signage_controller.config import (
    ApplicationConfig,
    ControlPlaneConfig,
    PlaybackConfig,
    PlayerConfig,
    TvConfig,
)
from signage_controller.control_plane.agent import HeartbeatAgent
from signage_controller.control_plane.client import ControlPlaneClient
from signage_controller.control_plane.identity import DeviceStore
from signage_controller.control_plane.report import build_heartbeat
from signage_controller.inventory.drm import Connector, Gpu, Inventory
from signage_controller.inventory.edid import EdidInfo

from .conftest import FakeControlPlaneServer


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
EXAMPLES = CONTRACTS / "examples"

pytestmark = pytest.mark.skipif(
    not CONTRACTS.is_dir(), reason="contracts/ is not present in this build"
)

CONFIG = ApplicationConfig(
    tvs=(
        TvConfig(
            id="dev-tv",
            name="Development LG TV",
            host="192.168.50.21",
            desired_input="HDMI_1",
            desired_volume=0,
        ),
    ),
    playback=PlaybackConfig(
        players=(
            PlayerConfig(
                id="dev-menu",
                name="Development Menu",
                media=Path("/srv/media/menu.mp4"),
                tv_id="dev-tv",
                screen_name="DP-1",
            ),
        )
    ),
)


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def _spec() -> dict:
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))


def test_every_documented_example_file_exists() -> None:
    for name in (
        "enrollment-request",
        "enrollment-created",
        "enrollment-claimed",
        "heartbeat",
    ):
        assert (EXAMPLES / f"{name}.json").is_file(), name


def test_the_spec_declares_the_three_v1_operations() -> None:
    spec = _spec()

    assert spec["openapi"].startswith("3.1")
    assert set(spec["paths"]) == {
        "/enrollments",
        "/enrollments/{enrollment_id}",
        "/players/{player_id}/heartbeat",
    }
    assert all(server["url"].endswith("/api/v1") for server in spec["servers"])


def test_the_device_token_is_never_a_response_field() -> None:
    """The server hashes the token and must never send it back."""
    spec = _spec()
    for name, schema in spec["components"]["schemas"].items():
        properties = schema.get("properties", {})
        if "device_token" in properties:
            # It appears in exactly one place, and only as a write-only input.
            assert name == "EnrollmentRequest"
            assert properties["device_token"]["writeOnly"] is True


def test_response_schemas_allow_unknown_fields() -> None:
    """Additive evolution: an older appliance must tolerate a newer server."""
    schemas = _spec()["components"]["schemas"]

    for name in ("EnrollmentCreated", "EnrollmentState", "NamedEntity", "Connector", "Edid"):
        assert schemas[name]["additionalProperties"] is True, name


def test_the_enrollment_request_the_client_sends_matches_the_example(tmp_path: Path) -> None:
    from .conftest import FakeTransport

    example = _example("enrollment-request")
    transport = FakeTransport([(201, _example("enrollment-created"))])
    store = DeviceStore(tmp_path)
    identity = store.load_or_create()

    ControlPlaneClient("https://screenkeeper.example.com", transport=transport).create_enrollment(
        identity, example["hostname"]
    )

    assert set(transport.requests[0]["payload"]) == set(example)


def test_the_created_enrollment_example_parses(tmp_path: Path) -> None:
    from .conftest import FakeTransport

    example = _example("enrollment-created")
    transport = FakeTransport([(201, example)])
    identity = DeviceStore(tmp_path).load_or_create()

    ticket = ControlPlaneClient(
        "https://screenkeeper.example.com", transport=transport
    ).create_enrollment(identity, "menu-player-01")

    assert ticket.enrollment_id == example["enrollment_id"]
    assert ticket.code == example["code"]
    assert ticket.expires_at == example["expires_at"]
    assert ticket.poll_interval_seconds == example["poll_interval_seconds"]


def test_the_claimed_enrollment_example_parses() -> None:
    from .conftest import FakeTransport

    example = _example("enrollment-claimed")
    transport = FakeTransport([(200, example)])

    state = ControlPlaneClient(
        "https://screenkeeper.example.com", transport=transport
    ).poll_enrollment("enr-1", "token")

    assert state.is_claimed
    assert state.player_id == example["player_id"]
    assert state.player_name == example["player_name"]
    assert state.organization_name == example["organization"]["name"]
    assert state.location_name == example["location"]["name"]


def test_the_heartbeat_the_agent_builds_matches_the_example_shape() -> None:
    example = _example("heartbeat")
    inventory = Inventory(
        gpus=(Gpu(card="card0", vendor_id="0x8086", device_id="0x5912", driver="i915"),),
        connectors=(
            Connector(
                name="DP-1",
                status="connected",
                enabled=True,
                modes=("1920x1080", "1280x720"),
                edid=EdidInfo(
                    sha256="9f2c1a4e8b6d3f570a1c2e4b6d8f0a2c4e6b8d0f2a4c6e8b0d2f4a6c8e0b2d4f",
                    manufacturer="GSM",
                    product_code=30456,
                    product_name="LG TV",
                    serial="205NTKM1G347",
                ),
            ),
            Connector(name="HDMI-A-1", status="disconnected", enabled=False),
        ),
    )

    report = build_heartbeat(CONFIG, inventory, reported_at=example["reported_at"])

    assert set(report) == set(example)
    assert report["inventory"] == example["inventory"]
    assert report["configured_bindings"] == example["configured_bindings"]
    assert set(report["agent"]) == set(example["agent"])


async def test_enrollment_and_heartbeat_work_over_real_http(tmp_path: Path) -> None:
    """End-to-end through urllib and a socket, not an injected transport."""
    routes = {
        ("POST", "/api/v1/enrollments"): (201, _example("enrollment-created")),
        ("GET", "/api/v1/enrollments/019254c1-7b3a-7c9e-9f21-3a4b5c6d7e8f"): (
            200,
            _example("enrollment-claimed"),
        ),
        ("POST", "/api/v1/players/019254c1-9c2e-7a13-8d4f-6e7f8a9b0c1d/heartbeat"): (202, {}),
    }

    with FakeControlPlaneServer(routes) as server:
        client = ControlPlaneClient(server.base_url)
        store = DeviceStore(tmp_path)
        identity = store.load_or_create()

        ticket = await asyncio.to_thread(client.create_enrollment, identity, "menu-player-01")
        assert ticket.code == "Q7KM-4HF2"

        state = await asyncio.to_thread(
            client.poll_enrollment, ticket.enrollment_id, identity.device_token
        )
        assert state.is_claimed

        claimed = store.record_claim(
            identity,
            player_id=state.player_id or "",
            player_name=state.player_name,
            organization_id=state.organization_id,
            organization_name=state.organization_name,
            location_id=state.location_id,
            location_name=state.location_name,
            enrolled_at="2026-08-29T04:00:00Z",
        )

        agent = HeartbeatAgent(
            CONFIG,
            ControlPlaneConfig(base_url=server.base_url, heartbeat_interval=30.0),
            client,
            store,
            claimed,
            sysfs_root=tmp_path / "no-drm",
        )
        stop_event = asyncio.Event()
        task = asyncio.create_task(agent.run(stop_event))
        while len(server.requests) < 3:
            await asyncio.sleep(0.01)
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)

    enrollment_request, poll_request, heartbeat_request = server.requests[:3]

    # The token travels in the body exactly once, at enrollment.
    assert enrollment_request["body"]["device_token"] == identity.device_token
    assert "Authorization" not in enrollment_request["headers"]

    # Thereafter it is a bearer credential.
    assert poll_request["headers"]["Authorization"] == f"Bearer {identity.device_token}"
    assert heartbeat_request["headers"]["Authorization"] == f"Bearer {identity.device_token}"
    assert heartbeat_request["headers"]["Content-Type"] == "application/json"
    assert set(heartbeat_request["body"]) == {
        "reported_at",
        "agent",
        "inventory",
        "configured_bindings",
    }
