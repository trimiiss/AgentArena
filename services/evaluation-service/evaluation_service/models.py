from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentarena_common.db import OutboxMixin, ProcessedMessageMixin


class Base(DeclarativeBase):
    pass


class Outbox(Base, OutboxMixin):
    pass


class ProcessedMessage(Base, ProcessedMessageMixin):
    pass


class Evaluation(Base):
    """One scored execution. This is the evaluation history, and it is never rewritten."""

    __tablename__ = "evaluations"
    __table_args__ = (
        Index("ix_evaluations_run", "run_id"),
        Index("ix_evaluations_org_created", "org_id", "created_at"),
        Index("ix_evaluations_org_agent", "org_id", "agent_id", "agent_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # One evaluation per execution, enforced here as well as by the consumer.
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    agent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario_name: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(48), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)

    execution_status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    failure_categories: Mapped[list[str]] = mapped_column(ARRAY(String(48)), nullable=False, default=list)
    evaluator_version: Mapped[str] = mapped_column(String(16), nullable=False)

    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    queue_wait_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    agent_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    checks: Mapped[list[EvaluatorResult]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", order_by="EvaluatorResult.evaluator"
    )
    judgement: Mapped[JudgeResult | None] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", uselist=False
    )


class EvaluatorResult(Base):
    __tablename__ = "evaluator_results"
    __table_args__ = (Index("ix_evaluator_results_evaluation", "evaluation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), nullable=False)
    evaluator: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    evaluation: Mapped[Evaluation] = relationship(back_populates="checks")


class JudgeResult(Base):
    """Qualitative and advisory. Stored apart from the scores on purpose."""

    __tablename__ = "judge_results"

    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    relevance: Mapped[int | None] = mapped_column(Integer)
    reasoning_quality: Mapped[int | None] = mapped_column(Integer)
    context_consistency: Mapped[int | None] = mapped_column(Integer)
    conversation_quality: Mapped[int | None] = mapped_column(Integer)
    error_analysis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    evaluation: Mapped[Evaluation] = relationship(back_populates="judgement")


class Benchmark(Base):
    """The finished result of one run, stored so reports stay stable over time."""

    __tablename__ = "benchmarks"
    __table_args__ = (
        Index("ix_benchmarks_org_created", "org_id", "created_at"),
        Index("ix_benchmarks_org_agent", "org_id", "agent_id", "agent_version"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    agent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_status: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    pass_rate: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
