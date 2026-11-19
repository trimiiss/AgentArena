from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    agent_id: UUID
    agent_version: int | None = Field(default=None, ge=1, description="Defaults to the latest version.")
    suite_id: UUID | None = None
    scenario_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)
    repetitions: int = Field(default=1, ge=1, le=100)
    concurrency: int = Field(default=4, ge=1, le=64)
    timeout_seconds: float = Field(default=30, ge=1, le=300)
    max_retries: int = Field(default=1, ge=0, le=5)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    judge_enabled: bool = False

    @model_validator(mode="after")
    def exactly_one_source(self) -> RunCreate:
        if (self.suite_id is None) == (self.scenario_ids is None):
            raise ValueError("Provide exactly one of suite_id or scenario_ids")
        return self


class Progress(BaseModel):
    total: int
    pending: int = 0
    dispatched: int = 0
    running: int = 0
    retry_wait: int = 0
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    cancelled: int = 0
    evaluated: int = 0
    passed: int = 0


class RunOut(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    status: str
    agent_id: UUID
    agent_name: str
    agent_version: int
    suite_id: UUID | None
    seed: int
    concurrency: int
    timeout_seconds: float
    max_retries: int
    repetitions: int
    judge_enabled: bool
    total_executions: int
    correlation_id: str
    trace_id: str | None
    replay_of: UUID | None
    warning: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: Progress


class RunList(BaseModel):
    items: list[RunOut]
    total: int


class RunStats(BaseModel):
    total_runs: int
    active_runs: int
    completed_runs: int
    failed_runs: int
    cancelled_runs: int
    total_executions: int
    finished_executions: int


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    scenario_id: UUID
    scenario_version: int
    scenario_name: str
    scenario_type: str
    difficulty: str
    repetition: int
    seed: int
    status: str
    attempt: int
    max_attempts: int
    worker_id: str | None
    error_code: str | None
    error_message: str | None
    latency_ms: float | None
    queue_wait_ms: float | None
    agent_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    passed: bool | None
    score: float | None
    trace_id: str | None
    dispatched_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    evaluated_at: datetime | None


class ExecutionList(BaseModel):
    items: list[ExecutionOut]
    total: int


class StepOut(BaseModel):
    attempt: int
    seq: int
    type: str
    latency_ms: float | None
    payload: dict[str, Any]
    created_at: datetime


class ExecutionDetail(ExecutionOut):
    agent_id: UUID
    agent_version: int
    agent_name: str
    run_name: str
    steps: list[StepOut]
