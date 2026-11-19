"""Published API contracts, and what each consumer relies on.

The first group proves the published files match the code. The second holds the
fields a consumer actually reads, so removing one breaks a test, not production.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from export_contracts import CONTRACTS, SERVICES, dump_yaml, openapi_document  # noqa: E402


def published(service: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / f"{service}.openapi.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", SERVICES)
def test_published_contract_is_valid_openapi(service: str) -> None:
    validate(published(service))


@pytest.mark.parametrize("service", SERVICES)
def test_published_contract_matches_the_code(service: str) -> None:
    expected = yaml.safe_load(dump_yaml(openapi_document(service)))
    assert published(service) == expected, f"Contract drift. Run: python scripts/export_contracts.py ({service})"


@pytest.mark.parametrize("service", SERVICES)
def test_every_service_documents_its_health_endpoints(service: str) -> None:
    paths = published(service)["paths"]
    assert "/health" in paths
    assert "/ready" in paths


def schema(service: str, name: str) -> dict[str, Any]:
    return published(service)["components"]["schemas"][name]


def properties(service: str, name: str) -> set[str]:
    return set(schema(service, name)["properties"])


class TestRunOrchestratorExpectations:
    """What POST /v1/runs reads from its two synchronous dependencies."""

    def test_agent_registry_provides_what_run_creation_reads(self) -> None:
        assert {"name", "status", "latest_version"} <= properties("agent-registry", "AgentOut")

    def test_agent_status_values_stay_compatible(self) -> None:
        statuses = schema("agent-registry", "AgentOut")["properties"]["status"]["enum"]
        assert "disabled" in statuses

    def test_scenario_service_provides_what_run_creation_reads(self) -> None:
        required = {"scenario_id", "version", "name", "scenario_type", "difficulty"}
        assert required <= properties("scenario-service", "ResolvedScenario")
        assert required <= set(schema("scenario-service", "ResolvedScenario")["required"])

    def test_resolve_endpoints_exist(self) -> None:
        paths = published("scenario-service")["paths"]
        assert "/v1/scenarios/resolve" in paths
        assert "/v1/suites/{suite_id}/resolve" in paths
        assert "/v1/agents/{agent_id}/versions/{version}" in published("agent-registry")["paths"]


class TestScenarioDefinitionExpectations:
    """What the worker and the evaluators read from stored scenarios."""

    def test_worker_can_rely_on_the_definition_fields(self) -> None:
        required = {"system_prompt", "turns", "tools", "context", "max_agent_calls"}
        assert required <= properties("scenario-service", "ScenarioDefinition")

    def test_tool_simulator_can_rely_on_the_tool_fields(self) -> None:
        required = {"name", "responses", "default_output", "failure"}
        assert required <= properties("scenario-service", "ToolDefinition")

    def test_evaluators_can_rely_on_the_expected_outcome_fields(self) -> None:
        required = {
            "task_status",
            "exact_response",
            "reference_answer",
            "required_phrases",
            "forbidden_phrases",
            "response_schema",
            "tool_calls",
            "forbidden_tools",
            "tool_order",
            "business_rules",
            "must_handle_failure",
            "failure_ack_phrases",
        }
        assert required <= properties("scenario-service", "ExpectedOutcome")

    def test_evaluators_can_rely_on_the_criteria_fields(self) -> None:
        required = {
            "pass_threshold",
            "weights",
            "critical_dimensions",
            "latency_budget_ms",
            "token_budget",
            "cost_budget_usd",
            "similarity_threshold",
        }
        assert required <= properties("scenario-service", "EvaluationCriteria")


def test_frontend_login_contract_is_stable() -> None:
    required = {"access_token", "expires_in", "user", "organization", "role"}
    assert required <= properties("identity-service", "SessionResponse")


def test_internal_routes_are_not_in_public_contracts() -> None:
    for service in SERVICES:
        assert not [path for path in published(service)["paths"] if path.startswith("/internal")]


def test_credentials_never_appear_in_response_schemas() -> None:
    for name in ("AgentOut", "CredentialOut", "VersionOut"):
        assert not {"secret", "ciphertext", "password"} & properties("agent-registry", name)
