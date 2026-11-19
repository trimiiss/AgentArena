"""OpenTelemetry setup and context propagation helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy.ext.asyncio import AsyncEngine

from agentarena_common.settings import CommonSettings

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None
_httpx_instrumented = False


def setup_tracing(settings: CommonSettings, process_name: str | None = None) -> None:
    """Configure the global tracer provider. Safe to call once per process."""
    global _provider, _httpx_instrumented
    if not settings.otel_enabled or _provider is not None:
        return
    resource = Resource.create(
        {
            "service.name": process_name or settings.service_name,
            "service.namespace": "agentarena",
            "deployment.environment": settings.environment,
        }
    )
    _provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)))
    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(_provider)
    if not _httpx_instrumented:
        HTTPXClientInstrumentor().instrument()
        _httpx_instrumented = True
    logger.info("tracing enabled", extra={"otlp_endpoint": settings.otel_exporter_otlp_endpoint})


def instrument_fastapi(app: FastAPI) -> None:
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics")


def instrument_engine(engine: AsyncEngine) -> None:
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def shutdown_tracing() -> None:
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def inject_context(carrier: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the current trace context into a carrier such as AMQP headers."""
    carrier = carrier if carrier is not None else {}
    propagate.inject(carrier)
    return carrier


def extract_context(carrier: Mapping[str, Any] | None) -> otel_context.Context:
    """Read trace context from message headers. Non-string values are ignored."""
    clean = {k: v.decode() if isinstance(v, bytes) else v for k, v in (carrier or {}).items()}
    return propagate.extract({k: v for k, v in clean.items() if isinstance(v, str)})


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    return format(span_context.trace_id, "032x") if span_context.is_valid else None
