"""Request-scoped context carried through logs, HTTP calls and events."""

from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

CORRELATION_HEADER = "X-Correlation-ID"


def new_correlation_id() -> str:
    return uuid4().hex


def get_correlation_id() -> str | None:
    return correlation_id_var.get()


def ensure_correlation_id() -> str:
    value = correlation_id_var.get()
    if value is None:
        value = new_correlation_id()
        correlation_id_var.set(value)
    return value
