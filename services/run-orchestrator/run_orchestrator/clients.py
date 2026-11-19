"""Synchronous calls to the Agent Registry and Scenario Service.

The API forwards the caller's token, so those services enforce organization
isolation themselves. Workers have no user token and use the internal routes.
Each call has a timeout, read retries with jitter, and a circuit breaker.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from agentarena_common.resilience import ServiceClient
from run_orchestrator.config import Settings


class NotFoundError(Exception):
    """The referenced resource does not exist for this organization."""


class Dependencies:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.registry = ServiceClient("agent-registry", settings.agent_registry_url, settings, transport)
        self.scenarios = ServiceClient("scenario-service", settings.scenario_service_url, settings, transport)

    async def aclose(self) -> None:
        await self.registry.aclose()
        await self.scenarios.aclose()

    @staticmethod
    def _unwrap(response: httpx.Response, what: str) -> Any:
        if response.status_code == 404:
            raise NotFoundError(f"{what} not found")
        response.raise_for_status()
        return response.json()

    async def get_agent(self, agent_id: UUID, headers: dict[str, str]) -> dict[str, Any]:
        return self._unwrap(await self.registry.get(f"/v1/agents/{agent_id}", headers=headers), "Agent")

    async def get_agent_version(self, agent_id: UUID, version: int, headers: dict[str, str]) -> dict[str, Any]:
        return self._unwrap(
            await self.registry.get(f"/v1/agents/{agent_id}/versions/{version}", headers=headers), "Agent version"
        )

    async def resolve_suite(self, suite_id: UUID, headers: dict[str, str]) -> list[dict[str, Any]]:
        body = self._unwrap(await self.scenarios.get(f"/v1/suites/{suite_id}/resolve", headers=headers), "Suite")
        return body["items"]

    async def resolve_scenarios(self, scenario_ids: list[UUID], headers: dict[str, str]) -> list[dict[str, Any]]:
        params = [("ids", str(scenario_id)) for scenario_id in scenario_ids]
        response = await self.scenarios.get("/v1/scenarios/resolve", headers=headers, params=params)
        return self._unwrap(response, "Scenario")["items"]

    def _internal_headers(self, org_id: UUID) -> dict[str, str]:
        return {"X-Internal-Token": self.settings.internal_service_token, "X-Org-Id": str(org_id)}

    async def load_agent_config(self, org_id: UUID, agent_id: UUID, version: int) -> dict[str, Any]:
        path = f"/internal/v1/agents/{agent_id}/versions/{version}"
        return self._unwrap(await self.registry.get(path, headers=self._internal_headers(org_id)), "Agent version")

    async def load_scenario(self, org_id: UUID, scenario_id: UUID, version: int) -> dict[str, Any]:
        path = f"/internal/v1/scenarios/{scenario_id}/versions/{version}"
        return self._unwrap(await self.scenarios.get(path, headers=self._internal_headers(org_id)), "Scenario version")
