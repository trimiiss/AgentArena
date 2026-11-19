"""Event and agent protocol contracts."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from agentarena_common.agent_protocol import AgentRequest, AgentResponse, ChatMessage
from agentarena_common.events import EVENT_MODELS, EvaluationRequested, EventEnvelope, make_event

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "examples" / "sample-agent"))

from export_contracts import CONTRACTS, agent_protocol_schemas, event_schemas  # noqa: E402

from sample_agent.main import Conversation, careful_agent, flawed_agent  # noqa: E402


def stored(directory: str, name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / directory / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", event_schemas())
def test_event_schema_files_match_the_code(name: str) -> None:
    assert stored("events", name) == event_schemas()[name], "Run: python scripts/export_contracts.py"


@pytest.mark.parametrize("name", agent_protocol_schemas())
def test_agent_protocol_files_match_the_code(name: str) -> None:
    assert stored("agent-protocol", name) == agent_protocol_schemas()[name], "Run: python scripts/export_contracts.py"


# Required fields of v1. Consumers in production depend on these, so a change here
# means publishing a v2 of the event rather than editing this list.
FROZEN_V1_FIELDS = {
    "run.requested": {"run_id", "total_executions", "concurrency"},
    "execution.requested": {"run_id", "execution_id", "attempt", "dispatched_at"},
    "execution.started": {"run_id", "execution_id", "attempt", "worker_id", "queue_wait_ms"},
    "execution.completed": {"run_id", "execution_id", "attempt", "latency_ms", "agent_calls", "tool_calls"},
    "execution.failed": {"run_id", "execution_id", "attempt", "status", "error_code", "error_message"},
    "evaluation.requested": {
        "run_id",
        "execution_id",
        "agent_id",
        "agent_version",
        "scenario_id",
        "scenario_version",
        "scenario_name",
        "scenario_type",
        "difficulty",
        "repetition",
        "seed",
        "execution_status",
    },
    "evaluation.completed": {"run_id", "execution_id", "evaluation_id", "passed", "score"},
    "run.completed": {"run_id", "agent_id", "agent_version", "status", "total_executions", "finished_at"},
}


@pytest.mark.parametrize(("event_type", "fields"), FROZEN_V1_FIELDS.items(), ids=FROZEN_V1_FIELDS)
def test_v1_required_fields_are_frozen(event_type: str, fields: set[str]) -> None:
    schema = stored("events", f"{event_type}.v1.schema.json")
    required = set(schema.get("required", []))
    assert fields <= required, f"Removed from {event_type} v1: {fields - required}. Publish a v2 instead."
    added = required - fields
    assert not added, f"New required fields break old producers: {added}. Make them optional."


def test_every_event_type_has_a_frozen_field_list() -> None:
    assert {event_type for event_type, _ in EVENT_MODELS} == set(FROZEN_V1_FIELDS)


def sample_evaluation_request() -> EvaluationRequested:
    return EvaluationRequested(
        run_id=uuid.uuid4(),
        execution_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        agent_version=1,
        scenario_id=uuid.uuid4(),
        scenario_version=1,
        scenario_name="s",
        scenario_type="tool_use",
        difficulty="easy",
        repetition=1,
        seed=1,
        execution_status="completed",
    )


def test_produced_event_validates_against_its_published_schemas() -> None:
    event = make_event("evaluation.requested", sample_evaluation_request(), producer="t", org_id=uuid.uuid4())
    document = json.loads(event.model_dump_json())
    jsonschema.validate(document, stored("events", "envelope.schema.json"))
    jsonschema.validate(document["data"], stored("events", "evaluation.requested.v1.schema.json"))


def test_event_survives_the_trip_through_the_broker() -> None:
    event = make_event("evaluation.requested", sample_evaluation_request(), producer="t", org_id=uuid.uuid4())
    received = EventEnvelope.model_validate(json.loads(event.model_dump_json()))
    assert received.event_id == event.event_id
    assert isinstance(received.payload(), EvaluationRequested)


def test_timestamps_are_timezone_aware() -> None:
    event = make_event("evaluation.requested", sample_evaluation_request(), producer="t", org_id=uuid.uuid4())
    assert event.occurred_at.tzinfo is not None
    assert event.occurred_at <= datetime.now(UTC)


class TestAgentProtocol:
    def request(self, text: str) -> dict[str, Any]:
        document = AgentRequest(session_id="s", turn=1, messages=[ChatMessage(role="user", content=text)]).model_dump(
            mode="json", exclude_none=True
        )
        jsonschema.validate(document, stored("agent-protocol", "request.v1.schema.json"))
        return document

    @pytest.mark.parametrize("agent", [careful_agent, flawed_agent], ids=["v2", "v1"])
    @pytest.mark.parametrize(
        "text", ["Where is my order A1001?", "I want my money back.", "Is SKU-42 in stock?", "Hello"]
    )
    def test_sample_agent_speaks_the_published_protocol(self, agent: Any, text: str) -> None:
        reply = agent(Conversation(self.request(text)["messages"]))
        jsonschema.validate(reply, stored("agent-protocol", "response.v1.schema.json"))
        assert isinstance(AgentResponse.model_validate(reply), AgentResponse)

    def test_minimal_agent_response_is_enough(self) -> None:
        """Integrating must stay easy: only a message is really needed."""
        parsed = AgentResponse.model_validate({"message": {"content": "hello"}})
        assert parsed.task_status == "completed"
        assert parsed.tool_calls == []

    def test_unknown_fields_from_agents_are_tolerated(self) -> None:
        parsed = AgentResponse.model_validate({"message": {"content": "hi"}, "framework_debug": {"x": 1}})
        assert parsed.message.content == "hi"
