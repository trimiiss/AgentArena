from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentarena_common.db import TimestampMixin


class Base(DeclarativeBase):
    pass


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'unreachable', 'disabled')", name="ck_agents_status"),
        # Names are unique per organization, and a deleted agent frees its name.
        Index("uq_agents_org_name", "org_id", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_agents_org_created", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[AgentVersion]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", order_by="AgentVersion.version"
    )
    credential: Mapped[AgentCredential | None] = relationship(
        back_populates="agent", cascade="all, delete-orphan", uselist=False
    )


class AgentVersion(Base):
    """Immutable, so a benchmark can always name exactly what it tested."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        CheckConstraint("version >= 1", name="ck_agent_versions_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    integration_type: Mapped[str] = mapped_column(String(32), nullable=False, default="http_v1")
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # Non-secret headers only. Secrets belong in the credential row.
    default_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model_name: Mapped[str | None] = mapped_column(String(120))
    input_cost_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_cost_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="versions")


class AgentCredential(Base, TimestampMixin):
    """Write-only from the API. Stored encrypted, bound to its organization and agent."""

    __tablename__ = "agent_credentials"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_agent_credentials_agent"),
        CheckConstraint("auth_type IN ('bearer', 'api_key')", name="ck_agent_credentials_auth_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False)
    header_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Authorization")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="credential")
