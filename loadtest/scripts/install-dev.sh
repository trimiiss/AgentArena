#!/usr/bin/env bash
# Installs every Python package in editable mode, with pinned versions.
set -euo pipefail

cd "$(dirname "$0")/.."
pip install -c constraints.txt \
  -e shared \
  -e services/identity-service \
  -e services/agent-registry \
  -e services/scenario-service \
  -e services/run-orchestrator \
  -e services/evaluation-service \
  -e examples/sample-agent \
  pytest pytest-asyncio pytest-cov ruff openapi-spec-validator pyyaml
