"""Local Prometheus metrics exposition.

Screenkeeper never transports telemetry itself: it only maintains in-memory
metric state and exposes it over a local `/metrics` HTTP endpoint for a
host-level agent (e.g. Grafana Alloy) to pull. There is no outbound network
call anywhere in this module, no credential, and no central-service URL —
those all belong to the host agent, not the application. This means metrics
collection can never block, retry, or fail control/playback behavior: the
worst case of a metrics bug is a `/metrics` response nobody scrapes.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Protocol

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    GCCollector,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

from .tv.base import TelevisionState


class StatusReporter(Protocol):
    """Receive partial TV controller status updates without affecting control flow."""

    def update(
        self,
        tv_id: str,
        *,
        connected: bool | None = None,
        state: TelevisionState | None = None,
    ) -> None:
        """Record the latest known connection and television state."""

    def record_command(
        self,
        tv_id: str,
        command: str,
        *,
        success: bool,
        duration: float | None = None,
    ) -> None:
        """Record the outcome and latency of one TV command."""


class PlayerStatusReporter(Protocol):
    """Receive partial mpv playback status updates without affecting control flow."""

    def record_player_healthy(self, player_id: str, *, healthy: bool) -> None:
        """Record whether a player's mpv process is currently running normally."""

    def record_player_restart(self, player_id: str) -> None:
        """Record one mpv restart for a player."""


class ControlPlaneStatusReporter(Protocol):
    """Receive heartbeat-agent outcomes without affecting control flow."""

    def record_heartbeat(self, *, success: bool, auth_rejected: bool = False) -> None:
        """Record the outcome of one heartbeat attempt."""


class PrometheusMetricsReporter:
    """Maintain in-memory Prometheus metrics for TVs and/or playback players.

    Each long-running process (the TV daemon, the playback process) owns one
    reporter and one `/metrics` server; a private `CollectorRegistry` avoids
    any global registry state, which keeps tests isolated and processes fully
    independent of each other.
    """

    def __init__(
        self,
        *,
        tv_ids: Iterable[str] = (),
        player_ids: Iterable[str] = (),
        control_plane: bool = False,
    ) -> None:
        self.registry = CollectorRegistry()
        self._tv_ids = tuple(tv_ids)
        self._player_ids = tuple(player_ids)
        self._control_plane = control_plane
        self._current_input: dict[str, str] = {}

        # A private registry starts empty, unlike prometheus_client's global
        # default -- register the standard process/runtime metrics explicitly
        # so `process_start_time_seconds` etc. answer "when did this process
        # last start" without a bespoke uptime gauge.
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)

        if self._tv_ids:
            self._tv_connection = Gauge(
                "signage_controller_tv_connection_up",
                "Whether the controller currently has a live connection to the TV.",
                ["tv_id"],
                registry=self.registry,
            )
            self._tv_power = Gauge(
                "signage_controller_tv_power_on",
                "Last observed TV power state (1 = on, 0 = off).",
                ["tv_id"],
                registry=self.registry,
            )
            self._tv_volume = Gauge(
                "signage_controller_tv_volume",
                "Last observed TV volume level.",
                ["tv_id"],
                registry=self.registry,
            )
            self._tv_input = Gauge(
                "signage_controller_tv_input",
                "Set to 1 for the currently active input id of a TV.",
                ["tv_id", "input_id"],
                registry=self.registry,
            )
            self._tv_updated = Gauge(
                "signage_controller_tv_status_updated_timestamp_seconds",
                "Unix timestamp of the last observed status update for a TV.",
                ["tv_id"],
                registry=self.registry,
            )
            self._tv_commands = Counter(
                "signage_controller_tv_commands_total",
                "TV commands issued, by outcome.",
                ["tv_id", "command", "result"],
                registry=self.registry,
            )
            self._tv_command_duration = Histogram(
                "signage_controller_tv_command_duration_seconds",
                "Latency of TV commands.",
                ["tv_id", "command"],
                registry=self.registry,
            )
            for tv_id in self._tv_ids:
                self._tv_connection.labels(tv_id=tv_id).set(0)

        if self._player_ids:
            self._player_up = Gauge(
                "signage_controller_player_up",
                "Whether a player's mpv process is currently running normally.",
                ["player_id"],
                registry=self.registry,
            )
            self._player_restarts = Counter(
                "signage_controller_player_restarts_total",
                "mpv restarts for a player, after an unexpected exit.",
                ["player_id"],
                registry=self.registry,
            )
            self._player_updated = Gauge(
                "signage_controller_player_status_updated_timestamp_seconds",
                "Unix timestamp of the last observed status update for a player.",
                ["player_id"],
                registry=self.registry,
            )
            for player_id in self._player_ids:
                self._player_up.labels(player_id=player_id).set(0)

        if self._control_plane:
            self._control_plane_reachable = Gauge(
                "signage_controller_control_plane_reachable",
                "Whether the last heartbeat attempt reached the control plane.",
                registry=self.registry,
            )
            self._control_plane_auth_rejected = Gauge(
                "signage_controller_control_plane_auth_rejected",
                "Whether the control plane is currently rejecting this device's credential.",
                registry=self.registry,
            )
            self._control_plane_heartbeats = Counter(
                "signage_controller_control_plane_heartbeats_total",
                "Heartbeat attempts, by outcome.",
                ["result"],
                registry=self.registry,
            )
            self._control_plane_last_success = Gauge(
                "signage_controller_control_plane_last_success_timestamp_seconds",
                "Unix timestamp of the last successful heartbeat.",
                registry=self.registry,
            )
            self._control_plane_reachable.set(0)
            self._control_plane_auth_rejected.set(0)
            self._control_plane_heartbeats.labels(result="success")
            self._control_plane_heartbeats.labels(result="failure")

    def update(
        self,
        tv_id: str,
        *,
        connected: bool | None = None,
        state: TelevisionState | None = None,
    ) -> None:
        """Record one partial TV status update."""
        changed = False
        if connected is not None:
            self._tv_connection.labels(tv_id=tv_id).set(int(connected))
            changed = True
        if state is not None:
            if state.is_on is not None:
                self._tv_power.labels(tv_id=tv_id).set(int(state.is_on))
                changed = True
            if state.current_input:
                previous = self._current_input.get(tv_id)
                if previous and previous != state.current_input:
                    self._tv_input.remove(tv_id, previous)
                self._current_input[tv_id] = state.current_input
                self._tv_input.labels(tv_id=tv_id, input_id=state.current_input).set(1)
                changed = True
            if state.volume is not None:
                self._tv_volume.labels(tv_id=tv_id).set(state.volume)
                changed = True
        if changed:
            self._tv_updated.labels(tv_id=tv_id).set(time.time())

    def record_command(
        self,
        tv_id: str,
        command: str,
        *,
        success: bool,
        duration: float | None = None,
    ) -> None:
        """Record one TV command's outcome and, if known, its latency."""
        result = "success" if success else "failure"
        self._tv_commands.labels(tv_id=tv_id, command=command, result=result).inc()
        if duration is not None:
            self._tv_command_duration.labels(tv_id=tv_id, command=command).observe(duration)

    def mark_all_unavailable(self) -> None:
        """Record graceful shutdown without discarding the last observed status."""
        for tv_id in self._tv_ids:
            self.update(tv_id, connected=False)

    def record_player_healthy(self, player_id: str, *, healthy: bool) -> None:
        """Record whether a player's mpv process is currently running normally."""
        self._player_up.labels(player_id=player_id).set(1 if healthy else 0)
        self._player_updated.labels(player_id=player_id).set(time.time())

    def record_player_restart(self, player_id: str) -> None:
        """Record one mpv restart for a player."""
        self._player_restarts.labels(player_id=player_id).inc()

    def record_heartbeat(self, *, success: bool, auth_rejected: bool = False) -> None:
        """Record the outcome of one heartbeat attempt."""
        result = "success" if success else "failure"
        self._control_plane_heartbeats.labels(result=result).inc()
        self._control_plane_reachable.set(1 if success else 0)
        self._control_plane_auth_rejected.set(1 if auth_rejected else 0)
        if success:
            self._control_plane_last_success.set(time.time())


def build_metrics_app(registry: CollectorRegistry) -> web.Application:
    """Build the `/metrics` aiohttp application for one registry.

    Separated from `MetricsServer` so tests can exercise the handler with
    `aiohttp.test_utils` instead of binding a real socket.
    """

    async def _handle(request: web.Request) -> web.Response:
        # aiohttp's `content_type=` rejects a charset in the same string, so the
        # full Prometheus content-type (which includes one) is set via headers.
        return web.Response(
            body=generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST}
        )

    app = web.Application()
    app.router.add_get("/metrics", _handle)
    return app


class MetricsServer:
    """Serve one reporter's registry as Prometheus exposition text.

    Runs on the caller's asyncio event loop (no extra thread), so it starts
    and stops cleanly alongside the rest of a daemon's lifecycle.
    """

    def __init__(self, registry: CollectorRegistry, host: str, port: int) -> None:
        self._registry = registry
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        runner = web.AppRunner(build_metrics_app(self._registry))
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        self._runner = runner

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
