from __future__ import annotations

import pytest
from aiohttp.client_exceptions import WSMessageTypeError

from signage_controller.tv.lg_webos import LgWebOsTelevision
from signage_controller.tv.base import TelevisionUnavailableError


class FakeWebOsClient:
    """Minimal aiowebostv-shaped client for input identifier normalization."""

    def __init__(self) -> None:
        self.input_list_calls = 0

    async def get_inputs(self):
        self.input_list_calls += 1
        return [
            {
                "id": "HDMI_1",
                "appId": "com.webos.app.hdmi1",
                "label": "Signage Player",
            }
        ]

    async def get_input(self):
        return "com.webos.app.hdmi1"


@pytest.mark.asyncio
async def test_lg_adapter_uses_device_id_for_switching_and_normalizes_current_app() -> None:
    client = FakeWebOsClient()
    television = object.__new__(LgWebOsTelevision)
    television._client = client
    television._input_ids_by_app_id = {}

    inputs = await television.get_inputs()
    current_input = await television.get_input()

    assert inputs[0].id == "HDMI_1"
    assert inputs[0].label == "Signage Player"
    assert current_input == "HDMI_1"
    assert client.input_list_calls == 1


@pytest.mark.asyncio
async def test_lg_adapter_treats_websocket_close_as_temporary_unavailability() -> None:
    television = object.__new__(LgWebOsTelevision)

    async def rejected_connection() -> None:
        raise WSMessageTypeError("Received message 8:1008 is not WSMsgType.TEXT")

    with pytest.raises(TelevisionUnavailableError, match="1008"):
        await television._translate(rejected_connection())
