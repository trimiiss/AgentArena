"""Shared Alembic environment runner for async engines.

Each service keeps its own migrations and version table in its own database.
A Postgres advisory lock makes concurrent starts safe: replicas wait, then find
nothing left to do.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import MetaData, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

_LOCK_ID = 7_236_451


def _run(connection: Connection, metadata: MetaData) -> None:
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _LOCK_ID})
    with context.begin_transaction():
        context.run_migrations()


async def _run_online(metadata: MetaData) -> None:
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ["DATABASE_URL"]
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run, metadata)
        await connection.commit()
    await engine.dispose()


def run_migrations(metadata: MetaData) -> None:
    if context.is_offline_mode():
        context.configure(
            url=os.environ["DATABASE_URL"], target_metadata=metadata, literal_binds=True, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        asyncio.run(_run_online(metadata))
