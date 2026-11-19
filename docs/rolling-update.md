# Rolling update under load

Objective SLO-7: no failed requests during a rolling update.

## Result

Measured on 6 November 2026 on the kind cluster, with 2 replicas of `run-orchestrator` receiving constant traffic.

| Measure | Value |
| --- | --- |
| Requests sent | 5,002 |
| Rate | 50 requests per second, for 100 s |
| 5xx responses | 0 |
| Connection errors | 0 |
| Failed requests | 0.00% |
| Latency p95 | 9.7 ms |
| Latency p99 | 22.9 ms |
| Slowest request | 387 ms |
| Time for the rollout | 20 s |

SLO-7 was met. This is one run, on one deployment.

## How it was measured

```bash
RATE=50 DURATION=100s scripts/rollout-under-load.sh update
```

The script starts [loadtest/steady-requests.js](../loadtest/steady-requests.js) through the ingress, waits 15 seconds, then runs:

```bash
kubectl -n agentarena rollout restart deployment/run-orchestrator
kubectl -n agentarena rollout status deployment/run-orchestrator
```

A rollout restart replaces every pod, exactly as a new image would. k6 counts each 5xx and each connection error separately, and its thresholds require both to be zero.

```text
connection_errors
✓ 'count==0' count=0

server_errors_5xx
✓ 'count==0' count=0

http_req_failed................: 0.00%  0 out of 5002
http_reqs......................: 5002   49.928809/s
```

## What makes it safe

| Setting | Value | Why it matters |
| --- | --- | --- |
| `strategy.rollingUpdate.maxUnavailable` | 0 | An old pod only stops once its replacement is ready. |
| `strategy.rollingUpdate.maxSurge` | 1 | One extra pod at a time, which suits this small cluster. |
| `readinessProbe` on `/ready` | Checks the database | A new pod gets traffic only when it can do useful work. |
| `livenessProbe` on `/health` | Process only | A database outage does not restart every pod. |
| `preStop` hook | `sleep 5` | Keeps serving while the removal of the pod reaches the ingress. Without it, requests can reach a pod that has already stopped. |
| `--timeout-graceful-shutdown 20` | uvicorn | Finishes requests in flight after SIGTERM. |
| `terminationGracePeriodSeconds` | 30 | Longer than the steps above, so nothing is killed early. |

Workers follow the same idea differently. They stop taking jobs on SIGTERM and finish what they hold, with a 35 second grace period. `test_graceful_stop_lets_running_work_finish` checks that all 12 executions complete on their first attempt.

## Database migrations

An init container runs `alembic upgrade head` before the new version starts, under an advisory lock. During a rollout old and new pods run side by side, so each migration must work with the previous version of the code. Add columns first and remove them in a later release.
