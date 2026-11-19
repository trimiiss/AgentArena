"""A small HTTP server for processes without an API, such as workers.

Workers still need /health, /ready and /metrics for Kubernetes and Prometheus.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Generator, Sequence

import uvicorn
from fastapi import FastAPI

from agentarena_common.app import ReadinessCheck, add_operational_routes


class _EmbeddedServer(uvicorn.Server):
    """The process owns SIGTERM so it can drain work, so uvicorn must leave signals alone."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


async def serve_ops(
    service: str,
    port: int,
    checks: Sequence[ReadinessCheck] = (),
    state: dict | None = None,
) -> tuple[uvicorn.Server, asyncio.Task]:
    app = FastAPI(title=f"{service} ops", docs_url=None, redoc_url=None, openapi_url=None)
    for key, value in (state or {}).items():
        setattr(app.state, key, value)
    add_operational_routes(app, service, checks)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_config=None, access_log=False)
    server = _EmbeddedServer(config)
    return server, asyncio.create_task(server.serve())
