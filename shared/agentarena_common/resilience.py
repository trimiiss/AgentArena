"""Timeouts, retries with jitter and circuit breaking for service-to-service calls."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import Any

import httpx

from agentarena_common.context import CORRELATION_HEADER, get_correlation_id
from agentarena_common.metrics import CIRCUIT_STATE, DOWNSTREAM_LATENCY, DOWNSTREAM_REQUESTS
from agentarena_common.settings import CommonSettings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {502, 503, 504}


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter. ``attempt`` starts at 1."""
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    return (rng or random).uniform(0, ceiling)


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base: float,
    cap: float,
    retry_on: tuple[type[BaseException], ...],
) -> T:
    """Run ``operation`` up to ``attempts`` times, sleeping between failures."""
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except retry_on:
            if attempt == attempts:
                raise
            await asyncio.sleep(backoff_delay(attempt, base, cap))
    raise AssertionError("unreachable")  # pragma: no cover


class CircuitState(IntEnum):
    CLOSED = 0
    HALF_OPEN = 1
    OPEN = 2


class CircuitOpenError(Exception):
    """Raised without calling the dependency while the breaker is open."""

    def __init__(self, target: str) -> None:
        super().__init__(f"circuit open for {target}")
        self.target = target


class CircuitBreaker:
    """Consecutive-failure breaker with a single half-open probe.

    Safe for use from one event loop. Each process keeps its own view of a
    dependency, which is what a client-side breaker should do.
    """

    def __init__(
        self,
        target: str,
        *,
        service: str = "unknown",
        failure_threshold: int = 5,
        recovery_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.target = target
        self.service = service
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at = 0.0
        self._state = CircuitState.CLOSED
        self._probe_in_flight = False
        self._publish()

    @property
    def state(self) -> CircuitState:
        return self._state

    def _publish(self) -> None:
        CIRCUIT_STATE.labels(self.service, self.target).set(int(self._state))

    def _transition(self, state: CircuitState) -> None:
        if state is not self._state:
            logger.warning(
                "circuit state changed",
                extra={"target": self.target, "from_state": self._state.name, "to_state": state.name},
            )
        self._state = state
        self._publish()

    def before_call(self) -> None:
        """Raise CircuitOpenError unless this call is allowed through."""
        if self._state is CircuitState.OPEN:
            if self._clock() - self._opened_at < self.recovery_seconds:
                raise CircuitOpenError(self.target)
            self._transition(CircuitState.HALF_OPEN)
            self._probe_in_flight = False
        if self._state is CircuitState.HALF_OPEN:
            if self._probe_in_flight:
                raise CircuitOpenError(self.target)
            self._probe_in_flight = True

    def record_success(self) -> None:
        self._failures = 0
        self._probe_in_flight = False
        self._transition(CircuitState.CLOSED)

    def record_failure(self) -> None:
        self._failures += 1
        self._probe_in_flight = False
        if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
            self._transition(CircuitState.OPEN)


class DownstreamError(Exception):
    """A dependency failed in a way the caller cannot fix by changing its input."""

    def __init__(self, target: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{target}: {message}")
        self.target = target
        self.status_code = status_code


class ServiceClient:
    """HTTP client for one downstream service.

    Every call has a timeout. Reads are retried with backoff and jitter because
    they are idempotent. Writes are not retried here: callers must make them
    idempotent first. 4xx responses are returned to the caller and do not count
    against the breaker, since they say nothing about the dependency's health.
    """

    def __init__(
        self,
        target: str,
        base_url: str,
        settings: CommonSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.target = target
        self.service = settings.service_name
        self.settings = settings
        self.breaker = CircuitBreaker(
            target,
            service=settings.service_name,
            failure_threshold=settings.breaker_failure_threshold,
            recovery_seconds=settings.breaker_recovery_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            transport=transport,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers = dict(extra or {})
        correlation_id = get_correlation_id()
        if correlation_id:
            headers[CORRELATION_HEADER] = correlation_id
        return headers

    async def _send_once(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self.breaker.before_call()
        started = time.perf_counter()
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            self.breaker.record_failure()
            DOWNSTREAM_REQUESTS.labels(self.service, self.target, "transport_error").inc()
            raise DownstreamError(self.target, f"{type(exc).__name__}: {exc}") from exc
        finally:
            DOWNSTREAM_LATENCY.labels(self.service, self.target).observe(time.perf_counter() - started)
        if response.status_code >= 500:
            self.breaker.record_failure()
            DOWNSTREAM_REQUESTS.labels(self.service, self.target, "server_error").inc()
            raise DownstreamError(self.target, f"status {response.status_code}", response.status_code)
        self.breaker.record_success()
        DOWNSTREAM_REQUESTS.labels(self.service, self.target, "ok").inc()
        return response

    async def get(self, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        merged = self._headers(headers)

        async def operation() -> httpx.Response:
            return await self._send_once("GET", path, headers=merged, **kwargs)

        return await retry_async(
            operation,
            attempts=self.settings.http_retry_attempts,
            base=self.settings.http_retry_base_seconds,
            cap=self.settings.http_retry_max_seconds,
            retry_on=(DownstreamError,),
        )

    async def post(self, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        return await self._send_once("POST", path, headers=self._headers(headers), **kwargs)
