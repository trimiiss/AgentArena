# Working in this repository

Rules for coding agents and people. Read this before changing anything.

## What this is

AgentArena tests and benchmarks AI agents. Five FastAPI services, a React frontend, PostgreSQL and RabbitMQ. See [docs/architecture.md](docs/architecture.md).

## Layout

| Path | Contents |
| --- | --- |
| `services/<name>/` | One microservice: code, Dockerfile, migrations. |
| `shared/agentarena_common/` | Cross-cutting infrastructure only. No domain logic. |
| `contracts/` | OpenAPI, event schemas and the agent protocol. Generated, never edited by hand. |
| `frontend/` | React, TypeScript and Tailwind. |
| `examples/sample-agent/` | A demo agent. Shares no code with the platform, on purpose. |
| `tests/` | `unit`, `contract`, `integration`, `e2e`. |
| `k8s/` | Kubernetes manifests. `infrastructure/` holds Postgres, RabbitMQ, gateway and kind config. |
| `observability/` | Prometheus, alert rules, Grafana, OpenTelemetry Collector. |
| `docs/` | Architecture, ADRs, SLOs, measurements and phase journals. |

## Commands

The Python commands assume the virtual environment is active.

```bash
cp .env.example .env
docker compose up --build --wait                 # the whole system
scripts/install-dev.sh                           # Python packages for tests

ruff check shared services examples scripts tests observability
ruff format shared services examples scripts tests observability
pytest tests/unit tests/contract                 # no dependencies needed
docker compose up -d --wait postgres rabbitmq
pytest tests/integration                         # real PostgreSQL and RabbitMQ
pytest tests/e2e -m "not chaos"                  # needs the running stack
pytest tests/e2e/test_chaos.py -m chaos          # kills containers, run it alone
cd frontend && npm test && npm run build

python scripts/export_contracts.py               # after changing an API or event
scripts/new-migration.sh <service> "<message>"   # after changing a model
python observability/grafana/generate_dashboards.py
scripts/smoke.sh                                 # full path through a deployment
scripts/deploy-kind.sh                           # local Kubernetes
```

## Rules that keep the architecture honest

1. **A service never touches another service's database.** Use its API or an event. Each database role can only connect to its own database.
2. **Every query is scoped by organization.** Another tenant's id must return 404, not 403. `tests/e2e/test_security.py` checks this for each resource.
3. **Events are written through the outbox**, in the same transaction as the change they describe. Never publish directly from a request handler.
4. **Consumers are idempotent.** Delivery is at least once. Use `first_delivery` inside the handler's transaction.
5. **The database is the source of truth for executions.** The broker only carries work. Scheduling decisions read rows, not queue state.
6. **Scores are deterministic.** The LLM judge is advisory and must never change `score` or `passed`.
7. **Agents are only reached over HTTP.** Never execute customer code. Every agent URL goes through the SSRF check, at registration and before each call.
8. **Versions are immutable.** Agent and scenario versions are never edited, so a past benchmark keeps its meaning.
9. **Every outbound call has a timeout.** Retry reads, never writes, unless the write is idempotent.
10. **Contracts change on purpose.** Regenerate them and review the diff. A required field in an event version is frozen: publish a new version instead.

## Conventions

- Metrics, dashboards and analytics show real data only. Never hardcode a number in the UI or a document. An unknown cost is "Not reported", never zero.
- Do not claim a measurement you did not make. Measured results in `docs/` state the command and the date.
- Secrets live in `.env` or Kubernetes Secrets. `.env.example` and `k8s/secrets.example.env` hold placeholders only.
- Log with `extra={...}`. The JSON formatter adds trace and correlation ids and redacts sensitive keys.
- A new process needs `/health`, `/ready` and `/metrics`. Use `serve_ops` when it has no API.
- A new folder under `services/` is treated as a full microservice by the course checks. It needs a contract, Dockerfile, Compose entry, manifests and a Prometheus target.

## Testing expectations

Change behaviour, change a test. A bug fix starts with a test that fails. Tests that exercise failure paths matter most here: retries, redelivery, lost leases, cross-tenant access. If green tests look too easy, inspect the actual numbers. That habit found real bugs in this project: see `docs/journal/`.

## Working with AI

AI tools are allowed, and the student remains responsible for every line. Log what you asked and where the AI was wrong in `docs/journal/fN.md`, section 4. Be ready to explain any change without the tool.
