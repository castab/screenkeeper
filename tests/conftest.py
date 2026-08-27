from __future__ import annotations

import asyncio

from signage_controller.tv.base import (
    StateListener,
    Television,
    TelevisionState,
    TelevisionUnavailableError,
    TvInput,
)


class FakeTelevision(Television):
    """In-memory television used to verify controller behavior without a TV."""

    def __init__(
        self,
        *,
        inputs: list[str] | None = None,
        current_input: str | None = "HDMI_1",
        volume: int | None = 0,
        connect_outcomes: list[Exception | None] | None = None,
    ) -> None:
        self.inputs = inputs or ["HDMI_1", "HDMI_2"]
        self.current_input = current_input
        self.volume = volume
        self.connect_outcomes = connect_outcomes or []
        self.connected = False
        self.key = "fake-client-key"
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.set_input_calls: list[str] = []
        self.set_volume_calls: list[int] = []
        self.listeners: list[StateListener] = []
        self.stop_event: asyncio.Event | None = None
        self.disconnect_after_input_calls = 0
        self.stop_after_input_calls = 0
        self.stop_after_volume_calls = 0
        self.wrong_input_on_connect_calls: set[int] = set()
        self.set_input_times: list[float] = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    @property
    def client_key(self) -> str | None:
        return self.key

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_outcomes:
            outcome = self.connect_outcomes.pop(0)
            if outcome is not None:
                raise outcome
        self.connected = True
        if self.connect_calls in self.wrong_input_on_connect_calls:
            self.current_input = "HDMI_2"

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def get_state(self) -> TelevisionState:
        return TelevisionState(is_on=self.connected, current_input=self.current_input, volume=self.volume)

    async def get_inputs(self) -> list[TvInput]:
        return [TvInput(id=input_id) for input_id in self.inputs]

    async def get_input(self) -> str | None:
        return self.current_input

    async def set_input(self, input_id: str) -> None:
        self.set_input_times.append(asyncio.get_running_loop().time())
        self.set_input_calls.append(input_id)
        self.current_input = input_id
        if len(self.set_input_calls) <= self.disconnect_after_input_calls:
            self.connected = False
        if self.stop_event is not None and len(self.set_input_calls) >= self.stop_after_input_calls > 0:
            self.stop_event.set()
        self._notify()

    async def get_volume(self) -> int | None:
        return self.volume

    async def set_volume(self, volume: int) -> None:
        self.set_volume_calls.append(volume)
        self.volume = volume
        if self.stop_event is not None and len(self.set_volume_calls) >= self.stop_after_volume_calls > 0:
            self.stop_event.set()
        self._notify()

    def add_state_listener(self, listener: StateListener) -> None:
        self.listeners.append(listener)

    def _notify(self) -> None:
        self.emit_state(is_on=self.connected)

    def emit_state(self, *, is_on: bool | None) -> None:
        state = TelevisionState(is_on=is_on, current_input=self.current_input, volume=self.volume)
        for listener in self.listeners:
            listener(state)


def unavailable() -> TelevisionUnavailableError:
    return TelevisionUnavailableError("TV is powered off")
