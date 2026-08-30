"""mpv argv construction and single-process launch/stop/query abstraction.

Flag choices below (`--no-config`, `--loop-file=inf`, `--osd-level=0`,
`--audio=no`/`--audio=auto`, `--screen`/`--fs-screen`,
`--screen-name`/`--fs-screen-name`, `--input-ipc-server`) were checked
against the current mpv manual and upstream PR history while designing this
module. `--screen-name`/`--fs-screen-name` take a display/connector name
directly (X11 via xrandr output names, Wayland via compositor output names,
macOS via NSScreen names) and are natively mutually exclusive with their
numeric `--screen`/`--fs-screen` counterparts, matching this project's own
`screen`/`screen_name` config validation. Re-check `mpv --list-options` on
the target machine's installed mpv version before relying on this in
production, since exact behavior can shift between mpv releases.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from ..config import PlaybackConfig, PlayerConfig
from .ipc import MpvIpcClient, MpvIpcError


LOGGER = logging.getLogger(__name__)
OUTPUT_BUFFER_LINES = 50
DEFAULT_IPC_QUIT_TIMEOUT = 1.0
DEFAULT_IPC_EXIT_WAIT = 2.0
DEFAULT_TERMINATE_TIMEOUT = 2.0


def build_mpv_argv(
    player: PlayerConfig,
    playback: PlaybackConfig,
    socket_path: Path,
    media_path: Path | None = None,
) -> list[str]:
    """Build the mpv argv for one player. Pure: no filesystem/subprocess/env access."""
    argv: list[str] = [playback.mpv_binary, "--no-config"]
    if playback.fullscreen:
        argv.append("--fullscreen")
    if playback.loop:
        argv.append("--loop-file=inf")
    argv += [
        "--no-osc",
        "--osd-level=0",
        "--input-default-bindings=no",
        "--input-terminal=no",
    ]
    argv.append("--audio=no" if not playback.audio else "--audio=auto")
    argv.append(f"--hwdec={playback.hwdec}")
    if player.screen is not None:
        argv.append(f"--screen={player.screen}")
        argv.append(f"--fs-screen={player.screen}")
    elif player.screen_name is not None:
        argv.append(f"--screen-name={player.screen_name}")
        argv.append(f"--fs-screen-name={player.screen_name}")
    argv.append(f"--input-ipc-server={socket_path}")
    argv.append("--")
    argv.append(str(media_path or player.media))
    return argv


class ProcessHandle(Protocol):
    """The narrow subset of asyncio.subprocess.Process this module depends on."""

    pid: int
    returncode: int | None
    stdout: asyncio.StreamReader | None

    async def wait(self) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


ProcessLauncher = Callable[[list[str]], Awaitable[ProcessHandle]]


async def default_launcher(argv: list[str]) -> asyncio.subprocess.Process:
    """The only call site of create_subprocess_exec in the playback subsystem."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


def _remove_stale_socket(socket_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        socket_path.unlink()


class MpvPlayer:
    """Launch, stop, and query one mpv process for one configured player."""

    def __init__(
        self,
        player: PlayerConfig,
        playback: PlaybackConfig,
        socket_path: Path,
        logger: logging.Logger | logging.LoggerAdapter,
        media_path: Path | None = None,
        *,
        launcher: ProcessLauncher = default_launcher,
        ipc_quit_timeout: float = DEFAULT_IPC_QUIT_TIMEOUT,
        ipc_exit_wait: float = DEFAULT_IPC_EXIT_WAIT,
        terminate_timeout: float = DEFAULT_TERMINATE_TIMEOUT,
    ) -> None:
        self.player = player
        self.playback = playback
        self.socket_path = socket_path
        self.logger = logger
        self.media_path = media_path or player.media
        self._launcher = launcher
        self._ipc_quit_timeout = ipc_quit_timeout
        self._ipc_exit_wait = ipc_exit_wait
        self._terminate_timeout = terminate_timeout
        self._process: ProcessHandle | None = None
        self._output: deque[str] = deque(maxlen=OUTPUT_BUFFER_LINES)
        self._output_task: asyncio.Task[None] | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def recent_output(self) -> list[str]:
        return list(self._output)

    async def start(self) -> None:
        """Launch mpv for this player."""
        _remove_stale_socket(self.socket_path)
        argv = build_mpv_argv(self.player, self.playback, self.socket_path, self.media_path)
        self.logger.info("%s: starting mpv for %s", self.player.id, self.media_path)
        self._process = await self._launcher(argv)
        if self._process.stdout is not None:
            self._output_task = asyncio.create_task(self._drain_output())
        self.logger.info("%s: mpv started pid=%s", self.player.id, self._process.pid)

    async def _drain_output(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            self._output.append(text)
            self.logger.debug("%s: mpv: %s", self.player.id, text)

    async def wait(self) -> int:
        """Wait for the mpv process to exit and return its exit code."""
        assert self._process is not None
        returncode = await self._process.wait()
        if self._output_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._output_task
        return returncode

    async def stop(self) -> None:
        """Stop mpv: IPC quit, then SIGTERM, then SIGKILL as a last resort."""
        if self._process is None or self._process.returncode is not None:
            self._cancel_output_task()
            return
        self.logger.info("%s: stopping mpv", self.player.id)
        if not await self._try_ipc_quit():
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()
            if not await self._wait_bounded(self._terminate_timeout):
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
                await self._process.wait()
        self._cancel_output_task()
        self.logger.info("%s: mpv stopped", self.player.id)

    def terminate_now(self) -> None:
        """Best-effort synchronous kill for cancellation paths that cannot await.

        Used as a last-resort safety net so a cancelled supervisor task never
        leaves an orphaned mpv process behind. Prefer `stop()` whenever the
        caller can await it.
        """
        self._cancel_output_task()
        if self._process is None or self._process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            self._process.kill()

    def _cancel_output_task(self) -> None:
        if self._output_task is not None and not self._output_task.done():
            self._output_task.cancel()

    async def _try_ipc_quit(self) -> bool:
        try:
            client = MpvIpcClient(self.socket_path, timeout=self._ipc_quit_timeout)
            await client.quit()
        except (MpvIpcError, OSError, TimeoutError) as err:
            self.logger.debug("%s: mpv IPC quit unavailable: %s", self.player.id, err)
            return False
        return await self._wait_bounded(self._ipc_exit_wait)

    async def _wait_bounded(self, timeout: float) -> bool:
        assert self._process is not None
        try:
            await asyncio.wait_for(self._process.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True
