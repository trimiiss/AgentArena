# Deployment and operations

## Docker Compose

```bash
cp .env.example .env
docker compose up --build --wait
scripts/smoke.sh
```

`.env.example` holds placeholders that are safe to publish, and that protect nothing. Never reuse them outside your own machine. All ports bind to localhost only.

```bash
docker compose logs -f run-orchestrator-worker
docker compose up -d --scale run-orchestrator-worker=3
docker compose down        # keeps data
docker compose down -v     # also deletes data
```

## Local Kubernetes with kind

Requirements: Docker Desktop, `kubectl` and [kind](https://kind.sigs.k8s.io/). On Windows, `winget install Kubernetes.kind`. The script also finds `kind` in `.tools/`.

Stop the Compose stack first if Docker has less than about 6 GB of memory.

```bash
docker compose stop
scripts/deploy-kind.sh
BASE_URL=http://127.0.0.1:8088 scripts/smoke.sh
```

The script:

1. Creates a one-node cluster from [infrastructure/kind/kind-config.yaml](../infrastructure/kind/kind-config.yaml).
2. Installs ingress-nginx and metrics-server.
3. Builds the images and loads them into the node. `SKIP_BUILD=1` reuses existing images.
4. Writes fresh random secrets to `infrastructure/kind/overlay/secrets.env`, which git ignores, unless the file already exists.
5. Applies `infrastructure/kind/overlay` and waits for every rollout.

The app is at <http://127.0.0.1:8088>. Internal tools need a port-forward:

```bash
kubectl -n agentarena-observability port-forward svc/grafana 3000
kubectl -n agentarena-observability port-forward svc/jaeger 16686
kubectl -n agentarena-observability port-forward svc/prometheus 9090
```

To remove everything: `scripts/deploy-kind.sh --delete`.

### What is deployed

| Workload | Replicas | Scaling |
| --- | --- | --- |
| run-orchestrator (API) | 2 | HPA, 2 to 5, on CPU |
| run-orchestrator-worker | 2 | HPA, 2 to 6, on CPU |
| run-orchestrator-scheduler | 1 | Safe to raise: locks use `SKIP LOCKED` |
| agent-registry, scenario-service | 2 | HPA, 2 to 4, on CPU. Both are on the run submission path, see [loadtest.md](loadtest.md) |
| identity-service, evaluation-service | 1 | Stateless, can be raised |
| evaluation-service-consumer | 1 | Safe to raise: consumers are idempotent |
| postgres, rabbitmq | 1 | StatefulSets. Local only, see [risks.md](risks.md) |

Migrations run in an init container, under an advisory lock.

### Security settings

- The namespace enforces the `restricted` Pod Security Standard, so pods run as non-root with no capabilities. Application containers use a read-only root filesystem.
- [networkpolicy.yaml](../k8s/networkpolicy.yaml) denies all traffic by default, then lists each allowed path.
- Application pods get no Kubernetes API token.
- Over HTTPS, set `COOKIE_SECURE=true` on identity-service.

## Publishing images

CI pushes `ghcr.io/<owner>/agentarena-<service>:<commit-sha>` on every push to `main`. Images are tagged by commit, never `latest`, so a deployment always names exactly what it runs. Deploying from GHCR instead of local images:

```bash
cd infrastructure/kind/overlay
kustomize edit set image agentarena/run-orchestrator=ghcr.io/<owner>/agentarena-run-orchestrator:<sha>
```

## Operations

### Messages in a dead-letter queue

The `DeadLetterQueueNotEmpty` alert fires. Open RabbitMQ management, select the `.dlq` queue and read `x-death-reason` on the message. A contract violation means a producer and consumer disagree, so fix that first. Once the cause is fixed, move the messages back with the management UI's shovel, or by re-publishing them. Handlers deduplicate, so replaying is safe.

### A run looks stuck

```bash
curl -H "Authorization: Bearer $TOKEN" localhost:8080/v1/runs/<id>
```

| Progress shows | Likely cause | What to check |
| --- | --- | --- |
| `dispatched` stays high, `running` is 0 | No workers are consuming | `rabbitmqctl list_queues name consumers`, worker logs |
| `running` stays high | Slow agent | Agent latency panel. Leases keep renewing while the worker lives. |
| Run stays `evaluating` | Consumer is down | Consumer pod, `evaluation.requests` depth. The run finishes with a warning after 5 minutes. |
| `pending` does not fall | Scheduler is down | Scheduler pod and the outbox backlog metric |

### Following one request

Every error response and log line carries a correlation id. Search the logs for it:

```bash
docker compose logs | grep <correlation_id>
```

A run shows its trace id on the live page. Paste it into Jaeger to see the whole path across services.

### Rotating secrets

| Secret | How |
| --- | --- |
| Database and broker passwords | Change them in the database or broker, update the Secret, then restart the workloads. |
| `INTERNAL_SERVICE_TOKEN` | Update the Secret, then restart all services together. |
| `CREDENTIALS_ENCRYPTION_KEY` | Not automated. Needs a job that re-encrypts `agent_credentials`. `key_id` is stored per row to allow this. |
| Token signing key | Insert a new row in `signing_keys` and retire the old one after 15 minutes. Services fetch new keys when they see an unknown `kid`. |

## Generated files

Run these after the matching change. Contract tests fail if you forget the first.

| After changing | Run |
| --- | --- |
| An API, event or the agent protocol | `python scripts/export_contracts.py` |
| A database model | `scripts/new-migration.sh <service> "<message>"` |
| A Grafana dashboard | `python observability/grafana/generate_dashboards.py` |
| `observability/rules.yml` | Copy it to `k8s/observability/rules.yml` |
| Python dependencies | Update `constraints.txt` with exact versions |
