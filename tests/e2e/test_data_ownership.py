"""Boundaries that must hold below the application."""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import httpx
import pytest
from conftest import BASE_URL

GATEWAY = BASE_URL or "http://127.0.0.1:8080"

ROLES = {
    "identity_svc": ("identity_db", "IDENTITY_DB_PASSWORD", "local-dev-identity"),
    "agents_svc": ("agents_db", "AGENTS_DB_PASSWORD", "local-dev-agents"),
    "scenarios_svc": ("scenarios_db", "SCENARIOS_DB_PASSWORD", "local-dev-scenarios"),
    "runs_svc": ("runs_db", "RUNS_DB_PASSWORD", "local-dev-runs"),
    "evaluations_svc": ("evaluations_db", "EVALUATIONS_DB_PASSWORD", "local-dev-evaluations"),
}


def connect(role: str, database: str) -> None:
    _, variable, default = ROLES[role]

    async def attempt() -> None:
        connection = await asyncpg.connect(
            host="127.0.0.1",
            user=role,
            password=os.environ.get(variable, default),
            database=database,
            timeout=5,
        )
        await connection.close()

    asyncio.run(attempt())


needs_compose = pytest.mark.skipif(bool(BASE_URL), reason="The database port is only published on Compose")


@needs_compose
@pytest.mark.parametrize("role", ROLES)
def test_each_role_reaches_its_own_database(role: str) -> None:
    connect(role, ROLES[role][0])


@needs_compose
@pytest.mark.parametrize("role", ROLES)
def test_no_role_reaches_another_services_database(role: str) -> None:
    for other, (database, _, _) in ROLES.items():
        if other != role:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                connect(role, database)


@pytest.mark.parametrize(
    "path",
    [
        f"/internal/v1/agents/{uuid.uuid4()}/versions/1",
        f"/internal/v1/scenarios/{uuid.uuid4()}/versions/1",
    ],
)
def test_internal_routes_are_not_exposed_through_the_gateway(path: str) -> None:
    """Without a route they reach the web app, never a service."""
    response = httpx.get(
        f"{GATEWAY}{path}",
        headers={"X-Internal-Token": "guess", "X-Org-Id": str(uuid.uuid4())},
        timeout=10,
    )
    assert "application/json" not in response.headers.get("content-type", "")
    assert "application/problem+json" not in response.headers.get("content-type", "")
