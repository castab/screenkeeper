"""Small, testable abstraction for television control."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


class TelevisionError(RuntimeError):
    """Base error raised by a television adapter."""


class TelevisionUnavailableError(TelevisionError):
    """The television cannot currently be reached."""


class TelevisionPairingError(TelevisionError):
    """The television rejected or could not complete pairing."""


class TelevisionCommandError(TelevisionError):
    """A connected television rejected or did not complete a command."""


@dataclass(frozen=True, slots=True)
class TvInput:
    """An input reported by a television."""

    id: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class TelevisionState:
    """Normalized state useful to the controller and diagnostics."""

    power_state: str | None = None
    is_on: bool | None = None
    current_input: str | None = None
    volume: int | None = None


StateListener = Callable[[TelevisionState], None]


class Television(ABC):
    """The small behavior surface required by the desired-state controller."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the adapter currently has a usable connection."""

    @property
    @abstractmethod
    def client_key(self) -> str | None:
        """Return the current pairing key, if the TV has authorized this client."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect and complete webOS pairing if required."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and release network resources."""

    @abstractmethod
    async def get_state(self) -> TelevisionState:
        """Return cached subscription state where available."""

    @abstractmethod
    async def get_inputs(self) -> list[TvInput]:
        """Return currently available input IDs."""

    @abstractmethod
    async def get_input(self) -> str | None:
        """Return the current input ID."""

    @abstractmethod
    async def set_input(self, input_id: str) -> None:
        """Select an input by its television-reported ID."""

    @abstractmethod
    async def get_volume(self) -> int | None:
        """Return the current volume."""

    @abstractmethod
    async def set_volume(self, volume: int) -> None:
        """Set an absolute volume without muting."""

    @abstractmethod
    def add_state_listener(self, listener: StateListener) -> None:
        """Register a callback invoked after state subscriptions update."""
