#!/usr/bin/env bash
# Generates an Alembic migration for one service against the local Compose database.
# Usage: scripts/new-migration.sh <service> "<message>"
set -euo pipefail

service=${1:?usage: new-migration.sh <service> <message>}
message=${2:?usage: new-migration.sh <service> <message>}

case "$service" in
  identity-service) db=identity_db; role=identity_svc; var=IDENTITY_DB_PASSWORD ;;
  agent-registry) db=agents_db; role=agents_svc; var=AGENTS_DB_PASSWORD ;;
  scenario-service) db=scenarios_db; role=scenarios_svc; var=SCENARIOS_DB_PASSWORD ;;
  run-orchestrator) db=runs_db; role=runs_svc; var=RUNS_DB_PASSWORD ;;
  evaluation-service) db=evaluations_db; role=evaluations_svc; var=EVALUATIONS_DB_PASSWORD ;;
  *) echo "Unknown service: $service" >&2; exit 1 ;;
esac

root=$(cd "$(dirname "$0")/.." && pwd)
password=$(grep -E "^${var}=" "$root/.env" | cut -d= -f2-)
python="$root/.venv/Scripts/python.exe"
[ -x "$python" ] || python="$root/.venv/bin/python"

cd "$root/services/$service"
export DATABASE_URL="postgresql+asyncpg://${role}:${password}@127.0.0.1:5432/${db}"
"$python" -m alembic upgrade head
"$python" -m alembic revision --autogenerate -m "$message"
"$python" -m alembic upgrade head
