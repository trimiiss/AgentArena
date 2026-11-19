"""State transitions for executions and runs, shared by the scheduler and workers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentarena_common.db import utcnow
from agentarena_common.events import (
    EvaluationRequested,
    ExecutionFailed,
    ExecutionMetrics,
    ExecutionRequested,
    RunCompleted,
    TranscriptStep,
    make_event,
)
from agentarena_common.outbox import enqueue
from run_orchestrator.metrics import RUNS_FINISHED
from run_orchestrator.models import ACTIVE_STATUSES, TERMINAL_STATUSES, Execution, ExecutionStep, Outbox, Run


async def status_counts(session: AsyncSession, run_id: Any) -> dict[str, int]:
    rows = await session.execute(
        select(Execution.status, func.count()).where(Execution.run_id == run_id).group_by(Execution.status)
    )
    return dict(rows.all())


async def dispatch(session: AsyncSession, run: Run, *, producer: str) -> int:
    """Fill free concurrency slots. The caller holds a lock on the run row."""
    if run.status not in ("queued", "running"):
        return 0
    active = await session.scalar(
        select(func.count())
        .select_from(Execution)
        .where(Execution.run_id == run.id, Execution.status.in_(ACTIVE_STATUSES))
    )
    slots = run.concurrency - (active or 0)
    if slots <= 0:
        return 0
    now = utcnow()
    rows = (
        await session.scalars(
            select(Execution)
            .where(
                Execution.run_id == run.id,
                (Execution.status == "pending")
                | ((Execution.status == "retry_wait") & (Execution.next_attempt_at <= now)),
            )
            # Retries go first, so a failing scenario cannot starve at the back of the run.
            .order_by(Execution.attempt.desc(), Execution.scenario_name, Execution.repetition)
            .limit(slots)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for execution in rows:
        if execution.status == "retry_wait":
            execution.attempt += 1
        execution.status = "dispatched"
        execution.dispatched_at = now
        execution.next_attempt_at = None
        enqueue(
            session,
            Outbox,
            make_event(
                "execution.requested",
                ExecutionRequested(
                    run_id=run.id, execution_id=execution.id, attempt=execution.attempt, dispatched_at=now
                ),
                producer=producer,
                org_id=run.org_id,
                correlation_id=run.correlation_id,
            ),
        )
    if rows and run.status == "queued":
        run.status = "running"
    return len(rows)


def evaluation_request(
    run: Run,
    execution: Execution,
    *,
    scenario: dict[str, Any] | None,
    transcript: list[TranscriptStep],
) -> EvaluationRequested:
    """Build the self-contained evaluation command for a finished execution."""
    scenario = scenario or {}
    return EvaluationRequested(
        run_id=run.id,
        run_name=run.name,
        execution_id=execution.id,
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        agent_version=run.agent_version,
        scenario_id=execution.scenario_id,
        scenario_version=execution.scenario_version,
        scenario_name=execution.scenario_name,
        scenario_type=execution.scenario_type,
        difficulty=execution.difficulty,
        repetition=execution.repetition,
        seed=execution.seed,
        execution_status=execution.status,
        error_code=execution.error_code,
        error_message=execution.error_message,
        expected=scenario.get("expected") or {},
        criteria=scenario.get("criteria") or {},
        transcript=transcript,
        metrics=ExecutionMetrics(
            latency_ms=execution.latency_ms or 0,
            queue_wait_ms=execution.queue_wait_ms or 0,
            agent_calls=execution.agent_calls,
            tool_calls=execution.tool_calls,
            input_tokens=execution.input_tokens,
            output_tokens=execution.output_tokens,
            cost_usd=execution.cost_usd,
            attempts=execution.attempt,
        ),
        judge_enabled=run.judge_enabled,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
    )


async def stored_transcript(session: AsyncSession, execution: Execution) -> list[TranscriptStep]:
    rows = await session.scalars(
        select(ExecutionStep)
        .where(ExecutionStep.execution_id == execution.id, ExecutionStep.attempt == execution.attempt)
        .order_by(ExecutionStep.seq)
    )
    return [TranscriptStep.model_validate(row.payload) for row in rows]


async def fail_execution(
    session: AsyncSession,
    run: Run,
    execution: Execution,
    *,
    status: str,
    error_code: str,
    error_message: str,
    producer: str,
) -> None:
    """Terminal failure. It still gets evaluated, so it counts in the benchmark."""
    execution.status = status
    execution.error_code = error_code
    execution.error_message = error_message
    execution.finished_at = utcnow()
    execution.lease_expires_at = None
    enqueue(
        session,
        Outbox,
        make_event(
            "execution.failed",
            ExecutionFailed(
                run_id=run.id,
                execution_id=execution.id,
                attempt=execution.attempt,
                status=status,
                error_code=error_code,
                error_message=error_message,
            ),
            producer=producer,
            org_id=run.org_id,
            correlation_id=run.correlation_id,
        ),
    )
    enqueue(
        session,
        Outbox,
        make_event(
            "evaluation.requested",
            evaluation_request(run, execution, scenario=None, transcript=await stored_transcript(session, execution)),
            producer=producer,
            org_id=run.org_id,
            correlation_id=run.correlation_id,
        ),
    )


async def maybe_finish(session: AsyncSession, run: Run, *, producer: str) -> None:
    """Advance a run whose executions have settled. The caller holds the run lock."""
    if run.status in ("completed", "failed", "cancelled"):
        return
    counts = await status_counts(session, run.id)
    unfinished = sum(count for status, count in counts.items() if status not in TERMINAL_STATUSES)

    if run.status == "cancelling":
        if not any(counts.get(status) for status in ACTIVE_STATUSES):
            await session.execute(
                update(Execution)
                .where(Execution.run_id == run.id, Execution.status.in_(("pending", "retry_wait")))
                .values(status="cancelled", finished_at=utcnow())
            )
            _finish(session, run, "cancelled", producer)
        return
    if unfinished:
        return

    pending_evaluations = await session.scalar(
        select(func.count())
        .select_from(Execution)
        .where(
            Execution.run_id == run.id,
            Execution.evaluated_at.is_(None),
            Execution.status != "cancelled",
        )
    )
    if pending_evaluations:
        if run.status != "evaluating":
            run.status = "evaluating"
            run.evaluating_since = utcnow()
        return
    failed = counts.get("failed", 0) + counts.get("timed_out", 0)
    _finish(session, run, "failed" if failed == run.total_executions else "completed", producer)


def _finish(session: AsyncSession, run: Run, status: str, producer: str) -> None:
    run.status = status
    run.finished_at = utcnow()
    enqueue(
        session,
        Outbox,
        make_event(
            "run.completed",
            RunCompleted(
                run_id=run.id,
                agent_id=run.agent_id,
                agent_name=run.agent_name,
                agent_version=run.agent_version,
                run_name=run.name,
                status=status,
                total_executions=run.total_executions,
                finished_at=run.finished_at,
            ),
            producer=producer,
            org_id=run.org_id,
            correlation_id=run.correlation_id,
        ),
    )
    RUNS_FINISHED.labels(producer, status).inc()
