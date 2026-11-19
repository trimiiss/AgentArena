"""FastAPI application factory with the operational surface every service needs."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentarena_common.auth import JwksCache
from agentarena_common.context import CORRELATION_HEADER, correlation_id_var, new_correlation_id
from agentarena_common.db import Database
from agentarena_common.logging import configure_logging
from agentarena_common.metrics import HTTP_IN_PROGRESS, HTTP_LATENCY, HTTP_REQUESTS
from agentarena_common.ratelimit import TokenBucketLimiter
from agentarena_common.settings import CommonSettings
from agentarena_common.telemetry import instrument_engine, instrument_fastapi, setup_tracing, shutdown_tracing

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[FastAPI], Awaitable[None]]
LifecycleHook = Callable[[FastAPI], Awaitable[None]]

_UNTRACKED_PATHS = {"/health", "/ready", "/metrics"}
_MAX_CORRELATION_LENGTH = 64


class ObservabilityMiddleware:
    """Pure ASGI middleware, so streaming responses and contextvars keep working."""

    def __init__(self, app: ASGIApp, service: str) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        incoming = headers.get(CORRELATION_HEADER.lower(), "")
        valid = 0 < len(incoming) <= _MAX_CORRELATION_LENGTH and incoming.replace("-", "").isalnum()
        correlation_id = incoming if valid else new_correlation_id()
        token = correlation_id_var.set(correlation_id)

        status_code = 500
        started = time.perf_counter()
        path = scope.get("path", "")
        tracked = path not in _UNTRACKED_PATHS
        if tracked:
            HTTP_IN_PROGRESS.labels(self.service).inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers[CORRELATION_HEADER] = correlation_id
                response_headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            correlation_id_var.reset(token)
            if tracked:
                HTTP_IN_PROGRESS.labels(self.service).dec()
                route = scope.get("route")
                template = getattr(route, "path", None) or "unmatched"
                method = scope.get("method", "GET")
                elapsed = time.perf_counter() - started
                HTTP_REQUESTS.labels(self.service, method, template, str(status_code)).inc()
                HTTP_LATENCY.labels(self.service, method, template).observe(elapsed)


def _problem(status_code: int, title: str, detail: Any, headers: dict[str, str] | None = None) -> JSONResponse:
    """Errors use the RFC 9457 problem shape, plus the id needed to find the logs."""
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "detail": detail,
            "correlation_id": correlation_id_var.get(),
        },
        media_type="application/problem+json",
        headers=headers,
    )


def add_operational_routes(app: FastAPI, service: str, checks: Sequence[ReadinessCheck]) -> None:
    @app.get("/health", tags=["operations"], summary="Liveness check")
    async def health() -> dict[str, str]:
        """Liveness: the process is running. Dependencies are not checked here,
        otherwise a database outage would restart every pod."""
        return {"status": "ok", "service": service}

    @app.get("/ready", tags=["operations"], summary="Readiness check")
    async def ready(response: Response) -> dict[str, Any]:
        """Readiness: this replica can do useful work right now."""
        failures: list[str] = []
        for check in checks:
            try:
                await check(app)
            except Exception as exc:  # noqa: BLE001 - any failure means not ready
                failures.append(f"{getattr(check, '__name__', 'check')}: {type(exc).__name__}")
        if failures:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable", "service": service, "failures": failures}
        return {"status": "ready", "service": service}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def check_database(app: FastAPI) -> None:
    await app.state.database.ping()


def create_service_app(
    settings: CommonSettings,
    *,
    title: str,
    description: str,
    version: str = "1.0.0",
    on_startup: LifecycleHook | None = None,
    on_shutdown: LifecycleHook | None = None,
    readiness_checks: Sequence[ReadinessCheck] = (check_database,),
) -> FastAPI:
    configure_logging(settings.service_name, settings.log_level)
    setup_tracing(settings)

    database = Database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if on_startup is not None:
            await on_startup(app)
        logger.info("service started", extra={"environment": settings.environment})
        yield
        if on_shutdown is not None:
            await on_shutdown(app)
        await database.dispose()
        shutdown_tracing()

    app = FastAPI(title=title, description=description, version=version, lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.jwks = JwksCache(settings.jwks_url)
    app.state.write_limiter = TokenBucketLimiter(settings.rate_limit_per_second, settings.rate_limit_burst)

    if settings.otel_enabled:
        instrument_fastapi(app)
        instrument_engine(database.engine)

    app.add_middleware(ObservabilityMiddleware, service=settings.service_name)
    add_operational_routes(app, settings.service_name, readiness_checks)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return _problem(exc.status_code, str(exc.detail), exc.detail, dict(exc.headers or {}))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [{"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return _problem(status.HTTP_422_UNPROCESSABLE_ENTITY, "Validation failed", detail)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"path": request.url.path}, exc_info=exc)
        return _problem(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error", "Internal server error")

    return app
