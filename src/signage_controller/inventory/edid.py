"""A deliberately small EDID reader.

This is not an EDID standards implementation. It answers one question — "is the
display on this connector still the same physical display?" — and reports the
few descriptive fields that make the answer legible to a human.

The SHA-256 of the raw bytes is the load-bearing field and is always present.
It is useful even when nothing else can be parsed, because a changed hash means
somebody swapped the panel. Every descriptive field is optional and is omitted
rather than guessed: a wrong manufacturer or serial in the control plane is
worse than a missing one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


EDID_BLOCK_SIZE = 128
EDID_HEADER = bytes((0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00))
# The four 18-byte descriptors in the base block.
DESCRIPTOR_OFFSETS = (54, 72, 90, 108)
DESCRIPTOR_SIZE = 18
MONITOR_NAME_TAG = 0xFC
MONITOR_SERIAL_TAG = 0xFF


@dataclass(frozen=True, slots=True)
class EdidInfo:
    """Identity read from one connector's EDID blob."""

    sha256: str
    manufacturer: str | None = None
    product_code: int | None = None
    product_name: str | None = None
    serial: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the contract shape, omitting fields that could not be read."""
        record: dict[str, Any] = {"sha256": self.sha256}
        if self.manufacturer is not None:
            record["manufacturer"] = self.manufacturer
        if self.product_code is not None:
            record["product_code"] = self.product_code
        if self.product_name is not None:
            record["product_name"] = self.product_name
        if self.serial is not None:
            record["serial"] = self.serial
        return record


def parse_edid(raw: bytes) -> EdidInfo | None:
    """Parse an EDID blob, returning None only when there is nothing to report.

    A blob that is truncated, has a bad header, or fails its checksum still
    yields a hash — the connector does have *something* attached, and noticing
    that it changed remains useful — but no descriptive fields.
    """
    if not raw:
        return None

    digest = hashlib.sha256(raw).hexdigest()
    block = raw[:EDID_BLOCK_SIZE]
    if len(block) < EDID_BLOCK_SIZE or block[:8] != EDID_HEADER:
        return EdidInfo(sha256=digest)
    if sum(block) % 256 != 0:
        return EdidInfo(sha256=digest)

    return EdidInfo(
        sha256=digest,
        manufacturer=_manufacturer(block),
        product_code=int.from_bytes(block[10:12], "little"),
        product_name=_descriptor_text(block, MONITOR_NAME_TAG),
        serial=_serial(block),
    )


def _manufacturer(block: bytes) -> str | None:
    """Decode the three 5-bit letters packed into bytes 8-9, big-endian."""
    packed = int.from_bytes(block[8:10], "big")
    letters = [(packed >> shift) & 0x1F for shift in (10, 5, 0)]
    if any(letter < 1 or letter > 26 for letter in letters):
        return None
    return "".join(chr(ord("A") + letter - 1) for letter in letters)


def _serial(block: bytes) -> str | None:
    """Prefer the serial descriptor string, falling back to the numeric serial."""
    text = _descriptor_text(block, MONITOR_SERIAL_TAG)
    if text is not None:
        return text
    numeric = int.from_bytes(block[12:16], "little")
    # 0 means "not provided" rather than "serial number zero".
    return str(numeric) if numeric else None


def _descriptor_text(block: bytes, tag: int) -> str | None:
    """Return the text of the first descriptor carrying `tag`, if any."""
    for offset in DESCRIPTOR_OFFSETS:
        descriptor = block[offset : offset + DESCRIPTOR_SIZE]
        # A text descriptor starts 00 00 00 <tag> 00; anything else at this
        # offset is a timing descriptor and is not ours to interpret.
        if descriptor[0:3] != b"\x00\x00\x00" or descriptor[3] != tag:
            continue
        text = descriptor[5:DESCRIPTOR_SIZE]
        # Terminated by 0x0A and padded with spaces.
        terminator = text.find(b"\n")
        if terminator != -1:
            text = text[:terminator]
        decoded = text.decode("ascii", errors="ignore").strip()
        if decoded:
            return decoded
    return None
