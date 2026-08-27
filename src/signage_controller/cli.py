"""Command-line commissioning and runtime controls."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from pathlib import Path

from .config import ApplicationConfig, ConfigurationError, TvConfig, load_config
from .controller import DesiredInputError, converge, run_all
from .observability import LokiHandler, ObservabilityError, PrometheusStatusReporter
from .runtime_lock import ControllerAlreadyRunningError, acquire_controller_lock
from .state_store import StateStore, StateStoreError
from .tv.base import Television, TelevisionError
from .tv.lg_webos import LgWebOsTelevision


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="Local desired-state controller for LG webOS TVs.")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="YAML configuration path")
    parser.add_argument("--state-dir", type=Path, help="Runtime state directory")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("pair", "status", "inputs", "apply"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("tv_id")
    commands.add_parser("run")
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
        exit_code = asyncio.run(_run(args))
    except (ConfigurationError, ObservabilityError, StateStoreError) as err:
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
            loki_handler = LokiHandler(config.observability.loki)
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
