"""Command-line commissioning and runtime controls."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import signal
from pathlib import Path

from . import __version__
from .config import ApplicationConfig, ConfigurationError, PlaybackConfig, TvConfig, load_config
from .control_plane.agent import run_agent
from .control_plane.client import ControlPlaneClient, ControlPlaneError
from .control_plane.enrollment import enroll
from .control_plane.identity import DeviceStore
from .content.reconciler import ContentReconciler, build_content_status, run_content
from .content.resolver import MediaResolver
from .content.store import ContentStore
from .controller import DesiredInputError, converge, run_all
from .inventory.drm import Inventory, collect_inventory
from .observability import MetricsServer, PrometheusMetricsReporter
from .playback.mpv import MpvPlayer, build_mpv_argv, default_launcher
from .playback.supervisor import resolve_socket_dir, run_playback
from .runtime_lock import ControllerAlreadyRunningError, acquire_controller_lock
from .state_store import StateStore, StateStoreError
from .tracing import configure_tracing
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

    device_parser = commands.add_parser(
        "device", help="Device identity, hardware inventory, and control-plane enrollment."
    )
    device_commands = device_parser.add_subparsers(dest="device_command", required=True)
    device_commands.add_parser("status", help="Show identity and enrollment, never the token.")
    device_inventory_parser = device_commands.add_parser(
        "inventory", help="Show locally observed display hardware. Contacts nothing."
    )
    device_inventory_parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit JSON for diagnostics."
    )
    device_commands.add_parser("enroll", help="Claim this player with a pairing code.")

    agent_parser = commands.add_parser(
        "agent", help="Outbound-only control-plane agent, independent of TV and playback."
    )
    agent_commands = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_commands.add_parser("run")

    content_parser = commands.add_parser(
        "content", help="Synchronize remotely assigned media into the persistent local cache."
    )
    content_commands = content_parser.add_subparsers(dest="content_command", required=True)
    content_status_parser = content_commands.add_parser("status", help="Show local content state.")
    content_status_parser.add_argument("--json", action="store_true", dest="as_json")
    content_commands.add_parser("sync", help="Run one content reconciliation.")
    content_commands.add_parser("run", help="Continuously reconcile desired content.")

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
    except (ConfigurationError, StateStoreError, UpdateError) as err:
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
    if args.command == "device":
        return await _run_device_command(args, config, state_store)
    if args.command == "agent":
        return await _run_agent_command(args, config, state_store)
    if args.command == "content":
        return await _run_content_command(args, config, state_store)

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

    reporter: PrometheusMetricsReporter | None = None
    metrics_server: MetricsServer | None = None
    tracing_handle = configure_tracing("signage-controller-playback", config.tracing)
    try:
        if config.metrics is not None and config.metrics.enabled:
            reporter = PrometheusMetricsReporter(
                player_ids=(player.id for player in playback.players)
            )
            # `run` and `playback run` read the same config.yaml and commonly run on
            # the same host, so they cannot share one `metrics.port`. +1 keeps the
            # config surface to a single 'metrics' block instead of a port per command.
            metrics_server = MetricsServer(
                reporter.registry, config.metrics.host, config.metrics.port + 1
            )
            await metrics_server.start()
        with acquire_controller_lock(
            state_store.state_dir, name="playback.lock", command="playback run"
        ):
            await run_playback(
                playback,
                stop_event,
                status_reporter=reporter,
                tracer=tracing_handle.tracer,
                media_resolver=MediaResolver(
                    ContentStore(
                        state_store.state_dir,
                        cache_dir=config.content.cache_dir if config.content is not None else None,
                    )
                    if config.content is not None and config.content.enabled
                    else None
                ),
            )
    except ControllerAlreadyRunningError as err:
        LOGGER.error("%s", err)
        return 1
    finally:
        if metrics_server is not None:
            await metrics_server.stop()
        tracing_handle.shutdown()
    return 0


async def _run_device_command(
    args: argparse.Namespace, config: ApplicationConfig, state_store: StateStore
) -> int:
    if args.device_command == "inventory":
        # Deliberately first, and deliberately without a DeviceStore: inventory
        # is a local hardware question that must not create identity or touch
        # the network.
        return _device_inventory(args.as_json)
    device_store = DeviceStore(state_store.state_dir)
    if args.device_command == "status":
        return _device_status(config, device_store)
    if args.device_command == "enroll":
        return await _device_enroll(config, device_store)
    raise AssertionError(f"Unhandled device command {args.device_command}")


async def _run_agent_command(
    args: argparse.Namespace, config: ApplicationConfig, state_store: StateStore
) -> int:
    if args.agent_command == "run":
        return await _agent_run(config, state_store)
    raise AssertionError(f"Unhandled agent command {args.agent_command}")


async def _run_content_command(
    args: argparse.Namespace, config: ApplicationConfig, state_store: StateStore
) -> int:
    content = config.content
    store = ContentStore(
        state_store.state_dir,
        cache_dir=content.cache_dir if content is not None else None,
    )
    if args.content_command == "status":
        return _content_status(store, as_json=args.as_json)
    if content is None or not content.enabled:
        if args.content_command == "run":
            LOGGER.info("No enabled 'content' section is configured; content synchronization is inactive.")
            return 0
        LOGGER.error("No enabled 'content' section is configured.")
        return 1
    if config.control_plane is None:
        LOGGER.error("Content synchronization requires a 'control_plane' section.")
        return 1
    device_store = DeviceStore(state_store.state_dir)
    identity = device_store.load()
    if identity is None or not identity.is_enrolled:
        LOGGER.error("This player is not enrolled. Run 'signage-controller device enroll' first.")
        return 1
    client = ControlPlaneClient(
        config.control_plane.base_url, timeout=config.control_plane.request_timeout
    )
    reconciler = ContentReconciler(config, content, client, identity, store)
    try:
        with acquire_controller_lock(
            state_store.state_dir, name="content.lock", command=f"content {args.content_command}"
        ):
            if args.content_command == "sync":
                try:
                    return 0 if await reconciler.reconcile() else 1
                except ControlPlaneError as err:
                    LOGGER.error("%s", err)
                    return 1
            if args.content_command == "run":
                stop_event = asyncio.Event()
                loop = asyncio.get_running_loop()
                for signum in (signal.SIGINT, signal.SIGTERM):
                    with contextlib.suppress(NotImplementedError):
                        loop.add_signal_handler(signum, stop_event.set)
                await run_content(reconciler, content, stop_event)
                return 0
    except ControllerAlreadyRunningError as err:
        LOGGER.error("%s", err)
        return 1
    raise AssertionError(f"Unhandled content command {args.content_command}")


def _content_status(store: ContentStore, *, as_json: bool) -> int:
    state = store.load_state()
    report = build_content_status(store, state)
    report["cache_dir"] = str(store.cache_dir)
    report["cached_assets"] = [path.name for path in store.cached_files()]
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(f"Manifest revision: {report['manifest_revision']}")
    print(f"Last successful sync: {state.get('last_successful_sync_at') or 'never'}")
    print(f"Cache directory: {store.cache_dir}")
    cached = report["cached_assets"]
    print(f"Cached assets: {', '.join(cached) if cached else '(none)'}")
    players = report["players"]
    if not players:
        print("Players: (no remote content state)")
    for player in players:
        print(
            f"{player['player_id']}: status={player['status']} "
            f"desired={player['desired_asset_revision_id'] or '-'} "
            f"cached={player['cached_asset_revision_id'] or '-'} "
            f"selected={player['active_asset_revision_id'] or '-'} "
            f"playing={player['playing_asset_revision_id'] or '-'}"
        )
        if player["last_error"]:
            print(f"  last error: {player['last_error']}")
    return 0


def _make_client(config: ApplicationConfig) -> ControlPlaneClient | None:
    if config.control_plane is None:
        LOGGER.error(
            "No 'control_plane' section is configured; add one with a 'base_url' to enroll."
        )
        return None
    return ControlPlaneClient(
        config.control_plane.base_url, timeout=config.control_plane.request_timeout
    )


def _device_status(config: ApplicationConfig, device_store: DeviceStore) -> int:
    """Print identity and enrollment. The device token is never shown."""
    identity = device_store.load()
    if identity is None:
        print("Installation ID: (none yet — generated on first enrollment)")
        print("Enrollment: not enrolled")
    else:
        print(f"Installation ID: {identity.installation_id}")
        print(f"Enrollment: {'enrolled' if identity.is_enrolled else 'not enrolled'}")
        if identity.is_enrolled:
            print(f"Player ID: {identity.player_id}")
            print(f"Player name: {identity.player_name or 'unknown'}")
            print(f"Organization: {identity.organization_name or 'unknown'}")
            print(f"Location: {identity.location_name or 'unknown'}")
            print(f"Enrolled at: {identity.enrolled_at or 'unknown'}")

    control_plane = config.control_plane
    print(f"Control plane: {control_plane.base_url if control_plane else '(not configured)'}")
    if identity is not None:
        print(f"Last heartbeat: {identity.last_heartbeat_at or 'never'}")
    return 0


def _device_inventory(as_json: bool) -> int:
    """Print observed display hardware. Never contacts the control plane."""
    inventory = collect_inventory()
    if as_json:
        print(json.dumps(inventory.to_dict(), indent=2, sort_keys=True))
        return 0
    _print_inventory(inventory)
    return 0


def _print_inventory(inventory: Inventory) -> None:
    if not inventory.gpus:
        print("GPUs: (none detected)")
    else:
        print("GPUs:")
        for gpu in inventory.gpus:
            details = [
                f"vendor={gpu.vendor_id}" if gpu.vendor_id else "",
                f"device={gpu.device_id}" if gpu.device_id else "",
                f"driver={gpu.driver}" if gpu.driver else "",
            ]
            suffix = "  ".join(detail for detail in details if detail)
            print(f"  {gpu.card}{'  ' + suffix if suffix else ''}")

    if not inventory.connectors:
        # Normal on a headless host, a VM, or WSL — not a failure.
        print("Connectors: (none detected)")
        return
    print("Connectors:")
    for connector in inventory.connectors:
        state = connector.status or "unknown"
        if connector.enabled is not None:
            state += ", enabled" if connector.enabled else ", disabled"
        print(f"  {connector.name}: {state}")
        if connector.modes:
            preview = ", ".join(connector.modes[:3])
            more = f" (+{len(connector.modes) - 3} more)" if len(connector.modes) > 3 else ""
            print(f"    modes: {preview}{more}")
        if connector.edid is not None:
            edid = connector.edid
            described = "  ".join(
                part
                for part in (
                    edid.manufacturer or "",
                    edid.product_name or "",
                    f"serial={edid.serial}" if edid.serial else "",
                )
                if part
            )
            print(f"    edid: {edid.sha256[:16]}…{'  ' + described if described else ''}")


async def _device_enroll(config: ApplicationConfig, device_store: DeviceStore) -> int:
    """Run the pairing-code workflow. Needs neither a TV nor mpv."""
    client = _make_client(config)
    if client is None:
        return 1

    # Generating identity here, not at import or on status, keeps a host that
    # never enrolls free of a credential it has no use for.
    identity = device_store.load_or_create()
    if identity.is_enrolled:
        print(f"This player is already enrolled as {identity.player_name or identity.player_id}.")
        print("Enrolling again is only necessary after the control plane removed this player.")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    try:
        claimed = await enroll(client, device_store, identity, stop_event)
    except ControlPlaneError as err:
        LOGGER.error("%s", err)
        return 1
    if claimed is None:
        print("\nEnrollment cancelled. This player's identity is unchanged.")
        return 1
    return 0


async def _agent_run(config: ApplicationConfig, state_store: StateStore) -> int:
    """Run the heartbeat agent under its own lock, independent of TV and playback."""
    control_plane = config.control_plane
    if control_plane is None:
        # Exit 0, not an error: the unit ships installed-but-disabled, and an
        # operator who starts it before configuring a control plane should get a
        # clean stop rather than a restart loop.
        LOGGER.info("No 'control_plane' section is configured; the agent has nothing to report.")
        return 0

    device_store = DeviceStore(state_store.state_dir)
    identity = device_store.load()
    if identity is None or not identity.is_enrolled:
        LOGGER.error(
            "This player is not enrolled. Run 'signage-controller device enroll' first."
        )
        return 1

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    reporter: PrometheusMetricsReporter | None = None
    metrics_server: MetricsServer | None = None
    tracing_handle = configure_tracing("signage-controller-agent", config.tracing)
    try:
        if config.metrics is not None and config.metrics.enabled:
            reporter = PrometheusMetricsReporter(control_plane=True)
            # `run` binds metrics.port, `playback run` binds +1; +2 keeps the
            # config surface to a single 'metrics' block instead of a port per command.
            metrics_server = MetricsServer(
                reporter.registry, config.metrics.host, config.metrics.port + 2
            )
            await metrics_server.start()
        with acquire_controller_lock(
            state_store.state_dir, name="agent.lock", command="agent run"
        ):
            await run_agent(
                config,
                control_plane,
                device_store,
                identity,
                stop_event,
                status_reporter=reporter,
                tracer=tracing_handle.tracer,
            )
    except ControllerAlreadyRunningError as err:
        LOGGER.error("%s", err)
        return 1
    finally:
        if metrics_server is not None:
            await metrics_server.stop()
        tracing_handle.shutdown()
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

    reporter: PrometheusMetricsReporter | None = None
    metrics_server: MetricsServer | None = None
    tracing_handle = configure_tracing("signage-controller", config.tracing)
    try:
        if config.metrics is not None and config.metrics.enabled:
            reporter = PrometheusMetricsReporter(tv_ids=(tv.id for tv in config.tvs))
            metrics_server = MetricsServer(
                reporter.registry, config.metrics.host, config.metrics.port
            )
            await metrics_server.start()
        with acquire_controller_lock(state_store.state_dir):
            await run_all(
                config.tvs,
                state_store,
                config.reconcile_interval,
                config.power_on_delay,
                factory,
                stop_event,
                reporter,
                tracing_handle.tracer,
            )
    except ControllerAlreadyRunningError as err:
        LOGGER.error("%s", err)
        return 1
    finally:
        if reporter is not None:
            reporter.mark_all_unavailable()
        if metrics_server is not None:
            await metrics_server.stop()
        tracing_handle.shutdown()
    return 0


if __name__ == "__main__":
    main()
