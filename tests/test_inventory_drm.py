from __future__ import annotations

from pathlib import Path

from signage_controller.inventory.drm import collect_inventory


FIXTURES = Path(__file__).parent / "fixtures" / "edid"


def _connector(
    root: Path,
    name: str,
    *,
    status: str | None = "connected",
    enabled: str | None = "enabled",
    modes: str | None = "1920x1080\n1280x720\n",
    edid: bytes | None = None,
) -> None:
    """Build one sysfs-shaped connector directory."""
    directory = root / name
    directory.mkdir(parents=True)
    if status is not None:
        (directory / "status").write_text(f"{status}\n")
    if enabled is not None:
        (directory / "enabled").write_text(f"{enabled}\n")
    if modes is not None:
        (directory / "modes").write_text(modes)
    if edid is not None:
        (directory / "edid").write_bytes(edid)


def _card(root: Path, name: str, *, vendor: str = "0x8086", device: str = "0x5912") -> None:
    """Build one sysfs-shaped DRM card with a driver symlink, as the kernel does."""
    card = root / name
    (card / "device").mkdir(parents=True)
    (card / "device" / "vendor").write_text(f"{vendor}\n")
    (card / "device" / "device").write_text(f"{device}\n")
    driver = root.parent / "drivers" / "i915"
    driver.mkdir(parents=True, exist_ok=True)
    (card / "device" / "driver").symlink_to(driver)


def test_no_drm_directory_returns_a_valid_empty_inventory(tmp_path: Path) -> None:
    """Normal on WSL, VMs, containers, and headless servers. Not an error."""
    inventory = collect_inventory(tmp_path / "does-not-exist")

    assert inventory.gpus == ()
    assert inventory.connectors == ()
    assert inventory.to_dict() == {"gpus": [], "connectors": []}


def test_an_empty_drm_directory_returns_an_empty_inventory(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()

    assert collect_inventory(root).to_dict() == {"gpus": [], "connectors": []}


def test_a_connected_connector_reports_status_modes_and_edid(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-DP-1", edid=(FIXTURES / "valid.bin").read_bytes())

    connectors = collect_inventory(root).connectors

    assert len(connectors) == 1
    connector = connectors[0]
    assert connector.name == "DP-1"
    assert connector.status == "connected"
    assert connector.enabled is True
    assert connector.modes == ("1920x1080", "1280x720")
    assert connector.edid is not None
    assert connector.edid.manufacturer == "GSM"


def test_a_disconnected_connector_is_still_reported(tmp_path: Path) -> None:
    """The control plane needs to know a connector exists even with nothing on it."""
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-HDMI-A-1", status="disconnected", enabled="disabled", modes="")

    connector = collect_inventory(root).connectors[0]

    assert connector.name == "HDMI-A-1"
    assert connector.status == "disconnected"
    assert connector.enabled is False
    assert connector.modes == ()
    assert connector.edid is None


def test_a_missing_edid_file_omits_the_field_rather_than_failing(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-DP-2", edid=None)

    connector = collect_inventory(root).connectors[0]

    assert connector.name == "DP-2"
    assert connector.edid is None
    assert "edid" not in connector.to_dict()


def test_an_empty_edid_file_omits_the_field(tmp_path: Path) -> None:
    """A disconnected connector commonly exposes a zero-length edid."""
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-DP-3", edid=b"")

    assert collect_inventory(root).connectors[0].edid is None


def test_a_truncated_edid_still_reports_a_hash(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-DP-4", edid=(FIXTURES / "truncated.bin").read_bytes())

    edid = collect_inventory(root).connectors[0].edid

    assert edid is not None
    assert edid.sha256
    assert edid.manufacturer is None


def test_unreadable_status_omits_the_field_without_dropping_the_connector(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-DP-5", status=None, enabled=None, modes=None)

    connector = collect_inventory(root).connectors[0]

    assert connector.name == "DP-5"
    assert connector.status is None
    assert connector.enabled is None
    assert connector.to_dict() == {"name": "DP-5", "modes": []}


def test_an_unrecognized_enabled_value_is_not_guessed(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-DP-6", enabled="unknown")

    assert collect_inventory(root).connectors[0].enabled is None


def test_gpu_vendor_device_and_driver_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _card(root, "card0")

    gpus = collect_inventory(root).gpus

    assert len(gpus) == 1
    assert gpus[0].card == "card0"
    assert gpus[0].vendor_id == "0x8086"
    assert gpus[0].device_id == "0x5912"
    assert gpus[0].driver == "i915"


def test_a_card_without_device_metadata_is_still_reported(tmp_path: Path) -> None:
    """An unusual driver exposing nothing must yield partial data, not nothing."""
    root = tmp_path / "drm"
    root.mkdir()
    (root / "card0").mkdir()

    gpus = collect_inventory(root).gpus

    assert len(gpus) == 1
    assert gpus[0].to_dict() == {"card": "card0"}


def test_cards_and_connectors_are_told_apart(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _card(root, "card0")
    _connector(root, "card0-DP-1")
    _connector(root, "card0-HDMI-A-1", status="disconnected")
    # sysfs also exposes non-DRM entries here; they must be ignored.
    (root / "version").write_text("drm 1.1.0\n")
    (root / "renderD128").mkdir()

    inventory = collect_inventory(root)

    assert [gpu.card for gpu in inventory.gpus] == ["card0"]
    assert [connector.name for connector in inventory.connectors] == ["DP-1", "HDMI-A-1"]


def test_a_flood_of_modes_is_capped(tmp_path: Path) -> None:
    """A driver advertising thousands of modes is a bug, not information."""
    root = tmp_path / "drm"
    root.mkdir()
    _connector(root, "card0-DP-1", modes="".join(f"mode-{index}\n" for index in range(500)))

    assert len(collect_inventory(root).connectors[0].modes) == 64


def test_inventory_dict_matches_the_contract_shape(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    _card(root, "card0")
    _connector(root, "card0-DP-1", edid=(FIXTURES / "valid.bin").read_bytes())

    record = collect_inventory(root).to_dict()

    assert set(record) == {"gpus", "connectors"}
    assert record["gpus"][0]["card"] == "card0"
    assert record["connectors"][0]["name"] == "DP-1"
    assert record["connectors"][0]["edid"]["manufacturer"] == "GSM"
