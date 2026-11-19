from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentarena_common.db import OutboxMixin, ProcessedMessageMixin, TimestampMixin

ACTIVE_STATUSES = ("dispatched", "running")
TERMINAL_STATUSES = ("completed", "failed", "timed_out", "cancelled")
RUN_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class Base(DeclarativeBase):
    pass


class Outbox(Base, OutboxMixin):
    pass


class ProcessedMessage(Base, ProcessedMessageMixin):
    pass


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_runs_org_idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'evaluating', 'completed', 'failed', 'cancelling', 'cancelled')",
            name="ck_runs_status",
        ),
        Index("ix_runs_org_created", "org_id", "created_at"),
        Index("ix_runs_open", "status", postgresql_where=text("status NOT IN ('completed', 'failed', 'cancelled')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")

    # The exact versions and seed, so the experiment can be repeated.
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    suite_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)

    concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False)
    repetitions: Mapped[int] = mapped_column(Integer, nullable=False)
    judge_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_executions: Mapped[int] = mapped_column(Integer, nullable=False)

    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    replay_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evaluating_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warning: Mapped[str | None] = mapped_column(Text)

    executions: Mapped[list[Execution]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Execution(Base, TimestampMixin):
    """One scenario repetition. This row, not the broker, is the source of truth."""

    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("run_id", "scenario_id", "repetition", name="uq_executions_run_scenario_repetition"),
        CheckConstraint(
            "status IN ('pending', 'dispatched', 'running', 'retry_wait', "
            "'completed', 'failed', 'timed_out', 'cancelled')",
            name="ck_executions_status",
        ),
        Index("ix_executions_run_status", "run_id", "status"),
        Index("ix_executions_org", "org_id"),
        Index("ix_executions_lease", "lease_expires_at", postgresql_where=text("status = 'running'")),
        Index("ix_executions_retry", "next_attempt_at", postgresql_where=text("status = 'retry_wait'")),
        Index("ix_executions_dispatched", "dispatched_at", postgresql_where=text("status = 'dispatched'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scenario_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario_name: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(48), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    queue_wait_ms: Mapped[float | None] = mapped_column(Float)
    agent_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    trace_id: Mapped[str | None] = mapped_column(String(32))

    # Read model updated from evaluation.completed, for the run results list.
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)

    run: Mapped[Run] = relationship(back_populates="executions")
    steps: Mapped[list[ExecutionStep]] = relationship(
        back_populates="execution", cascade="all, delete-orphan", order_by="ExecutionStep.id"
    )


class ExecutionStep(Base):
    """The recorded transcript, kept per attempt for the trace view."""

    __tablename__ = "execution_steps"
    __table_args__ = (UniqueConstraint("execution_id", "attempt", "seq", name="uq_execution_steps_attempt_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    execution: Mapped[Execution] = relationship(back_populates="steps")
