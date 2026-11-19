"""Execution worker process.

Consumes execution.requested, claims the row, runs the scenario and records the
result. Scaling workers needs no coordination beyond the database claim.

Crash safety: the message is acked only after the result commits, and a worker
keeps its claim alive with a heartbeat. If a worker dies, RabbitMQ redelivers
the message, which is dropped as a duplicate, and the scheduler's reaper retries
the execution once the lease expires. A worker that loses its lease discards
its result, so an execution can never record two outcomes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update

from agentarena_common.app import ReadinessCheck
from agentarena_common.cache import AsyncTTLCache
from agentarena_common.context import correlation_id_var
from agentarena_common.db import Database, utcnow
from agentarena_common.events import (
    EventEnvelope,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionRequested,
    ExecutionStarted,
    make_event,
)
from agentarena_common.idempotency import first_delivery
from agentarena_common.logging import configure_logging
from agentarena_common.messaging import JOBS_QUEUE, MessageBus
from agentarena_common.ops import serve_ops
from agentarena_common.outbox import enqueue
from agentarena_common.resilience import CircuitOpenError, DownstreamError, backoff_delay
from agentarena_common.telemetry import current_trace_id, get_tracer, setup_tracing, shutdown_tracing
from run_orchestrator.clients import Dependencies, NotFoundError
from run_orchestrator.config import Settings
from run_orchestrator.engine import AgentFailure, Outcome, ScenarioRunner
from run_orchestrator.lifecycle import evaluation_request
from run_orchestrator.metrics import (
    CONFIG_CACHE,
    EXECUTION_DURATION,
    EXECUTION_RETRIES,
    EXECUTIONS,
    QUEUE_WAIT,
    WORKER_BUSY,
    WORKER_CAPACITY,
)
from run_orchestrator.models import Execution, ExecutionStep, Outbox, ProcessedMessage, Run

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

CONSUMER = "execution-worker"


class Worker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        dependencies: Dependencies,
        http_client: httpx.AsyncClient,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.dependencies = dependencies
        self.runner = ScenarioRunner(settings, http_client)
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:6]}"
        self.service = settings.service_name
        self.busy = 0
        self.idle = asyncio.Event()
        self.idle.set()
        # Versions are immutable, so caching them is safe. Credentials can rotate,
        # which is why the TTL stays short.
        self.agent_cache: AsyncTTLCache[dict[str, Any]] = AsyncTTLCache(
            ttl_seconds=settings.config_cache_ttl_seconds, on_lookup=self._counter("agent")
        )
        self.scenario_cache: AsyncTTLCache[dict[str, Any]] = AsyncTTLCache(
            ttl_seconds=settings.config_cache_ttl_seconds, max_entries=4096, on_lookup=self._counter("scenario")
        )

    def _counter(self, kind: str) -> Callable[[bool], None]:
        def record(hit: bool) -> None:
            CONFIG_CACHE.labels(self.service, kind, "hit" if hit else "miss").inc()

        return record

    async def handle(self, envelope: EventEnvelope, _: Any) -> None:
        payload: ExecutionRequested = envelope.payload()  # type: ignore[assignment]
        claim = await self._claim(envelope, payload)
        if claim is None:
            return
        self.busy += 1
        self.idle.clear()
        WORKER_BUSY.labels(self.service).set(self.busy)
        try:
            await self._execute(envelope, payload, claim)
        finally:
            self.busy -= 1
            WORKER_BUSY.labels(self.service).set(self.busy)
            if self.busy == 0:
                self.idle.set()

    async def _claim(self, envelope: EventEnvelope, payload: ExecutionRequested) -> dict[str, Any] | None:
        """Take ownership in one transaction. Returns None when there is nothing to do."""
        async with self.database.sessions() as session, session.begin():
            if not await first_delivery(session, ProcessedMessage, CONSUMER, envelope.event_id):
                logger.info("duplicate job ignored", extra={"execution_id": str(payload.execution_id)})
                return None
            execution = await session.scalar(
                select(Execution).where(Execution.id == payload.execution_id).with_for_update()
            )
            if execution is None or execution.status != "dispatched" or execution.attempt != payload.attempt:
                logger.info("stale job ignored", extra={"execution_id": str(payload.execution_id)})
                return None
            run = await session.get(Run, execution.run_id)
            assert run is not None
            if run.status in ("cancelling", "cancelled"):
                execution.status = "cancelled"
                execution.finished_at = utcnow()
                return None

            now = utcnow()
            queue_wait_ms = max(0.0, (now - payload.dispatched_at).total_seconds() * 1000)
            execution.status = "running"
            execution.worker_id = self.worker_id
            execution.started_at = execution.started_at or now
            execution.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            execution.queue_wait_ms = queue_wait_ms
            execution.trace_id = current_trace_id()
            QUEUE_WAIT.labels(self.service).observe(queue_wait_ms / 1000)
            enqueue(
                session,
                Outbox,
                make_event(
                    "execution.started",
                    ExecutionStarted(
                        run_id=run.id,
                        execution_id=execution.id,
                        attempt=execution.attempt,
                        worker_id=self.worker_id,
                        queue_wait_ms=queue_wait_ms,
                    ),
                    producer=self.service,
                    org_id=run.org_id,
                    correlation_id=run.correlation_id,
                ),
            )
            return {
                "org_id": run.org_id,
                "run_id": run.id,
                "agent_id": run.agent_id,
                "agent_version": run.agent_version,
                "timeout_seconds": run.timeout_seconds,
                "scenario_id": execution.scenario_id,
                "scenario_version": execution.scenario_version,
                "seed": execution.seed,
                "attempt": execution.attempt,
            }

    async def _load_config(self, claim: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        org_id: UUID = claim["org_id"]

        async def load_agent() -> dict[str, Any]:
            return await self.dependencies.load_agent_config(org_id, claim["agent_id"], claim["agent_version"])

        async def load_scenario() -> dict[str, Any]:
            return await self.dependencies.load_scenario(org_id, claim["scenario_id"], claim["scenario_version"])

        agent, scenario = await asyncio.gather(
            self.agent_cache.get_or_load((org_id, claim["agent_id"], claim["agent_version"]), load_agent),
            self.scenario_cache.get_or_load((org_id, claim["scenario_id"], claim["scenario_version"]), load_scenario),
        )
        return agent, scenario

    async def _execute(self, envelope: EventEnvelope, payload: ExecutionRequested, claim: dict[str, Any]) -> None:
        started = time.perf_counter()
        scenario: dict[str, Any] | None = None
        with tracer.start_as_current_span(
            "execution.run",
            attributes={"execution.id": str(payload.execution_id), "execution.attempt": payload.attempt},
        ):
            work = asyncio.create_task(self._run(claim, payload))
            heartbeat = asyncio.create_task(self._heartbeat(payload, work))
            try:
                outcome, scenario = await work
            except asyncio.CancelledError:
                # The heartbeat cancelled us: the lease is gone or the run was
                # cancelled. Another worker owns the outcome now.
                logger.warning("execution abandoned", extra={"execution_id": str(payload.execution_id)})
                return
            finally:
                heartbeat.cancel()
            await self._finish(payload, claim, outcome, scenario)
        EXECUTION_DURATION.labels(self.service).observe(time.perf_counter() - started)
        EXECUTIONS.labels(self.service, outcome.status).inc()

    async def _run(self, claim: dict[str, Any], payload: ExecutionRequested) -> tuple[Outcome, dict[str, Any] | None]:
        try:
            agent, scenario = await self._load_config(claim)
        except NotFoundError as exc:
            return self._config_failure("config_not_found", str(exc), retryable=False), None
        except (DownstreamError, CircuitOpenError, httpx.HTTPError) as exc:
            return self._config_failure("dependency_unavailable", str(exc), retryable=True), None
        outcome = await self.runner.run(
            execution_id=payload.execution_id,
            run_id=payload.run_id,
            seed=claim["seed"],
            attempt=payload.attempt,
            agent=agent,
            scenario=scenario,
            timeout_seconds=claim["timeout_seconds"],
        )
        return outcome, scenario

    @staticmethod
    def _config_failure(code: str, message: str, *, retryable: bool) -> Outcome:
        outcome = Outcome()
        ScenarioRunner._fail(outcome, AgentFailure(code, message, retryable=retryable))
        return outcome

    async def _heartbeat(self, payload: ExecutionRequested, work: asyncio.Task) -> None:
        interval = self.settings.lease_seconds / 3
        while not work.done():
            await asyncio.sleep(interval)
            try:
                async with self.database.sessions() as session, session.begin():
                    result = await session.execute(
                        update(Execution)
                        .where(
                            Execution.id == payload.execution_id,
                            Execution.status == "running",
                            Execution.worker_id == self.worker_id,
                            Execution.attempt == payload.attempt,
                        )
                        .values(lease_expires_at=utcnow() + timedelta(seconds=self.settings.lease_seconds))
                    )
                    run_status = await session.scalar(select(Run.status).where(Run.id == payload.run_id))
                if result.rowcount == 0 or run_status in ("cancelling", "cancelled"):
                    work.cancel()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a missed beat is survivable, the lease has slack
                logger.warning("heartbeat failed", extra={"execution_id": str(payload.execution_id)})

    async def _finish(
        self,
        payload: ExecutionRequested,
        claim: dict[str, Any],
        outcome: Outcome,
        scenario: dict[str, Any] | None,
    ) -> None:
        async with self.database.sessions() as session, session.begin():
            execution = await session.scalar(
                select(Execution).where(Execution.id == payload.execution_id).with_for_update()
            )
            if (
                execution is None
                or execution.status != "running"
                or execution.worker_id != self.worker_id
                or execution.attempt != payload.attempt
            ):
                logger.warning("lease lost, discarding result", extra={"execution_id": str(payload.execution_id)})
                return
            run = await session.get(Run, execution.run_id)
            assert run is not None

            for step in outcome.steps:
                session.add(
                    ExecutionStep(
                        execution_id=execution.id,
                        attempt=execution.attempt,
                        seq=step.seq,
                        type=step.type,
                        payload=step.model_dump(mode="json"),
                        latency_ms=step.latency_ms,
                    )
                )
            execution.lease_expires_at = None
            execution.latency_ms = round(outcome.latency_ms, 2)
            execution.agent_calls = outcome.agent_calls
            execution.tool_calls = outcome.tool_calls
            execution.input_tokens = outcome.input_tokens
            execution.output_tokens = outcome.output_tokens
            execution.cost_usd = outcome.cost_usd
            execution.error_code = outcome.error_code
            execution.error_message = outcome.error_message

            common = {
                "producer": self.service,
                "org_id": run.org_id,
                "correlation_id": run.correlation_id,
            }
            if outcome.status != "completed" and outcome.retryable and execution.attempt < execution.max_attempts:
                delay = backoff_delay(
                    execution.attempt, self.settings.retry_base_seconds, self.settings.retry_max_seconds
                )
                execution.status = "retry_wait"
                execution.next_attempt_at = utcnow() + timedelta(seconds=delay)
                EXECUTION_RETRIES.labels(self.service, outcome.error_code or "unknown").inc()
                enqueue(
                    session,
                    Outbox,
                    make_event(
                        "execution.failed",
                        ExecutionFailed(
                            run_id=run.id,
                            execution_id=execution.id,
                            attempt=execution.attempt,
                            status=outcome.status,
                            error_code=outcome.error_code or "unknown",
                            error_message=outcome.error_message or "",
                            will_retry=True,
                        ),
                        **common,
                    ),
                )
                return

            execution.status = outcome.status
            execution.finished_at = utcnow()
            if outcome.status == "completed":
                event = make_event(
                    "execution.completed",
                    ExecutionCompleted(
                        run_id=run.id,
                        execution_id=execution.id,
                        attempt=execution.attempt,
                        latency_ms=outcome.latency_ms,
                        agent_calls=outcome.agent_calls,
                        tool_calls=outcome.tool_calls,
                    ),
                    **common,
                )
            else:
                event = make_event(
                    "execution.failed",
                    ExecutionFailed(
                        run_id=run.id,
                        execution_id=execution.id,
                        attempt=execution.attempt,
                        status=outcome.status,
                        error_code=outcome.error_code or "unknown",
                        error_message=outcome.error_message or "",
                    ),
                    **common,
                )
            enqueue(session, Outbox, event)
            enqueue(
                session,
                Outbox,
                make_event(
                    "evaluation.requested",
                    evaluation_request(run, execution, scenario=scenario, transcript=outcome.steps),
                    **common,
                ),
            )


async def amain() -> None:
    settings = Settings()
    settings.service_name = os.environ.get("SERVICE_NAME", "run-orchestrator-worker")
    configure_logging(settings.service_name, settings.log_level)
    setup_tracing(settings)

    database = Database(settings)
    dependencies = Dependencies(settings)
    http_client = httpx.AsyncClient(limits=httpx.Limits(max_connections=200, max_keepalive_connections=50))
    worker = Worker(settings, database, dependencies, http_client)
    bus = MessageBus(settings.rabbitmq_url, settings.service_name)

    async def database_ready(_: FastAPI) -> None:
        await database.ping()

    async def broker_ready(_: FastAPI) -> None:
        if not bus.is_connected:
            raise ConnectionError("broker disconnected")

    checks: tuple[ReadinessCheck, ...] = (database_ready, broker_ready)
    server, server_task = await serve_ops(settings.service_name, settings.worker_ops_port, checks)

    await bus.connect()
    await bus.consume(JOBS_QUEUE, worker.handle, prefetch=settings.worker_concurrency)
    WORKER_CAPACITY.labels(settings.service_name).set(settings.worker_concurrency)
    logger.info("worker started", extra={"worker_id": worker.worker_id, "concurrency": settings.worker_concurrency})

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except (NotImplementedError, AttributeError):
            pass  # Windows development only.
    await stop.wait()

    logger.info("draining", extra={"in_flight": worker.busy})
    await bus.stop_consuming()
    try:
        await asyncio.wait_for(worker.idle.wait(), timeout=settings.shutdown_grace_seconds)
    except TimeoutError:
        logger.warning("drain timed out, leases will expire", extra={"in_flight": worker.busy})
    await bus.close()
    await http_client.aclose()
    await dependencies.aclose()
    await database.dispose()
    server.should_exit = True
    await server_task
    shutdown_tracing()


def main() -> None:
    correlation_id_var.set(None)
    asyncio.run(amain())


if __name__ == "__main__":
    main()
