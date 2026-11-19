"""Integration tests run against real PostgreSQL and RabbitMQ.

Each test gets a throwaway database and uniquely named queues, so a running
stack on the same machine is left alone. Start the dependencies with:

    docker compose up -d --wait postgres rabbitmq
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine

from agentarena_common.db import Database
from agentarena_common.messaging import MessageBus
from agentarena_common.settings import CommonSettings

POSTGRES_HOST = os.environ.get("TEST_POSTGRES_HOST", "127.0.0.1")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "local-dev-postgres")
RABBITMQ_URL = os.environ.get("TEST_RABBITMQ_URL", "amqp://agentarena:local-dev-rabbitmq@127.0.0.1:5672/agentarena")


async def _admin() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=POSTGRES_HOST, user="postgres", password=POSTGRES_PASSWORD, database="postgres", timeout=3
    )


@pytest.fixture(scope="session", autouse=True)
def dependencies_available() -> None:
    async def probe() -> None:
        await (await _admin()).close()

    try:
        asyncio.run(probe())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL is not available: {exc}")


async def _make_database(metadata: MetaData) -> tuple[str, Database]:
    name = f"aa_test_{uuid.uuid4().hex[:12]}"
    admin = await _admin()
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = f"postgresql+asyncpg://postgres:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:5432/{name}"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    await engine.dispose()
    return name, Database(CommonSettings(database_url=url, db_pool_size=20, db_max_overflow=20))


async def _drop_database(name: str, database: Database) -> None:
    await database.dispose()
    admin = await _admin()
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()


@pytest.fixture()
async def runs_database() -> AsyncIterator[Database]:
    from run_orchestrator.models import Base

    name, database = await _make_database(Base.metadata)
    yield database
    await _drop_database(name, database)


@pytest.fixture()
async def evaluations_database() -> AsyncIterator[Database]:
    from evaluation_service.models import Base

    name, database = await _make_database(Base.metadata)
    yield database
    await _drop_database(name, database)


@pytest.fixture()
async def bus() -> AsyncIterator[MessageBus]:
    client = MessageBus(RABBITMQ_URL, "integration-test")
    await client.connect(attempts=3)
    yield client
    await client.close()


@pytest.fixture()
def org_id() -> uuid.UUID:
    return uuid.uuid4()


def run_values(org: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "org_id": org,
        "created_by": uuid.uuid4(),
        "name": "test run",
        "agent_id": uuid.uuid4(),
        "agent_name": "agent",
        "agent_version": 1,
        "seed": 1,
        "concurrency": 2,
        "timeout_seconds": 5,
        "max_retries": 1,
        "repetitions": 1,
        "total_executions": 1,
        "correlation_id": "corr",
        **overrides,
    }


def execution_values(run: Any, repetition: int = 1, **overrides: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "run_id": run.id,
        "org_id": run.org_id,
        "scenario_id": uuid.uuid4(),
        "scenario_version": 1,
        "scenario_name": f"scenario {repetition}",
        "scenario_type": "tool_use",
        "difficulty": "easy",
        "repetition": repetition,
        "seed": 1,
        "max_attempts": 2,
        **overrides,
    }
