"""Per-player playback supervision: retry/backoff and missing-media handling.

Mirrors the async supervision shape of `controller.TvManager`/`run_all`
(catch-and-backoff on everything but CancelledError, one failed task never
cancels the others) but is otherwise fully independent of TV control.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from ..config import PlaybackConfig, PlayerConfig
from ..content.resolver import MediaResolver, ResolvedMedia
from ..observability import PlayerStatusReporter
from .mpv import MpvPlayer, ProcessLauncher, default_launcher


LOGGER = logging.getLogger(__name__)
RESTART_BACKOFF_MULTIPLIERS = (1.0, 2.0, 4.0, 8.0, 15.0)
HEALTHY_RESET_SECONDS = 60.0
MEDIA_POLL_INTERVAL_SECONDS = 5.0


def resolve_socket_dir() -> Path:
    """Prefer $XDG_RUNTIME_DIR/screenkeeper/mpv, else a per-uid temp fallback."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        base = Path(runtime_dir) / "screenkeeper" / "mpv"
    else:
        base = Path(tempfile.gettempdir()) / f"screenkeeper-{os.getuid()}" / "mpv"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    return base


def _restart_delay(base: float, failure_streak: int, multipliers: tuple[float, ...]) -> float:
    """Return the capped-backoff delay for the current consecutive-failure count."""
    if not multipliers:
        return base
    multiplier = multipliers[min(failure_streak, len(multipliers) - 1)]
    return base * multiplier


class PlayerSupervisor:
    """Keep one configured player alive: retry/backoff and missing-media wait."""

    def __init__(
        self,
        player: PlayerConfig,
        playback: PlaybackConfig,
        socket_path: Path,
        *,
        launcher: ProcessLauncher = default_launcher,
        logger: logging.Logger | None = None,
        media_poll_interval: float = MEDIA_POLL_INTERVAL_SECONDS,
        restart_backoff_multipliers: tuple[float, ...] = RESTART_BACKOFF_MULTIPLIERS,
        healthy_reset_seconds: float = HEALTHY_RESET_SECONDS,
        status_reporter: PlayerStatusReporter | None = None,
        tracer: trace.Tracer | None = None,
        media_resolver: MediaResolver | None = None,
    ) -> None:
        self.player = player
        self.playback = playback
        self.socket_path = socket_path
        self._launcher = launcher
        self.logger = logging.LoggerAdapter(logger or LOGGER, {"player_id": player.id})
        self._media_poll_interval = media_poll_interval
        self._backoff = restart_backoff_multipliers
        self._healthy_reset_seconds = healthy_reset_seconds
        self.status_reporter = status_reporter
        self.tracer = tracer or trace.get_tracer(__name__)
        self._current_player: MpvPlayer | None = None
        self.media_resolver = media_resolver or MediaResolver()

    def _report_healthy(self, *, healthy: bool) -> None:
        if self.status_reporter is not None:
            self.status_reporter.record_player_healthy(self.player.id, healthy=healthy)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run until stopped, waiting on missing media and restarting on crash."""
        # Clear a marker left by an ungraceful prior process before claiming
        # that this new supervisor has actually started anything.
        self.media_resolver.record_playing(self.player.id, None)
        try:
            await self._run_loop(stop_event, failure_streak=0, media_missing_logged=False)
        finally:
            # Safety net: a cancelled supervisor cannot await a graceful stop,
            # so make sure no mpv process is left running behind us.
            if self._current_player is not None:
                self._current_player.terminate_now()
                self._current_player = None
            self.media_resolver.record_playing(self.player.id, None)

    async def _run_loop(
        self, stop_event: asyncio.Event, *, failure_streak: int, media_missing_logged: bool
    ) -> None:
        while not stop_event.is_set():
            try:
                resolved = self.media_resolver.resolve(self.player)
                if not resolved.path.exists():
                    if not media_missing_logged:
                        self.logger.info(
                            "%s: media unavailable; waiting for %s",
                            self.player.id,
                            resolved.path,
                        )
                        media_missing_logged = True
                        self._report_healthy(healthy=False)
                    await self._wait_or_stop(stop_event, self._media_poll_interval)
                    continue
                if media_missing_logged:
                    self.logger.info("%s: media became available", self.player.id)
                    media_missing_logged = False

                mpv_player = MpvPlayer(
                    self.player,
                    self.playback,
                    self.socket_path,
                    self.logger,
                    media_path=resolved.path,
                    launcher=self._launcher,
                )
                start_time = asyncio.get_running_loop().time()
                span_attributes = {"player.id": self.player.id}
                if self.player.tv_id is not None:
                    span_attributes["player.tv_id"] = self.player.tv_id
                with self.tracer.start_as_current_span(
                    "signage_controller.playback.start", attributes=span_attributes
                ) as span:
                    try:
                        await mpv_player.start()
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR))
                        raise
                self._current_player = mpv_player
                self.logger.info("%s: playback healthy", self.player.id)
                self._report_healthy(healthy=True)
                self.media_resolver.record_playing(self.player.id, resolved.asset_revision_id)

                wait_task = asyncio.create_task(mpv_player.wait())
                stop_task = asyncio.create_task(stop_event.wait())
                change_task = asyncio.create_task(self._wait_for_media_change(stop_event, resolved))
                done, pending = await asyncio.wait(
                    {wait_task, stop_task, change_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

                if stop_task in done:
                    await mpv_player.stop()
                    self._current_player = None
                    self.media_resolver.record_playing(self.player.id, None)
                    self._report_healthy(healthy=False)
                    return

                if change_task in done and change_task.result() is not None:
                    replacement = change_task.result()
                    self.logger.info(
                        "%s: verified media changed to %s; restarting playback",
                        self.player.id,
                        replacement.path,
                    )
                    await mpv_player.stop()
                    self._current_player = None
                    self.media_resolver.record_playing(self.player.id, None)
                    continue

                self._current_player = None
                self.media_resolver.record_playing(self.player.id, None)
                returncode = wait_task.result()
                elapsed = asyncio.get_running_loop().time() - start_time
                if elapsed >= self._healthy_reset_seconds:
                    failure_streak = 0
                delay = _restart_delay(self.playback.restart_delay, failure_streak, self._backoff)
                tail = "; ".join(mpv_player.recent_output[-5:])
                self.logger.warning(
                    "%s: mpv exited unexpectedly with status %s; retrying in %ss%s",
                    self.player.id,
                    returncode,
                    round(delay, 1),
                    f" (recent output: {tail})" if tail else "",
                )
                failure_streak += 1
                self._report_healthy(healthy=False)
                if self.status_reporter is not None:
                    self.status_reporter.record_player_restart(self.player.id)
                await self._wait_or_stop(stop_event, delay)
            except asyncio.CancelledError:
                raise
            except OSError as err:
                # e.g. the mpv binary could not be found or executed.
                delay = _restart_delay(self.playback.restart_delay, failure_streak, self._backoff)
                self.logger.warning(
                    "%s: failed to start mpv: %s; retrying in %ss",
                    self.player.id,
                    err,
                    round(delay, 1),
                )
                failure_streak += 1
                self._report_healthy(healthy=False)
                if self.status_reporter is not None:
                    self.status_reporter.record_player_restart(self.player.id)
                await self._wait_or_stop(stop_event, delay)
            except Exception:
                # One unexpected supervisor bug must not end this or other players' tasks.
                self.logger.exception("%s: unexpected playback supervisor failure", self.player.id)
                delay = _restart_delay(self.playback.restart_delay, failure_streak, self._backoff)
                failure_streak += 1
                self._report_healthy(healthy=False)
                if self.status_reporter is not None:
                    self.status_reporter.record_player_restart(self.player.id)
                await self._wait_or_stop(stop_event, delay)

    @staticmethod
    async def _wait_or_stop(stop_event: asyncio.Event, delay: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)

    async def _wait_for_media_change(
        self, stop_event: asyncio.Event, current: ResolvedMedia
    ) -> ResolvedMedia | None:
        while not stop_event.is_set():
            await self._wait_or_stop(stop_event, self._media_poll_interval)
            resolved = self.media_resolver.resolve(self.player)
            if resolved != current and resolved.path.exists():
                return resolved
        return None


async def run_playback(
    playback: PlaybackConfig,
    stop_event: asyncio.Event,
    *,
    socket_dir: Path | None = None,
    launcher: ProcessLauncher = default_launcher,
    status_reporter: PlayerStatusReporter | None = None,
    tracer: trace.Tracer | None = None,
    media_resolver: MediaResolver | None = None,
) -> None:
    """Run an independent supervisor task for every configured player."""
    if not playback.players:
        LOGGER.info("No playback players configured; nothing to supervise.")
        return
    resolved_socket_dir = socket_dir if socket_dir is not None else resolve_socket_dir()
    supervisors = [
        PlayerSupervisor(
            player,
            playback,
            resolved_socket_dir / f"{player.id}.sock",
            launcher=launcher,
            status_reporter=status_reporter,
            tracer=tracer,
            media_resolver=media_resolver,
        )
        for player in playback.players
    ]
    await asyncio.gather(*(supervisor.run(stop_event) for supervisor in supervisors))
