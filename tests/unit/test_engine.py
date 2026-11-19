"""The execution engine, with the agent replaced by an in-memory HTTP transport."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from run_orchestrator.config import Settings
from run_orchestrator.engine import Outcome, ScenarioRunner, ToolSimulator, estimate_cost

AGENT = {
    "agent_id": str(uuid.uuid4()),
    "version": 1,
    "endpoint_url": "http://agent/run",
    "headers": {"Authorization": "Bearer agent-secret"},
    "request_timeout_seconds": 5,
}


def reply(text: str = "done", **extra: Any) -> dict[str, Any]:
    return {"message": {"content": text}, "tool_calls": [], "task_status": "completed", **extra}


def scenario(*turns: str, tools: list[dict[str, Any]] | None = None, max_agent_calls: int = 5) -> dict[str, Any]:
    return {
        "scenario_id": str(uuid.uuid4()),
        "scenario_type": "tool_use",
        "definition": {
            "system_prompt": "be helpful",
            "turns": [{"role": "user", "content": turn} for turn in turns or ("hello",)],
            "tools": tools or [],
            "context": {"store": "ACME"},
            "max_agent_calls": max_agent_calls,
        },
    }


def runner(handler: Callable[[httpx.Request], httpx.Response], **settings: Any) -> ScenarioRunner:
    config = Settings(agent_endpoint_allowlist="agent", allow_http_endpoints=True, **settings)
    return ScenarioRunner(config, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def execute(run: ScenarioRunner, spec: dict[str, Any], timeout: float = 5, agent: dict | None = None) -> Outcome:
    return await run.run(
        execution_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        seed=7,
        attempt=1,
        agent=agent or AGENT,
        scenario=spec,
        timeout_seconds=timeout,
    )


class TestToolSimulator:
    def test_returns_the_response_matching_the_arguments(self) -> None:
        tools = ToolSimulator(
            [
                {
                    "name": "lookup",
                    "responses": [
                        {"when": {"id": "A"}, "output": "first"},
                        {"when": {"id": "B"}, "output": "second"},
                    ],
                    "default_output": "none",
                }
            ]
        )
        assert tools.invoke("lookup", {"id": "B"}) == ("second", False, False)
        assert tools.invoke("lookup", {"id": "Z"}) == ("none", False, False)

    def test_fails_only_on_the_scripted_calls(self) -> None:
        tools = ToolSimulator([{"name": "pay", "default_output": "ok", "failure": {"on_calls": [2], "error": "boom"}}])
        assert tools.invoke("pay", {})[1] is False
        assert tools.invoke("pay", {}) == ({"error": "boom"}, True, True)
        assert tools.invoke("pay", {})[1] is False

    def test_unknown_tool_is_an_error_not_a_crash(self) -> None:
        output, is_error, injected = ToolSimulator([]).invoke("nope", {})
        assert is_error is True
        assert injected is False
        assert "Unknown tool" in output["error"]


async def test_single_turn_is_recorded_with_usage() -> None:
    body = reply("hi", usage={"input_tokens": 10, "output_tokens": 5})
    outcome = await execute(runner(lambda _: httpx.Response(200, json=body)), scenario("hello"))
    assert outcome.status == "completed"
    assert [step.type for step in outcome.steps] == ["user_message", "agent_response"]
    assert (outcome.input_tokens, outcome.output_tokens, outcome.agent_calls) == (10, 5, 1)


async def test_tool_results_are_fed_back_to_the_agent() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            call = {"id": "c1", "name": "lookup", "arguments": {"id": "A"}}
            return httpx.Response(200, json=reply("", tool_calls=[call], task_status="in_progress"))
        return httpx.Response(200, json=reply("found it"))

    tools = [{"name": "lookup", "responses": [{"when": {"id": "A"}, "output": {"status": "ok"}}]}]
    outcome = await execute(runner(handler), scenario("find A", tools=tools))
    assert outcome.tool_calls == 1
    tool_message = requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "c1"
    assert json.loads(tool_message["content"]) == {"status": "ok"}


async def test_every_user_turn_reaches_the_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][-1]["content"])
        return httpx.Response(200, json=reply())

    outcome = await execute(runner(handler), scenario("first", "second"))
    assert seen == ["first", "second"]
    assert outcome.agent_calls == 2


async def test_agent_credentials_are_sent_to_the_agent() -> None:
    received: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.update(request.headers)
        return httpx.Response(200, json=reply())

    await execute(runner(handler), scenario())
    assert received["authorization"] == "Bearer agent-secret"


async def test_agent_that_never_stops_is_cut_off() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        call = {"id": "c", "name": "loop", "arguments": {}}
        return httpx.Response(200, json=reply("", tool_calls=[call]))

    outcome = await execute(runner(handler), scenario(tools=[{"name": "loop"}], max_agent_calls=3))
    assert outcome.status == "failed"
    assert outcome.error_code == "max_agent_calls_exceeded"
    assert outcome.agent_calls == 3
    assert outcome.retryable is False


@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [(500, "agent_http_5xx", True), (503, "agent_http_5xx", True), (401, "agent_http_error", False)],
)
async def test_http_errors_are_classified(status_code: int, code: str, retryable: bool) -> None:
    outcome = await execute(runner(lambda _: httpx.Response(status_code)), scenario())
    assert outcome.status == "failed"
    assert outcome.error_code == code
    assert outcome.retryable is retryable


async def test_redirects_are_not_followed() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    outcome = await execute(runner(handler), scenario())
    assert calls == 1
    assert outcome.error_code == "agent_http_error"


async def test_invalid_json_is_a_protocol_error() -> None:
    outcome = await execute(runner(lambda _: httpx.Response(200, text="<html>")), scenario())
    assert outcome.error_code == "agent_protocol_error"
    assert outcome.retryable is False


async def test_wrong_shape_is_a_protocol_error() -> None:
    body = {"message": {"role": "user", "content": "hi"}}
    outcome = await execute(runner(lambda _: httpx.Response(200, json=body)), scenario())
    assert outcome.error_code == "agent_protocol_error"


async def test_connection_failure_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    outcome = await execute(runner(handler), scenario())
    assert outcome.error_code == "agent_connection_error"
    assert outcome.retryable is True


async def test_oversized_response_is_rejected() -> None:
    outcome = await execute(
        runner(lambda _: httpx.Response(200, content=b"x" * 5000), max_agent_response_bytes=1000), scenario()
    )
    assert outcome.error_code == "agent_response_too_large"


async def test_long_replies_are_clipped_in_the_transcript() -> None:
    body = reply("x" * 500)
    outcome = await execute(runner(lambda _: httpx.Response(200, json=body), max_step_content_chars=100), scenario())
    assert len(outcome.steps[1].content or "") < 200


async def test_overall_timeout_is_enforced() -> None:
    class Hanging(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(30)
            return httpx.Response(200)

    config = Settings(agent_endpoint_allowlist="agent", allow_http_endpoints=True)
    run = ScenarioRunner(config, httpx.AsyncClient(transport=Hanging()))
    outcome = await execute(run, scenario(), timeout=0.05)
    assert outcome.status == "timed_out"
    assert outcome.error_code == "agent_timeout"
    assert outcome.retryable is True


async def test_private_address_is_refused_before_any_call() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=reply())

    agent = {**AGENT, "endpoint_url": "http://169.254.169.254/latest/meta-data"}
    outcome = await execute(runner(handler), scenario(), agent=agent)
    assert calls == 0
    assert outcome.error_code == "unsafe_endpoint"
    assert outcome.retryable is False


class TestCost:
    def test_reported_cost_wins(self) -> None:
        outcome = Outcome(input_tokens=1000, reported_cost_usd=0.5)
        assert estimate_cost(outcome, {"input_cost_per_1k": 1}) == 0.5

    def test_cost_is_calculated_from_the_price_list(self) -> None:
        outcome = Outcome(input_tokens=2000, output_tokens=1000)
        cost = estimate_cost(outcome, {"input_cost_per_1k": 0.003, "output_cost_per_1k": 0.015})
        assert cost == pytest.approx(0.021)

    def test_unknown_cost_stays_unknown_instead_of_zero(self) -> None:
        assert estimate_cost(Outcome(input_tokens=2000), {}) is None
