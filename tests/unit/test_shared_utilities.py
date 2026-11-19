from __future__ import annotations

import json
import logging
import uuid

import pytest
from fastapi import HTTPException

from agentarena_common.auth import LocalKeys
from agentarena_common.cache import AsyncTTLCache
from agentarena_common.events import (
    EVENT_MODELS,
    EventEnvelope,
    ExecutionFailed,
    RunRequested,
    UnknownEventError,
    make_event,
)
from agentarena_common.logging import JsonFormatter, redact, redact_text
from agentarena_common.ratelimit import TokenBucketLimiter
from agentarena_common.ssrf import (
    UnsafeEndpointError,
    assert_safe_endpoint,
    check_url_shape,
    is_public_address,
    parse_allowlist,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestLocalKeys:
    async def test_known_key_is_returned_without_any_network_call(self) -> None:
        assert await LocalKeys({"k1": "public-key"}).get_key("k1") == "public-key"

    async def test_unknown_key_is_rejected(self) -> None:
        with pytest.raises(HTTPException) as caught:
            await LocalKeys({}).get_key("other")
        assert caught.value.status_code == 401


class TestRateLimiter:
    def test_allows_a_burst_then_rejects(self) -> None:
        limiter = TokenBucketLimiter(1, 3, clock=Clock())
        assert [limiter.check("org") for _ in range(3)] == [None, None, None]
        assert limiter.check("org") is not None

    def test_tokens_refill_over_time(self) -> None:
        clock = Clock()
        limiter = TokenBucketLimiter(2, 1, clock=clock)
        limiter.check("org")
        assert limiter.check("org") is not None
        clock.now = 1
        assert limiter.check("org") is None

    def test_organizations_do_not_share_a_bucket(self) -> None:
        limiter = TokenBucketLimiter(1, 1, clock=Clock())
        limiter.check("first")
        assert limiter.check("second") is None

    def test_memory_is_bounded(self) -> None:
        limiter = TokenBucketLimiter(1, 1, max_keys=2, clock=Clock())
        for key in ("a", "b", "c"):
            limiter.check(key)
        assert len(limiter._buckets) == 2


class TestSsrfGuard:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.5",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "0.0.0.0",
            "::1",
            "fd00::1",
            "fe80::1",
        ],
    )
    def test_private_addresses_are_rejected(self, address: str) -> None:
        assert is_public_address(address) is False

    def test_mapped_ipv4_cannot_hide_a_private_address(self) -> None:
        assert is_public_address("::ffff:169.254.169.254") is False

    def test_public_address_is_allowed(self) -> None:
        assert is_public_address("8.8.8.8") is True

    @pytest.mark.parametrize(
        "url", ["ftp://example.com/a", "file:///etc/passwd", "http://user:pw@example.com/", "https://"]
    )
    def test_bad_urls_are_rejected(self, url: str) -> None:
        with pytest.raises(UnsafeEndpointError):
            check_url_shape(url, allow_http=True)

    def test_plain_http_needs_opting_in(self) -> None:
        with pytest.raises(UnsafeEndpointError):
            check_url_shape("http://example.com/", allow_http=False)

    async def test_metadata_address_is_blocked_end_to_end(self) -> None:
        with pytest.raises(UnsafeEndpointError):
            await assert_safe_endpoint("http://169.254.169.254/latest", allowlist=frozenset(), allow_http=True)

    async def test_localhost_is_blocked(self) -> None:
        with pytest.raises(UnsafeEndpointError):
            await assert_safe_endpoint("http://localhost:8000/", allowlist=frozenset(), allow_http=True)

    async def test_allowlisted_host_skips_the_address_check(self) -> None:
        await assert_safe_endpoint(
            "http://sample-agent:8000/x", allowlist=parse_allowlist("sample-agent"), allow_http=True
        )

    def test_allowlist_parsing_ignores_case_and_blanks(self) -> None:
        assert parse_allowlist(" A.example , ,b ") == frozenset({"a.example", "b"})


class TestRedaction:
    def test_sensitive_keys_are_masked_at_any_depth(self) -> None:
        value = redact({"user": "a", "nested": {"password": "x", "api_key": "y"}, "list": [{"token": "z"}]})
        assert value == {"user": "a", "nested": {"password": "***", "api_key": "***"}, "list": [{"token": "***"}]}

    def test_bearer_tokens_are_removed_from_text(self) -> None:
        assert "abc.def" not in redact_text("Authorization: Bearer abc.def")

    def test_passwords_in_connection_strings_are_removed(self) -> None:
        text = redact_text("failed: postgresql://svc:hunter2@db:5432/x")
        assert "hunter2" not in text
        assert "svc" in text

    def test_log_lines_are_json_and_carry_redacted_extras(self) -> None:
        record = logging.makeLogRecord(
            {"name": "x", "levelname": "INFO", "msg": "hello", "token": "secret", "agent": "a1"}
        )
        line = json.loads(JsonFormatter("svc").format(record))
        assert line["service"] == "svc"
        assert line["token"] == "***"
        assert line["agent"] == "a1"


class TestCache:
    async def test_value_is_loaded_once_within_the_ttl(self) -> None:
        clock = Clock()
        cache: AsyncTTLCache[int] = AsyncTTLCache(ttl_seconds=10, clock=clock)
        calls = 0

        async def load() -> int:
            nonlocal calls
            calls += 1
            return calls

        assert await cache.get_or_load("k", load) == 1
        assert await cache.get_or_load("k", load) == 1
        clock.now = 11
        assert await cache.get_or_load("k", load) == 2

    async def test_every_lookup_is_reported(self) -> None:
        results: list[bool] = []
        cache: AsyncTTLCache[int] = AsyncTTLCache(ttl_seconds=10, on_lookup=results.append)

        async def load() -> int:
            return 1

        await cache.get_or_load("k", load)
        await cache.get_or_load("k", load)
        assert results == [False, True]

    async def test_failed_loads_are_not_cached(self) -> None:
        cache: AsyncTTLCache[int] = AsyncTTLCache(ttl_seconds=10)

        async def fail() -> int:
            raise ValueError("down")

        async def succeed() -> int:
            return 1

        with pytest.raises(ValueError):
            await cache.get_or_load("k", fail)
        assert await cache.get_or_load("k", succeed) == 1

    async def test_least_recently_used_entry_is_evicted(self) -> None:
        cache: AsyncTTLCache[str] = AsyncTTLCache(ttl_seconds=10, max_entries=2)

        def loader(value: str):
            async def load() -> str:
                return value

            return load

        for key in ("a", "b", "c"):
            await cache.get_or_load(key, loader(key))
        assert "a" not in cache._entries


class TestEvents:
    def test_make_event_wraps_payload_with_metadata(self) -> None:
        org = uuid.uuid4()
        event = make_event(
            "run.requested",
            RunRequested(run_id=uuid.uuid4(), total_executions=1, concurrency=1),
            producer="test",
            org_id=org,
            correlation_id="abc",
        )
        assert event.org_id == org
        assert event.correlation_id == "abc"
        assert isinstance(event.payload(), RunRequested)

    def test_payload_must_match_the_declared_type(self) -> None:
        with pytest.raises(UnknownEventError):
            make_event(
                "execution.failed",
                RunRequested(run_id=uuid.uuid4(), total_executions=1, concurrency=1),
                producer="t",
                org_id=uuid.uuid4(),
            )

    def test_unknown_version_is_rejected(self) -> None:
        envelope = EventEnvelope(
            event_type="run.requested",
            event_version=99,
            producer="t",
            org_id=uuid.uuid4(),
            correlation_id="c",
            data={},
        )
        with pytest.raises(UnknownEventError):
            envelope.payload()

    def test_unknown_payload_fields_are_ignored(self) -> None:
        """Lets a producer add an optional field without breaking older consumers."""
        data = {
            "run_id": str(uuid.uuid4()),
            "execution_id": str(uuid.uuid4()),
            "attempt": 1,
            "status": "failed",
            "error_code": "x",
            "error_message": "y",
            "field_from_the_future": True,
        }
        assert isinstance(ExecutionFailed.model_validate(data), ExecutionFailed)

    def test_every_required_event_type_has_a_contract(self) -> None:
        required = {
            "run.requested",
            "execution.started",
            "execution.completed",
            "execution.failed",
            "evaluation.requested",
            "evaluation.completed",
        }
        assert required <= {event_type for event_type, _ in EVENT_MODELS}
