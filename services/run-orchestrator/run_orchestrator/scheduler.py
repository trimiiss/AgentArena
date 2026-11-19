"""Scheduler process: the single place that moves runs through their lifecycle.

It relays the outbox, reacts to execution and evaluation events, keeps each run
within its concurrency limit, and periodically recovers work that was lost: a
dead worker, a job that never arrived, or an evaluation that never came back.
Every step uses row locks with SKIP LOCKED, so several schedulers can run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentarena_common.db import Database, utcnow
from agentarena_common.events import EvaluationCompleted, EventEnvelope, ExecutionStarted
from agentarena_common.idempotency import first_delivery
from agentarena_common.logging import configure_logging
from agentarena_common.messaging import ALL_QUEUES, SCHEDULER_QUEUE, MessageBus
from agentarena_common.ops import serve_ops
from agentarena_common.outbox import OutboxRelay
from agentarena_common.telemetry import setup_tracing, shutdown_tracing
from run_orchestrator.config import Settings
from run_orchestrator.lifecycle import dispatch, fail_execution, maybe_finish
from run_orchestrator.metrics import EXECUTION_RETRIES, LEASES_RECOVERED
from run_orchestrator.models import Execution, Outbox, ProcessedMessage, Run

logger = logging.getLogger(__name__)

CONSUMER = "scheduler"


class Scheduler:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.service = settings.service_name
        self._ticks = 0

    async def handle(self, envelope: EventEnvelope, _: Any) -> None:
        payload = envelope.payload()
        if isinstance(payload, ExecutionStarted):
            await self._record_start(envelope, payload)
            return
        async with self.database.sessions() as session, session.begin():
            if not await first_delivery(session, ProcessedMessage, CONSUMER, envelope.event_id):
                return
            run = await self._lock_run(session, payload.run_id)  # type: ignore[attr-defined]
            if run is None:
                logger.warning("event for unknown run", extra={"event_type": envelope.event_type})
                return
            if isinstance(payload, EvaluationCompleted):
                execution = await session.get(Execution, payload.execution_id)
                if execution is not None:
                    execution.evaluated_at = utcnow()
                    execution.passed = payload.passed
                    execution.score = payload.score
            await dispatch(session, run, producer=self.service)
            await maybe_finish(session, run, producer=self.service)

    async def _record_start(self, envelope: EventEnvelope, payload: ExecutionStarted) -> None:
        """Idempotent by nature and takes no long lock, so the hot path stays cheap."""
        async with self.database.sessions() as session, session.begin():
            await session.execute(
                update(Run)
                .where(Run.id == payload.run_id, Run.started_at.is_(None))
                .values(started_at=envelope.occurred_at)
            )

    @staticmethod
    async def _lock_run(session: AsyncSession, run_id: UUID) -> Run | None:
        return await session.scalar(select(Run).where(Run.id == run_id).with_for_update())

    async def tick(self) -> None:
        await self._recover_expired_leases()
        await self._recover_lost_dispatches()
        await self._dispatch_due_retries()
        self._ticks += 1
        if self._ticks % self.settings.sweep_every_ticks == 0:
            await self._advance_open_runs()

    async def _dispatch_due_retries(self) -> None:
        """Retries have no event to wake them, so they are found by time."""
        async with self.database.sessions() as session, session.begin():
            run_ids = (
                await session.scalars(
                    select(Execution.run_id)
                    .where(Execution.status == "retry_wait", Execution.next_attempt_at <= utcnow())
                    .distinct()
                    .limit(100)
                )
            ).all()
            if not run_ids:
                return
            runs = (
                await session.scalars(select(Run).where(Run.id.in_(run_ids)).with_for_update(skip_locked=True))
            ).all()
            for run in runs:
                await dispatch(session, run, producer=self.service)
                await maybe_finish(session, run, producer=self.service)

    async def _recover_expired_leases(self) -> None:
        """A running execution with an old lease means its worker is gone."""
        now = utcnow()
        async with self.database.sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(Execution)
                    .where(Execution.status == "running", Execution.lease_expires_at < now)
                    .limit(200)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for execution in rows:
                await self._recover(session, execution, reason="worker_lost")

    async def _recover_lost_dispatches(self) -> None:
        """A dispatched job nobody claimed in time was lost or dead-lettered."""
        cutoff = utcnow() - timedelta(seconds=self.settings.dispatch_timeout_seconds)
        async with self.database.sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(Execution)
                    .where(Execution.status == "dispatched", Execution.dispatched_at < cutoff)
                    .limit(200)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for execution in rows:
                await self._recover(session, execution, reason="dispatch_lost")

    async def _recover(self, session: AsyncSession, execution: Execution, *, reason: str) -> None:
        run = await session.get(Run, execution.run_id)
        assert run is not None
        LEASES_RECOVERED.labels(self.service, reason).inc()
        logger.warning(
            "recovering execution",
            extra={"execution_id": str(execution.id), "reason": reason, "attempt": execution.attempt},
        )
        if execution.attempt < execution.max_attempts:
            execution.status = "retry_wait"
            execution.next_attempt_at = utcnow()
            execution.lease_expires_at = None
            execution.worker_id = None
            EXECUTION_RETRIES.labels(self.service, reason).inc()
        else:
            await fail_execution(
                session,
                run,
                execution,
                status="failed",
                error_code=reason,
                error_message="Execution was lost and has no retries left",
                producer=self.service,
            )

    async def _advance_open_runs(self) -> None:
        """Safety net. Events normally do this work, so a lost event only costs a tick."""
        now = utcnow()
        async with self.database.sessions() as session, session.begin():
            runs = (
                await session.scalars(
                    select(Run)
                    .where(Run.status.not_in(("completed", "failed", "cancelled")))
                    # Oldest touched first, so a large backlog is swept in rotation.
                    .order_by(Run.updated_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for run in runs:
                await dispatch(session, run, producer=self.service)
                await maybe_finish(session, run, producer=self.service)
                timed_out = (
                    run.status == "evaluating"
                    and run.evaluating_since is not None
                    and (now - run.evaluating_since).total_seconds() > self.settings.evaluation_timeout_seconds
                )
                if timed_out:
                    await self._give_up_on_evaluations(session, run, now)

    async def _give_up_on_evaluations(self, session: AsyncSession, run: Run, now: Any) -> None:
        rows = (
            await session.scalars(
                select(Execution).where(
                    Execution.run_id == run.id,
                    Execution.evaluated_at.is_(None),
                    Execution.status != "cancelled",
                )
            )
        ).all()
        for execution in rows:
            execution.evaluated_at = now
        run.warning = f"{len(rows)} evaluations did not complete in time"
        logger.error("evaluations timed out", extra={"run_id": str(run.id), "missing": len(rows)})
        await maybe_finish(session, run, producer=self.service)

    async def tick_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive database outages
                logger.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.tick_seconds)
            except TimeoutError:
                pass


async def amain() -> None:
    settings = Settings()
    settings.service_name = os.environ.get("SERVICE_NAME", "run-orchestrator-scheduler")
    configure_logging(settings.service_name, settings.log_level)
    setup_tracing(settings)

    database = Database(settings)
    bus = MessageBus(settings.rabbitmq_url, settings.service_name)
    scheduler = Scheduler(settings, database)

    async def database_ready(_: FastAPI) -> None:
        await database.ping()

    async def broker_ready(_: FastAPI) -> None:
        if not bus.is_connected:
            raise ConnectionError("broker disconnected")

    server, server_task = await serve_ops(
        settings.service_name, settings.scheduler_ops_port, (database_ready, broker_ready)
    )

    await bus.connect()
    for spec in ALL_QUEUES:
        await bus.declare(spec)
    await bus.consume(SCHEDULER_QUEUE, scheduler.handle, prefetch=32)

    relay = OutboxRelay(database, bus, Outbox, service=settings.service_name)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except (NotImplementedError, AttributeError):
            pass  # Windows development only.

    tasks = [asyncio.create_task(relay.run()), asyncio.create_task(scheduler.tick_loop(stop))]
    logger.info("scheduler started")
    await stop.wait()

    relay.stop()
    await asyncio.gather(*tasks, return_exceptions=True)
    await bus.close()
    await database.dispose()
    server.should_exit = True
    await server_task
    shutdown_tracing()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
