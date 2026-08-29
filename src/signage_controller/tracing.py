"""Optional OpenTelemetry tracing, exported locally to a host-level agent.

Screenkeeper never transports telemetry itself: spans are exported over OTLP
to a local endpoint only (Grafana Alloy's already-open OTLP receiver at
127.0.0.1:4318 by default), never to a remote/central URL, and never with a
credential the application holds. OTLP itself is push-based, unlike the
pull-based `/metrics` exposition in `observability.py` -- but the push never
leaves localhost, so this does not cross the "Screenkeeper never transports
telemetry anywhere" line; it's the same boundary as metrics, just push
instead of pull.

Tracing is entirely optional: when disabled (the default absence of a
`tracing:` config block), or if the SDK fails to initialize for any reason,
every call site's tracer stays a true OpenTelemetry no-op -- creating spans
costs nothing and never touches the network. When enabled, `BatchSpanProcessor`
exports spans on its own background thread; if the local OTLP receiver is
unreachable, that failure is logged and retried by the SDK itself and never
raised into application code, so control/playback behavior is unaffected
either way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import TracingConfig


LOGGER = logging.getLogger(__name__)


def _noop_shutdown() -> None:
    return None


@dataclass
class TracingHandle:
    """A tracer plus its shutdown, safe to use whether or not tracing is enabled."""

    tracer: trace.Tracer
    shutdown: Callable[[], None] = field(default=_noop_shutdown)


def configure_tracing(service_name: str, config: TracingConfig | None) -> TracingHandle:
    """Return a `TracingHandle` for one process's lifetime. Never raises.

    If `config` is `None` or disabled, the returned tracer is a genuine
    OpenTelemetry no-op (no SDK objects are constructed, `set_tracer_provider`
    is never called) and `.shutdown()` is a no-op -- callers never need an
    `if enabled:` guard around either the tracer or its shutdown.
    """
    if config is None or not config.enabled:
        return TracingHandle(tracer=trace.get_tracer(service_name))

    try:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        exporter = OTLPSpanExporter(endpoint=f"{config.endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    except Exception:
        LOGGER.warning("Failed to configure tracing; continuing without it.", exc_info=True)
        return TracingHandle(tracer=trace.get_tracer(service_name))

    return TracingHandle(tracer=provider.get_tracer(service_name), shutdown=provider.shutdown)
