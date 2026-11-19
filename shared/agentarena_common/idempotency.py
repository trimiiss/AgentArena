"""Idempotent consumption: record each event id inside the handler's transaction."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession


async def first_delivery(session: AsyncSession, model: type[Any], consumer: str, event_id: UUID) -> bool:
    """Return True the first time this consumer sees this event.

    The insert shares the transaction with the handler's writes, so the
    deduplication record and the effect commit or roll back together.
    """
    statement = (
        insert(model)
        .values(consumer=consumer, event_id=event_id)
        .on_conflict_do_nothing(index_elements=["consumer", "event_id"])
    )
    result = cast(CursorResult, await session.execute(statement))
    return bool(result.rowcount)
