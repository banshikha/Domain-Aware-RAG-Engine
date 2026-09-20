"""
app/core/observability.py
─────────────────────────
Configures OpenTelemetry distributed tracing and structlog-based structured
logging for the ML microservice.

Design decisions:
  • A single ``setup_observability()`` call wires everything at startup via the
    FastAPI lifespan.  Callers never touch OTEL internals directly.
  • Trace context is propagated over HTTP using the W3C TraceContext +
    Baggage propagators — compatible with the Node.js gateway and Jaeger.
  • The OTLP gRPC exporter is used (matches the gateway's choice of gRPC for
    inter-service comms).  Falls back to a NoOp exporter when OTEL is disabled.
  • structlog is bound to the standard ``logging`` module so existing
    ``logging.getLogger(__name__)`` calls in third-party libraries are
    captured without modification.
  • Every log record is automatically enriched with the active trace_id and
    span_id so logs and traces correlate in Jaeger / Grafana.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Resource,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator

from app.core.config import Settings, get_settings

# ── Module-level tracer (lazy, replaced after setup_observability) ────────────
_tracer: trace.Tracer = trace.get_tracer(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def setup_observability(settings: Settings | None = None) -> None:
    """
    Wire up OpenTelemetry tracing and structlog.  Call once inside the
    FastAPI lifespan *before* the application starts serving requests.
    """
    settings = settings or get_settings()
    _configure_tracing(settings)
    _configure_logging(settings)

    global _tracer
    _tracer = trace.get_tracer(
        settings.app_name,
        schema_url="https://opentelemetry.io/schemas/1.24.0",
    )

    structlog.get_logger(__name__).info(
        "observability_initialised",
        otel_enabled=settings.otel_enabled,
        log_level=settings.log_level,
        service=settings.otel_service_name,
    )


def get_tracer() -> trace.Tracer:
    """Return the application-scoped tracer.  Safe to call at import time."""
    return _tracer


def instrument_fastapi(app: Any) -> None:
    """
    Apply FastAPIInstrumentor to an existing FastAPI application instance.
    Must be called *after* setup_observability().
    """
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=trace.get_tracer_provider(),
        excluded_urls="/health,/metrics",
    )


# ── Tracing internals ─────────────────────────────────────────────────────────

def _configure_tracing(settings: Settings) -> None:
    resource = Resource.create(
        {
            SERVICE_NAME:    settings.otel_service_name,
            SERVICE_VERSION: settings.app_version,
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)

    if settings.otel_enabled:
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=True,          # TLS handled at the infrastructure layer
        )
        # BatchSpanProcessor is non-blocking; safe for production throughput.
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    else:
        # Development: emit spans to stdout so engineers can inspect locally
        # without running a collector.
        if settings.debug:
            provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter())
            )

    trace.set_tracer_provider(provider)

    # W3C TraceContext + Baggage — compatible with Node.js gateway propagation
    set_global_textmap(
        CompositePropagator(
            [TraceContextTextMapPropagator(), W3CBaggagePropagator()]
        )
    )


# ── Logging internals ─────────────────────────────────────────────────────────

def _configure_logging(settings: Settings) -> None:
    """
    Configure structlog with:
      • JSON renderer in production / staging
      • Coloured console renderer in development
      • stdlib integration so third-party ``logging.*`` calls are captured
      • Automatic trace_id / span_id injection from the active OTEL span
    """
    log_level = getattr(logging, settings.log_level, logging.INFO)

    # ── stdlib root logger ────────────────────────────────────────────────────
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )
    # Silence noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "grpc", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # ── Shared processors (run for every log event) ───────────────────────────
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,        # request-scoped context
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _OtelTraceInjector(),                           # inject trace_id, span_id
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    is_prod = settings.environment in ("staging", "production")
    if is_prod:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            # Prepare for stdlib integration
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Attach structlog formatter to every stdlib handler
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    for handler in logging.root.handlers:
        handler.setFormatter(formatter)


# ── OTEL trace context processor ─────────────────────────────────────────────

class _OtelTraceInjector:
    """
    structlog processor that reads the active OpenTelemetry span and injects
    ``trace_id`` and ``span_id`` as hex strings into every log record.

    This makes log-to-trace correlation trivial in Grafana / Jaeger:
    filter logs by ``trace_id`` to see the full distributed trace, or click
    the trace link to jump directly from a log line to a waterfall.
    """

    def __call__(
        self,
        logger: Any,
        method: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        span = trace.get_current_span()
        ctx  = span.get_span_context()

        if ctx and ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"]  = format(ctx.span_id,  "016x")
            event_dict["trace_sampled"] = ctx.trace_flags.sampled
        return event_dict


# ── Span helper ───────────────────────────────────────────────────────────────

class span:
    """
    Thin context-manager / decorator wrapper around ``tracer.start_as_current_span``.

    Usage (context manager)::

        with span("retrieval.dense_search", domain=domain.value):
            results = await qdrant.search(...)

    Usage (decorator)::

        @span.decorator("embedder.encode")
        async def encode(self, text: str) -> list[float]:
            ...
    """

    def __init__(self, name: str, **attributes: Any) -> None:
        self._name       = name
        self._attributes = attributes
        self._cm         = None

    def __enter__(self) -> trace.Span:
        self._cm = _tracer.start_as_current_span(
            self._name,
            attributes={k: str(v) for k, v in self._attributes.items()},
        )
        return self._cm.__enter__()

    def __exit__(self, *args: Any) -> bool | None:
        return self._cm.__exit__(*args)

    @staticmethod
    def decorator(name: str, **fixed_attrs: Any):
        """Decorator that wraps an async function in a named span."""
        import functools

        def _decorator(fn):
            @functools.wraps(fn)
            async def _wrapper(*args, **kwargs):
                with span(name, **fixed_attrs):
                    return await fn(*args, **kwargs)
            return _wrapper
        return _decorator


# ── Request-scoped context helpers ────────────────────────────────────────────

def bind_request_context(
    *,
    request_id: str,
    domain: str,
    user_id: str | None = None,
) -> None:
    """
    Bind per-request fields to structlog's context-var store.
    Call this at the top of each route handler so all downstream log lines
    carry request_id and domain automatically.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        domain=domain,
        **({"user_id": user_id} if user_id else {}),
    )


def clear_request_context() -> None:
    """Clear per-request context vars (call in a finally block or middleware)."""
    structlog.contextvars.clear_contextvars()
