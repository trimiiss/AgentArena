from __future__ import annotations

import random

import httpx
import pytest

from agentarena_common.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    DownstreamError,
    ServiceClient,
    backoff_delay,
    retry_async,
)
from agentarena_common.settings import CommonSettings


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_breaker(clock: FakeClock, threshold: int = 3) -> CircuitBreaker:
    return CircuitBreaker("test", failure_threshold=threshold, recovery_seconds=10, clock=clock)


def test_backoff_never_exceeds_the_exponential_ceiling() -> None:
    rng = random.Random(1)
    for attempt, ceiling in ((1, 0.1), (2, 0.2), (3, 0.4)):
        assert all(0 <= backoff_delay(attempt, 0.1, 5.0, rng) <= ceiling for _ in range(200))


def test_backoff_is_capped() -> None:
    rng = random.Random(1)
    assert all(backoff_delay(20, 0.1, 2.0, rng) <= 2.0 for _ in range(200))


def test_backoff_has_jitter() -> None:
    rng = random.Random(1)
    assert len({backoff_delay(3, 1.0, 30.0, rng) for _ in range(20)}) > 1


def test_breaker_opens_after_consecutive_failures() -> None:
    breaker = make_breaker(FakeClock())
    for _ in range(3):
        breaker.before_call()
        breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_success_resets_the_failure_count() -> None:
    breaker = make_breaker(FakeClock())
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED


def test_breaker_allows_one_probe_after_recovery_time() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1)
    breaker.record_failure()
    clock.now = 11
    breaker.before_call()
    assert breaker.state is CircuitState.HALF_OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_successful_probe_closes_the_breaker() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1)
    breaker.record_failure()
    clock.now = 11
    breaker.before_call()
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    breaker.before_call()


def test_failed_probe_reopens_for_another_full_period() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1)
    breaker.record_failure()
    clock.now = 11
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    clock.now = 15
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


async def test_retry_returns_after_transient_failures() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ValueError("transient")
        return "ok"

    assert await retry_async(operation, attempts=3, base=0, cap=0, retry_on=(ValueError,)) == "ok"
    assert calls == 3


async def test_retry_gives_up_after_the_limit() -> None:
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("always")

    with pytest.raises(ValueError):
        await retry_async(operation, attempts=3, base=0, cap=0, retry_on=(ValueError,))
    assert calls == 3


async def test_retry_ignores_unrelated_errors() -> None:
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise KeyError("bug")

    with pytest.raises(KeyError):
        await retry_async(operation, attempts=3, base=0, cap=0, retry_on=(ValueError,))
    assert calls == 1


def client_with(handler) -> ServiceClient:
    settings = CommonSettings(
        http_retry_attempts=3,
        http_retry_base_seconds=0,
        http_retry_max_seconds=0,
        breaker_failure_threshold=2,
    )
    return ServiceClient("dep", "http://dep", settings, transport=httpx.MockTransport(handler))


async def test_client_retries_reads_on_server_errors() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={})

    assert (await client_with(handler).get("/x")).status_code == 200
    assert calls == 2


async def test_client_does_not_retry_writes() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with pytest.raises(DownstreamError):
        await client_with(handler).post("/x")
    assert calls == 1


async def test_client_errors_do_not_trip_the_breaker() -> None:
    client = client_with(lambda _: httpx.Response(404))
    for _ in range(10):
        assert (await client.get("/x")).status_code == 404
    assert client.breaker.state is CircuitState.CLOSED


async def test_open_breaker_stops_calling_the_dependency() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = client_with(handler)
    with pytest.raises((DownstreamError, CircuitOpenError)):
        await client.get("/x")
    calls_before = calls
    with pytest.raises(CircuitOpenError):
        await client.get("/x")
    assert calls == calls_before
