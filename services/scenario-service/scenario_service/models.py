from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentarena_common.db import TimestampMixin


class Base(DeclarativeBase):
    pass


class Scenario(Base, TimestampMixin):
    __tablename__ = "scenarios"
    __table_args__ = (
        Index("uq_scenarios_org_name", "org_id", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_scenarios_org_type", "org_id", "scenario_type"),
        Index("ix_scenarios_parent", "parent_scenario_id"),
        CheckConstraint("difficulty IN ('easy', 'medium', 'hard')", name="ck_scenarios_difficulty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scenario_type: Mapped[str] = mapped_column(String(48), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(48)), nullable=False, default=list)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[ScenarioVersion]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="ScenarioVersion.version"
    )


class ScenarioVersion(Base):
    """Immutable, so a past run can always be reproduced exactly."""

    __tablename__ = "scenario_versions"
    __table_args__ = (UniqueConstraint("scenario_id", "version", name="uq_scenario_versions_scenario_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expected: Mapped[dict] = mapped_column(JSONB, nullable=False)
    criteria: Mapped[dict] = mapped_column(JSONB, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    scenario: Mapped[Scenario] = relationship(back_populates="versions")


class Suite(Base, TimestampMixin):
    __tablename__ = "test_suites"
    __table_args__ = (
        Index("uq_suites_org_name", "org_id", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[SuiteScenario]] = relationship(
        back_populates="suite", cascade="all, delete-orphan", order_by="SuiteScenario.position"
    )


class SuiteScenario(Base):
    __tablename__ = "suite_scenarios"
    __table_args__ = (Index("ix_suite_scenarios_scenario", "scenario_id"),)

    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_suites.id", ondelete="CASCADE"), primary_key=True)
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True)
    # When null, the suite follows the scenario's latest version.
    pinned_version: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    suite: Mapped[Suite] = relationship(back_populates="items")
    scenario: Mapped[Scenario] = relationship()


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"
    __table_args__ = (
        Index("uq_datasets_org_name", "org_id", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rows: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
