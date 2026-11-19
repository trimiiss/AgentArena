"""Scenario Service. Exposes /health, /ready, /metrics and the scenarios API."""

from __future__ import annotations

from agentarena_common.app import create_service_app
from scenario_service.api import router
from scenario_service.config import Settings

settings = Settings()

app = create_service_app(
    settings,
    title="AgentArena Scenario Service",
    description="Versioned test scenarios, test suites, datasets and scenario variations.",
)
app.include_router(router)
