"""Writes the published contracts under contracts/ from the code that implements them.

Run after changing an API, an event or the agent protocol, then review the diff:
that diff is the contract change. tests/contract/ fails if this was forgotten.

    python scripts/export_contracts.py
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"

SERVICES = {
    "identity-service": "identity_service.main",
    "agent-registry": "agent_registry.main",
    "scenario-service": "scenario_service.main",
    "run-orchestrator": "run_orchestrator.main",
    "evaluation-service": "evaluation_service.main",
}


def prepare_environment() -> None:
    """Importing an app needs settings, but nothing here connects to anything."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://contract:contract@localhost/contract")
    os.environ.setdefault("OTEL_ENABLED", "false")
    os.environ.setdefault("CREDENTIALS_ENCRYPTION_KEY", "Y29udHJhY3QtZXhwb3J0LW9ubHktMDAwMDAwMDAwMDA=")
    os.environ.setdefault("IDENTITY_KEY_ENCRYPTION_KEY", "Y29udHJhY3QtZXhwb3J0LW9ubHktMDAwMDAwMDAwMDA=")


def openapi_document(service: str) -> dict[str, Any]:
    prepare_environment()
    return importlib.import_module(SERVICES[service]).app.openapi()


def event_schemas() -> dict[str, dict[str, Any]]:
    from agentarena_common.events import EVENT_MODELS, EventEnvelope

    schemas = {"envelope.schema.json": EventEnvelope.model_json_schema()}
    for (event_type, version), model in EVENT_MODELS.items():
        schemas[f"{event_type}.v{version}.schema.json"] = model.model_json_schema()
    return schemas


def agent_protocol_schemas() -> dict[str, dict[str, Any]]:
    from agentarena_common.agent_protocol import AgentRequest, AgentResponse

    return {
        "request.v1.schema.json": AgentRequest.model_json_schema(),
        "response.v1.schema.json": AgentResponse.model_json_schema(),
    }


def dump_yaml(document: dict[str, Any]) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=120)


def dump_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> None:
    for service in SERVICES:
        path = CONTRACTS / f"{service}.openapi.yaml"
        path.write_text(dump_yaml(openapi_document(service)), encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)}")
    for directory, schemas in (("events", event_schemas()), ("agent-protocol", agent_protocol_schemas())):
        target = CONTRACTS / directory
        target.mkdir(parents=True, exist_ok=True)
        for name, schema in schemas.items():
            (target / name).write_text(dump_json(schema), encoding="utf-8", newline="\n")
            print(f"wrote {(target / name).relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
