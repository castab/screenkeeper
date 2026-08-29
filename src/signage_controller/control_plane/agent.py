"""The long-running heartbeat agent.

Structurally a sibling of `playback.PlayerSupervisor` and `controller.TvManager`:
one `stop_event`-driven loop, `CancelledError` always re-raised, everything else
caught and backed off, nothing allowed to escape and end the process.

It is isolated from the other two runtimes by design. It holds its own
`agent.lock`, never touches a `Television` or an `MpvPlayer`, and makes only
outbound requests. A control plane that is down, or a credential that is
rejected, cannot stop a TV converging or an mpv process looping; equally, a
crashed mpv or an unreachable TV cannot stop heartbeats.

Logging is transition-based on purpose. A signage host may spend days offline,
and a line per failed retry would bury everything else in the journal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random

from ..config import ApplicationConfig, ControlPlaneConfig
from ..inventory.drm import DEFAULT_SYSFS_DRM, collect_inventory
from ..observability import ControlPlaneStatusReporter
from .client import (
    ControlPlaneAuthError,
    ControlPlaneClient,
    ControlPlaneError,
    ControlPlaneUnavailableError,
)
from .identity import DeviceIdentity, DeviceStore
from .report import build_heartbeat, utc_now


LOGGER = logging.getLogger(__name__)

# 5s, 10s, 20s, 30s, then 60s forever.
HEARTBEAT_BACKOFF_SECONDS = (5.0, 10.0, 20.0, 30.0, 60.0)
# A rejected credential is an operator problem, not a network blip. Retrying it
# every 30 seconds would spam the control plane and hide the real error.
AUTH_FAILURE_BACKOFF_SECONDS = 300.0
# Spreads reconnection across a fleet that lost the WAN at the same instant.
JITTER_FRACTION = 0.2


def _backoff_delay(failure_streak: int, delays: tuple[float, ...]) -> float:
    """Return the capped-backoff delay for the current consecutive-failure count."""
    if not delays:
        return 0.0
    return delays[min(failure_streak, len(delays) - 1)]


def _with_jitter(delay: float, fraction: float, rng: random.Random) -> float:
    if delay <= 0 or fraction <= 0:
        return delay
    return max(0.0, delay + delay * rng.uniform(-fraction, fraction))


class HeartbeatAgent:
    """Report observed state on a cadence, surviving control-plane outages."""

    def __init__(
        self,
        config: ApplicationConfig,
        control_plane: ControlPlaneConfig,
        client: ControlPlaneClient,
        store: DeviceStore,
        identity: DeviceIdentity,
        *,
        logger: logging.Logger | None = None,
        sysfs_root=DEFAULT_SYSFS_DRM,
        backoff_delays: tuple[float, ...] = HEARTBEAT_BACKOFF_SECONDS,
        auth_backoff_seconds: float = AUTH_FAILURE_BACKOFF_SECONDS,
        jitter_fraction: float = JITTER_FRACTION,
        rng: random.Random | None = None,
        status_reporter: ControlPlaneStatusReporter | None = None,
    ) -> None:
        self.config = config
        self.control_plane = control_plane
        self.client = client
        self.store = store
        self.identity = identity
        self.logger = logger or LOGGER
        self._sysfs_root = sysfs_root
        self._status_reporter = status_reporter
        self._backoff_delays = backoff_delays
        self._auth_backoff_seconds = auth_backoff_seconds
        self._jitter_fraction = jitter_fraction
        self._rng = rng or random.Random()
        self._failure_streak = 0
        # None until the first attempt resolves, so the first outcome always
        # counts as a transition and gets logged.
        self._reachable: bool | None = None
        self._auth_rejected = False

    async def run(self, stop_event: asyncio.Event) -> None:
        """Send heartbeats until stopped. Never raises except on cancellation."""
        self.logger.info(
            "Reporting to control plane at %s every %ss",
            self.client.host,
            int(self.control_plane.heartbeat_interval),
        )
        while not stop_event.is_set():
            delay = await self._beat_once()
            await self._wait_or_stop(stop_event, delay)
        self.logger.info("Control-plane agent stopped.")

    async def _beat_once(self) -> float:
        """Send one heartbeat and return how long to wait before the next."""
        try:
            report = await asyncio.to_thread(self._collect)
            await asyncio.to_thread(
                self.client.send_heartbeat,
                self.identity.player_id,
                self.identity.device_token,
                report,
            )
        except ControlPlaneAuthError as err:
            return self._on_auth_failure(err)
        except ControlPlaneUnavailableError as err:
            return self._on_unavailable(err)
        except ControlPlaneError as err:
            # A 4xx that will not fix itself. Back off hard rather than hammering.
            self._reachable = False
            self._record_heartbeat(success=False)
            self.logger.error("Control plane rejected this heartbeat: %s", err)
            self._failure_streak += 1
            return self._jittered(_backoff_delay(self._failure_streak, self._backoff_delays))
        except asyncio.CancelledError:
            raise
        except Exception:
            # An unexpected bug here must not take the agent process down.
            self._record_heartbeat(success=False)
            self.logger.exception("Unexpected control-plane agent failure")
            self._failure_streak += 1
            return self._jittered(_backoff_delay(self._failure_streak, self._backoff_delays))
        return self._on_success()

    def _collect(self) -> dict:
        """Gather a fresh snapshot. Runs off the event loop: it reads sysfs."""
        inventory = collect_inventory(self._sysfs_root)
        return build_heartbeat(self.config, inventory)

    def _on_success(self) -> float:
        if self._reachable is None:
            self.logger.info("Control plane reachable.")
        elif not self._reachable:
            self.logger.info("Heartbeat restored after %s failed attempts.", self._failure_streak)
        self._reachable = True
        self._auth_rejected = False
        self._failure_streak = 0
        self._remember_heartbeat()
        self._record_heartbeat(success=True)
        return self.control_plane.heartbeat_interval

    def _on_unavailable(self, err: ControlPlaneUnavailableError) -> float:
        if self._reachable is not False:
            self.logger.info("Control plane unavailable; signage is unaffected. %s", err)
        else:
            self.logger.debug("Control plane still unavailable: %s", err)
        self._reachable = False
        self._failure_streak += 1
        self._record_heartbeat(success=False)
        return self._jittered(_backoff_delay(self._failure_streak, self._backoff_delays))

    def _on_auth_failure(self, err: ControlPlaneAuthError) -> float:
        # Deliberately never regenerates identity: a new token would abandon the
        # enrollment an administrator already claimed and make this unrecoverable.
        if not self._auth_rejected:
            self.logger.error(
                "Authentication rejected: %s. Re-enroll this player with "
                "'signage-controller device enroll'. Retrying in %s minutes.",
                err,
                int(self._auth_backoff_seconds // 60),
            )
        else:
            self.logger.debug("Authentication still rejected: %s", err)
        self._reachable = False
        self._auth_rejected = True
        self._failure_streak += 1
        self._record_heartbeat(success=False, auth_rejected=True)
        return self._jittered(self._auth_backoff_seconds)

    def _record_heartbeat(self, *, success: bool, auth_rejected: bool = False) -> None:
        if self._status_reporter is not None:
            self._status_reporter.record_heartbeat(success=success, auth_rejected=auth_rejected)

    def _remember_heartbeat(self) -> None:
        try:
            self.identity = self.store.record_heartbeat(self.identity, utc_now())
        except Exception as err:
            # A read-only or full state directory must not stop reporting.
            self.logger.warning("Could not record the last heartbeat time: %s", err)

    def _jittered(self, delay: float) -> float:
        return _with_jitter(delay, self._jitter_fraction, self._rng)

    @staticmethod
    async def _wait_or_stop(stop_event: asyncio.Event, delay: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)


async def run_agent(
    config: ApplicationConfig,
    control_plane: ControlPlaneConfig,
    store: DeviceStore,
    identity: DeviceIdentity,
    stop_event: asyncio.Event,
    *,
    client: ControlPlaneClient | None = None,
    status_reporter: ControlPlaneStatusReporter | None = None,
) -> None:
    """Run one heartbeat agent for this appliance."""
    resolved = client or ControlPlaneClient(
        control_plane.base_url, timeout=control_plane.request_timeout
    )
    await HeartbeatAgent(
        config, control_plane, resolved, store, identity, status_reporter=status_reporter
    ).run(stop_event)
