"""The pairing-code enrollment workflow.

Operator-initiated only. `signage-controller device enroll` runs this in the
foreground while somebody reads the code off the screen; no daemon calls it, so
an appliance can never sit in a loop requesting enrollments nobody is watching.

The appliance's identity is fixed before this starts and survives everything
that happens here. A code that expires is replaced by asking for another one
with the *same* `installation_id` and `device_token` — regenerating identity
would silently orphan an enrollment an administrator may be about to claim.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket

from .client import (
    ControlPlaneClient,
    EnrollmentExpiredError,
    EnrollmentState,
    EnrollmentTicket,
)
from .identity import DeviceIdentity, DeviceStore
from .report import utc_now


LOGGER = logging.getLogger(__name__)


def format_pairing_code(ticket: EnrollmentTicket) -> str:
    """Render the code block an operator reads off the appliance."""
    lines = [
        "",
        "Screenkeeper enrollment",
        "",
        "Pairing code:",
        "",
        f"    {ticket.code}",
        "",
    ]
    if ticket.expires_at:
        lines.append(f"This code expires at {ticket.expires_at}.")
    lines.append("Waiting for this player to be claimed...")
    return "\n".join(lines)


def format_claim(state: EnrollmentState) -> str:
    """Render what the control plane assigned, once an administrator claims it."""
    lines = ["", "Player claimed.", ""]
    lines.append(f"Player:       {state.player_name or state.player_id}")
    if state.organization_name:
        lines.append(f"Organization: {state.organization_name}")
    if state.location_name:
        lines.append(f"Location:     {state.location_name}")
    lines.append("")
    lines.append("Start reporting with: systemctl --user enable --now screenkeeper-agent.service")
    return "\n".join(lines)


async def enroll(
    client: ControlPlaneClient,
    store: DeviceStore,
    identity: DeviceIdentity,
    stop_event: asyncio.Event,
    *,
    hostname: str | None = None,
) -> DeviceIdentity | None:
    """Run the pairing flow until claimed or interrupted.

    Returns the updated identity on a successful claim, or None when the
    operator interrupted the wait. Blocking HTTP is offloaded so Ctrl-C stays
    responsive while a poll is in flight.
    """
    host = hostname or socket.gethostname()
    while not stop_event.is_set():
        ticket = await asyncio.to_thread(client.create_enrollment, identity, host)
        print(format_pairing_code(ticket))

        claimed = await _wait_for_claim(client, identity, ticket, stop_event)
        if claimed is None:
            if stop_event.is_set():
                return None
            # The code lapsed unclaimed. Same identity, new code.
            print("\nThe pairing code expired before it was claimed. Requesting a new one.")
            continue

        updated = store.record_claim(
            identity,
            player_id=claimed.player_id or "",
            player_name=claimed.player_name,
            organization_id=claimed.organization_id,
            organization_name=claimed.organization_name,
            location_id=claimed.location_id,
            location_name=claimed.location_name,
            enrolled_at=utc_now(),
        )
        print(format_claim(claimed))
        return updated
    return None


async def _wait_for_claim(
    client: ControlPlaneClient,
    identity: DeviceIdentity,
    ticket: EnrollmentTicket,
    stop_event: asyncio.Event,
) -> EnrollmentState | None:
    """Poll one enrollment. None means expired, or interrupted."""
    while not stop_event.is_set():
        await _wait_or_stop(stop_event, ticket.poll_interval_seconds)
        if stop_event.is_set():
            return None
        try:
            state = await asyncio.to_thread(
                client.poll_enrollment, ticket.enrollment_id, identity.device_token
            )
        except EnrollmentExpiredError:
            return None
        if state.is_claimed:
            return state
    return None


async def _wait_or_stop(stop_event: asyncio.Event, delay: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
