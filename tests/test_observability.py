from __future__ import annotations

import json
import logging
import struct
import threading

import pytest
import snappy

from signage_controller.config import LokiConfig, PrometheusConfig
from signage_controller.observability import (
    LokiHandler,
    ObservabilityError,
    PrometheusStatusReporter,
    _encode_remote_write,
    get_bearer_token,
)
from signage_controller.tv.base import TelevisionState


def test_loki_handler_sends_bearer_authenticated_labeled_payload(monkeypatch) -> None:
    monkeypatch.setenv("LOKI_TOKEN", "loki-secret")
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    def fake_urlopen(request, timeout: float):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr("signage_controller.observability.urlopen", fake_urlopen)
    handler = LokiHandler(
        LokiConfig(
            url="https://loki.example.com/loki/api/v1/push",
            bearer_token_env="LOKI_TOKEN",
            labels=(("environment", "test"),),
        ),
        instance="test-host",
    )
    try:
        handler._send([(123, "TV connected", {"service": "signage-controller", "tv_id": "left"})])
    finally:
        handler.close()

    request, timeout = requests[0]
    assert request.get_header("Authorization") == "Bearer loki-secret"
    assert timeout == 10.0
    assert json.loads(request.data) == {
        "streams": [
            {
                "stream": {"service": "signage-controller", "tv_id": "left"},
                "values": [["123", "TV connected"]],
            }
        ]
    }


def test_loki_handler_adds_record_metadata(monkeypatch) -> None:
    monkeypatch.setenv("LOKI_TOKEN", "loki-secret")
    monkeypatch.setattr("signage_controller.observability.LOKI_BATCH_INTERVAL", 0.01)
    handler = LokiHandler(
        LokiConfig(
            url="https://loki.example.com/loki/api/v1/push",
            bearer_token_env="LOKI_TOKEN",
            labels=(("environment", "test"),),
        ),
        instance="test-host",
    )
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "connected", (), None)
    record.tv_id = "left"
    sent: list[tuple[int, str, dict[str, str]]] = []
    delivered = threading.Event()

    def fake_send(batch: list[tuple[int, str, dict[str, str]]]) -> None:
        sent.extend(batch)
        delivered.set()

    monkeypatch.setattr(handler, "_send", fake_send)
    try:
        handler.emit(record)
        assert delivered.wait(timeout=1)
    finally:
        handler.close()

    timestamp, message, labels = sent[0]

    assert timestamp > 0
    assert message == "connected"
    assert labels == {
        "service": "signage-controller",
        "instance": "test-host",
        "environment": "test",
        "level": "info",
        "tv_id": "left",
    }


def test_prometheus_reporter_retains_last_status_when_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("PROMETHEUS_TOKEN", "prometheus-secret")
    reporter = PrometheusStatusReporter(
        PrometheusConfig(
            remote_write_url="https://prometheus.example.com/api/v1/write",
            bearer_token_env="PROMETHEUS_TOKEN",
            instance="test-host",
        ),
        ("left",),
    )

    reporter.update(
        "left",
        connected=True,
        state=TelevisionState(is_on=True, current_input="HDMI_1", volume=7),
    )
    reporter.update("left", connected=False)
    samples = reporter._samples()

    assert any(
        dict(sample.labels)["__name__"] == "signage_controller_tv_connection_up"
        and sample.value == 0
        for sample in samples
    )
    assert any(
        dict(sample.labels)["__name__"] == "signage_controller_tv_power_on"
        and sample.value == 1
        for sample in samples
    )
    assert any(
        dict(sample.labels)["__name__"] == "signage_controller_tv_volume" and sample.value == 7
        for sample in samples
    )
    assert any(
        dict(sample.labels).get("input_id") == "HDMI_1" and sample.value == 1
        for sample in samples
    )


def test_prometheus_reporter_replaces_input_and_uses_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("PROMETHEUS_TOKEN", "prometheus-secret")
    reporter = PrometheusStatusReporter(
        PrometheusConfig(
            remote_write_url="https://prometheus.example.com/api/v1/write",
            bearer_token_env="PROMETHEUS_TOKEN",
            job="screenkeeper",
            instance="test-host",
        ),
        ("left",),
    )
    reporter.update("left", state=TelevisionState(current_input="HDMI_1"))
    reporter.update("left", state=TelevisionState(current_input="HDMI_2"))
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    def fake_urlopen(request, timeout: float):
        captured.append((request, timeout))
        return Response()

    monkeypatch.setattr("signage_controller.observability.urlopen", fake_urlopen)
    samples = reporter._samples()
    reporter._send(snappy.compress(_encode_remote_write(samples)))

    request, timeout = captured[0]
    assert request.full_url == "https://prometheus.example.com/api/v1/write"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer prometheus-secret"
    assert request.get_header("Content-encoding") == "snappy"
    assert request.get_header("X-prometheus-remote-write-version") == "0.1.0"
    assert timeout == 10.0
    decoded = snappy.decompress(request.data)
    assert decoded[0] == 0x0A  # WriteRequest.timeseries is length-delimited.
    assert b"signage_controller_tv_input" in decoded
    assert any(
        dict(sample.labels).get("input_id") == "HDMI_1" and sample.stale for sample in samples
    )
    assert any(
        dict(sample.labels).get("input_id") == "HDMI_2" and not sample.stale for sample in samples
    )
    assert struct.pack("<Q", 0x7FF0000000000002) in _encode_remote_write(samples)


@pytest.mark.asyncio
async def test_prometheus_delivery_failure_does_not_escape(monkeypatch, caplog) -> None:
    monkeypatch.setenv("PROMETHEUS_TOKEN", "prometheus-secret")
    reporter = PrometheusStatusReporter(
        PrometheusConfig(
            remote_write_url="https://prometheus.example.com/api/v1/write",
            bearer_token_env="PROMETHEUS_TOKEN",
        ),
        ("left",),
    )

    def failed_send(payload: bytes) -> None:
        raise OSError("unreachable")

    monkeypatch.setattr(reporter, "_send", failed_send)
    with caplog.at_level(logging.WARNING, logger="signage_controller.observability"):
        await reporter.push()

    assert "remote-write status delivery failed: unreachable" in caplog.text


def test_missing_bearer_token_does_not_include_a_secret(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)

    with pytest.raises(ObservabilityError, match="MISSING_TOKEN"):
        get_bearer_token("MISSING_TOKEN")
