"""Desired-state convergence and long-running television management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .config import TvConfig
from .observability import StatusReporter
from .state_store import StateStore
from .tv.base import Television, TelevisionError, TelevisionState


LOGGER = logging.getLogger(__name__)
RETRY_DELAYS = (5.0, 10.0, 15.0, 30.0)
POWER_OFF_STATES = frozenset({"Power Off", "Suspend", "Active Standby"})


class DesiredInputError(TelevisionError):
    """The configured input is not available from the connected television."""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Changes made while converging one television."""

    input_changed: bool = False
    volume_changed: bool = False
    available_input_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        """Whether convergence had to send any change command."""
        return self.input_changed or self.volume_changed


async def _run_command(
    command: Callable[[Any], Awaitable[None]],
    value: Any,
    tv_id: str,
    name: str,
    status_reporter: StatusReporter | None,
) -> None:
    """Run one TV command and record its outcome/latency without swallowing errors."""
    start = asyncio.get_running_loop().time()
    try:
        await command(value)
    except Exception:
        if status_reporter is not None:
            status_reporter.record_command(
                tv_id, name, success=False, duration=asyncio.get_running_loop().time() - start
            )
        raise
    else:
        if status_reporter is not None:
            status_reporter.record_command(
                tv_id, name, success=True, duration=asyncio.get_running_loop().time() - start
            )


async def converge(
    tv: Television,
    config: TvConfig,
    logger: logging.Logger | logging.LoggerAdapter,
    status_reporter: StatusReporter | None = None,
) -> ApplyResult:
    """Read actual state and make only the changes needed for desired state."""
    inputs = await tv.get_inputs()
    available_input_ids = tuple(input_.id for input_ in inputs)
    if config.desired_input not in available_input_ids:
        valid_ids = ", ".join(available_input_ids) or "(none reported)"
        raise DesiredInputError(
            f"{config.id}: configured input {config.desired_input!r} is not available. "
            f"TV reported: {valid_ids}"
        )

    current_input = await tv.get_input()
    current_volume = await tv.get_volume()
    input_changed = current_input != config.desired_input
    volume_changed = current_volume != config.desired_volume
    if status_reporter is not None:
        status_reporter.update(
            config.id,
            state=TelevisionState(current_input=current_input, volume=current_volume),
        )

    if input_changed:
        logger.info(
            "%s: current input %s; switching to %s",
            config.id,
            current_input if current_input is not None else "unknown",
            config.desired_input,
        )
        await _run_command(tv.set_input, config.desired_input, config.id, "set_input", status_reporter)
        if status_reporter is not None:
            status_reporter.update(
                config.id, state=TelevisionState(current_input=config.desired_input)
            )
    else:
        logger.debug("%s: input already %s; no change", config.id, config.desired_input)
    if volume_changed:
        logger.info(
            "%s: volume %s; setting to %s",
            config.id,
            current_volume if current_volume is not None else "unknown",
            config.desired_volume,
        )
        await _run_command(tv.set_volume, config.desired_volume, config.id, "set_volume", status_reporter)
        if status_reporter is not None:
            status_reporter.update(config.id, state=TelevisionState(volume=config.desired_volume))
    else:
        logger.debug("%s: volume already %s; no change", config.id, config.desired_volume)

    if input_changed or volume_changed:
        logger.info("%s: desired state reached", config.id)

    return ApplyResult(
        input_changed=input_changed,
        volume_changed=volume_changed,
        available_input_ids=available_input_ids,
    )


class TvManager:
    """Keep a single television connected and converged until asked to stop."""

    def __init__(
        self,
        config: TvConfig,
        television: Television,
        state_store: StateStore,
        reconcile_interval: float,
        power_on_delay: float,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        logger: logging.Logger | None = None,
        status_reporter: StatusReporter | None = None,
    ) -> None:
        self.config = config
        self.television = television
        self.state_store = state_store
        self.reconcile_interval = reconcile_interval
        self.power_on_delay = power_on_delay
        self.retry_delays = retry_delays
        self.logger = logging.LoggerAdapter(logger or LOGGER, {"tv_id": config.id})
        self.status_reporter = status_reporter
        self._state_changed = asyncio.Event()
        self._is_on: bool | None = None
        self._reconcile_after: float | None = None
        self._shutdown_reported = False
        self.television.add_state_listener(self._on_state_changed)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run until stopped, treating an unavailable TV as a normal condition."""
        retry_index = 0
        first_attempt = True
        try:
            while not stop_event.is_set():
                try:
                    if first_attempt:
                        self.logger.info("%s: connecting to %s", self.config.id, self.config.host)
                        first_attempt = False
                    self._is_on = None
                    self._reconcile_after = None
                    self._shutdown_reported = False
                    await self.television.connect()
                    self._persist_pairing_key()
                    self._record_status(connected=True, state=await self.television.get_state())
                    self.logger.info("%s: connected", self.config.id)
                    retry_index = 0
                    self._schedule_reconciliation_after_delay()
                    await self._wait_for_settle_delay(stop_event)
                    if self.television.is_connected and not stop_event.is_set() and self._is_on is not False:
                        await self._converge(announce_if_unchanged=True)
                    await self._monitor_connected(stop_event)
                    if not stop_event.is_set():
                        self._log_connection_lost()
                except DesiredInputError as err:
                    self.logger.error("%s", err)
                    await self._wait_or_stop(stop_event, self.reconcile_interval)
                    continue
                except TelevisionError as err:
                    with contextlib.suppress(TelevisionError):
                        await self.television.disconnect()
                    self._record_status(connected=False)
                    delay = self.retry_delays[min(retry_index, len(self.retry_delays) - 1)]
                    if retry_index == 0:
                        if self._shutdown_reported:
                            self.logger.info(
                                "%s: TV remains unavailable after reported shutdown; retrying in %ss",
                                self.config.id,
                                int(delay),
                            )
                        else:
                            self.logger.info(
                                "%s: TV unavailable (power state unknown: it may be off, booting, "
                                "or network-unreachable: %s); retrying in %ss",
                                self.config.id,
                                err,
                                int(delay),
                            )
                    else:
                        self.logger.debug("%s: TV still unavailable: %s", self.config.id, err)
                    retry_index += 1
                    await self._wait_or_stop(stop_event, delay)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Keep one unexpected adapter failure from ending this or other TV tasks.
                    self.logger.exception("%s: unexpected controller failure", self.config.id)
                    with contextlib.suppress(TelevisionError):
                        await self.television.disconnect()
                    self._record_status(connected=False)
                    delay = self.retry_delays[min(retry_index, len(self.retry_delays) - 1)]
                    retry_index += 1
                    await self._wait_or_stop(stop_event, delay)
        finally:
            with contextlib.suppress(TelevisionError):
                await self.television.disconnect()
            self._record_status(connected=False)

    async def _monitor_connected(self, stop_event: asyncio.Event) -> None:
        while self.television.is_connected and not stop_event.is_set():
            if self._is_on is False:
                # A clear-state callback after a socket close is indistinguishable from
                # a false state without a reported power value. Recheck connection soon.
                timeout = self.reconcile_interval if self._shutdown_reported else 1.0
                await self._wait_for_state_change_or_stop(stop_event, timeout)
                continue

            timeout = self.reconcile_interval
            if self._reconcile_after is not None:
                timeout = min(timeout, max(0.0, self._reconcile_after - self._monotonic()))
            await self._wait_for_state_change_or_stop(stop_event, timeout)

            if self._is_on is False or not self.television.is_connected or stop_event.is_set():
                continue
            if self._reconcile_after is not None:
                if self._monotonic() < self._reconcile_after:
                    continue
                delayed_reconciliation = True
                self._reconcile_after = None
            else:
                delayed_reconciliation = False
            if self.television.is_connected:
                await self._converge(announce_if_unchanged=delayed_reconciliation)

    async def _converge(self, *, announce_if_unchanged: bool) -> ApplyResult:
        result = await converge(
            self.television,
            self.config,
            self.logger,
            self.status_reporter,
        )
        if announce_if_unchanged and not result.changed:
            self.logger.info("%s: desired state verified; monitoring", self.config.id)
        return result

    async def _wait_for_settle_delay(self, stop_event: asyncio.Event) -> None:
        while self.television.is_connected and not stop_event.is_set():
            if self._is_on is False:
                await self._wait_for_state_change_or_stop(stop_event, self.reconcile_interval)
                continue
            if self._reconcile_after is None:
                return
            remaining_delay = self._reconcile_after - self._monotonic()
            if remaining_delay <= 0:
                self._reconcile_after = None
                return
            await self._wait_for_state_change_or_stop(stop_event, remaining_delay)

    async def _wait_for_state_change_or_stop(
        self, stop_event: asyncio.Event, timeout: float
    ) -> None:
        if self._state_changed.is_set() or stop_event.is_set():
            self._state_changed.clear()
            return

        state_task = asyncio.create_task(self._state_changed.wait())
        stop_task = asyncio.create_task(stop_event.wait())
        tasks = {state_task, stop_task}
        try:
            done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if state_task in done:
            self._state_changed.clear()

    def _schedule_reconciliation_after_delay(self) -> None:
        self._reconcile_after = self._monotonic() + self.power_on_delay
        if self.power_on_delay:
            self.logger.info(
                "%s: waiting %ss for TV services to settle",
                self.config.id,
                int(self.power_on_delay),
            )

    @staticmethod
    def _monotonic() -> float:
        return asyncio.get_running_loop().time()

    def _persist_pairing_key(self) -> None:
        client_key = self.television.client_key
        if client_key is None:
            raise TelevisionError(f"{self.config.id}: TV connected without a pairing key")
        if self.state_store.get_client_key(self.config.id) != client_key:
            self.state_store.set_client_key(self.config.id, client_key)

    def _on_state_changed(self, state: TelevisionState) -> None:
        self._record_status(connected=self.television.is_connected, state=state)
        was_on = self._is_on
        self._is_on = state.is_on
        if state.power_state in POWER_OFF_STATES:
            self._reconcile_after = None
            if not self._shutdown_reported:
                self.logger.info("%s: TV shutting down", self.config.id)
            self._shutdown_reported = True
            self._state_changed.set()
        elif state.is_on is True and was_on is False:
            self._shutdown_reported = False
            self._schedule_reconciliation_after_delay()
            self.logger.info("%s: TV powered on; scheduling reconciliation", self.config.id)
            self._state_changed.set()
        elif state.is_on is False:
            self._state_changed.set()

    def _log_connection_lost(self) -> None:
        if self._shutdown_reported:
            self.logger.info("%s: connection closed after TV reported shutdown", self.config.id)
        else:
            self.logger.info(
                "%s: connection lost without a shutdown notification; treating TV as unavailable",
                self.config.id,
            )

    def _record_status(
        self, *, connected: bool | None = None, state: TelevisionState | None = None
    ) -> None:
        if self.status_reporter is not None:
            self.status_reporter.update(self.config.id, connected=connected, state=state)

    @staticmethod
    async def _wait_or_stop(stop_event: asyncio.Event, delay: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)


TelevisionFactory = Callable[[TvConfig, str | None], Television]


async def run_all(
    configs: tuple[TvConfig, ...],
    state_store: StateStore,
    reconcile_interval: float,
    power_on_delay: float,
    factory: TelevisionFactory,
    stop_event: asyncio.Event,
    status_reporter: StatusReporter | None = None,
) -> None:
    """Run an independent management task for every configured television."""
    managers = [
        TvManager(
            config=tv_config,
            television=factory(tv_config, state_store.get_client_key(tv_config.id)),
            state_store=state_store,
            reconcile_interval=reconcile_interval,
            power_on_delay=power_on_delay,
            status_reporter=status_reporter,
        )
        for tv_config in configs
    ]
    await asyncio.gather(*(manager.run(stop_event) for manager in managers))
