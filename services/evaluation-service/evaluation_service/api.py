from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy import Float, Integer, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agentarena_common.auth import CanRead
from agentarena_common.db import get_session, utcnow
from evaluation_service import reports
from evaluation_service.benchmarks import compare, compute_summary
from evaluation_service.config import Settings
from evaluation_service.models import Benchmark, Evaluation
from evaluation_service.schemas import (
    AgentPerformance,
    AnalyticsOverview,
    BenchmarkList,
    BenchmarkListItem,
    BenchmarkOut,
    CategoryCount,
    ComparisonOut,
    ComparisonSide,
    DimensionAverage,
    EvaluationDetail,
    EvaluationList,
    EvaluationOut,
    TimeseriesPoint,
)

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]
Days = Annotated[int, Query(ge=1, le=365)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/v1/evaluations", response_model=EvaluationList, tags=["evaluations"])
async def list_evaluations(
    session: Session,
    principal: CanRead,
    run_id: uuid.UUID | None = None,
    passed: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvaluationList:
    conditions = [Evaluation.org_id == principal.org_id]
    if run_id is not None:
        conditions.append(Evaluation.run_id == run_id)
    if passed is not None:
        conditions.append(Evaluation.passed.is_(passed))
    total = await session.scalar(select(func.count()).select_from(Evaluation).where(*conditions))
    rows = await session.scalars(
        select(Evaluation).where(*conditions).order_by(Evaluation.created_at.desc()).limit(limit).offset(offset)
    )
    return EvaluationList(items=[EvaluationOut.model_validate(row) for row in rows], total=total or 0)


@router.get("/v1/evaluations/executions/{execution_id}", response_model=EvaluationDetail, tags=["evaluations"])
async def get_execution_evaluation(execution_id: uuid.UUID, session: Session, principal: CanRead) -> Evaluation:
    row = await session.scalar(
        select(Evaluation)
        .where(Evaluation.execution_id == execution_id, Evaluation.org_id == principal.org_id)
        .options(selectinload(Evaluation.checks), selectinload(Evaluation.judgement))
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Evaluation not found")
    return row


@router.get("/v1/benchmarks", response_model=BenchmarkList, tags=["benchmarks"])
async def list_benchmarks(
    session: Session,
    principal: CanRead,
    agent_id: uuid.UUID | None = None,
    agent_version: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BenchmarkList:
    conditions = [Benchmark.org_id == principal.org_id]
    if agent_id is not None:
        conditions.append(Benchmark.agent_id == agent_id)
    if agent_version is not None:
        conditions.append(Benchmark.agent_version == agent_version)
    total = await session.scalar(select(func.count()).select_from(Benchmark).where(*conditions))
    rows = await session.scalars(
        select(Benchmark).where(*conditions).order_by(Benchmark.created_at.desc()).limit(limit).offset(offset)
    )
    return BenchmarkList(items=[BenchmarkListItem.model_validate(row) for row in rows], total=total or 0)


async def _load_benchmark(session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> Benchmark:
    row = await session.scalar(select(Benchmark).where(Benchmark.run_id == run_id, Benchmark.org_id == org_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Benchmark not found. The run may still be in progress.")
    return row


@router.get("/v1/benchmarks/runs/{run_id}", response_model=BenchmarkOut, tags=["benchmarks"])
async def get_benchmark(run_id: uuid.UUID, request: Request, session: Session, principal: CanRead) -> BenchmarkOut:
    """Returns the final benchmark, or a live partial one while the run is in progress."""
    row = await session.scalar(
        select(Benchmark).where(Benchmark.run_id == run_id, Benchmark.org_id == principal.org_id)
    )
    if row is not None:
        return BenchmarkOut(
            run_id=row.run_id,
            run_name=row.run_name,
            agent_id=row.agent_id,
            agent_name=row.agent_name,
            agent_version=row.agent_version,
            run_status=row.run_status,
            verdict=row.verdict,
            pass_rate=row.pass_rate,
            final=True,
            summary=row.summary,
            created_at=row.created_at,
        )
    rows = (
        await session.scalars(
            select(Evaluation).where(Evaluation.run_id == run_id, Evaluation.org_id == principal.org_id)
        )
    ).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No evaluations for this run yet")
    summary = compute_summary(rows, _settings(request).benchmark_pass_rate)
    return BenchmarkOut(
        run_id=run_id,
        run_name=rows[0].run_name,
        agent_id=rows[0].agent_id,
        agent_name=rows[0].agent_name,
        agent_version=rows[0].agent_version,
        run_status="in_progress",
        verdict=summary["verdict"],
        pass_rate=summary["totals"]["pass_rate"],
        final=False,
        summary=summary,
        created_at=None,
    )


@router.get("/v1/benchmarks/runs/{run_id}/report", tags=["benchmarks"], summary="Download a benchmark report")
async def download_report(
    run_id: uuid.UUID,
    session: Session,
    principal: CanRead,
    report_format: Annotated[Literal["json", "csv", "md", "html"], Query(alias="format")] = "json",
) -> Response:
    benchmark = await _load_benchmark(session, principal.org_id, run_id)
    filename = f"agentarena-benchmark-{run_id}.{report_format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if report_format == "json":
        return JSONResponse(reports.report_json(benchmark), headers=headers)
    if report_format == "md":
        return PlainTextResponse(reports.report_markdown(benchmark), media_type="text/markdown", headers=headers)
    if report_format == "html":
        return HTMLResponse(reports.report_html(benchmark), headers=headers)
    rows = (
        await session.scalars(
            select(Evaluation).where(Evaluation.run_id == run_id, Evaluation.org_id == principal.org_id)
        )
    ).all()
    return PlainTextResponse(reports.report_csv(list(rows)), media_type="text/csv", headers=headers)


def _side(benchmark: Benchmark) -> ComparisonSide:
    return ComparisonSide(
        run_id=benchmark.run_id,
        run_name=benchmark.run_name,
        agent_name=benchmark.agent_name,
        agent_version=benchmark.agent_version,
        created_at=benchmark.created_at,
    )


@router.get("/v1/comparisons/runs", response_model=ComparisonOut, tags=["comparisons"])
async def compare_runs(
    base_run_id: uuid.UUID, candidate_run_id: uuid.UUID, session: Session, principal: CanRead
) -> ComparisonOut:
    base = await _load_benchmark(session, principal.org_id, base_run_id)
    candidate = await _load_benchmark(session, principal.org_id, candidate_run_id)
    return ComparisonOut(
        base=_side(base), candidate=_side(candidate), comparison=compare(base.summary, candidate.summary)
    )


@router.get("/v1/comparisons/agent-versions", response_model=ComparisonOut, tags=["comparisons"])
async def compare_agent_versions(
    agent_id: uuid.UUID,
    base_version: int,
    candidate_version: int,
    session: Session,
    principal: CanRead,
) -> ComparisonOut:
    """Compares the most recent benchmark of each version."""

    async def latest(version: int) -> Benchmark:
        row = await session.scalar(
            select(Benchmark)
            .where(
                Benchmark.org_id == principal.org_id,
                Benchmark.agent_id == agent_id,
                Benchmark.agent_version == version,
            )
            .order_by(Benchmark.created_at.desc())
            .limit(1)
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No benchmark for version {version} yet")
        return row

    base = await latest(base_version)
    candidate = await latest(candidate_version)
    return ComparisonOut(
        base=_side(base), candidate=_side(candidate), comparison=compare(base.summary, candidate.summary)
    )


def _window(principal_org: uuid.UUID, days: int, agent_id: uuid.UUID | None) -> list:
    conditions = [Evaluation.org_id == principal_org, Evaluation.created_at >= utcnow() - timedelta(days=days)]
    if agent_id is not None:
        conditions.append(Evaluation.agent_id == agent_id)
    return conditions


def _p(quantile: float):
    """Latency percentiles only use executions that finished, so a failed call at
    0 ms cannot make an agent look fast."""
    return (
        func.percentile_cont(quantile)
        .within_group(Evaluation.latency_ms)
        .filter(Evaluation.execution_status == "completed")
    )


_AVG_LATENCY = func.avg(Evaluation.latency_ms).filter(Evaluation.execution_status == "completed")


@router.get("/v1/analytics/overview", response_model=AnalyticsOverview, tags=["analytics"])
async def analytics_overview(
    session: Session,
    principal: CanRead,
    days: Days = 30,
    agent_id: uuid.UUID | None = None,
) -> AnalyticsOverview:
    """Every value is computed from stored evaluations and benchmarks."""
    row = (
        await session.execute(
            select(
                func.count().label("executions"),
                func.coalesce(func.sum(cast(Evaluation.passed, Integer)), 0).label("passed"),
                _AVG_LATENCY.label("avg_latency"),
                _p(0.5).label("p50"),
                _p(0.95).label("p95"),
                func.coalesce(func.sum(Evaluation.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(Evaluation.output_tokens), 0).label("output_tokens"),
                func.sum(Evaluation.cost_usd).label("cost"),
                func.count().filter(Evaluation.execution_status == "timed_out").label("timeouts"),
                func.count().filter(Evaluation.execution_status == "failed").label("errors"),
            ).where(*_window(principal.org_id, days, agent_id))
        )
    ).one()

    benchmark_conditions = [
        Benchmark.org_id == principal.org_id,
        Benchmark.created_at >= utcnow() - timedelta(days=days),
    ]
    if agent_id is not None:
        benchmark_conditions.append(Benchmark.agent_id == agent_id)
    verdicts = await session.execute(
        select(Benchmark.verdict, func.count()).where(*benchmark_conditions).group_by(Benchmark.verdict)
    )
    counts = dict(verdicts.all())

    def rounded(value: float | None) -> float | None:
        return None if value is None else round(value, 2)

    return AnalyticsOverview(
        days=days,
        benchmarked_runs=sum(counts.values()),
        runs_passed=counts.get("passed", 0),
        runs_failed=counts.get("failed", 0),
        executions=row.executions,
        executions_passed=row.passed,
        pass_rate=round(row.passed / row.executions, 4) if row.executions else None,
        avg_latency_ms=rounded(row.avg_latency),
        p50_latency_ms=rounded(row.p50),
        p95_latency_ms=rounded(row.p95),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=None if row.cost is None else round(row.cost, 6),
        timeouts=row.timeouts,
        errors=row.errors,
    )


@router.get("/v1/analytics/timeseries", response_model=list[TimeseriesPoint], tags=["analytics"])
async def analytics_timeseries(
    session: Session,
    principal: CanRead,
    days: Days = 30,
    agent_id: uuid.UUID | None = None,
    bucket: Literal["hour", "day"] = "day",
) -> list[TimeseriesPoint]:
    period = func.date_trunc(literal_column(f"'{bucket}'"), Evaluation.created_at)
    rows = await session.execute(
        select(
            period.label("bucket"),
            func.count().label("executions"),
            func.avg(cast(cast(Evaluation.passed, Integer), Float)).label("pass_rate"),
            _AVG_LATENCY.label("avg_latency"),
            _p(0.95).label("p95"),
            func.sum(Evaluation.cost_usd).label("cost"),
        )
        .where(*_window(principal.org_id, days, agent_id))
        .group_by(period)
        .order_by(period)
    )
    return [
        TimeseriesPoint(
            bucket=row.bucket,
            executions=row.executions,
            pass_rate=round(row.pass_rate, 4),
            avg_latency_ms=None if row.avg_latency is None else round(row.avg_latency, 2),
            p95_latency_ms=None if row.p95 is None else round(row.p95, 2),
            cost_usd=None if row.cost is None else round(row.cost, 6),
        )
        for row in rows.all()
    ]


@router.get("/v1/analytics/agents", response_model=list[AgentPerformance], tags=["analytics"])
async def analytics_agents(session: Session, principal: CanRead, days: Days = 30) -> list[AgentPerformance]:
    rows = await session.execute(
        select(
            Evaluation.agent_id,
            func.max(Evaluation.agent_name).label("agent_name"),
            Evaluation.agent_version,
            func.count().label("executions"),
            func.avg(cast(cast(Evaluation.passed, Integer), Float)).label("pass_rate"),
            _AVG_LATENCY.label("avg_latency"),
            _p(0.95).label("p95"),
            func.sum(Evaluation.cost_usd).label("cost"),
            func.max(Evaluation.created_at).label("last_run_at"),
        )
        .where(*_window(principal.org_id, days, None))
        .group_by(Evaluation.agent_id, Evaluation.agent_version)
        .order_by(func.max(Evaluation.created_at).desc())
    )
    return [
        AgentPerformance(
            agent_id=row.agent_id,
            agent_name=row.agent_name,
            agent_version=row.agent_version,
            executions=row.executions,
            pass_rate=round(row.pass_rate, 4),
            avg_latency_ms=None if row.avg_latency is None else round(row.avg_latency, 2),
            p95_latency_ms=None if row.p95 is None else round(row.p95, 2),
            cost_usd=None if row.cost is None else round(row.cost, 6),
            last_run_at=row.last_run_at,
        )
        for row in rows.all()
    ]


@router.get("/v1/analytics/failures", response_model=list[CategoryCount], tags=["analytics"])
async def analytics_failures(
    session: Session, principal: CanRead, days: Days = 30, agent_id: uuid.UUID | None = None
) -> list[CategoryCount]:
    category = func.unnest(Evaluation.failure_categories).label("category")
    subquery = select(category).where(*_window(principal.org_id, days, agent_id)).subquery()
    rows = await session.execute(
        select(subquery.c.category, func.count().label("total"))
        .group_by(subquery.c.category)
        .order_by(func.count().desc())
    )
    return [CategoryCount(category=row.category, count=row.total) for row in rows.all()]


@router.get("/v1/analytics/dimensions", response_model=list[DimensionAverage], tags=["analytics"])
async def analytics_dimensions(
    session: Session, principal: CanRead, days: Days = 30, agent_id: uuid.UUID | None = None
) -> list[DimensionAverage]:
    entries = (
        func.jsonb_each_text(Evaluation.dimension_scores).table_valued("key", "value", name="scores").render_derived()
    )
    rows = await session.execute(
        select(
            entries.c.key.label("dimension"),
            func.avg(cast(entries.c.value, Float)).label("average"),
            func.count().label("samples"),
        )
        .select_from(Evaluation)
        .join(entries, literal_column("true"))
        .where(*_window(principal.org_id, days, agent_id))
        .group_by(entries.c.key)
        .order_by(entries.c.key)
    )
    return [
        DimensionAverage(dimension=row.dimension, average=round(row.average, 4), samples=row.samples)
        for row in rows.all()
    ]
