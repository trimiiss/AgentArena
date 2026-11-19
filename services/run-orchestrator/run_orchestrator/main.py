"""Run Orchestrator API. Exposes /health, /ready, /metrics and the runs API."""

from __future__ import annotations

from fastapi import FastAPI

from agentarena_common.app import create_service_app
from run_orchestrator.api import router
from run_orchestrator.clients import Dependencies
from run_orchestrator.config import Settings

settings = Settings()


async def startup(app: FastAPI) -> None:
    app.state.dependencies = Dependencies(settings)


async def shutdown(app: FastAPI) -> None:
    await app.state.dependencies.aclose()


app = create_service_app(
    settings,
    title="AgentArena Run Orchestrator",
    description="Accepts simulation runs and tracks their executions.",
    on_startup=startup,
    on_shutdown=shutdown,
)
app.include_router(router)
