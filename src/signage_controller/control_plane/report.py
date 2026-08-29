"""Building the heartbeat body.

This module is the sanitization boundary. Everything the appliance tells the
control plane is assembled here from an allowlist of fields, never by
serializing a configuration object wholesale, so a credential added to
`config.yaml` later cannot start leaking by accident.

Never reported: LG client keys, the device token, observability bearer tokens
or their environment-variable names, and local media paths.

The body deliberately separates two things the control plane must not conflate:

    OBSERVED     inventory              DP-1 is connected, its EDID hashes to abc123
    CONFIGURED   configured_bindings    dev-menu is configured for DP-1 and dev-tv
"""

from __future__ import annotations

import platform
import socket
from datetime import datetime, timezone
from typing import Any

from .. import __version__
from ..config import ApplicationConfig
from ..inventory.drm import Inventory


TV_DRIVER = "lg-webos"


def utc_now() -> str:
    """Return the current UTC instant in the contract's timestamp format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_agent_info() -> dict[str, Any]:
    """Describe this Screenkeeper installation's software and host."""
    return {
        "screenkeeper_version": __version__,
        "hostname": socket.gethostname(),
        "os": platform.system() or "Linux",
        "kernel": platform.release(),
        "architecture": platform.machine(),
    }


def build_configured_bindings(config: ApplicationConfig) -> dict[str, Any]:
    """Report the operator-configured relationships, without any secrets.

    Physical inventory says "DP-1 exists and has an LG attached". This says
    "dev-menu is configured for DP-1 and dev-tv". The control plane needs both
    to notice later that they disagree.
    """
    return {
        "tvs": [
            {
                "id": tv.id,
                "name": tv.name,
                "host": tv.host,
                "desired_input": tv.desired_input,
                "desired_volume": tv.desired_volume,
                "driver": TV_DRIVER,
            }
            for tv in config.tvs
        ],
        "playback_players": [
            {
                "id": player.id,
                "name": player.name,
                "tv_id": player.tv_id,
                "screen": player.screen,
                "screen_name": player.screen_name,
                # `player.media` is intentionally absent: what a display plays
                # is a local path, and no current requirement needs it.
            }
            for player in (config.playback.players if config.playback else ())
        ],
    }


def build_heartbeat(
    config: ApplicationConfig, inventory: Inventory, *, reported_at: str | None = None
) -> dict[str, Any]:
    """Assemble one heartbeat: a snapshot of now, not a log of what happened."""
    return {
        "reported_at": reported_at or utc_now(),
        "agent": build_agent_info(),
        "inventory": inventory.to_dict(),
        "configured_bindings": build_configured_bindings(config),
    }
