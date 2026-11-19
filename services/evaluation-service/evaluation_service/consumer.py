"""Evaluation consumer process.

Consumes evaluation.requested and run.completed, and relays this service's
outbox. Scoring and the evaluation.completed event commit in one transaction,
together with the deduplication record, so a redelivered message has no effect.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from typing import Any

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentarena_common.db import Database
from agentarena_common.events import (
    EvaluationCompleted,
    EvaluationRequested,
    EventEnvelope,
    RunCompleted,
    make_event,
)
from agentarena_common.idempotency import first_delivery
from agentarena_common.logging import configure_logging
from agentarena_common.messaging import EVALUATION_QUEUE, MessageBus
from agentarena_common.metrics import LATENCY_BUCKETS
from agentarena_common.ops import serve_ops
from agentarena_common.outbox import OutboxRelay, enqueue
from agentarena_common.telemetry import get_tracer, setup_tracing, shutdown_tracing
from evaluation_service.benchmarks import compute_summary
from evaluation_service.config import Settings
from evaluation_service.evaluators import EVALUATOR_VERSION, evaluate, transcript_json
from evaluation_service.judge import LlmJudge
from evaluation_service.models import (
    Benchmark,
    Evaluation,
    EvaluatorResult,
    JudgeResult,
    Outbox,
    ProcessedMessage,
)

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

CONSUMER = "evaluation-consumer"

EVALUATION_SECONDS = Histogram(
    "evaluation_duration_seconds", "Time to score one execution.", ["service"], buckets=LATENCY_BUCKETS
)
EVALUATIONS = Counter("evaluations_total", "Executions scored, by result.", ["service", "result"])
JUDGE_CALLS = Counter("judge_calls_total", "LLM judge calls, by status.", ["service", "status"])


class EvaluationConsumer:
    def __init__(self, settings: Settings, database: Database, judge: LlmJudge | None = None) -> None:
        self.settings = settings
        self.database = database
        self.judge = judge
        self.service = settings.service_name

    async def handle(self, envelope: EventEnvelope, _: Any) -> None:
        payload = envelope.payload()
        if isinstance(payload, EvaluationRequested):
            evaluation_id = await self._evaluate(envelope, payload)
            if evaluation_id is not None and payload.judge_enabled and self.judge is not None:
                await self._run_judge(envelope, payload, evaluation_id)
        elif isinstance(payload, RunCompleted):
            await self._finalize(envelope, payload)

    async def _evaluate(self, envelope: EventEnvelope, payload: EvaluationRequested) -> uuid.UUID | None:
        started = time.perf_counter()
        with tracer.start_as_current_span(
            "evaluation.score",
            attributes={"execution.id": str(payload.execution_id), "scenario.type": payload.scenario_type},
        ):
            verdict = evaluate(
                expected=payload.expected,
                criteria=payload.criteria,
                transcript=payload.transcript,
                metrics=payload.metrics,
                execution_status=payload.execution_status,
                error_code=payload.error_code,
            )
            async with self.database.sessions() as session, session.begin():
                if not await first_delivery(session, ProcessedMessage, CONSUMER, envelope.event_id):
                    return None
                evaluation_id = uuid.uuid4()
                inserted = await session.execute(
                    insert(Evaluation)
                    .values(
                        id=evaluation_id,
                        org_id=envelope.org_id,
                        run_id=payload.run_id,
                        run_name=payload.run_name,
                        execution_id=payload.execution_id,
                        agent_id=payload.agent_id,
                        agent_name=payload.agent_name,
                        agent_version=payload.agent_version,
                        scenario_id=payload.scenario_id,
                        scenario_version=payload.scenario_version,
                        scenario_name=payload.scenario_name,
                        scenario_type=payload.scenario_type,
                        difficulty=payload.difficulty,
                        repetition=payload.repetition,
                        seed=payload.seed,
                        execution_status=payload.execution_status,
                        error_code=payload.error_code,
                        passed=verdict.passed,
                        score=verdict.score,
                        dimension_scores=verdict.dimension_scores,
                        failure_categories=verdict.failure_categories,
                        evaluator_version=EVALUATOR_VERSION,
                        latency_ms=payload.metrics.latency_ms,
                        queue_wait_ms=payload.metrics.queue_wait_ms,
                        agent_calls=payload.metrics.agent_calls,
                        tool_calls=payload.metrics.tool_calls,
                        input_tokens=payload.metrics.input_tokens,
                        output_tokens=payload.metrics.output_tokens,
                        cost_usd=payload.metrics.cost_usd,
                        attempts=payload.metrics.attempts,
                        started_at=payload.started_at,
                        finished_at=payload.finished_at,
                    )
                    .on_conflict_do_nothing(index_elements=["execution_id"])
                )
                if not inserted.rowcount:
                    return None
                session.add_all(
                    EvaluatorResult(
                        evaluation_id=evaluation_id,
                        evaluator=check.evaluator,
                        dimension=check.dimension,
                        score=check.score,
                        passed=check.passed,
                        details=check.details,
                    )
                    for check in verdict.checks
                )
                enqueue(
                    session,
                    Outbox,
                    make_event(
                        "evaluation.completed",
                        EvaluationCompleted(
                            run_id=payload.run_id,
                            execution_id=payload.execution_id,
                            evaluation_id=evaluation_id,
                            passed=verdict.passed,
                            score=verdict.score,
                        ),
                        producer=self.service,
                        org_id=envelope.org_id,
                        correlation_id=envelope.correlation_id,
                        causation_id=envelope.event_id,
                    ),
                )
        EVALUATION_SECONDS.labels(self.service).observe(time.perf_counter() - started)
        EVALUATIONS.labels(self.service, "passed" if verdict.passed else "failed").inc()
        return evaluation_id

    async def _run_judge(self, envelope: EventEnvelope, payload: EvaluationRequested, evaluation_id: uuid.UUID) -> None:
        """Runs after the scores commit, so a slow or failing judge cannot affect them."""
        assert self.judge is not None
        expected = {k: v for k, v in payload.expected.items() if v not in (None, [], {}, False)}
        try:
            outcome = await self.judge.review(
                scenario_name=payload.scenario_name,
                expected_summary=str(expected)[:2000],
                transcript=transcript_json(payload.transcript),
            )
        except Exception:  # noqa: BLE001 - the judge is advisory and must never fail the pipeline
            logger.exception("judge failed", extra={"evaluation_id": str(evaluation_id)})
            JUDGE_CALLS.labels(self.service, "exception").inc()
            return
        JUDGE_CALLS.labels(self.service, outcome.status).inc()
        verdict = outcome.verdict
        async with self.database.sessions() as session, session.begin():
            session.add(
                JudgeResult(
                    evaluation_id=evaluation_id,
                    org_id=envelope.org_id,
                    model=self.judge.model,
                    status=outcome.status,
                    relevance=verdict.relevance if verdict else None,
                    reasoning_quality=verdict.reasoning_quality if verdict else None,
                    context_consistency=verdict.context_consistency if verdict else None,
                    conversation_quality=verdict.conversation_quality if verdict else None,
                    error_analysis=verdict.error_analysis if verdict else outcome.detail,
                )
            )

    async def _finalize(self, envelope: EventEnvelope, payload: RunCompleted) -> None:
        async with self.database.sessions() as session, session.begin():
            if not await first_delivery(session, ProcessedMessage, CONSUMER, envelope.event_id):
                return
            await store_benchmark(session, self.settings, envelope.org_id, payload)


async def store_benchmark(session: AsyncSession, settings: Settings, org_id: uuid.UUID, payload: RunCompleted) -> None:
    rows = (
        await session.scalars(
            select(Evaluation).where(Evaluation.run_id == payload.run_id, Evaluation.org_id == org_id)
        )
    ).all()
    summary = compute_summary(rows, settings.benchmark_pass_rate)
    values = {
        "org_id": org_id,
        "run_name": payload.run_name,
        "agent_id": payload.agent_id,
        "agent_name": payload.agent_name,
        "agent_version": payload.agent_version,
        "run_status": payload.status,
        "verdict": summary["verdict"],
        "pass_rate": summary["totals"]["pass_rate"],
        "summary": summary,
    }
    await session.execute(
        insert(Benchmark)
        .values(run_id=payload.run_id, **values)
        .on_conflict_do_update(index_elements=["run_id"], set_=values)
    )


async def amain() -> None:
    settings = Settings()
    settings.service_name = os.environ.get("SERVICE_NAME", "evaluation-service-consumer")
    configure_logging(settings.service_name, settings.log_level)
    setup_tracing(settings)

    database = Database(settings)
    bus = MessageBus(settings.rabbitmq_url, settings.service_name)
    judge = LlmJudge(settings) if settings.anthropic_api_key else None
    consumer = EvaluationConsumer(settings, database, judge)

    async def database_ready(_: FastAPI) -> None:
        await database.ping()

    async def broker_ready(_: FastAPI) -> None:
        if not bus.is_connected:
            raise ConnectionError("broker disconnected")

    server, server_task = await serve_ops(
        settings.service_name, settings.consumer_ops_port, (database_ready, broker_ready)
    )
    await bus.connect()
    await bus.consume(EVALUATION_QUEUE, consumer.handle, prefetch=settings.consumer_prefetch)
    relay = OutboxRelay(database, bus, Outbox, service=settings.service_name)
    relay_task = asyncio.create_task(relay.run())
    logger.info("evaluation consumer started", extra={"judge_enabled": judge is not None})

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except (NotImplementedError, AttributeError):
            pass  # Windows development only.
    await stop.wait()

    relay.stop()
    await bus.stop_consuming()
    await asyncio.gather(relay_task, return_exceptions=True)
    await bus.close()
    await database.dispose()
    server.should_exit = True
    await server_task
    shutdown_tracing()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
