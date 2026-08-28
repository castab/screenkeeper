"""Command-line commissioning and runtime controls."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import shlex
import shutil
import signal
from pathlib import Path

from . import __version__
from .config import ApplicationConfig, ConfigurationError, PlaybackConfig, TvConfig, load_config
from .controller import DesiredInputError, converge, run_all
from .observability import LokiHandler, ObservabilityError, PrometheusStatusReporter
from .playback.mpv import MpvPlayer, build_mpv_argv, default_launcher
from .playback.supervisor import resolve_socket_dir, run_playback
from .runtime_lock import ControllerAlreadyRunningError, acquire_controller_lock
from .state_store import StateStore, StateStoreError
from .tv.base import Television, TelevisionError
from .tv.lg_webos import LgWebOsTelevision
from .updater import (
    DEFAULT_KEEP_VERSIONS,
    UPDATE_AVAILABLE_EXIT_CODE,
    InstallLayout,
    UpdateError,
    activate_version,
    default_repository,
    install_release,
    normalize_version,
    prune_versions,
    resolve_release,
    restart_services,
    update_lock,
    version_key,
)


LOGGER = logging.getLogger(__name__)

# Commands that manage the installation itself. They must not load config.yaml:
# an upgrade has to work on a host whose configuration is missing or broken.
UPDATE_COMMANDS = frozenset({"upgrade", "rollback", "versions"})


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Local desired-state controller for LG webOS TVs and mpv signage playback."
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="YAML configuration path")
    parser.add_argument("--state-dir", type=Path, help="Runtime state directory")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("pair", "status", "inputs", "apply"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("tv_id")
    commands.add_parser("run")

    playback_parser = commands.add_parser(
        "playback", help="Local mpv signage playback, independent of TV control."
    )
    playback_commands = playback_parser.add_subparsers(dest="playback_command", required=True)
    playback_commands.add_parser("check")
    playback_command_parser = playback_commands.add_parser("command")
    playback_command_parser.add_argument("player_id")
    playback_start_parser = playback_commands.add_parser("start")
    playback_start_parser.add_argument("player_id")
    playback_commands.add_parser("run")

    upgrade_parser = commands.add_parser(
        "upgrade", help="Install and activate the latest published release."
    )
    upgrade_parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Report whether a newer release exists without installing it. "
            f"Exits {UPDATE_AVAILABLE_EXIT_CODE} when an update is available."
        ),
    )
    upgrade_parser.add_argument(
        "--to", dest="target_version", help="Install this version instead of the latest release."
    )
    upgrade_parser.add_argument(
        "--repository", default=default_repository(), help="GitHub repository publishing releases."
    )
    upgrade_parser.add_argument(
        "--force", action="store_true", help="Rebuild the version directory if it already exists."
    )
    upgrade_parser.add_argument(
        "--allow-downgrade", action="store_true", help="Permit installing an older version."
    )
    upgrade_parser.add_argument(
        "--require-checksum",
        action="store_true",
        help="Fail when the release publishes no SHA256SUMS entry for its archive.",
    )
    upgrade_parser.add_argument(
        "--no-restart", action="store_true", help="Activate the new version without restarting units."
    )
    upgrade_parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP_VERSIONS,
        help="Number of installed versions to retain (default: %(default)s).",
    )

    rollback_parser = commands.add_parser(
        "rollback", help="Reactivate the previously installed version."
    )
    rollback_parser.add_argument(
        "--to", dest="target_version", help="Activate this installed version instead."
    )
    rollback_parser.add_argument(
        "--no-restart", action="store_true", help="Activate without restarting units."
    )

    commands.add_parser("versions", help="List installed versions and the active one.")

    return parser


def main() -> None:
    """Run the asynchronous CLI and map expected errors to a nonzero exit code."""
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        if args.command in UPDATE_COMMANDS:
            exit_code = _run_update_command(args)
        else:
            exit_code = asyncio.run(_run(args))
    except (ConfigurationError, ObservabilityError, StateStoreError, UpdateError) as err:
        LOGGER.error("%s", err)
        exit_code = 2
    except KeyboardInterrupt:
        exit_code = 130
    raise SystemExit(exit_code)


async def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    state_store = StateStore(args.state_dir)
    if args.command == "run":
        return await _run_daemon(config, state_store)
    if args.command == "playback":
        return await _run_playback_command(args, config, state_store)

    tv_config = config.get_tv(args.tv_id)
    television = _make_television(tv_config, state_store)
    if args.command == "pair":
        return await _pair(tv_config, television, state_store)
    if args.command == "status":
        return await _status(tv_config, television, state_store)
    if args.command == "inputs":
        return await _inputs(tv_config, television, state_store)
    if args.command == "apply":
        return await _apply(tv_config, television, state_store)
    raise AssertionError(f"Unhandled command {args.command}")


def _make_television(tv_config: TvConfig, state_store: StateStore) -> Television:
    return LgWebOsTelevision(tv_config.host, state_store.get_client_key(tv_config.id))


async def _pair(tv_config: TvConfig, television: Television, state_store: StateStore) -> int:
    existing_key = state_store.get_client_key(tv_config.id)
    if existing_key is None:
        print(f"{tv_config.id}: approve the pairing request displayed on the LG TV.")
    else:
        print(f"{tv_config.id}: attempting connection with the stored pairing key.")
    try:
        await television.connect()
        _persist_key(tv_config, television, state_store)
        print(f"{tv_config.id}: paired and connected.")
        return 0
    except TelevisionError as err:
        LOGGER.error("%s: pairing failed: %s", tv_config.id, err)
        return 1
    finally:
        with contextlib.suppress(TelevisionError):
            await television.disconnect()


async def _status(tv_config: TvConfig, television: Television, state_store: StateStore) -> int:
    print(f"TV: {tv_config.name}")
    print(f"ID: {tv_config.id}")
    print(f"Host: {tv_config.host}")
    print(f"Paired: {'yes' if state_store.get_client_key(tv_config.id) else 'no'}")
    try:
        await television.connect()
        _persist_key(tv_config, television, state_store)
        state = await television.get_state()
        await television.get_inputs()
        current_input = await television.get_input()
        volume = await television.get_volume()
        print("Connected: yes")
        print(f"Power state: {state.power_state or 'unknown'}")
        print(f"TV on: {'yes' if state.is_on else 'no' if state.is_on is not None else 'unknown'}")
        print(f"Current input: {current_input or 'unknown'}")
        print(f"Current volume: {volume if volume is not None else 'unknown'}")
        return 0
    except TelevisionError as err:
        print("Connected: no")
        print("Power state: unavailable")
        print("Current input: unavailable")
        print("Current volume: unavailable")
        LOGGER.info("%s: unavailable: %s", tv_config.id, err)
        return 1
    finally:
        with contextlib.suppress(TelevisionError):
            await television.disconnect()


async def _inputs(tv_config: TvConfig, television: Television, state_store: StateStore) -> int:
    try:
        await television.connect()
        _persist_key(tv_config, television, state_store)
        print(f"Inputs reported by {tv_config.name} ({tv_config.host}):")
        for input_ in await television.get_inputs():
            suffix = f" ({input_.label})" if input_.label else ""
            print(f"{input_.id}{suffix}")
        return 0
    except TelevisionError as err:
        LOGGER.error("%s: could not list inputs: %s", tv_config.id, err)
        return 1
    finally:
        with contextlib.suppress(TelevisionError):
            await television.disconnect()


async def _apply(tv_config: TvConfig, television: Television, state_store: StateStore) -> int:
    try:
        await television.connect()
        _persist_key(tv_config, television, state_store)
        result = await converge(television, tv_config, LOGGER)
        if not result.changed:
            print(f"{tv_config.id}: desired state already reached.")
        else:
            changed = []
            if result.input_changed:
                changed.append("input")
            if result.volume_changed:
                changed.append("volume")
            print(f"{tv_config.id}: updated {', '.join(changed)}.")
        return 0
    except TelevisionError as err:
        LOGGER.error("%s", err)
        return 1
    finally:
        with contextlib.suppress(TelevisionError):
            await television.disconnect()


async def _run_playback_command(
    args: argparse.Namespace, config: ApplicationConfig, state_store: StateStore
) -> int:
    if args.playback_command == "check":
        return await _playback_check(config)
    if args.playback_command == "command":
        return _playback_command(config, args.player_id)
    if args.playback_command == "start":
        return await _playback_start(config, args.player_id)
    if args.playback_command == "run":
        return await _playback_run(config, state_store)
    raise AssertionError(f"Unhandled playback command {args.playback_command}")


def _resolve_mpv_binary(mpv_binary: str) -> str | None:
    return shutil.which(mpv_binary)


def _require_playback(config: ApplicationConfig) -> PlaybackConfig | None:
    if config.playback is None:
        LOGGER.error("No 'playback' section is configured.")
        return None
    return config.playback


async def _playback_check(config: ApplicationConfig) -> int:
    playback = _require_playback(config)
    if playback is None:
        return 1

    mpv_path = _resolve_mpv_binary(playback.mpv_binary)
    if mpv_path is None:
        print(
            f"mpv executable {playback.mpv_binary!r} was not found. "
            "Install mpv or set playback.mpv_binary."
        )
        return 1
    print(f"mpv binary: {mpv_path}")

    try:
        process = await asyncio.create_subprocess_exec(
            mpv_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        version_line = stdout.decode(errors="replace").splitlines()[0] if stdout else "unknown"
    except OSError as err:
        version_line = f"could not run --version: {err}"
    print(f"mpv version: {version_line}")

    print(
        f"hwdec: {playback.hwdec}  fullscreen: {playback.fullscreen}  "
        f"loop: {playback.loop}  audio: {playback.audio}"
    )
    print(
        f"DISPLAY={os.environ.get('DISPLAY', '(unset)')}  "
        f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '(unset)')}"
    )

    if not playback.players:
        print("No players configured under 'playback.players'.")
        return 0
    for player in playback.players:
        exists = "yes" if player.media.exists() else "no"
        if player.screen is not None:
            screen = f"screen={player.screen}"
        elif player.screen_name is not None:
            screen = f"screen_name={player.screen_name}"
        else:
            screen = "screen=(default)"
        tv = f" tv_id={player.tv_id}" if player.tv_id else ""
        print(f"{player.id}: media={player.media} exists={exists} {screen}{tv}")
    return 0


def _playback_command(config: ApplicationConfig, player_id: str) -> int:
    playback = _require_playback(config)
    if playback is None:
        return 1
    player = config.get_player(player_id)
    socket_path = resolve_socket_dir() / f"{player.id}.sock"
    argv = build_mpv_argv(player, playback, socket_path)
    print(shlex.join(argv))
    return 0


async def _playback_start(config: ApplicationConfig, player_id: str) -> int:
    playback = _require_playback(config)
    if playback is None:
        return 1
    player = config.get_player(player_id)
    if _resolve_mpv_binary(playback.mpv_binary) is None:
        LOGGER.error(
            "mpv executable %r was not found. Install mpv or set playback.mpv_binary.",
            playback.mpv_binary,
        )
        return 1

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    socket_path = resolve_socket_dir() / f"{player.id}.sock"
    logger = logging.LoggerAdapter(LOGGER, {"player_id": player.id})
    mpv_player = MpvPlayer(player, playback, socket_path, logger, launcher=default_launcher)

    if not player.media.exists():
        LOGGER.error("%s: media not found: %s", player.id, player.media)
        return 1

    try:
        await mpv_player.start()
    except OSError as err:
        LOGGER.error("%s: could not start mpv: %s", player.id, err)
        return 1

    wait_task = asyncio.create_task(mpv_player.wait())
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({wait_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if stop_task in done:
        await mpv_player.stop()
        return 0
    return 0 if wait_task.result() == 0 else 1


async def _playback_run(config: ApplicationConfig, state_store: StateStore) -> int:
    playback = _require_playback(config)
    if playback is None or not playback.players:
        LOGGER.error("No 'playback' section with players is configured; nothing to run.")
        return 1

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    try:
        with acquire_controller_lock(
            state_store.state_dir, name="playback.lock", command="playback run"
        ):
            await run_playback(playback, stop_event)
    except ControllerAlreadyRunningError as err:
        LOGGER.error("%s", err)
        return 1
    return 0


def _run_update_command(args: argparse.Namespace) -> int:
    layout = InstallLayout.from_environment()
    if args.command == "versions":
        return _versions(layout)
    if args.command == "upgrade":
        return _upgrade(args, layout)
    if args.command == "rollback":
        return _rollback(args, layout)
    raise AssertionError(f"Unhandled update command {args.command}")


def _require_managed_install(layout: InstallLayout) -> None:
    """Refuse to manage an installation this command did not create."""
    if not layout.root.is_dir():
        raise UpdateError(
            f"{layout.root} is not a managed installation, so there is nothing to upgrade. "
            "Install with scripts/install.sh, or point SCREENKEEPER_INSTALL_ROOT at an "
            "existing install tree. A development checkout is upgraded with git instead."
        )


def _versions(layout: InstallLayout) -> int:
    print(f"Running version: {__version__}")
    print(f"Install root: {layout.root}")
    installed = layout.installed_versions()
    if not installed:
        print("Installed versions: (none — this is not a managed installation)")
        return 0
    current = layout.current_version()
    print("Installed versions:")
    for version in reversed(installed):
        print(f"  {version}{'  (active)' if version == current else ''}")
    return 0


def _upgrade(args: argparse.Namespace, layout: InstallLayout) -> int:
    release = resolve_release(args.repository, args.target_version)
    running = version_key(__version__)
    target = version_key(release.version)

    if target < running and not args.allow_downgrade:
        raise UpdateError(
            f"Release {release.version} is older than the running version {__version__}. "
            "Pass --allow-downgrade to install it anyway."
        )
    if target == running and not args.force:
        print(f"signage-controller {__version__} is already the newest release.")
        return 0
    if args.check:
        print(f"Update available: {__version__} -> {release.version}")
        return UPDATE_AVAILABLE_EXIT_CODE

    _require_managed_install(layout)
    with update_lock(layout):
        install_release(
            release, layout, require_checksum=args.require_checksum, force=args.force
        )
        activate_version(layout, release.version)
        print(f"Activated signage-controller {release.version}.")
        removed = prune_versions(layout, keep=args.keep)
        if removed:
            print(f"Removed older versions: {', '.join(removed)}")
        return _finish_activation(release.version, no_restart=args.no_restart)


def _rollback(args: argparse.Namespace, layout: InstallLayout) -> int:
    _require_managed_install(layout)
    installed = layout.installed_versions()
    current = layout.current_version()

    if args.target_version is not None:
        target = normalize_version(args.target_version)
        if target not in installed:
            available = ", ".join(reversed(installed)) or "(none)"
            raise UpdateError(f"Version {target} is not installed. Installed versions: {available}")
    else:
        candidates = [version for version in installed if version != current]
        if not candidates:
            raise UpdateError(
                "No other version is installed to roll back to. "
                f"Installed versions: {', '.join(installed) or '(none)'}"
            )
        target = candidates[-1]

    if target == current:
        print(f"signage-controller {target} is already active.")
        return 0

    with update_lock(layout):
        activate_version(layout, target)
        print(f"Rolled back to signage-controller {target}.")
        return _finish_activation(target, no_restart=args.no_restart)


def _finish_activation(version: str, *, no_restart: bool) -> int:
    if no_restart:
        print("Units were not restarted; they keep running the previous code until restarted.")
        return 0
    failed = restart_services()
    if failed:
        commands = " ".join(f"systemctl --user restart {unit};" for unit in failed).rstrip(";")
        LOGGER.warning(
            "Version %s is active but these units did not restart: %s. Restart them with: %s",
            version,
            ", ".join(failed),
            commands,
        )
    return 0


def _persist_key(tv_config: TvConfig, television: Television, state_store: StateStore) -> None:
    key = television.client_key
    if key is None:
        raise TelevisionError(f"{tv_config.id}: TV connected without returning a pairing key")
    if state_store.get_client_key(tv_config.id) != key:
        state_store.set_client_key(tv_config.id, key)


async def _run_daemon(config: ApplicationConfig, state_store: StateStore) -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    def factory(tv_config: TvConfig, client_key: str | None) -> Television:
        return LgWebOsTelevision(tv_config.host, client_key)

    root_logger = logging.getLogger()
    loki_handler: LokiHandler | None = None
    reporter: PrometheusStatusReporter | None = None
    reporter_task: asyncio.Task[None] | None = None
    runtime_started = False
    try:
        if config.observability.loki is not None:
            loki_handler = LokiHandler(
                config.observability.loki,
                instance=(
                    config.observability.prometheus.instance
                    if config.observability.prometheus is not None
                    else None
                ),
            )
            root_logger.addHandler(loki_handler)
        if config.observability.prometheus is not None:
            reporter = PrometheusStatusReporter(
                config.observability.prometheus, (tv.id for tv in config.tvs)
            )
        with acquire_controller_lock(state_store.state_dir):
            runtime_started = True
            if reporter is not None:
                reporter_task = asyncio.create_task(reporter.run(stop_event))
            await run_all(
                config.tvs,
                state_store,
                config.reconcile_interval,
                config.power_on_delay,
                factory,
                stop_event,
                reporter,
            )
    except ControllerAlreadyRunningError as err:
        LOGGER.error("%s", err)
        return 1
    finally:
        if runtime_started and reporter is not None:
            reporter.mark_all_unavailable()
            await reporter.push()
            stop_event.set()
            if reporter_task is not None:
                await reporter_task
        if loki_handler is not None:
            root_logger.removeHandler(loki_handler)
            loki_handler.close()
    return 0


if __name__ == "__main__":
    main()
