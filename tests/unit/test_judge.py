"""The LLM judge, through the real Anthropic SDK over a mock HTTP transport.

No request leaves the machine. This proves what is sent and how each kind of
response is handled, but not how a live model behaves.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from evaluation_service.config import Settings
from evaluation_service.judge import JudgeVerdict, LlmJudge

VERDICT = {
    "relevance": 4,
    "reasoning_quality": 3,
    "context_consistency": 5,
    "conversation_quality": 4,
    "error_analysis": "It answered without checking the order.",
}


def reply(text: str, stop_reason: str = "end_turn") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        },
    )


def judge(handler: Callable[[httpx2.Request], httpx2.Response]) -> LlmJudge:
    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return LlmJudge(Settings(), client)


async def review(subject: LlmJudge) -> Any:
    return await subject.review(scenario_name="Refund", expected_summary="Escalate", transcript='[{"seq": 1}]')


async def test_a_good_response_becomes_a_verdict() -> None:
    outcome = await review(judge(lambda _: reply(json.dumps(VERDICT))))
    assert outcome.status == "completed"
    assert isinstance(outcome.verdict, JudgeVerdict)
    assert outcome.verdict.context_consistency == 5


async def test_request_has_the_expected_shape() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return reply(json.dumps(VERDICT))

    await review(judge(handler))
    body = captured["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in captured["headers"]["anthropic-beta"]


async def test_transcript_is_marked_as_untrusted_data() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(json.loads(request.content))
        return reply(json.dumps(VERDICT))

    await review(judge(handler))
    assert "<transcript>" in captured["messages"][0]["content"]
    assert "untrusted" in captured["system"]


async def test_refusal_with_an_empty_body_is_reported_without_a_verdict() -> None:
    """Regression: the SDK raises while parsing, before stop_reason can be read."""
    outcome = await review(judge(lambda _: reply("", "refusal")))
    assert outcome.status == "no_verdict"
    assert outcome.verdict is None


async def test_refusal_after_valid_text_is_still_a_refusal() -> None:
    outcome = await review(judge(lambda _: reply(json.dumps(VERDICT), "refusal")))
    assert outcome.status == "refused"
    assert outcome.verdict is None


async def test_out_of_range_scores_are_rejected() -> None:
    outcome = await review(judge(lambda _: reply(json.dumps({**VERDICT, "relevance": 9}))))
    assert outcome.status == "no_verdict"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "rate_limited"), (500, "error"), (401, "error")],
)
async def test_api_errors_are_reported_and_not_raised(status: int, expected: str) -> None:
    outcome = await review(judge(lambda _: httpx2.Response(status, json={"type": "error", "error": {}})))
    assert outcome.status == expected


async def test_unreachable_api_is_reported() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    outcome = await review(judge(handler))
    assert outcome.status == "unreachable"
