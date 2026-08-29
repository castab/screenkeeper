from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from signage_controller.inventory.edid import parse_edid


FIXTURES = Path(__file__).parent / "fixtures" / "edid"


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.bin").read_bytes()


def test_valid_edid_yields_every_descriptive_field() -> None:
    info = parse_edid(_fixture("valid"))

    assert info is not None
    assert info.manufacturer == "GSM"
    assert info.product_code == 30456
    assert info.product_name == "LG TV"
    assert info.serial == "205NTKM1G347"


def test_the_hash_is_of_the_raw_bytes() -> None:
    raw = _fixture("valid")
    info = parse_edid(raw)

    assert info is not None
    assert info.sha256 == hashlib.sha256(raw).hexdigest()


def test_a_changed_panel_changes_the_hash() -> None:
    valid = parse_edid(_fixture("valid"))
    other = parse_edid(_fixture("bad-header"))

    assert valid is not None and other is not None
    assert valid.sha256 != other.sha256


@pytest.mark.parametrize("name", ["truncated", "bad-checksum", "bad-header"])
def test_unparseable_edid_reports_a_hash_and_invents_nothing(name: str) -> None:
    info = parse_edid(_fixture(name))

    assert info is not None
    assert info.sha256  # still useful: it detects that the display changed
    assert info.manufacturer is None
    assert info.product_code is None
    assert info.product_name is None
    assert info.serial is None


def test_empty_edid_reports_nothing_at_all() -> None:
    assert parse_edid(b"") is None


def test_to_dict_omits_fields_that_could_not_be_read() -> None:
    info = parse_edid(_fixture("truncated"))

    assert info is not None
    assert set(info.to_dict()) == {"sha256"}


def test_to_dict_carries_every_parsed_field() -> None:
    info = parse_edid(_fixture("valid"))

    assert info is not None
    assert info.to_dict() == {
        "sha256": info.sha256,
        "manufacturer": "GSM",
        "product_code": 30456,
        "product_name": "LG TV",
        "serial": "205NTKM1G347",
    }


def test_random_bytes_never_raise() -> None:
    """A hostile or corrupt blob must degrade, not crash an inventory scan."""
    for length in (1, 8, 64, 127, 128, 256):
        assert parse_edid(bytes(range(256))[:length] * 2) is not None
