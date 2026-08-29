from __future__ import annotations

from opentelemetry import trace

from signage_controller.config import TracingConfig
from signage_controller.tracing import configure_tracing


def test_disabled_config_returns_a_true_noop_tracer_without_touching_the_global() -> None:
    global_provider_before = trace.get_tracer_provider()

    handle = configure_tracing("signage-controller-test", None)

    assert trace.get_tracer_provider() is global_provider_before
    # A no-op tracer's spans carry an invalid context; this is the cheapest
    # possible proof that no real SDK objects were constructed.
    with handle.tracer.start_as_current_span("should-not-be-recorded") as span:
        assert not span.get_span_context().is_valid
    handle.shutdown()  # must never raise, even though nothing was configured


def test_config_with_enabled_false_also_returns_a_noop_tracer() -> None:
    handle = configure_tracing("signage-controller-test", TracingConfig(enabled=False))

    with handle.tracer.start_as_current_span("should-not-be-recorded") as span:
        assert not span.get_span_context().is_valid
    handle.shutdown()


def test_enabled_config_builds_a_real_tracer_and_shutdown_is_callable() -> None:
    # Endpoint is never actually reached here (no network call in `configure_tracing`
    # itself -- export happens later, off-thread, via BatchSpanProcessor); this
    # only proves a real SDK TracerProvider gets built and returns real spans.
    handle = configure_tracing(
        "signage-controller-test", TracingConfig(enabled=True, endpoint="http://127.0.0.1:4318")
    )

    with handle.tracer.start_as_current_span("real-span") as span:
        assert span.get_span_context().is_valid
    handle.shutdown()
