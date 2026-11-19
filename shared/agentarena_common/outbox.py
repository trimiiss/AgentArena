"""Transactional outbox: write events with the state change, publish afterwards.

``enqueue`` adds an event to the caller's open transaction. If the transaction
rolls back, the event never existed. ``OutboxRelay`` then publishes committed
rows and waits for broker confirms. A crash after the confirm but before the
row is marked published sends the event again, which is why consumers must be
idempotent: the guarantee is at-least-once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from typing import Any

from opentelemetry.trace import SpanKind
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentarena_common.db import Database, utcnow
from agentarena_common.events import EventEnvelope
from agentarena_common.messaging import MessageBus
from agentarena_common.metrics import OUTBOX_LAG, OUTBOX_PENDING
from agentarena_common.telemetry import extract_context, get_tracer, inject_context

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)


def enqueue(session: AsyncSession, outbox_model: type[Any], event: EventEnvelope) -> None:
    """Add an event to the current transaction, keeping the caller's trace."""
    session.add(
        outbox_model(
            event_id=event.event_id,
            routing_key=event.event_type,
            payload=event.model_dump(mode="json"),
            trace_headers=inject_context(),
        )
    )


class OutboxRelay:
    def __init__(
        self,
        database: Database,
        bus: MessageBus,
        outbox_model: type[Any],
        *,
        service: str,
        batch_size: int = 100,
        poll_seconds: float = 0.2,
        retention: timedelta = timedelta(days=7),
    ) -> None:
        self.database = database
        self.bus = bus
        self.model = outbox_model
        self.service = service
        self.batch_size = batch_size
        self.poll_seconds = poll_seconds
        self.retention = retention
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        last_cleanup = 0.0
        while not self._stopping.is_set():
            try:
                published = await self.publish_batch()
                now = time.monotonic()
                if now - last_cleanup > 3600:
                    await self._cleanup()
                    last_cleanup = now
                await self._record_backlog()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the relay must outlive broker and database outages
                logger.exception("outbox relay iteration failed")
                published = 0
                await asyncio.sleep(1)
            if published < self.batch_size:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass

    async def publish_batch(self) -> int:
        """Publish one batch. SKIP LOCKED lets several relays run side by side.

        Rows go out concurrently, because each publish waits for a broker confirm.
        Awaiting those one at a time is what capped this relay in docs/loadtest.md.
        Confirmed rows are committed even when others fail, so a partial failure
        only resends what failed. Nothing relies on order within a batch: delivery
        is at-least-once, and consumers read state from the database.
        """
        async with self.database.sessions() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(self.model)
                        .where(self.model.published_at.is_(None))
                        .order_by(self.model.id)
                        .limit(self.batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            outcomes = await asyncio.gather(*(self._publish_row(row) for row in rows), return_exceptions=True)
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if failures:
            raise failures[0]
        return len(rows)

    async def _publish_row(self, row: Any) -> None:
        with tracer.start_as_current_span(
            f"{row.routing_key} publish",
            context=extract_context(row.trace_headers),
            kind=SpanKind.PRODUCER,
            attributes={
                "messaging.system": "rabbitmq",
                "messaging.destination.name": row.routing_key,
                "messaging.message.id": str(row.event_id),
                "messaging.operation.type": "publish",
            },
        ):
            headers = inject_context({"event_version": row.payload.get("event_version", 1)})
            correlation_id = row.payload.get("correlation_id")
            if correlation_id:
                headers["correlation_id"] = correlation_id
            await self.bus.publish_raw(
                row.routing_key,
                json.dumps(row.payload, separators=(",", ":")).encode(),
                headers,
                str(row.event_id),
            )
        row.published_at = utcnow()
        row.attempts += 1
        OUTBOX_LAG.labels(self.service).observe(max(0.0, (row.published_at - row.created_at).total_seconds()))

    async def _record_backlog(self) -> None:
        async with self.database.sessions() as session:
            pending = await session.scalar(
                select(func.count()).select_from(self.model).where(self.model.published_at.is_(None))
            )
        OUTBOX_PENDING.labels(self.service).set(pending or 0)

    async def _cleanup(self) -> None:
        cutoff = utcnow() - self.retention
        async with self.database.sessions() as session, session.begin():
            await session.execute(
                delete(self.model).where(self.model.published_at.is_not(None), self.model.published_at < cutoff)
            )
