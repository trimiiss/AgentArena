from __future__ import annotations

import asyncio
import json
import secrets
import uuid
import zlib
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agentarena_common.auth import CanRead, CanWrite, Principal
from agentarena_common.context import ensure_correlation_id
from agentarena_common.db import Database, get_session
from agentarena_common.events import RunRequested, make_event
from agentarena_common.outbox import enqueue
from agentarena_common.ratelimit import enforce
from agentarena_common.resilience import CircuitOpenError, DownstreamError
from agentarena_common.telemetry import current_trace_id
from run_orchestrator.clients import Dependencies, NotFoundError
from run_orchestrator.config import Settings
from run_orchestrator.metrics import RUNS_CREATED
from run_orchestrator.models import RUN_TERMINAL_STATUSES, Execution, Outbox, Run
from run_orchestrator.schemas import (
    ExecutionDetail,
    ExecutionList,
    ExecutionOut,
    Progress,
    RunCreate,
    RunList,
    RunOut,
    RunStats,
    StepOut,
)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _dependencies(request: Request) -> Dependencies:
    return request.app.state.dependencies


def _execution_seed(run_seed: int, scenario_id: Any, repetition: int) -> int:
    """Stable per execution, so replaying a run gives every scenario the same seed."""
    return zlib.crc32(f"{run_seed}:{scenario_id}:{repetition}".encode())


def _progress_expression() -> list[Any]:
    return [
        Execution.run_id,
        Execution.status,
        func.count().label("total"),
        func.count(Execution.evaluated_at).label("evaluated"),
        func.sum(case((Execution.passed.is_(True), 1), else_=0)).label("passed"),
    ]


async def _progress(session: AsyncSession, runs: list[Run]) -> dict[uuid.UUID, Progress]:
    result = {run.id: Progress(total=run.total_executions) for run in runs}
    if not runs:
        return result
    rows = await session.execute(
        select(*_progress_expression())
        .where(Execution.run_id.in_(result.keys()))
        .group_by(Execution.run_id, Execution.status)
    )
    for row in rows.all():
        progress = result[row.run_id]
        setattr(progress, row.status, row.total)
        progress.evaluated += row.evaluated
        progress.passed += int(row.passed or 0)
    return result


def _run_out(run: Run, progress: Progress) -> RunOut:
    return RunOut(
        id=run.id,
        org_id=run.org_id,
        name=run.name,
        status=run.status,
        agent_id=run.agent_id,
        agent_name=run.agent_name,
        agent_version=run.agent_version,
        suite_id=run.suite_id,
        seed=run.seed,
        concurrency=run.concurrency,
        timeout_seconds=run.timeout_seconds,
        max_retries=run.max_retries,
        repetitions=run.repetitions,
        judge_enabled=run.judge_enabled,
        total_executions=run.total_executions,
        correlation_id=run.correlation_id,
        trace_id=run.trace_id,
        replay_of=run.replay_of,
        warning=run.warning,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        progress=progress,
    )


async def _load_run(session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> Run:
    run = await session.scalar(select(Run).where(Run.id == run_id, Run.org_id == org_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"A required service is unavailable: {getattr(exc, 'target', 'dependency')}",
        headers={"Retry-After": "5"},
    )


async def _persist_run(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    agent_id: uuid.UUID,
    agent_name: str,
    agent_version: int,
    suite_id: uuid.UUID | None,
    seed: int,
    concurrency: int,
    timeout_seconds: float,
    max_retries: int,
    repetitions: int,
    judge_enabled: bool,
    scenarios: list[dict[str, Any]],
    service: str,
    idempotency_key: str | None = None,
    replay_of: uuid.UUID | None = None,
) -> Run:
    """The run, its executions and run.requested commit in one transaction."""
    correlation_id = ensure_correlation_id()
    run = Run(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        created_by=principal.user_id,
        name=name,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_version=agent_version,
        suite_id=suite_id,
        seed=seed,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        repetitions=repetitions,
        judge_enabled=judge_enabled,
        total_executions=len(scenarios) * repetitions,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        trace_id=current_trace_id(),
        replay_of=replay_of,
    )
    session.add(run)
    await session.flush()
    session.add_all(
        Execution(
            run_id=run.id,
            org_id=principal.org_id,
            scenario_id=scenario["scenario_id"],
            scenario_version=scenario["version"],
            scenario_name=scenario["name"],
            scenario_type=scenario["scenario_type"],
            difficulty=scenario["difficulty"],
            repetition=repetition,
            seed=_execution_seed(seed, scenario["scenario_id"], repetition),
            max_attempts=max_retries + 1,
        )
        for scenario in scenarios
        for repetition in range(1, repetitions + 1)
    )
    enqueue(
        session,
        Outbox,
        make_event(
            "run.requested",
            RunRequested(run_id=run.id, total_executions=run.total_executions, concurrency=concurrency),
            producer=service,
            org_id=principal.org_id,
            correlation_id=correlation_id,
        ),
    )
    await session.commit()
    RUNS_CREATED.labels(service).inc()
    return run


@router.post("/v1/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED, tags=["runs"])
async def create_run(
    body: RunCreate,
    request: Request,
    response: Response,
    session: Session,
    principal: CanWrite,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> RunOut:
    """Accepts a run for asynchronous execution.

    Send an Idempotency-Key header to make retries safe: repeating the request
    returns the original run instead of starting another one.
    """
    settings = _settings(request)
    enforce(request.app.state.write_limiter, str(principal.org_id), service=settings.service_name, scope="runs")

    async def existing_run() -> RunOut | None:
        if idempotency_key is None:
            return None
        run = await session.scalar(
            select(Run).where(Run.org_id == principal.org_id, Run.idempotency_key == idempotency_key)
        )
        if run is None:
            return None
        response.status_code = status.HTTP_200_OK
        return _run_out(run, (await _progress(session, [run]))[run.id])

    if (found := await existing_run()) is not None:
        return found

    dependencies = _dependencies(request)
    headers = principal.auth_headers()
    try:
        agent = await dependencies.get_agent(body.agent_id, headers)
        version = body.agent_version or agent["latest_version"]
        if body.agent_version is not None:
            await dependencies.get_agent_version(body.agent_id, version, headers)
        if body.suite_id is not None:
            scenarios = await dependencies.resolve_suite(body.suite_id, headers)
        else:
            scenarios = await dependencies.resolve_scenarios(body.scenario_ids or [], headers)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DownstreamError, CircuitOpenError) as exc:
        raise _unavailable(exc) from exc

    if agent["status"] == "disabled":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This agent is disabled")
    if not scenarios:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The run has no scenarios to execute")
    total = len(scenarios) * body.repetitions
    if total > settings.max_executions_per_run:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A run may have at most {settings.max_executions_per_run} executions, this one has {total}",
        )

    try:
        run = await _persist_run(
            session,
            principal,
            name=body.name or f"{agent['name']} v{version}",
            agent_id=body.agent_id,
            agent_name=agent["name"],
            agent_version=version,
            suite_id=body.suite_id,
            seed=body.seed if body.seed is not None else secrets.randbelow(2**31),
            concurrency=body.concurrency,
            timeout_seconds=body.timeout_seconds,
            max_retries=body.max_retries,
            repetitions=body.repetitions,
            judge_enabled=body.judge_enabled,
            scenarios=scenarios,
            service=settings.service_name,
            idempotency_key=idempotency_key,
        )
    except IntegrityError:
        # Two identical requests raced. The unique constraint picked the winner.
        await session.rollback()
        if (found := await existing_run()) is not None:
            return found
        raise
    return _run_out(run, Progress(total=run.total_executions, pending=run.total_executions))


@router.get("/v1/runs", response_model=RunList, tags=["runs"])
async def list_runs(
    session: Session,
    principal: CanRead,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    agent_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunList:
    conditions = [Run.org_id == principal.org_id]
    if run_status:
        conditions.append(Run.status == run_status)
    if agent_id:
        conditions.append(Run.agent_id == agent_id)
    total = await session.scalar(select(func.count()).select_from(Run).where(*conditions))
    runs = list(
        await session.scalars(
            select(Run).where(*conditions).order_by(Run.created_at.desc()).limit(limit).offset(offset)
        )
    )
    progress = await _progress(session, runs)
    return RunList(items=[_run_out(run, progress[run.id]) for run in runs], total=total or 0)


@router.get("/v1/runs/stats", response_model=RunStats, tags=["runs"])
async def run_stats(session: Session, principal: CanRead) -> RunStats:
    rows = await session.execute(
        select(Run.status, func.count()).where(Run.org_id == principal.org_id).group_by(Run.status)
    )
    counts: dict[str, int] = dict(rows.all())
    total_executions, finished = (
        await session.execute(
            select(func.count(), func.count(Execution.finished_at)).where(Execution.org_id == principal.org_id)
        )
    ).one()
    return RunStats(
        total_runs=sum(counts.values()),
        active_runs=sum(count for name, count in counts.items() if name not in RUN_TERMINAL_STATUSES),
        completed_runs=counts.get("completed", 0),
        failed_runs=counts.get("failed", 0),
        cancelled_runs=counts.get("cancelled", 0),
        total_executions=total_executions,
        finished_executions=finished,
    )


@router.get("/v1/runs/{run_id}", response_model=RunOut, tags=["runs"])
async def get_run(run_id: uuid.UUID, session: Session, principal: CanRead) -> RunOut:
    run = await _load_run(session, principal.org_id, run_id)
    return _run_out(run, (await _progress(session, [run]))[run.id])


@router.post("/v1/runs/{run_id}/cancel", response_model=RunOut, tags=["runs"])
async def cancel_run(run_id: uuid.UUID, request: Request, session: Session, principal: CanWrite) -> RunOut:
    settings = _settings(request)
    enforce(request.app.state.write_limiter, str(principal.org_id), service=settings.service_name, scope="runs")
    run = await session.scalar(select(Run).where(Run.id == run_id, Run.org_id == principal.org_id).with_for_update())
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.status in RUN_TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Run is already {run.status}")
    run.status = "cancelling"
    await session.commit()
    return _run_out(run, (await _progress(session, [run]))[run.id])


@router.post("/v1/runs/{run_id}/replay", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED, tags=["runs"])
async def replay_run(run_id: uuid.UUID, request: Request, session: Session, principal: CanWrite) -> RunOut:
    """Repeats a run with the same agent version, scenario versions and seed."""
    settings = _settings(request)
    enforce(request.app.state.write_limiter, str(principal.org_id), service=settings.service_name, scope="runs")
    original = await _load_run(session, principal.org_id, run_id)
    rows = await session.execute(
        select(
            Execution.scenario_id,
            Execution.scenario_version,
            Execution.scenario_name,
            Execution.scenario_type,
            Execution.difficulty,
        )
        .where(Execution.run_id == original.id)
        .distinct()
    )
    scenarios = [
        {
            "scenario_id": row.scenario_id,
            "version": row.scenario_version,
            "name": row.scenario_name,
            "scenario_type": row.scenario_type,
            "difficulty": row.difficulty,
        }
        for row in rows.all()
    ]
    run = await _persist_run(
        session,
        principal,
        name=f"{original.name} (replay)"[:200],
        agent_id=original.agent_id,
        agent_name=original.agent_name,
        agent_version=original.agent_version,
        suite_id=original.suite_id,
        seed=original.seed,
        concurrency=original.concurrency,
        timeout_seconds=original.timeout_seconds,
        max_retries=original.max_retries,
        repetitions=original.repetitions,
        judge_enabled=original.judge_enabled,
        scenarios=scenarios,
        service=settings.service_name,
        replay_of=original.id,
    )
    return _run_out(run, Progress(total=run.total_executions, pending=run.total_executions))


@router.get("/v1/runs/{run_id}/executions", response_model=ExecutionList, tags=["executions"])
async def list_executions(
    run_id: uuid.UUID,
    session: Session,
    principal: CanRead,
    execution_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExecutionList:
    run = await _load_run(session, principal.org_id, run_id)
    conditions = [Execution.run_id == run.id]
    if execution_status:
        conditions.append(Execution.status == execution_status)
    total = await session.scalar(select(func.count()).select_from(Execution).where(*conditions))
    rows = await session.scalars(
        select(Execution)
        .where(*conditions)
        .order_by(Execution.scenario_name, Execution.repetition)
        .limit(limit)
        .offset(offset)
    )
    return ExecutionList(items=[ExecutionOut.model_validate(row) for row in rows], total=total or 0)


@router.get("/v1/executions/{execution_id}", response_model=ExecutionDetail, tags=["executions"])
async def get_execution(execution_id: uuid.UUID, session: Session, principal: CanRead) -> ExecutionDetail:
    execution = await session.scalar(
        select(Execution)
        .where(Execution.id == execution_id, Execution.org_id == principal.org_id)
        .options(selectinload(Execution.steps), selectinload(Execution.run))
    )
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Execution not found")
    summary = ExecutionOut.model_validate(execution).model_dump()
    return ExecutionDetail(
        **summary,
        agent_id=execution.run.agent_id,
        agent_version=execution.run.agent_version,
        agent_name=execution.run.agent_name,
        run_name=execution.run.name,
        steps=[
            StepOut(
                attempt=step.attempt,
                seq=step.seq,
                type=step.type,
                latency_ms=step.latency_ms,
                payload=step.payload,
                created_at=step.created_at,
            )
            for step in execution.steps
        ],
    )


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _stream_run(request: Request, database: Database, org_id: uuid.UUID, run_id: uuid.UUID) -> AsyncIterator[str]:
    """Polls the database, so any API replica can serve any client without shared state."""
    seen: dict[uuid.UUID, tuple[Any, ...]] = {}
    last_progress: dict[str, Any] | None = None
    idle_ticks = 0
    while not await request.is_disconnected():
        async with database.sessions() as session:
            run = await session.scalar(select(Run).where(Run.id == run_id, Run.org_id == org_id))
            if run is None:
                return
            progress = (await _progress(session, [run]))[run.id]
            rows = await session.execute(
                select(
                    Execution.id,
                    Execution.status,
                    Execution.attempt,
                    Execution.scenario_name,
                    Execution.repetition,
                    Execution.latency_ms,
                    Execution.passed,
                    Execution.score,
                    Execution.error_code,
                ).where(Execution.run_id == run.id)
            )
            changed = []
            for row in rows.all():
                state = (row.status, row.attempt, row.passed)
                if seen.get(row.id) != state:
                    seen[row.id] = state
                    changed.append({**row._mapping, "id": str(row.id)})

        snapshot = {"status": run.status, "warning": run.warning, "progress": progress.model_dump()}
        if snapshot != last_progress:
            last_progress = snapshot
            yield _sse("progress", snapshot)
        if changed:
            idle_ticks = 0
            yield _sse("executions", changed)
        else:
            idle_ticks += 1
            if idle_ticks % 15 == 0:
                yield ": keep-alive\n\n"
        if run.status in RUN_TERMINAL_STATUSES:
            yield _sse("done", {"status": run.status})
            return
        await asyncio.sleep(1)


@router.get("/v1/runs/{run_id}/stream", tags=["runs"], summary="Live progress as Server-Sent Events")
async def stream_run(run_id: uuid.UUID, request: Request, session: Session, principal: CanRead) -> StreamingResponse:
    await _load_run(session, principal.org_id, run_id)
    return StreamingResponse(
        _stream_run(request, request.app.state.database, principal.org_id, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
