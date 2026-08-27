"""A minimal client for mpv's documented JSON IPC protocol over a Unix socket."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from pathlib import Path
from typing import Any


DEFAULT_IPC_TIMEOUT = 2.0


class MpvIpcError(RuntimeError):
    """Raised when the mpv JSON IPC endpoint is unreachable or reports an error."""


class MpvIpcClient:
    """Send one JSON IPC request per connection and parse mpv's reply.

    mpv interleaves unsolicited event lines with command replies on the same
    socket, so replies are matched by `request_id` and any non-matching line
    is skipped rather than treated as the response.
    """

    _request_ids = itertools.count(1)

    def __init__(self, socket_path: Path, *, timeout: float = DEFAULT_IPC_TIMEOUT) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    async def get_property(self, name: str) -> Any:
        """Return the value of an mpv property, e.g. 'path', 'pause', 'idle-active'."""
        return await self._request(["get_property", name])

    async def quit(self) -> None:
        """Ask mpv to exit cleanly."""
        await self._request(["quit"])

    async def _request(self, command: list[Any]) -> Any:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)), timeout=self._timeout
            )
        except (OSError, TimeoutError) as err:
            raise MpvIpcError(
                f"could not connect to mpv IPC socket {self._socket_path}: {err}"
            ) from err

        try:
            request_id = next(self._request_ids)
            payload = json.dumps({"command": command, "request_id": request_id}) + "\n"
            writer.write(payload.encode("utf-8"))
            await writer.drain()
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
                except TimeoutError as err:
                    raise MpvIpcError("timed out waiting for an mpv IPC response") from err
                if not line:
                    raise MpvIpcError("mpv closed the IPC connection without a response")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as err:
                    raise MpvIpcError(f"invalid JSON from mpv IPC: {line!r}") from err
                if not isinstance(message, dict) or message.get("request_id") != request_id:
                    continue
                if message.get("error") != "success":
                    raise MpvIpcError(f"mpv IPC error for {command}: {message.get('error')}")
                return message.get("data")
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
