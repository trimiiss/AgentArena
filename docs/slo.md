# Service level objectives

These targets were set before the system was measured. Actual results and any deviation are in [loadtest.md](loadtest.md) and [chaos](chaos/). A missed target is recorded as missed, not rewritten.

## User journey

A developer submits a run and expects a fast acknowledgement, reliable execution, and no lost or double-counted results.

## Objectives

| ID | Objective | Target | Measured by |
| --- | --- | --- | --- |
| SLO-1 | Run submission latency | p95 ≤ 300 ms at 20 requests per second | k6 `http_req_duration{name:submit}`, and `http_request_duration_seconds` for `POST /v1/runs` |
| SLO-2 | Run submission availability | ≥ 99% of valid submissions accepted, under the same load | k6 `run_accepted`, and `http_requests_total` by status |
| SLO-3 | API availability | ≥ 99.5% of requests over 30 days answered without a 5xx | `http_requests_total` |
| SLO-4 | Execution durability | 100% of executions reach exactly one final state after a worker is killed | `tests/e2e/test_chaos.py`, counted from `/v1/evaluations` |
| SLO-5 | Recovery from a lost worker | Interrupted executions resume within 30 s | Time from the kill to the first retried execution |
| SLO-6 | Queue wait | p95 ≤ 5 s while workers have free capacity | `execution_queue_wait_seconds` |
| SLO-7 | Zero-downtime deployment | 0 failed requests during a rolling update | `loadtest/steady-requests.js` during `kubectl rollout` |

## Test conditions

Latency SLOs are measured on one laptop: Windows 11, 12 logical processors, 16 GB of RAM, with 7.7 GiB given to Docker Desktop. The load generator shares that machine. Each submitted run has one scenario, so the test stresses submission and not the agent.

Results on this hardware say whether the design works, not what a production cluster could sustain.

## Error budget

SLO-3 allows 0.5% of requests to fail, which is about 3.6 hours of total outage in 30 days. If the budget is spent, reliability work comes before new features until it recovers.

## Alerts

| Alert | Condition | SLO |
| --- | --- | --- |
| `RunSubmissionLatencyHigh` | p95 above 300 ms for 2 minutes | SLO-1 |
| `RunSubmissionErrorBudgetBurn` | More than 1% of submissions return 5xx for 2 minutes | SLO-2 |
| `HighErrorRate` | A service returns more than 5% 5xx for 2 minutes | SLO-3 |
| `ServiceDown` | A scrape target is down for 30 seconds | SLO-3 |
| `ExecutionsLost` | The reaper recovered an execution | SLO-4, SLO-5 |
| `ExecutionQueueBacklog` | More than 200 jobs waiting for 2 minutes | SLO-6 |
| `DeadLetterQueueNotEmpty` | A message reached a DLQ | SLO-4 |
| `OutboxBacklog` | More than 200 unpublished events for 1 minute | SLO-4 |
| `CircuitBreakerOpen` | A breaker was open at some point in the last minute | SLO-2 |
| `WorkersSaturated` | Workers over 90% busy for 5 minutes | SLO-6 |

Rules are in [observability/rules.yml](../observability/rules.yml).
