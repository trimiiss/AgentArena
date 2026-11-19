"""Drives one agent through one scenario over the Agent Protocol.

Agents stay outside AgentArena and are only reached through this HTTP client.
Nothing here runs customer code. Tools are simulated from the scenario, which
makes tool failures scriptable and a run repeatable.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import ValidationError

from agentarena_common.agent_protocol import (
    AgentRequest,
    AgentResponse,
    ChatMessage,
    ToolCall,
    ToolSpec,
)
from agentarena_common.events import TranscriptStep
from agentarena_common.ssrf import UnsafeEndpointError, assert_safe_endpoint, parse_allowlist
from agentarena_common.telemetry import get_tracer
from run_orchestrator.config import Settings
from run_orchestrator.metrics import AGENT_CALL_DURATION, AGENT_CALLS

tracer = get_tracer(__name__)

_TRUNCATED = "...[truncated]"


class AgentFailure(Exception):
    """The agent could not complete the interaction."""

    def __init__(self, code: str, message: str, *, retryable: bool, status: str = "failed") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


@dataclass
class Outcome:
    status: str = "completed"
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    steps: list[TranscriptStep] = field(default_factory=list)
    latency_ms: float = 0.0
    agent_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost_usd: float | None = None
    cost_usd: float | None = None


class ToolSimulator:
    """Answers tool calls from the scenario, counting calls to script failures."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = {tool["name"]: tool for tool in tools}
        self.calls: dict[str, int] = {}

    def invoke(self, name: str, arguments: dict[str, Any]) -> tuple[Any, bool, bool]:
        """Return (output, is_error, injected_failure)."""
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"Unknown tool: {name}"}, True, False
        self.calls[name] = self.calls.get(name, 0) + 1
        failure = tool.get("failure") or {}
        if self.calls[name] in (failure.get("on_calls") or []):
            return {"error": failure.get("error", "Tool failed")}, True, True
        for response in tool.get("responses") or []:
            expected = response.get("when") or {}
            if all(arguments.get(key) == value for key, value in expected.items()):
                return response.get("output"), False, False
        return tool.get("default_output"), False, False


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + _TRUNCATED


def estimate_cost(outcome: Outcome, agent: dict[str, Any]) -> float | None:
    """Prefer what the agent reports, otherwise use the version's price list."""
    if outcome.reported_cost_usd is not None:
        return outcome.reported_cost_usd
    input_price = agent.get("input_cost_per_1k")
    output_price = agent.get("output_cost_per_1k")
    if input_price is None and output_price is None:
        return None
    return (outcome.input_tokens / 1000) * (input_price or 0) + (outcome.output_tokens / 1000) * (output_price or 0)


class ScenarioRunner:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self.allowlist = parse_allowlist(settings.agent_endpoint_allowlist)

    async def run(
        self,
        *,
        execution_id: UUID,
        run_id: UUID,
        seed: int,
        attempt: int,
        agent: dict[str, Any],
        scenario: dict[str, Any],
        timeout_seconds: float,
    ) -> Outcome:
        outcome = Outcome()
        try:
            async with asyncio.timeout(timeout_seconds):
                await self._converse(outcome, execution_id, run_id, seed, attempt, agent, scenario)
        except TimeoutError:
            self._fail(
                outcome,
                AgentFailure(
                    "agent_timeout",
                    f"Execution exceeded {timeout_seconds:g}s",
                    retryable=True,
                    status="timed_out",
                ),
            )
        except AgentFailure as failure:
            self._fail(outcome, failure)
        outcome.cost_usd = estimate_cost(outcome, agent)
        return outcome

    @staticmethod
    def _fail(outcome: Outcome, failure: AgentFailure) -> None:
        outcome.status = failure.status
        outcome.error_code = failure.code
        outcome.error_message = failure.message
        outcome.retryable = failure.retryable
        outcome.steps.append(
            TranscriptStep(seq=len(outcome.steps) + 1, type="error", content=failure.message, name=failure.code)
        )

    def _record(self, outcome: Outcome, **fields: Any) -> None:
        outcome.steps.append(TranscriptStep(seq=len(outcome.steps) + 1, **fields))

    async def _converse(
        self,
        outcome: Outcome,
        execution_id: UUID,
        run_id: UUID,
        seed: int,
        attempt: int,
        agent: dict[str, Any],
        scenario: dict[str, Any],
    ) -> None:
        definition = scenario["definition"]
        tools = ToolSimulator(definition.get("tools") or [])
        tool_specs = [
            ToolSpec(name=t["name"], description=t.get("description", ""), parameters=t.get("parameters") or {})
            for t in definition.get("tools") or []
        ]
        max_calls = definition.get("max_agent_calls", 10)
        messages: list[ChatMessage] = []
        if definition.get("system_prompt"):
            messages.append(ChatMessage(role="system", content=definition["system_prompt"]))

        for turn_number, turn in enumerate(definition["turns"], start=1):
            messages.append(ChatMessage(role="user", content=turn["content"]))
            self._record(outcome, type="user_message", content=turn["content"])
            while True:
                if outcome.agent_calls >= max_calls:
                    raise AgentFailure(
                        "max_agent_calls_exceeded",
                        f"Agent did not finish within {max_calls} calls",
                        retryable=False,
                    )
                request = AgentRequest(
                    session_id=str(execution_id),
                    turn=turn_number,
                    messages=messages,
                    tools=tool_specs,
                    context=definition.get("context") or {},
                    metadata={"run_id": str(run_id), "seed": seed, "attempt": attempt},
                )
                response, latency_ms = await self._call_agent(agent, request, scenario)
                outcome.agent_calls += 1
                outcome.latency_ms += latency_ms
                if response.usage is not None:
                    outcome.input_tokens += response.usage.input_tokens
                    outcome.output_tokens += response.usage.output_tokens
                    if response.usage.cost_usd is not None:
                        outcome.reported_cost_usd = (outcome.reported_cost_usd or 0) + response.usage.cost_usd

                content = _clip(response.message.content, self.settings.max_step_content_chars)
                self._record(
                    outcome,
                    type="agent_response",
                    content=content,
                    latency_ms=round(latency_ms, 2),
                    task_status=response.task_status,
                    structured_output=response.structured_output,
                )
                messages.append(ChatMessage(role="assistant", content=content, tool_calls=response.tool_calls or None))
                if not response.tool_calls:
                    break
                self._run_tools(outcome, tools, response.tool_calls, messages)

    def _run_tools(
        self,
        outcome: Outcome,
        tools: ToolSimulator,
        calls: list[ToolCall],
        messages: list[ChatMessage],
    ) -> None:
        for call in calls:
            outcome.tool_calls += 1
            self._record(outcome, type="tool_call", name=call.name, arguments=call.arguments)
            output, is_error, injected = tools.invoke(call.name, call.arguments)
            self._record(
                outcome,
                type="tool_result",
                name=call.name,
                output=output,
                is_error=is_error,
                injected_failure=injected,
            )
            messages.append(
                ChatMessage(role="tool", name=call.name, tool_call_id=call.id, content=json.dumps(output, default=str))
            )

    async def _call_agent(
        self, agent: dict[str, Any], request: AgentRequest, scenario: dict[str, Any]
    ) -> tuple[AgentResponse, float]:
        url = agent["endpoint_url"]
        service = self.settings.service_name
        try:
            # Checked before every call, because DNS can change after registration.
            await assert_safe_endpoint(url, allowlist=self.allowlist, allow_http=self.settings.allow_http_endpoints)
        except UnsafeEndpointError as exc:
            raise AgentFailure("unsafe_endpoint", str(exc), retryable=False) from exc

        with tracer.start_as_current_span(
            "agent.call",
            kind=SpanKind.CLIENT,
            attributes={
                "agent.id": str(agent["agent_id"]),
                "agent.version": agent["version"],
                "scenario.id": str(scenario.get("scenario_id", "")),
                "scenario.type": scenario.get("scenario_type", ""),
                "agent.turn": request.turn,
            },
        ) as span:
            started = time.perf_counter()
            try:
                body = await self._post(url, request, agent)
                latency_ms = (time.perf_counter() - started) * 1000
                parsed = self._parse(body)
            except AgentFailure as failure:
                AGENT_CALLS.labels(service, failure.code).inc()
                span.set_status(Status(StatusCode.ERROR, failure.code))
                raise
            finally:
                AGENT_CALL_DURATION.labels(service).observe(time.perf_counter() - started)
            AGENT_CALLS.labels(service, "ok").inc()
            return parsed, latency_ms

    async def _post(self, url: str, request: AgentRequest, agent: dict[str, Any]) -> bytes:
        """Redirects are not followed and the body size is capped, so a hostile
        endpoint can neither bounce the worker elsewhere nor exhaust its memory."""
        limit = self.settings.max_agent_response_bytes
        try:
            async with self.client.stream(
                "POST",
                url,
                json=request.model_dump(mode="json", exclude_none=True),
                headers=agent.get("headers") or {},
                timeout=agent.get("request_timeout_seconds", 30),
                follow_redirects=False,
            ) as response:
                if response.status_code >= 500:
                    raise AgentFailure("agent_http_5xx", f"Agent returned HTTP {response.status_code}", retryable=True)
                if response.status_code != 200:
                    raise AgentFailure(
                        "agent_http_error", f"Agent returned HTTP {response.status_code}", retryable=False
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise AgentFailure(
                            "agent_response_too_large", f"Response exceeded {limit} bytes", retryable=False
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise AgentFailure("agent_timeout", "Agent request timed out", retryable=True, status="timed_out") from exc
        except httpx.HTTPError as exc:
            raise AgentFailure(
                "agent_connection_error", f"Could not reach agent: {type(exc).__name__}", retryable=True
            ) from exc

    @staticmethod
    def _parse(body: bytes) -> AgentResponse:
        try:
            return AgentResponse.model_validate(json.loads(body))
        except (ValueError, ValidationError) as exc:
            raise AgentFailure(
                "agent_protocol_error", f"Response violates the agent protocol: {str(exc)[:300]}", retryable=False
            ) from exc
