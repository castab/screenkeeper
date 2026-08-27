from __future__ import annotations

import asyncio
import json

import pytest

from signage_controller.playback.ipc import MpvIpcClient, MpvIpcError


async def _serve_once(socket_path, handle_request):
    """Start a fake-mpv unix server that runs `handle_request` for one connection."""
    ready = asyncio.Event()
    received: list[dict] = []

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await handle_request(reader, writer, received)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    server = await asyncio.start_unix_server(_handler, path=str(socket_path))
    ready.set()
    return server, received


async def _read_request(reader: asyncio.StreamReader) -> dict:
    line = await reader.readline()
    return json.loads(line)


async def _reply(writer: asyncio.StreamWriter, request_id: int, *, data=None, error="success") -> None:
    payload = json.dumps({"request_id": request_id, "error": error, "data": data}) + "\n"
    writer.write(payload.encode("utf-8"))
    await writer.drain()


@pytest.mark.asyncio
async def test_get_property_returns_data(tmp_path) -> None:
    socket_path = tmp_path / "mpv.sock"

    async def handler(reader, writer, received):
        request = await _read_request(reader)
        received.append(request)
        await _reply(writer, request["request_id"], data="/media/menu.mp4")

    server, received = await _serve_once(socket_path, handler)
    async with server:
        client = MpvIpcClient(socket_path, timeout=2.0)
        result = await client.get_property("path")

    assert result == "/media/menu.mp4"
    assert received[0]["command"] == ["get_property", "path"]


@pytest.mark.asyncio
async def test_error_response_raises_mpv_ipc_error(tmp_path) -> None:
    socket_path = tmp_path / "mpv.sock"

    async def handler(reader, writer, received):
        request = await _read_request(reader)
        await _reply(writer, request["request_id"], error="property unavailable")

    server, _ = await _serve_once(socket_path, handler)
    async with server:
        client = MpvIpcClient(socket_path, timeout=2.0)
        with pytest.raises(MpvIpcError, match="property unavailable"):
            await client.get_property("path")


@pytest.mark.asyncio
async def test_unsolicited_event_line_is_skipped_before_matching_reply(tmp_path) -> None:
    socket_path = tmp_path / "mpv.sock"

    async def handler(reader, writer, received):
        request = await _read_request(reader)
        # Simulate an mpv event notification arriving before our reply.
        event = json.dumps({"event": "pause"}) + "\n"
        writer.write(event.encode("utf-8"))
        await writer.drain()
        await _reply(writer, request["request_id"], data=True)

    server, _ = await _serve_once(socket_path, handler)
    async with server:
        client = MpvIpcClient(socket_path, timeout=2.0)
        result = await client.get_property("pause")

    assert result is True


@pytest.mark.asyncio
async def test_quit_sends_quit_command(tmp_path) -> None:
    socket_path = tmp_path / "mpv.sock"

    async def handler(reader, writer, received):
        request = await _read_request(reader)
        received.append(request)
        await _reply(writer, request["request_id"])

    server, received = await _serve_once(socket_path, handler)
    async with server:
        client = MpvIpcClient(socket_path, timeout=2.0)
        await client.quit()

    assert received[0]["command"] == ["quit"]


@pytest.mark.asyncio
async def test_missing_socket_raises_mpv_ipc_error(tmp_path) -> None:
    socket_path = tmp_path / "no-such.sock"

    client = MpvIpcClient(socket_path, timeout=0.5)
    with pytest.raises(MpvIpcError, match="could not connect"):
        await client.get_property("path")


@pytest.mark.asyncio
async def test_no_reply_times_out(tmp_path) -> None:
    socket_path = tmp_path / "mpv.sock"

    async def handler(reader, writer, received):
        await _read_request(reader)
        await asyncio.sleep(10)

    server, _ = await _serve_once(socket_path, handler)
    async with server:
        client = MpvIpcClient(socket_path, timeout=0.2)
        with pytest.raises(MpvIpcError, match="timed out"):
            await client.get_property("path")
