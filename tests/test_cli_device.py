from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from signage_controller.cli import (
    _agent_run,
    _device_enroll,
    _device_inventory,
    _device_status,
    build_parser,
)
from signage_controller.config import ApplicationConfig, ControlPlaneConfig, TvConfig
from signage_controller.control_plane.identity import DeviceStore
from signage_controller.state_store import StateStore


TVS = (
    TvConfig(
        id="dev-tv",
        name="Development LG TV",
        host="192.168.50.21",
        desired_input="HDMI_1",
        desired_volume=0,
    ),
)
CONTROL_PLANE = ControlPlaneConfig(base_url="https://screenkeeper.example.com")


def _enrolled(tmp_path: Path) -> DeviceStore:
    store = DeviceStore(tmp_path)
    store.record_claim(
        store.load_or_create(),
        player_id="player-1",
        player_name="Main Menu Wall",
        organization_id="org-1",
        organization_name="Example Restaurant",
        location_id="loc-1",
        location_name="Downtown",
        enrolled_at="2026-08-29T04:00:00Z",
    )
    return store


def test_parser_accepts_the_device_commands() -> None:
    for command in ("status", "inventory", "enroll"):
        args = build_parser().parse_args(["device", command])
        assert args.command == "device"
        assert args.device_command == command


def test_parser_accepts_inventory_json_flag() -> None:
    assert build_parser().parse_args(["device", "inventory"]).as_json is False
    assert build_parser().parse_args(["device", "inventory", "--json"]).as_json is True


def test_parser_accepts_agent_run() -> None:
    args = build_parser().parse_args(["agent", "run"])

    assert args.command == "agent"
    assert args.agent_command == "run"


def test_parser_requires_a_device_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["device"])


def test_parser_keeps_existing_commands_unchanged() -> None:
    """The new groups must not disturb the commands operators already use."""
    for command in ("pair", "status", "inputs", "apply"):
        args = build_parser().parse_args([command, "dev-tv"])
        assert args.command == command
        assert args.tv_id == "dev-tv"

    assert build_parser().parse_args(["run"]).command == "run"
    assert build_parser().parse_args(["playback", "run"]).playback_command == "run"


def test_device_status_never_prints_the_token(tmp_path: Path, capsys) -> None:
    store = _enrolled(tmp_path)
    identity = store.load()
    assert identity is not None
    config = ApplicationConfig(tvs=TVS, control_plane=CONTROL_PLANE)

    exit_code = _device_status(config, store)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert identity.device_token not in output
    assert identity.installation_id in output


def test_device_status_shows_the_assignment_and_control_plane(tmp_path: Path, capsys) -> None:
    store = _enrolled(tmp_path)
    config = ApplicationConfig(tvs=TVS, control_plane=CONTROL_PLANE)

    _device_status(config, store)

    output = capsys.readouterr().out
    assert "enrolled" in output
    assert "player-1" in output
    assert "Example Restaurant" in output
    assert "Downtown" in output
    assert "https://screenkeeper.example.com" in output
    assert "Last heartbeat: never" in output


def test_device_status_before_enrollment_reports_no_identity(tmp_path: Path, capsys) -> None:
    config = ApplicationConfig(tvs=TVS)

    exit_code = _device_status(config, DeviceStore(tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "not enrolled" in output
    assert "(not configured)" in output


def test_device_status_does_not_create_an_identity(tmp_path: Path, capsys) -> None:
    """Reading status must not mint a credential the host has no use for."""
    store = DeviceStore(tmp_path)

    _device_status(ApplicationConfig(tvs=TVS), store)
    capsys.readouterr()

    assert not store.path.exists()


def test_device_inventory_prints_a_readable_report(capsys) -> None:
    exit_code = _device_inventory(as_json=False)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "GPUs:" in output
    assert "Connectors:" in output


def test_device_inventory_json_is_machine_readable(capsys) -> None:
    import json

    exit_code = _device_inventory(as_json=True)

    record = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert set(record) == {"gpus", "connectors"}


async def test_enroll_without_a_control_plane_fails_clearly(tmp_path: Path) -> None:
    exit_code = await _device_enroll(ApplicationConfig(tvs=TVS), DeviceStore(tmp_path))

    assert exit_code == 1
    # No control plane means no reason to have generated a credential.
    assert not (tmp_path / "device.json").exists()


async def test_agent_run_without_a_control_plane_exits_cleanly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The unit ships installed-but-disabled; starting it unconfigured must not loop."""
    import logging

    with caplog.at_level(logging.INFO, logger="signage_controller.cli"):
        exit_code = await _agent_run(ApplicationConfig(tvs=TVS), StateStore(tmp_path))

    assert exit_code == 0
    assert any("control_plane" in record.getMessage() for record in caplog.records)


async def test_agent_run_without_an_enrollment_reports_the_next_step(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    config = ApplicationConfig(tvs=TVS, control_plane=CONTROL_PLANE)

    with caplog.at_level(logging.ERROR, logger="signage_controller.cli"):
        exit_code = await _agent_run(config, StateStore(tmp_path))

    assert exit_code == 1
    assert any("device enroll" in record.getMessage() for record in caplog.records)


async def test_agent_run_refuses_to_share_its_lock(tmp_path: Path) -> None:
    """agent.lock is its own lock, so it can run alongside run and playback run."""
    from signage_controller.runtime_lock import acquire_controller_lock

    _enrolled(tmp_path)
    config = ApplicationConfig(tvs=TVS, control_plane=CONTROL_PLANE)

    with acquire_controller_lock(tmp_path, name="agent.lock", command="agent run"):
        exit_code = await asyncio.wait_for(_agent_run(config, StateStore(tmp_path)), timeout=5)

    assert exit_code == 1


async def test_the_agent_lock_is_independent_of_the_other_runtimes(tmp_path: Path) -> None:
    from signage_controller.runtime_lock import acquire_controller_lock

    with acquire_controller_lock(tmp_path, name="controller.lock"):
        with acquire_controller_lock(tmp_path, name="playback.lock"):
            with acquire_controller_lock(tmp_path, name="agent.lock"):
                # All three runtimes coexist, which is the whole point.
                assert (tmp_path / "agent.lock").exists()
