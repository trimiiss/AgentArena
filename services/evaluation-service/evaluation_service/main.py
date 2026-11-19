"""Evaluation Service API. Exposes /health, /ready, /metrics and the results API."""

from __future__ import annotations

from agentarena_common.app import create_service_app
from evaluation_service.api import router
from evaluation_service.config import Settings

settings = Settings()

app = create_service_app(
    settings,
    title="AgentArena Evaluation Service",
    description="Evaluation results, benchmarks, version comparisons, analytics and reports.",
)
app.include_router(router)
