"""LG webOS implementation of the television abstraction."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

import aiohttp
from aiowebostv import WebOsClient, WebOsTvCommandError, WebOsTvPairError
from aiowebostv.models import WebOsTvState

from .base import (
    StateListener,
    Television,
    TelevisionCommandError,
    TelevisionPairingError,
    TelevisionState,
    TelevisionUnavailableError,
    TvInput,
)


LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class LgWebOsTelevision(Television):
    """Adapt aiowebostv's public API to the controller's narrow interface."""

    def __init__(self, host: str, client_key: str | None = None) -> None:
        self._client = WebOsClient(host, client_key)
        self._listeners: list[StateListener] = []
        self._client_callback_registered = False
        self._input_ids_by_app_id: dict[str, str] = {}

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected()

    @property
    def client_key(self) -> str | None:
        return self._client.client_key

    async def connect(self) -> None:
        if not self._client_callback_registered:
            await self._client.register_state_update_callback(self._handle_state_update)
            self._client_callback_registered = True
        connected = await self._translate(self._client.connect())
        if not connected:
            raise TelevisionUnavailableError("LG TV did not accept the connection")

    async def disconnect(self) -> None:
        await self._translate(self._client.disconnect())

    async def get_state(self) -> TelevisionState:
        return self._state_from_webos(self._client.tv_state)

    async def get_inputs(self) -> list[TvInput]:
        devices = await self._get_input_devices()
        inputs: list[TvInput] = []
        for device in devices:
            command_id = device.get("id")
            app_id = device.get("appId")
            if not isinstance(command_id, str) or not command_id:
                command_id = app_id
            if not isinstance(command_id, str) or not command_id:
                continue
            if isinstance(app_id, str) and app_id:
                self._input_ids_by_app_id[app_id] = command_id
            label = device.get("label") or device.get("name")
            inputs.append(TvInput(id=command_id, label=label if isinstance(label, str) else None))
        return inputs

    async def get_input(self) -> str | None:
        input_id = await self._translate(self._client.get_input())
        if input_id is not None and not isinstance(input_id, str):
            raise TelevisionCommandError("LG webOS returned an invalid current input ID.")
        if input_id is None:
            return None
        if input_id in self._input_ids_by_app_id:
            return self._input_ids_by_app_id[input_id]

        # webOS returns an appId for the active HDMI source but set_input()
        # requires the separate device id, so refresh the mapping on demand.
        await self.get_inputs()
        return self._input_ids_by_app_id.get(input_id, input_id)

    async def _get_input_devices(self) -> list[dict[str, object]]:
        devices = await self._translate(self._client.get_inputs())
        if devices is None:
            return []
        if not isinstance(devices, list):
            raise TelevisionCommandError("LG webOS returned an invalid input list.")
        valid_devices: list[dict[str, object]] = []
        for device in devices:
            if isinstance(device, dict):
                valid_devices.append(device)
        return valid_devices

    async def set_input(self, input_id: str) -> None:
        await self._translate(self._client.set_input(input_id))

    async def get_volume(self) -> int | None:
        volume = await self._translate(self._client.get_volume())
        if volume is not None and (isinstance(volume, bool) or not isinstance(volume, int)):
            raise TelevisionCommandError("LG webOS returned an invalid volume.")
        return volume

    async def set_volume(self, volume: int) -> None:
        await self._translate(self._client.set_volume(volume))

    def add_state_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    async def _handle_state_update(self, state: WebOsTvState) -> None:
        normalized_state = self._state_from_webos(state)
        for listener in self._listeners:
            try:
                listener(normalized_state)
            except Exception:
                LOGGER.exception("LG webOS state listener failed")

    def _state_from_webos(self, state: WebOsTvState) -> TelevisionState:
        power_value = state.power_state.get("state")
        current_input = state.current_app_id
        if current_input in self._input_ids_by_app_id:
            current_input = self._input_ids_by_app_id[current_input]
        elif current_input not in self._input_ids_by_app_id.values():
            current_input = None
        return TelevisionState(
            power_state=power_value if isinstance(power_value, str) else None,
            is_on=state.is_on,
            current_input=current_input,
            volume=state.volume,
        )

    async def _translate(self, operation: Awaitable[T]) -> T:
        try:
            return await operation
        except asyncio.CancelledError:
            raise
        except WebOsTvPairError as err:
            raise TelevisionPairingError(
                "LG TV pairing was rejected or did not complete. Approve the connection "
                "prompt on the TV, then run the pair command again."
            ) from err
        except (aiohttp.ClientError, aiohttp.WSMessageTypeError, OSError, TimeoutError) as err:
            raise TelevisionUnavailableError(str(err) or "LG TV is unavailable") from err
        except WebOsTvCommandError as err:
            raise TelevisionCommandError(str(err) or "LG webOS command failed") from err
