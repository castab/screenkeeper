"""DRM connector and GPU discovery through sysfs.

`/sys/class/drm` is read directly rather than shelling out to `xrandr`,
`drm_info`, or `wlr-randr`: those need a running display server, and inventory
has to work on a headless host, over SSH, and before a graphical session exists.

Everything here is best-effort. Each file is read independently and an
unreadable one omits its field instead of failing the scan, so a VM, a WSL
instance, or an unusual driver still returns a usable partial answer. A missing
`/sys/class/drm` returns an empty inventory, which is a valid result.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .edid import EdidInfo, parse_edid


LOGGER = logging.getLogger(__name__)

DEFAULT_SYSFS_DRM = Path("/sys/class/drm")
CARD_RE = re.compile(r"^card(\d+)$")
CONNECTOR_RE = re.compile(r"^card(\d+)-(?P<name>.+)$")
# A panel advertising thousands of modes is a driver bug, not information.
MAX_MODES = 64


@dataclass(frozen=True, slots=True)
class Connector:
    """One DRM connector, whether or not a display is attached to it."""

    name: str
    status: str | None = None
    enabled: bool | None = None
    modes: tuple[str, ...] = ()
    edid: EdidInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the contract shape, omitting fields that could not be read."""
        record: dict[str, Any] = {"name": self.name}
        if self.status is not None:
            record["status"] = self.status
        if self.enabled is not None:
            record["enabled"] = self.enabled
        record["modes"] = list(self.modes)
        if self.edid is not None:
            record["edid"] = self.edid.to_dict()
        return record


@dataclass(frozen=True, slots=True)
class Gpu:
    """Best-effort identity of one DRM card."""

    card: str
    vendor_id: str | None = None
    device_id: str | None = None
    driver: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the contract shape, omitting fields that could not be read."""
        record: dict[str, Any] = {"card": self.card}
        for name in ("vendor_id", "device_id", "driver"):
            value = getattr(self, name)
            if value is not None:
                record[name] = value
        return record


@dataclass(frozen=True, slots=True)
class Inventory:
    """Everything this host currently observes about its display hardware."""

    gpus: tuple[Gpu, ...] = ()
    connectors: tuple[Connector, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        """Return the contract shape. Empty lists are a valid inventory."""
        return {
            "gpus": [gpu.to_dict() for gpu in self.gpus],
            "connectors": [connector.to_dict() for connector in self.connectors],
        }


def collect_inventory(sysfs_root: Path = DEFAULT_SYSFS_DRM) -> Inventory:
    """Scan sysfs for DRM cards and connectors, never raising on a hostile tree."""
    try:
        entries = sorted(entry for entry in sysfs_root.iterdir())
    except OSError as err:
        # No DRM at all: a headless server, a container, or WSL. Not an error.
        LOGGER.debug("No DRM inventory available at %s: %s", sysfs_root, err)
        return Inventory()

    gpus: list[Gpu] = []
    connectors: list[Connector] = []
    for entry in entries:
        if CARD_RE.match(entry.name):
            gpus.append(_read_gpu(entry))
            continue
        match = CONNECTOR_RE.match(entry.name)
        if match is not None:
            connectors.append(_read_connector(entry, match.group("name")))
    return Inventory(gpus=tuple(gpus), connectors=tuple(connectors))


def _read_gpu(path: Path) -> Gpu:
    return Gpu(
        card=path.name,
        vendor_id=_read_text(path / "device" / "vendor"),
        device_id=_read_text(path / "device" / "device"),
        driver=_read_driver(path / "device" / "driver"),
    )


def _read_connector(path: Path, name: str) -> Connector:
    return Connector(
        name=name,
        status=_read_text(path / "status"),
        enabled=_read_enabled(path / "enabled"),
        modes=_read_modes(path / "modes"),
        edid=_read_edid(path / "edid"),
    )


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return value or None


def _read_enabled(path: Path) -> bool | None:
    value = _read_text(path)
    if value is None:
        return None
    # The kernel writes "enabled"/"disabled"; anything else is not ours to guess.
    return {"enabled": True, "disabled": False}.get(value.lower())


def _read_modes(path: Path) -> tuple[str, ...]:
    value = _read_text(path)
    if value is None:
        return ()
    modes = [line.strip() for line in value.splitlines() if line.strip()]
    return tuple(modes[:MAX_MODES])


def _read_edid(path: Path) -> EdidInfo | None:
    try:
        raw = path.read_bytes()
    except OSError:
        # Reading `edid` fails routinely on a disconnected connector.
        return None
    return parse_edid(raw)


def _read_driver(path: Path) -> str | None:
    """Read the driver name from sysfs's `driver` symlink.

    Read the link rather than resolving the path: `Path.resolve()` on a missing
    link returns the path unchanged, which would report every driverless card as
    being driven by something called "driver".
    """
    try:
        if not path.is_symlink():
            return None
        return Path(os.readlink(path)).name or None
    except OSError:
        return None
