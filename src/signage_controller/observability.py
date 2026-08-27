"""Optional Loki logging and Prometheus Remote Write status reporting."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import socket
import struct
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import snappy

from .config import LokiConfig, PrometheusConfig
from .tv.base import TelevisionState


LOGGER = logging.getLogger(__name__)
SERVICE_NAME = "signage-controller"
LOKI_QUEUE_SIZE = 1_000
LOKI_BATCH_SIZE = 100
LOKI_BATCH_INTERVAL = 1.0
REQUEST_TIMEOUT = 10.0
REMOTE_WRITE_VERSION = "0.1.0"
STALE_NAN_BITS = 0x7FF0000000000002


class ObservabilityError(ValueError):
    """Raised when configured telemetry cannot be initialized safely."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so bearer tokens and request bodies stay at the configured host."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def _open_url(request: Request, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class _ApplicationLogFilter(logging.Filter):
    """Keep dependency protocol logs out of the external Loki stream."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("signage_controller")


class StatusReporter(Protocol):
    """Receive partial controller status updates without affecting control flow."""

    def update(
        self,
        tv_id: str,
        *,
        connected: bool | None = None,
        state: TelevisionState | None = None,
    ) -> None:
        """Record the latest known connection and television state."""


def get_bearer_token(environment_variable: str) -> str:
    """Read a required bearer token without ever including it in an error."""
    token = os.environ.get(environment_variable)
    if not token:
        raise ObservabilityError(
            f"Environment variable {environment_variable!r} is required for configured telemetry."
        )
    return token


class LokiHandler(logging.Handler):
    """Send log records to Loki from a bounded background queue."""

    def __init__(self, config: LokiConfig, *, instance: str | None = None) -> None:
        super().__init__()
        self._url = config.url
        self._token = get_bearer_token(config.bearer_token_env)
        self._base_labels = {
            "service": SERVICE_NAME,
            "instance": instance or socket.gethostname(),
            **dict(config.labels),
        }
        self._queue: queue.Queue[tuple[int, str, dict[str, str]] | None] = queue.Queue(
            maxsize=LOKI_QUEUE_SIZE
        )
        self._closed = threading.Event()
        self._worker = threading.Thread(target=self._run, name="loki-log-sender", daemon=True)
        self.setFormatter(logging.Formatter("%(message)s"))
        self.addFilter(_ApplicationLogFilter())
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        """Queue an already-rendered record without doing network I/O."""
        if self._closed.is_set():
            return
        try:
            labels = {
                **self._base_labels,
                "level": record.levelname.lower(),
            }
            tv_id = getattr(record, "tv_id", None)
            if isinstance(tv_id, str) and tv_id:
                labels["tv_id"] = tv_id
            self._queue.put_nowait((int(record.created * 1_000_000_000), self.format(record), labels))
        except queue.Full:
            self._diagnostic("Loki log queue is full; dropping a log record")
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Flush queued records for a bounded time during shutdown."""
        if not self._closed.is_set():
            self._closed.set()
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            self._worker.join(timeout=REQUEST_TIMEOUT)
        super().close()

    def _run(self) -> None:
        while True:
            try:
                entry = self._queue.get(timeout=LOKI_BATCH_INTERVAL)
            except queue.Empty:
                if self._closed.is_set():
                    return
                continue
            if entry is None:
                return

            batch = [entry]
            deadline = time.monotonic() + LOKI_BATCH_INTERVAL
            while len(batch) < LOKI_BATCH_SIZE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    entry = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if entry is None:
                    self._send(batch)
                    return
                batch.append(entry)
            self._send(batch)

    def _send(self, batch: list[tuple[int, str, dict[str, str]]]) -> None:
        streams: dict[tuple[tuple[str, str], ...], list[list[str]]] = {}
        for timestamp, message, labels in batch:
            stream = streams.setdefault(tuple(sorted(labels.items())), [])
            stream.append([str(timestamp), message])
        payload = json.dumps(
            {
                "streams": [
                    {"stream": dict(labels), "values": values}
                    for labels, values in streams.items()
                ]
            }
        ).encode("utf-8")

        for attempt in range(3):
            try:
                request = Request(
                    self._url,
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with _open_url(request, timeout=REQUEST_TIMEOUT):
                    return
            except HTTPError as err:
                if err.code != 429 and err.code < 500:
                    self._diagnostic(f"Loki log delivery failed: HTTP {err.code}")
                    return
                delivery_error: Exception = err
            except (OSError, TimeoutError, URLError) as err:
                delivery_error = err
            if attempt == 2:
                self._diagnostic(f"Loki log delivery failed: {delivery_error}")
                return
            time.sleep(0.25 * (attempt + 1))

    @staticmethod
    def _diagnostic(message: str) -> None:
        # Do not use logging here: this handler may be attached to the root logger.
        sys.stderr.write(f"{SERVICE_NAME}: {message}\n")


@dataclass(frozen=True, slots=True)
class _RemoteWriteSample:
    labels: tuple[tuple[str, str], ...]
    value: float
    timestamp: int
    stale: bool = False


class PrometheusStatusReporter:
    """Maintain latest TV gauges and send them using Remote Write 1.0."""

    def __init__(self, config: PrometheusConfig, tv_ids: Iterable[str]) -> None:
        self._config = config
        self._token = get_bearer_token(config.bearer_token_env)
        self._common_labels = {
            "job": config.job,
            "instance": config.instance or socket.gethostname(),
        }
        self._connection = {tv_id: 0 for tv_id in tv_ids}
        self._power: dict[str, int] = {}
        self._volume: dict[str, int] = {}
        self._input: dict[str, str] = {}
        self._updated: dict[str, float] = {}
        self._stale_samples: list[_RemoteWriteSample] = []
        self._changed = asyncio.Event()
        self._push_lock = asyncio.Lock()

    def update(
        self,
        tv_id: str,
        *,
        connected: bool | None = None,
        state: TelevisionState | None = None,
    ) -> None:
        """Record one partial status update and request a prompt write."""
        changed = False
        if connected is not None:
            self._connection[tv_id] = int(connected)
            changed = True
        if state is not None:
            if state.is_on is not None:
                self._power[tv_id] = int(state.is_on)
                changed = True
            if state.current_input:
                previous = self._input.get(tv_id)
                if previous and previous != state.current_input:
                    self._stale_samples.append(
                        _RemoteWriteSample(
                            self._labels(
                                "signage_controller_tv_input", tv_id, input_id=previous
                            ),
                            0,
                            int(time.time() * 1000),
                            stale=True,
                        )
                    )
                self._input[tv_id] = state.current_input
                changed = True
            if state.volume is not None:
                self._volume[tv_id] = state.volume
                changed = True
        if changed:
            self._updated[tv_id] = time.time()
            self._changed.set()

    def mark_all_unavailable(self) -> None:
        """Record graceful shutdown without discarding the last observed status."""
        for tv_id in self._connection:
            self.update(tv_id, connected=False)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Write at startup, after changes, and on the configured cadence."""
        while not stop_event.is_set():
            self._changed.clear()
            await self.push()
            if stop_event.is_set():
                return
            if self._changed.is_set():
                continue
            await self._wait_for_change_or_stop(stop_event)

    async def push(self) -> None:
        """Send current metrics without allowing transport failures to stop control."""
        async with self._push_lock:
            samples = self._samples()
            stale_count = len(self._stale_samples)
            payload = snappy.compress(_encode_remote_write(samples))
            try:
                await asyncio.to_thread(self._send, payload)
            except Exception as err:
                LOGGER.warning("Prometheus remote-write status delivery failed: %s", err)
            else:
                del self._stale_samples[:stale_count]

    def _samples(self) -> list[_RemoteWriteSample]:
        timestamp = int(time.time() * 1000)
        samples = list(self._stale_samples)
        samples.extend(
            _RemoteWriteSample(
                self._labels("signage_controller_tv_connection_up", tv_id), value, timestamp
            )
            for tv_id, value in self._connection.items()
        )
        samples.extend(
            _RemoteWriteSample(
                self._labels("signage_controller_tv_power_on", tv_id), value, timestamp
            )
            for tv_id, value in self._power.items()
        )
        samples.extend(
            _RemoteWriteSample(
                self._labels("signage_controller_tv_volume", tv_id), value, timestamp
            )
            for tv_id, value in self._volume.items()
        )
        samples.extend(
            _RemoteWriteSample(
                self._labels("signage_controller_tv_input", tv_id, input_id=input_id),
                1,
                timestamp,
            )
            for tv_id, input_id in self._input.items()
        )
        samples.extend(
            _RemoteWriteSample(
                self._labels("signage_controller_tv_status_updated_timestamp_seconds", tv_id),
                value,
                timestamp,
            )
            for tv_id, value in self._updated.items()
        )
        return samples

    def _labels(self, metric: str, tv_id: str, **extra: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                {
                    "__name__": metric,
                    "tv_id": tv_id,
                    **self._common_labels,
                    **extra,
                }.items()
            )
        )

    async def _wait_for_change_or_stop(self, stop_event: asyncio.Event) -> None:
        changed_task = asyncio.create_task(self._changed.wait())
        stop_task = asyncio.create_task(stop_event.wait())
        tasks = {changed_task, stop_task}
        try:
            await asyncio.wait(
                tasks,
                timeout=self._config.push_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _send(self, payload: bytes) -> None:
        request = Request(
            self._config.remote_write_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Encoding": "snappy",
                "Content-Type": "application/x-protobuf",
                "User-Agent": f"{SERVICE_NAME}/0.1.0",
                "X-Prometheus-Remote-Write-Version": REMOTE_WRITE_VERSION,
            },
            method="POST",
        )
        for attempt in range(3):
            try:
                with _open_url(request, timeout=REQUEST_TIMEOUT):
                    return
            except HTTPError as err:
                if err.code < 400 or 400 <= err.code < 500 and err.code != 429:
                    raise RuntimeError(f"remote-write receiver returned HTTP {err.code}") from err
                last_error: Exception = err
            except (OSError, TimeoutError, URLError) as err:
                last_error = err
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"remote-write delivery failed: {last_error}")


def _encode_remote_write(samples: list[_RemoteWriteSample]) -> bytes:
    """Encode the stable Remote Write 1.0 protobuf schema without a code generator."""
    return b"".join(_protobuf_bytes(1, _encode_time_series(sample)) for sample in samples)


def _encode_time_series(sample: _RemoteWriteSample) -> bytes:
    labels = b"".join(
        _protobuf_bytes(1, _protobuf_bytes(1, name.encode()) + _protobuf_bytes(2, value.encode()))
        for name, value in sample.labels
    )
    value = struct.pack("<Q", STALE_NAN_BITS) if sample.stale else struct.pack("<d", sample.value)
    encoded_sample = b"\x09" + value + _protobuf_varint(2, sample.timestamp)
    return labels + _protobuf_bytes(2, encoded_sample)


def _protobuf_bytes(field: int, value: bytes) -> bytes:
    return _protobuf_key(field, 2) + _encode_varint(len(value)) + value


def _protobuf_varint(field: int, value: int) -> bytes:
    return _protobuf_key(field, 0) + _encode_varint(value)


def _protobuf_key(field: int, wire_type: int) -> bytes:
    return _encode_varint((field << 3) | wire_type)


def _encode_varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)
