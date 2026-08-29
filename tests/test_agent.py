from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from signage_controller.config import (
    ApplicationConfig,
    ControlPlaneConfig,
    PlaybackConfig,
    PlayerConfig,
    TvConfig,
)
from signage_controller.control_plane.agent import (
    HeartbeatAgent,
    _backoff_delay,
    _with_jitter,
)
from signage_controller.control_plane.client import ControlPlaneClient
from signage_controller.control_plane.identity import DeviceStore
from signage_controller.http_client import URLError

from .conftest import FakeTransport


class StubStatusReporter:
    """Records `record_heartbeat` calls without touching any real metric."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, bool]] = []

    def record_heartbeat(self, *, success: bool, auth_rejected: bool = False) -> None:
        self.calls.append((success, auth_rejected))


BASE_URL = "https://screenkeeper.example.com"
ACCEPTED = (202, {})
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
# Near-zero delays keep the loop tests fast without changing their shape.
FAST_BACKOFF = (0.01, 0.02, 0.03)


def _agent(
    tmp_path: Path,
    transport: FakeTransport,
    *,
    heartbeat_interval: float = 0.01,
    logger: logging.Logger | None = None,
    **overrides,
) -> tuple[HeartbeatAgent, DeviceStore]:
    store = DeviceStore(tmp_path)
    identity = store.record_claim(
        store.load_or_create(),
        player_id="player-1",
        player_name="Main Menu Wall",
        organization_id="org-1",
        organization_name="Example Restaurant",
        location_id="loc-1",
        location_name="Downtown",
        enrolled_at="2026-08-29T04:00:00Z",
    )
    control_plane = ControlPlaneConfig(base_url=BASE_URL, heartbeat_interval=heartbeat_interval)
    settings = {
        "backoff_delays": FAST_BACKOFF,
        "auth_backoff_seconds": 0.05,
        # Deterministic timing: jitter is exercised separately.
        "jitter_fraction": 0.0,
        "sysfs_root": tmp_path / "no-drm",
    }
    settings.update(overrides)
    agent = HeartbeatAgent(
        CONFIG,
        control_plane,
        ControlPlaneClient(BASE_URL, transport=transport),
        store,
        identity,
        logger=logger,
        **settings,
    )
    return agent, store


def _tracer_with_exporter() -> tuple:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


async def _run_until(agent: HeartbeatAgent, transport: FakeTransport, calls: int) -> None:
    """Run the agent until it has made `calls` requests, then stop it cleanly."""
    stop_event = asyncio.Event()
    task = asyncio.create_task(agent.run(stop_event))
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while transport.call_count < calls:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"only {transport.call_count} of {calls} calls were made")
            await asyncio.sleep(0.01)
    finally:
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)


async def test_successful_heartbeat_emits_a_span_without_error_status(tmp_path: Path) -> None:
    transport = FakeTransport([ACCEPTED])
    tracer, exporter = _tracer_with_exporter()
    agent, _ = _agent(tmp_path, transport, tracer=tracer)

    await _run_until(agent, transport, 1)

    spans = [
        s for s in exporter.get_finished_spans() if s.name == "signage_controller.control_plane.heartbeat"
    ]
    assert len(spans) == 1
    # OTel convention: successful spans stay UNSET; only failures get ERROR.
    assert spans[0].status.status_code == StatusCode.UNSET
    assert spans[0].attributes["heartbeat.result"] == "success"


async def test_auth_rejected_heartbeat_emits_an_error_span(tmp_path: Path) -> None:
    transport = FakeTransport([(401, {"error": "unknown_device"})])
    tracer, exporter = _tracer_with_exporter()
    agent, _ = _agent(tmp_path, transport, tracer=tracer)

    await _run_until(agent, transport, 1)

    spans = [
        s for s in exporter.get_finished_spans() if s.name == "signage_controller.control_plane.heartbeat"
    ]
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert spans[0].attributes["heartbeat.result"] == "auth_rejected"


async def test_heartbeat_uses_the_authenticated_v1_player_path(tmp_path: Path) -> None:
    transport = FakeTransport([ACCEPTED])
    agent, _ = _agent(tmp_path, transport)

    await _run_until(agent, transport, 1)

    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"{BASE_URL}/api/v1/players/player-1/heartbeat"
    assert request["token"] == agent.identity.device_token


async def test_serialized_report_matches_the_contract_shape(tmp_path: Path) -> None:
    transport = FakeTransport([ACCEPTED])
    agent, _ = _agent(tmp_path, transport)

    await _run_until(agent, transport, 1)

    body = transport.requests[0]["payload"]
    assert set(body) == {"reported_at", "agent", "inventory", "configured_bindings"}
    assert body["reported_at"].endswith("Z")
    assert set(body["inventory"]) == {"gpus", "connectors"}
    assert body["configured_bindings"]["tvs"][0]["driver"] == "lg-webos"
    assert body["configured_bindings"]["playback_players"][0]["screen_name"] == "DP-1"


async def test_the_report_never_carries_the_device_token(tmp_path: Path) -> None:
    transport = FakeTransport([ACCEPTED])
    agent, _ = _agent(tmp_path, transport)

    await _run_until(agent, transport, 1)

    import json

    assert agent.identity.device_token not in json.dumps(transport.requests[0]["payload"])


async def test_a_transient_failure_does_not_kill_the_agent(tmp_path: Path) -> None:
    transport = FakeTransport([URLError("network down"), (503, {}), ACCEPTED])
    agent, _ = _agent(tmp_path, transport)

    await _run_until(agent, transport, 3)

    assert transport.call_count >= 3


async def test_a_successful_heartbeat_after_failures_resets_the_failure_state(
    tmp_path: Path,
) -> None:
    transport = FakeTransport([URLError("network down"), URLError("still down"), ACCEPTED])
    agent, _ = _agent(tmp_path, transport)

    await _run_until(agent, transport, 3)

    assert agent._failure_streak == 0
    assert agent._reachable is True


async def test_outage_and_recovery_are_logged_once_each_not_per_retry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A host offline for days must not fill the journal with retry lines."""
    logger = logging.getLogger("test_agent.transitions")
    transport = FakeTransport(
        [URLError("down"), URLError("down"), URLError("down"), ACCEPTED]
    )
    agent, _ = _agent(tmp_path, transport, logger=logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        await _run_until(agent, transport, 4)

    messages = [record.getMessage() for record in caplog.records]
    assert sum("unavailable" in message for message in messages) == 1
    assert sum("restored" in message for message in messages) == 1


async def test_an_authentication_failure_backs_off_heavily_and_keeps_identity(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("test_agent.auth")
    transport = FakeTransport([(401, {"error": "unknown_device"})] * 4)
    agent, store = _agent(tmp_path, transport, auth_backoff_seconds=30.0, logger=logger)
    before = store.load()

    with caplog.at_level(logging.ERROR, logger=logger.name):
        stop_event = asyncio.Event()
        task = asyncio.create_task(agent.run(stop_event))
        await asyncio.sleep(0.1)
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)

    # One attempt, then a 30-second wait: no hammering on a rejected credential.
    assert transport.call_count == 1
    errors = [record.getMessage() for record in caplog.records]
    assert any("Authentication rejected" in message for message in errors)
    assert any("device enroll" in message for message in errors)

    after = store.load()
    assert before is not None and after is not None
    assert after.installation_id == before.installation_id
    assert after.device_token == before.device_token
    assert after.player_id == before.player_id


async def test_a_rejected_report_does_not_stop_the_agent(tmp_path: Path) -> None:
    transport = FakeTransport([(400, {"error": "bad_request"}), ACCEPTED])
    agent, _ = _agent(tmp_path, transport)

    await _run_until(agent, transport, 2)

    assert agent._reachable is True


async def test_a_successful_heartbeat_is_recorded_for_device_status(tmp_path: Path) -> None:
    transport = FakeTransport([ACCEPTED])
    agent, store = _agent(tmp_path, transport)

    await _run_until(agent, transport, 1)

    reloaded = store.load()
    assert reloaded is not None
    assert reloaded.last_heartbeat_at is not None
    assert reloaded.last_heartbeat_at.endswith("Z")


async def test_shutdown_exits_cleanly(tmp_path: Path) -> None:
    transport = FakeTransport([ACCEPTED] * 20)
    agent, _ = _agent(tmp_path, transport, heartbeat_interval=30.0)
    stop_event = asyncio.Event()

    task = asyncio.create_task(agent.run(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()

    # A long heartbeat interval must not delay shutdown: the wait races the stop.
    await asyncio.wait_for(task, timeout=2)
    assert task.done()
    assert task.exception() is None


async def test_an_unexpected_error_is_contained(tmp_path: Path) -> None:
    """One bug in collection must not end the agent process."""
    transport = FakeTransport([ACCEPTED])
    agent, _ = _agent(tmp_path, transport)
    calls: list[int] = []
    original = agent._collect

    def exploding() -> dict:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("inventory blew up")
        return original()

    agent._collect = exploding

    await _run_until(agent, transport, 1)

    assert len(calls) >= 2


async def test_a_successful_heartbeat_reports_success_to_the_status_reporter(
    tmp_path: Path,
) -> None:
    transport = FakeTransport([ACCEPTED])
    stub = StubStatusReporter()
    agent, _ = _agent(tmp_path, transport, status_reporter=stub)

    await _run_until(agent, transport, 1)

    assert stub.calls == [(True, False)]


async def test_a_transient_failure_reports_failure_to_the_status_reporter(
    tmp_path: Path,
) -> None:
    transport = FakeTransport([URLError("network down"), ACCEPTED])
    stub = StubStatusReporter()
    agent, _ = _agent(tmp_path, transport, status_reporter=stub)

    await _run_until(agent, transport, 2)

    assert stub.calls == [(False, False), (True, False)]


async def test_an_auth_failure_reports_auth_rejected_to_the_status_reporter(
    tmp_path: Path,
) -> None:
    transport = FakeTransport([(401, {"error": "unknown_device"})] * 2)
    stub = StubStatusReporter()
    agent, _ = _agent(tmp_path, transport, auth_backoff_seconds=0.01, status_reporter=stub)

    await _run_until(agent, transport, 2)

    assert stub.calls == [(False, True), (False, True)]


def test_backoff_progresses_then_caps() -> None:
    delays = (5.0, 10.0, 20.0, 30.0, 60.0)

    progression = [_backoff_delay(streak, delays) for streak in range(8)]

    assert progression == [5.0, 10.0, 20.0, 30.0, 60.0, 60.0, 60.0, 60.0]


def test_backoff_with_no_configured_delays_is_zero() -> None:
    assert _backoff_delay(3, ()) == 0.0


def test_jitter_stays_within_the_configured_fraction() -> None:
    rng = random.Random(1234)

    samples = [_with_jitter(60.0, 0.2, rng) for _ in range(200)]

    assert all(48.0 <= sample <= 72.0 for sample in samples)
    # Jitter must actually vary, or a fleet reconnects in lockstep.
    assert len(set(samples)) > 1


def test_jitter_is_skipped_when_disabled() -> None:
    assert _with_jitter(60.0, 0.0, random.Random(1)) == 60.0
    assert _with_jitter(0.0, 0.2, random.Random(1)) == 0.0
