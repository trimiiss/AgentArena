from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvaluatorResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evaluator: str
    dimension: str
    score: float
    passed: bool
    details: dict[str, Any]


class JudgeOut(BaseModel):
    """Advisory. Never part of the score."""

    model_config = ConfigDict(from_attributes=True)

    model: str
    status: str
    relevance: int | None
    reasoning_quality: int | None
    context_consistency: int | None
    conversation_quality: int | None
    error_analysis: str


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    execution_id: UUID
    agent_id: UUID
    agent_version: int
    scenario_id: UUID
    scenario_version: int
    scenario_name: str
    scenario_type: str
    difficulty: str
    repetition: int
    seed: int
    execution_status: str
    error_code: str | None
    passed: bool
    score: float
    dimension_scores: dict[str, float]
    failure_categories: list[str]
    evaluator_version: str
    latency_ms: float
    queue_wait_ms: float
    agent_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    attempts: int
    created_at: datetime


class EvaluationDetail(EvaluationOut):
    checks: list[EvaluatorResultOut]
    judgement: JudgeOut | None


class EvaluationList(BaseModel):
    items: list[EvaluationOut]
    total: int


class BenchmarkOut(BaseModel):
    run_id: UUID
    run_name: str
    agent_id: UUID
    agent_name: str
    agent_version: int
    run_status: str
    verdict: str
    pass_rate: float
    final: bool
    summary: dict[str, Any]
    created_at: datetime | None


class BenchmarkListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    run_name: str
    agent_id: UUID
    agent_name: str
    agent_version: int
    run_status: str
    verdict: str
    pass_rate: float
    created_at: datetime


class BenchmarkList(BaseModel):
    items: list[BenchmarkListItem]
    total: int


class ComparisonSide(BaseModel):
    run_id: UUID
    run_name: str
    agent_name: str
    agent_version: int
    created_at: datetime


class ComparisonOut(BaseModel):
    base: ComparisonSide
    candidate: ComparisonSide
    comparison: dict[str, Any]


class AnalyticsOverview(BaseModel):
    days: int
    benchmarked_runs: int
    runs_passed: int
    runs_failed: int
    executions: int
    executions_passed: int
    pass_rate: float | None
    avg_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    timeouts: int
    errors: int


class TimeseriesPoint(BaseModel):
    bucket: datetime
    executions: int
    pass_rate: float
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    cost_usd: float | None


class AgentPerformance(BaseModel):
    agent_id: UUID
    agent_name: str
    agent_version: int
    executions: int
    pass_rate: float
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    cost_usd: float | None
    last_run_at: datetime


class CategoryCount(BaseModel):
    category: str
    count: int


class DimensionAverage(BaseModel):
    dimension: str
    average: float
    samples: int
