"""Versioned event contracts.

These Pydantic models are the single definition of every message. The JSON
Schemas under contracts/events/ are generated from them, and a contract test
fails if the two drift. Adding an optional field keeps the version; removing a
field or changing its meaning requires a new version.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentarena_common.context import ensure_correlation_id
from agentarena_common.db import utcnow

EventStatus = Literal["completed", "failed", "timed_out", "cancelled"]


class _Payload(BaseModel):
    # Unknown fields are ignored so producers can add optional fields safely.
    model_config = ConfigDict(extra="ignore")


class RunRequested(_Payload):
    run_id: UUID
    total_executions: int = Field(ge=1)
    concurrency: int = Field(ge=1)


class ExecutionRequested(_Payload):
    """A command to a worker. The database row remains the source of truth."""

    run_id: UUID
    execution_id: UUID
    attempt: int = Field(ge=1)
    dispatched_at: datetime


class ExecutionStarted(_Payload):
    run_id: UUID
    execution_id: UUID
    attempt: int = Field(ge=1)
    worker_id: str
    queue_wait_ms: float = Field(ge=0)


class ExecutionCompleted(_Payload):
    run_id: UUID
    execution_id: UUID
    attempt: int = Field(ge=1)
    status: Literal["completed"] = "completed"
    latency_ms: float = Field(ge=0)
    agent_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)


class ExecutionFailed(_Payload):
    run_id: UUID
    execution_id: UUID
    attempt: int = Field(ge=1)
    status: EventStatus
    error_code: str
    error_message: str
    will_retry: bool = False


class TranscriptStep(_Payload):
    """One recorded interaction inside an execution."""

    seq: int
    type: Literal["user_message", "agent_response", "tool_call", "tool_result", "error"]
    content: str | None = None
    name: str | None = None
    arguments: dict[str, Any] | None = None
    output: Any = None
    is_error: bool = False
    latency_ms: float | None = None
    task_status: str | None = None
    structured_output: Any = None
    injected_failure: bool = False


class ExecutionMetrics(_Payload):
    latency_ms: float = 0
    queue_wait_ms: float = 0
    agent_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    attempts: int = 1


class EvaluationRequested(_Payload):
    """Everything the evaluator needs, so it never reads another service's data."""

    run_id: UUID
    run_name: str = ""
    execution_id: UUID
    agent_id: UUID
    agent_name: str = ""
    agent_version: int
    scenario_id: UUID
    scenario_version: int
    scenario_name: str
    scenario_type: str
    difficulty: str
    repetition: int
    seed: int
    execution_status: EventStatus
    error_code: str | None = None
    error_message: str | None = None
    expected: dict[str, Any] = Field(default_factory=dict)
    criteria: dict[str, Any] = Field(default_factory=dict)
    transcript: list[TranscriptStep] = Field(default_factory=list)
    metrics: ExecutionMetrics = Field(default_factory=ExecutionMetrics)
    judge_enabled: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EvaluationCompleted(_Payload):
    run_id: UUID
    execution_id: UUID
    evaluation_id: UUID
    passed: bool
    score: float = Field(ge=0, le=1)


class RunCompleted(_Payload):
    run_id: UUID
    run_name: str = ""
    agent_id: UUID
    agent_name: str = ""
    agent_version: int
    status: Literal["completed", "failed", "cancelled"]
    total_executions: int
    finished_at: datetime


EVENT_MODELS: dict[tuple[str, int], type[_Payload]] = {
    ("run.requested", 1): RunRequested,
    ("execution.requested", 1): ExecutionRequested,
    ("execution.started", 1): ExecutionStarted,
    ("execution.completed", 1): ExecutionCompleted,
    ("execution.failed", 1): ExecutionFailed,
    ("evaluation.requested", 1): EvaluationRequested,
    ("evaluation.completed", 1): EvaluationCompleted,
    ("run.completed", 1): RunCompleted,
}


class EventEnvelope(BaseModel):
    """Metadata wrapper common to every event."""

    model_config = ConfigDict(extra="ignore")

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    event_version: int = 1
    occurred_at: datetime = Field(default_factory=utcnow)
    producer: str
    org_id: UUID
    correlation_id: str
    causation_id: UUID | None = None
    data: dict[str, Any]

    def payload(self) -> _Payload:
        """Validate ``data`` against the contract for this type and version."""
        model = EVENT_MODELS.get((self.event_type, self.event_version))
        if model is None:
            raise UnknownEventError(self.event_type, self.event_version)
        return model.model_validate(self.data)


class UnknownEventError(Exception):
    def __init__(self, event_type: str, version: int) -> None:
        super().__init__(f"no contract for {event_type} v{version}")


def make_event(
    event_type: str,
    payload: _Payload,
    *,
    producer: str,
    org_id: UUID,
    correlation_id: str | None = None,
    causation_id: UUID | None = None,
    version: int = 1,
) -> EventEnvelope:
    if EVENT_MODELS.get((event_type, version)) is not type(payload):
        raise UnknownEventError(event_type, version)
    return EventEnvelope(
        event_type=event_type,
        event_version=version,
        producer=producer,
        org_id=org_id,
        correlation_id=correlation_id or ensure_correlation_id(),
        causation_id=causation_id,
        data=payload.model_dump(mode="json"),
    )
