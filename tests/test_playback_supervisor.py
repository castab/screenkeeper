from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from signage_controller.config import PlaybackConfig, PlayerConfig
from signage_controller.playback.supervisor import (
    PlayerSupervisor,
    _restart_delay,
    run_playback,
)

from .conftest import FakeProcessLauncher


def _tracer_with_exporter() -> tuple:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


FAST_KWARGS = dict(media_poll_interval=0.02, restart_backoff_multipliers=(1.0,))


def _player(id_: str = "dev-menu", media: Path | None = None) -> PlayerConfig:
    return PlayerConfig(id=id_, name=id_, media=media or Path("/media/menu.mp4"))


def _playback() -> PlaybackConfig:
    return PlaybackConfig(restart_delay=0.01)


@pytest.mark.asyncio
async def test_successful_spawn_logs_expected_sequence(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        **FAST_KWARGS,
    )
    (tmp_path / "menu.mp4").write_bytes(b"fake")

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    assert len(launcher.launches) == 1
    assert "starting mpv for" in caplog.text
    assert "mpv started pid=" in caplog.text
    assert "playback healthy" in caplog.text


@pytest.mark.asyncio
async def test_successful_start_emits_a_span_without_error_status(tmp_path) -> None:
    launcher = FakeProcessLauncher()
    tracer, exporter = _tracer_with_exporter()
    stop_event = asyncio.Event()
    (tmp_path / "menu.mp4").write_bytes(b"fake")
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        tracer=tracer,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    spans = [s for s in exporter.get_finished_spans() if s.name == "signage_controller.playback.start"]
    assert len(spans) == 1
    # OTel convention: successful spans stay UNSET; only failures get ERROR.
    assert spans[0].status.status_code == StatusCode.UNSET
    assert spans[0].attributes["player.id"] == "dev-menu"


@pytest.mark.asyncio
async def test_failed_start_emits_an_error_span_and_keeps_retrying(tmp_path) -> None:
    launcher = FakeProcessLauncher(outcomes=[OSError("no such file")] * 5)
    tracer, exporter = _tracer_with_exporter()
    stop_event = asyncio.Event()
    (tmp_path / "menu.mp4").write_bytes(b"fake")
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        tracer=tracer,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    spans = [s for s in exporter.get_finished_spans() if s.name == "signage_controller.playback.start"]
    assert len(spans) >= 1
    assert all(span.status.status_code == StatusCode.ERROR for span in spans)


@pytest.mark.asyncio
async def test_unexpected_exit_triggers_restart(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    (tmp_path / "menu.mp4").write_bytes(b"fake")
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    assert len(launcher.processes) == 1
    launcher.processes[0].finish(1)
    await asyncio.sleep(0.1)

    assert len(launcher.launches) >= 2
    assert "exited unexpectedly with status 1" in caplog.text

    stop_event.set()
    await task


@pytest.mark.asyncio
async def test_one_failed_player_does_not_stop_another(tmp_path) -> None:
    stop_event = asyncio.Event()
    (tmp_path / "good.mp4").write_bytes(b"fake")

    failing_launcher = FakeProcessLauncher(outcomes=[OSError("no such file")] * 20)
    healthy_launcher = FakeProcessLauncher()

    # The "failing" player's media exists so it reaches (and fails at) the
    # launch attempt, rather than sitting in the missing-media wait state.
    failing_media = tmp_path / "failing.mp4"
    failing_media.write_bytes(b"fake")
    failing_supervisor = PlayerSupervisor(
        _player("failing", media=failing_media),
        _playback(),
        tmp_path / "failing.sock",
        launcher=failing_launcher,
        **FAST_KWARGS,
    )

    healthy_supervisor = PlayerSupervisor(
        _player("healthy", media=tmp_path / "good.mp4"),
        _playback(),
        tmp_path / "healthy.sock",
        launcher=healthy_launcher,
        **FAST_KWARGS,
    )

    task = asyncio.gather(failing_supervisor.run(stop_event), healthy_supervisor.run(stop_event))
    await asyncio.sleep(0.15)

    assert len(failing_launcher.launches) >= 2
    assert len(healthy_launcher.launches) == 1
    assert not task.done() or task.exception() is None

    stop_event.set()
    await task


@pytest.mark.asyncio
async def test_missing_media_waits_without_spawning(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "missing.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.1)
    stop_event.set()
    await task

    assert launcher.launches == []
    assert caplog.text.count("media unavailable") == 1


@pytest.mark.asyncio
async def test_media_appearing_later_triggers_spawn(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO)
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    media_path = tmp_path / "menu.mp4"
    supervisor = PlayerSupervisor(
        _player(media=media_path),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    assert launcher.launches == []
    media_path.write_bytes(b"fake")
    await asyncio.sleep(0.1)

    assert len(launcher.launches) == 1
    assert "media became available" in caplog.text

    stop_event.set()
    await task


@pytest.mark.asyncio
async def test_shutdown_does_not_restart_and_cleans_up_process(tmp_path) -> None:
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    (tmp_path / "menu.mp4").write_bytes(b"fake")
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    assert len(launcher.launches) == 1

    stop_event.set()
    await asyncio.wait_for(task, timeout=5.0)

    assert len(launcher.launches) == 1
    process = launcher.processes[0]
    assert process.returncode is not None
    assert process.terminated or process.killed


def test_restart_delay_uses_supplied_multipliers_and_caps() -> None:
    multipliers = (1.0, 2.0, 4.0)

    assert _restart_delay(2.0, 0, multipliers) == 2.0
    assert _restart_delay(2.0, 1, multipliers) == 4.0
    assert _restart_delay(2.0, 2, multipliers) == 8.0
    # Beyond the last multiplier the delay stays capped rather than growing.
    assert _restart_delay(2.0, 3, multipliers) == 8.0
    assert _restart_delay(2.0, 99, multipliers) == 8.0


def test_restart_delay_without_multipliers_falls_back_to_base() -> None:
    assert _restart_delay(2.0, 5, ()) == 2.0


@pytest.mark.asyncio
async def test_supervisor_honors_injected_backoff_multipliers(tmp_path, caplog) -> None:
    """The injected multipliers must actually shape the logged retry delay."""
    caplog.set_level(logging.WARNING)
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    (tmp_path / "menu.mp4").write_bytes(b"fake")
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        PlaybackConfig(restart_delay=1.0),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        media_poll_interval=0.02,
        restart_backoff_multipliers=(7.0,),
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    launcher.processes[0].finish(1)
    await asyncio.sleep(0.05)

    assert "retrying in 7.0s" in caplog.text

    stop_event.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancelled_supervisor_does_not_orphan_process(tmp_path) -> None:
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    (tmp_path / "menu.mp4").write_bytes(b"fake")
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    assert len(launcher.processes) == 1

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert launcher.processes[0].returncode is not None


@pytest.mark.asyncio
async def test_run_playback_with_no_players_returns_immediately(tmp_path) -> None:
    stop_event = asyncio.Event()
    await asyncio.wait_for(
        run_playback(PlaybackConfig(players=()), stop_event, socket_dir=tmp_path),
        timeout=1.0,
    )


class RecordingPlayerStatusReporter:
    def __init__(self) -> None:
        self.healthy: list[tuple[str, bool]] = []
        self.restarts: list[str] = []

    def record_player_healthy(self, player_id: str, *, healthy: bool) -> None:
        self.healthy.append((player_id, healthy))

    def record_player_restart(self, player_id: str) -> None:
        self.restarts.append(player_id)


@pytest.mark.asyncio
async def test_supervisor_reports_healthy_and_restarts(tmp_path) -> None:
    launcher = FakeProcessLauncher()
    stop_event = asyncio.Event()
    (tmp_path / "menu.mp4").write_bytes(b"fake")
    reporter = RecordingPlayerStatusReporter()
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "menu.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=launcher,
        status_reporter=reporter,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    assert ("dev-menu", True) in reporter.healthy

    launcher.processes[0].finish(1)
    await asyncio.sleep(0.1)
    assert ("dev-menu", False) in reporter.healthy
    assert reporter.restarts == ["dev-menu"]

    stop_event.set()
    await task
    assert reporter.healthy[-1] == ("dev-menu", False)


@pytest.mark.asyncio
async def test_supervisor_reports_unhealthy_while_media_missing(tmp_path) -> None:
    stop_event = asyncio.Event()
    reporter = RecordingPlayerStatusReporter()
    supervisor = PlayerSupervisor(
        _player(media=tmp_path / "missing.mp4"),
        _playback(),
        tmp_path / "dev-menu.sock",
        launcher=FakeProcessLauncher(),
        status_reporter=reporter,
        **FAST_KWARGS,
    )

    task = asyncio.create_task(supervisor.run(stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await task

    assert reporter.healthy == [("dev-menu", False)]
