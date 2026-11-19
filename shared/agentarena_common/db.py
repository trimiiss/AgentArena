"""Async SQLAlchemy engine, session handling and shared table mixins."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import BigInteger, DateTime, Identity, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from agentarena_common.settings import CommonSettings


def utcnow() -> datetime:
    return datetime.now(UTC)


class Database:
    """Owns one engine and session factory for a single service database."""

    def __init__(self, settings: CommonSettings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_timeout=10,
            pool_recycle=1800,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back on error."""
    database: Database = request.app.state.database
    async with database.sessions() as session:
        yield session


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OutboxMixin:
    """Events written in the same transaction as the state change they describe."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    routing_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)

    @declared_attr.directive
    def __table_args__(cls) -> tuple:  # noqa: N805
        # A partial index keeps the relay's poll cheap however large the table grows.
        return (
            Index(
                "ix_outbox_unpublished",
                "id",
                postgresql_where=text("published_at IS NULL"),
            ),
        )


class ProcessedMessageMixin:
    """Deduplication record for idempotent consumers."""

    __tablename__ = "processed_messages"

    consumer: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
