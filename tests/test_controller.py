from __future__ import annotations

import asyncio
import logging

import pytest

from signage_controller.config import TvConfig
from signage_controller.controller import DesiredInputError, TvManager, converge
from signage_controller.state_store import StateStore
from signage_controller.tv.base import TelevisionState

from .conftest import FakeTelevision, unavailable


CONFIG = TvConfig(
    id="dev-tv",
    name="Development LG TV",
    host="192.168.1.50",
    desired_input="HDMI_1",
    desired_volume=0,
)
LOGGER = logging.getLogger("tests.controller")


class RecordingStatusReporter:
    def __init__(self) -> None:
        self.updates: list[tuple[str, bool | None, TelevisionState | None]] = []
        self.commands: list[tuple[str, str, bool]] = []

    def update(
        self,
        tv_id: str,
        *,
        connected: bool | None = None,
        state: TelevisionState | None = None,
    ) -> None:
        self.updates.append((tv_id, connected, state))

    def record_command(
        self, tv_id: str, command: str, *, success: bool, duration: float | None = None
    ) -> None:
        self.commands.append((tv_id, command, success))


@pytest.mark.asyncio
async def test_already_correct_state_causes_no_changes(caplog) -> None:
    tv = FakeTelevision(current_input="HDMI_1", volume=0)

    with caplog.at_level(logging.DEBUG, logger="tests.controller"):
        result = await converge(tv, CONFIG, LOGGER)

    assert not result.changed
    assert tv.set_input_calls == []
    assert tv.set_volume_calls == []
    assert "input already HDMI_1; no change" in caplog.text
    assert "volume already 0; no change" in caplog.text
    assert all(record.levelno == logging.DEBUG for record in caplog.records)


@pytest.mark.asyncio
async def test_converge_reports_observed_and_changed_status() -> None:
    tv = FakeTelevision(current_input="HDMI_2", volume=12)
    reporter = RecordingStatusReporter()

    await converge(tv, CONFIG, LOGGER, reporter)

    assert reporter.updates == [
        ("dev-tv", None, TelevisionState(current_input="HDMI_2", volume=12)),
        ("dev-tv", None, TelevisionState(current_input="HDMI_1")),
        ("dev-tv", None, TelevisionState(volume=0)),
    ]
    assert reporter.commands == [
        ("dev-tv", "set_input", True),
        ("dev-tv", "set_volume", True),
    ]


@pytest.mark.asyncio
async def test_wrong_input_causes_exactly_one_input_change() -> None:
    tv = FakeTelevision(current_input="HDMI_2", volume=0)

    result = await converge(tv, CONFIG, LOGGER)

    assert result.input_changed
    assert not result.volume_changed
    assert tv.set_input_calls == ["HDMI_1"]
    assert tv.set_volume_calls == []


@pytest.mark.asyncio
async def test_wrong_volume_causes_exactly_one_volume_change() -> None:
    tv = FakeTelevision(current_input="HDMI_1", volume=12)

    result = await converge(tv, CONFIG, LOGGER)

    assert not result.input_changed
    assert result.volume_changed
    assert tv.set_input_calls == []
    assert tv.set_volume_calls == [0]


@pytest.mark.asyncio
async def test_wrong_input_and_volume_change_both() -> None:
    tv = FakeTelevision(current_input="HDMI_2", volume=12)

    result = await converge(tv, CONFIG, LOGGER)

    assert result.changed
    assert tv.set_input_calls == ["HDMI_1"]
    assert tv.set_volume_calls == [0]


@pytest.mark.asyncio
async def test_invalid_configured_input_lists_reported_ids() -> None:
    tv = FakeTelevision(inputs=["HDMI_2", "HDMI_3"])

    with pytest.raises(DesiredInputError, match="HDMI_2, HDMI_3"):
        await converge(tv, CONFIG, LOGGER)


@pytest.mark.asyncio
async def test_unavailable_tv_retries_without_terminating_manager(tmp_path) -> None:
    stop_event = asyncio.Event()
    tv = FakeTelevision(
        current_input="HDMI_2",
        volume=4,
        connect_outcomes=[unavailable(), None],
    )
    tv.stop_event = stop_event
    tv.stop_after_volume_calls = 1
    manager = TvManager(
        CONFIG,
        tv,
        StateStore(tmp_path / "state"),
        reconcile_interval=1,
        power_on_delay=0,
        retry_delays=(0.001,),
    )

    await asyncio.wait_for(manager.run(stop_event), timeout=1)

    assert tv.connect_calls == 2
    assert tv.set_input_calls == ["HDMI_1"]
    assert tv.set_volume_calls == [0]


@pytest.mark.asyncio
async def test_reconnection_triggers_state_reconciliation(tmp_path) -> None:
    stop_event = asyncio.Event()
    tv = FakeTelevision(current_input="HDMI_2", volume=0)
    tv.stop_event = stop_event
    tv.disconnect_after_input_calls = 1
    tv.stop_after_input_calls = 2
    tv.wrong_input_on_connect_calls = {2}
    manager = TvManager(
        CONFIG,
        tv,
        StateStore(tmp_path / "state"),
        reconcile_interval=1,
        power_on_delay=0,
        retry_delays=(0.001,),
    )

    await asyncio.wait_for(manager.run(stop_event), timeout=1)

    assert tv.connect_calls >= 2
    assert tv.set_input_calls == ["HDMI_1", "HDMI_1"]


@pytest.mark.asyncio
async def test_initial_connection_waits_for_power_on_delay(tmp_path) -> None:
    stop_event = asyncio.Event()
    tv = FakeTelevision(current_input="HDMI_2", volume=0)
    tv.stop_event = stop_event
    tv.stop_after_input_calls = 1
    manager = TvManager(
        CONFIG,
        tv,
        StateStore(tmp_path / "state"),
        reconcile_interval=1,
        power_on_delay=0.03,
        retry_delays=(0.001,),
    )
    started_at = asyncio.get_running_loop().time()

    await asyncio.wait_for(manager.run(stop_event), timeout=1)

    assert tv.set_input_times[0] - started_at >= 0.025


@pytest.mark.asyncio
async def test_initial_correct_state_logs_verified_monitoring(tmp_path, caplog) -> None:
    stop_event = asyncio.Event()
    manager = TvManager(
        CONFIG,
        FakeTelevision(),
        StateStore(tmp_path / "state"),
        reconcile_interval=1,
        power_on_delay=0,
        logger=LOGGER,
    )
    manager_task = asyncio.create_task(manager.run(stop_event))

    with caplog.at_level(logging.INFO, logger="tests.controller"):
        for _ in range(100):
            if "dev-tv: desired state verified; monitoring" in caplog.text:
                break
            await asyncio.sleep(0)

    stop_event.set()
    await asyncio.wait_for(manager_task, timeout=1)

    assert "dev-tv: desired state verified; monitoring" in caplog.text


@pytest.mark.asyncio
async def test_power_on_callback_delays_reconciliation(tmp_path) -> None:
    stop_event = asyncio.Event()
    tv = FakeTelevision(current_input="HDMI_1", volume=0)
    tv.stop_event = stop_event
    tv.stop_after_input_calls = 1
    manager = TvManager(
        CONFIG,
        tv,
        StateStore(tmp_path / "state"),
        reconcile_interval=1,
        power_on_delay=0.03,
        retry_delays=(0.001,),
    )
    manager_task = asyncio.create_task(manager.run(stop_event))

    while not tv.connected:
        await asyncio.sleep(0)
    tv.emit_state(is_on=False)
    await asyncio.sleep(0)
    tv.current_input = "HDMI_2"
    powered_on_at = asyncio.get_running_loop().time()
    tv.emit_state(is_on=True)

    await asyncio.wait_for(manager_task, timeout=1)

    assert tv.set_input_calls == ["HDMI_1"]
    assert tv.set_input_times[0] - powered_on_at >= 0.025


@pytest.mark.asyncio
async def test_transient_app_state_update_does_not_trigger_immediate_reconciliation(tmp_path) -> None:
    stop_event = asyncio.Event()
    tv = FakeTelevision(current_input="HDMI_1", volume=0)
    manager = TvManager(
        CONFIG,
        tv,
        StateStore(tmp_path / "state"),
        reconcile_interval=0.05,
        power_on_delay=0,
        retry_delays=(0.001,),
    )
    manager_task = asyncio.create_task(manager.run(stop_event))

    while not tv.connected:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    tv.current_input = "com.webos.app.lifeonscreen"
    tv.emit_state(is_on=True)
    await asyncio.sleep(0.02)

    assert tv.set_input_calls == []
    stop_event.set()
    await asyncio.wait_for(manager_task, timeout=1)


def test_power_off_callback_logs_shutdown_once(tmp_path, caplog) -> None:
    manager = TvManager(
        CONFIG,
        FakeTelevision(),
        StateStore(tmp_path / "state"),
        reconcile_interval=1,
        power_on_delay=0,
        logger=LOGGER,
    )
    manager._is_on = True

    with caplog.at_level(logging.INFO, logger="tests.controller"):
        manager._on_state_changed(TelevisionState(power_state="Power Off", is_on=False))
        manager._on_state_changed(TelevisionState(power_state="Power Off", is_on=False))

    assert caplog.messages == ["dev-tv: TV shutting down"]


def test_connection_loss_logs_whether_shutdown_was_confirmed(tmp_path, caplog) -> None:
    manager = TvManager(
        CONFIG,
        FakeTelevision(),
        StateStore(tmp_path / "state"),
        reconcile_interval=1,
        power_on_delay=0,
        logger=LOGGER,
    )

    with caplog.at_level(logging.INFO, logger="tests.controller"):
        manager._log_connection_lost()
        manager._shutdown_reported = True
        manager._log_connection_lost()

    assert caplog.messages == [
        "dev-tv: connection lost without a shutdown notification; treating TV as unavailable",
        "dev-tv: connection closed after TV reported shutdown",
    ]
