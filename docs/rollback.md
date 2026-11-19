# Rollback

## Rolling back a release

```bash
kubectl -n agentarena rollout undo deployment/run-orchestrator
kubectl -n agentarena rollout status deployment/run-orchestrator
```

To return to a specific revision:

```bash
kubectl -n agentarena rollout history deployment/run-orchestrator
kubectl -n agentarena rollout undo deployment/run-orchestrator --to-revision=<number>
```

Images are tagged with their commit SHA in GHCR, so each revision names exactly what it runs. Rolling back through the pipeline means re-running the deploy job of the last good commit.

## Tested rollback

Tested on 6 November 2026 on the kind cluster, with 50 requests per second of real traffic throughout:

```bash
RATE=50 DURATION=110s scripts/rollout-under-load.sh rollback
```

The script releases a broken version, in this case a wrong database password, and then undoes it.

### 1. The bad release does not reach users

```text
$ kubectl -n agentarena set env deployment/run-orchestrator DATABASE_URL=postgresql+asyncpg://runs_svc:wrong-password@postgres:5432/runs_db
deployment.apps/run-orchestrator env updated
$ kubectl -n agentarena rollout status deployment/run-orchestrator --timeout=30s
error: timed out waiting for the condition

NAME                                READY   STATUS       RESTARTS      AGE
run-orchestrator-656c9c849c-5gjp2   1/1     Running      0             2m13s
run-orchestrator-656c9c849c-5rk5g   1/1     Running      0             2m6s
run-orchestrator-656c9c849c-6djp8   1/1     Running      0             80s
run-orchestrator-8b76db875-tsj5c    0/1     Init:Error   2 (22s ago)   30s
```

The new pod's migration step fails, so it never becomes ready. With `maxUnavailable: 0`, Kubernetes keeps every old pod serving. The rollout simply stops and waits.

### 2. Undo

```text
$ kubectl -n agentarena rollout undo deployment/run-orchestrator
deployment.apps/run-orchestrator rolled back
deployment "run-orchestrator" successfully rolled out

NAME                                READY   STATUS        RESTARTS   AGE
run-orchestrator-656c9c849c-5gjp2   1/1     Running       0          2m13s
run-orchestrator-656c9c849c-5rk5g   1/1     Running       0          2m6s
run-orchestrator-656c9c849c-6djp8   1/1     Running       0          80s
run-orchestrator-8b76db875-tsj5c    0/1     Terminating   2          30s
```

The rollback finished in under a second, because the old pods had never stopped. Only the broken pod was removed.

### 3. What users saw

| Measure | Value |
| --- | --- |
| Requests during the whole test | 5,502 |
| 5xx responses | 0 |
| Connection errors | 0 |
| Latency p95 | 7.68 ms |
| Slowest request | 253 ms |

## When to roll back

| Signal | Where |
| --- | --- |
| New pods stay in `Init:Error` or `CrashLoopBackOff` | `kubectl get pods` |
| `HighErrorRate` or `RunSubmissionErrorBudgetBurn` fires | Prometheus alerts |
| `RunSubmissionLatencyHigh` fires after a deploy | Grafana, System health |
| The smoke test fails | `scripts/smoke.sh`, also in CI |

## Limits

This protects against a release that fails to start. A version that starts and then misbehaves passes readiness, so it needs the alerts above and a manual undo. Automatic rollback on metrics would need a tool such as Argo Rollouts.

`rollout undo` does not reverse database migrations. Migrations must stay compatible with the previous version, as [rolling-update.md](rolling-update.md) explains. If a migration must be undone, run `alembic downgrade -1` deliberately, after checking what it would remove.
