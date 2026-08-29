from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from prometheus_client.parser import text_string_to_metric_families

from signage_controller.observability import PrometheusMetricsReporter, build_metrics_app
from signage_controller.tv.base import TelevisionState


def _metric_families(reporter: PrometheusMetricsReporter) -> dict[str, list]:
    from prometheus_client import generate_latest

    text = generate_latest(reporter.registry).decode("utf-8")
    return {family.name: list(family.samples) for family in text_string_to_metric_families(text)}


def test_reporter_exposes_tv_gauges() -> None:
    reporter = PrometheusMetricsReporter(tv_ids=("left",))
    reporter.update(
        "left", connected=True, state=TelevisionState(is_on=True, current_input="HDMI_1", volume=7)
    )

    families = _metric_families(reporter)
    assert families["signage_controller_tv_connection_up"][0].value == 1
    assert families["signage_controller_tv_power_on"][0].value == 1
    assert families["signage_controller_tv_volume"][0].value == 7
    input_samples = families["signage_controller_tv_input"]
    assert any(
        sample.labels["input_id"] == "HDMI_1" and sample.value == 1 for sample in input_samples
    )
    assert families["signage_controller_tv_status_updated_timestamp_seconds"][0].value > 0


def test_reporter_starts_connection_gauge_at_zero_for_known_tvs() -> None:
    reporter = PrometheusMetricsReporter(tv_ids=("left", "right"))

    families = _metric_families(reporter)
    connection = {s.labels["tv_id"]: s.value for s in families["signage_controller_tv_connection_up"]}
    assert connection == {"left": 0, "right": 0}


def test_reporter_replaces_current_input() -> None:
    reporter = PrometheusMetricsReporter(tv_ids=("left",))
    reporter.update("left", state=TelevisionState(current_input="HDMI_1"))
    reporter.update("left", state=TelevisionState(current_input="HDMI_2"))

    families = _metric_families(reporter)
    input_ids = {sample.labels["input_id"] for sample in families["signage_controller_tv_input"]}
    assert input_ids == {"HDMI_2"}


def test_reporter_mark_all_unavailable_zeroes_connection() -> None:
    reporter = PrometheusMetricsReporter(tv_ids=("left",))
    reporter.update("left", connected=True, state=TelevisionState(is_on=True))

    reporter.mark_all_unavailable()

    families = _metric_families(reporter)
    assert families["signage_controller_tv_connection_up"][0].value == 0


def test_reporter_records_tv_command_outcomes() -> None:
    reporter = PrometheusMetricsReporter(tv_ids=("left",))

    reporter.record_command("left", "set_input", success=True, duration=0.05)
    reporter.record_command("left", "set_input", success=False, duration=0.1)

    families = _metric_families(reporter)
    counts = {
        (sample.labels["command"], sample.labels["result"]): sample.value
        for sample in families["signage_controller_tv_commands"]
    }
    assert counts[("set_input", "success")] == 1
    assert counts[("set_input", "failure")] == 1
    assert any(
        sample.name == "signage_controller_tv_command_duration_seconds_count"
        for sample in families["signage_controller_tv_command_duration_seconds"]
    )


def test_reporter_exposes_player_metrics() -> None:
    reporter = PrometheusMetricsReporter(player_ids=("dev-menu",))

    reporter.record_player_healthy("dev-menu", healthy=True)
    reporter.record_player_restart("dev-menu")
    reporter.record_player_restart("dev-menu")

    families = _metric_families(reporter)
    assert families["signage_controller_player_up"][0].value == 1
    assert families["signage_controller_player_restarts"][0].value == 2
    assert families["signage_controller_player_status_updated_timestamp_seconds"][0].value > 0


def test_reporter_registers_standard_runtime_collectors() -> None:
    # A private CollectorRegistry starts empty, unlike prometheus_client's
    # global default -- these must be registered explicitly (observability.py
    # does this in __init__) or "process_start_time_seconds" silently vanishes.
    reporter = PrometheusMetricsReporter(tv_ids=("left",))

    families = _metric_families(reporter)
    assert "python_info" in families
    assert "python_gc_objects_collected" in families


def test_reporter_without_tv_ids_omits_tv_metrics() -> None:
    reporter = PrometheusMetricsReporter(player_ids=("dev-menu",))

    families = _metric_families(reporter)
    assert "signage_controller_tv_connection_up" not in families


@pytest.mark.asyncio
async def test_metrics_endpoint_serves_prometheus_exposition() -> None:
    reporter = PrometheusMetricsReporter(tv_ids=("left",))
    reporter.update("left", connected=True, state=TelevisionState(is_on=True))

    server = TestServer(build_metrics_app(reporter.registry))
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.get("/metrics")
        assert response.status == 200
        assert "text/plain" in response.content_type
        body = await response.text()
        families = {family.name for family in text_string_to_metric_families(body)}
        assert "signage_controller_tv_connection_up" in families
    finally:
        await client.close()
