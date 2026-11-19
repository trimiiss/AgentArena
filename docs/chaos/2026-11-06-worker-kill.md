# Experiment: killing every worker during a run

Date: 6 November 2026. Environment: kind cluster on the laptop described in [slo.md](../slo.md), with 4 worker pods.

## Hypothesis

If every worker pod is killed without warning while a run is executing, the run still completes. Each execution reaches exactly one final state and is evaluated exactly once, and the interrupted executions resume within 30 s (SLO-4 and SLO-5).

The mechanism being tested is the lease. A worker renews a 15 second lease every 5 seconds. A dead worker stops renewing, the reaper in the scheduler finds the expired lease and schedules a new attempt.

## Method

```bash
python scripts/experiment.py --base-url http://127.0.0.1:8088 kill-worker --platform kubernetes
```

1. Create a new organization and agent, using the slow sample agent, which adds several seconds of latency to each reply.
2. Baseline: run the 12-scenario starter suite twice, 24 executions, concurrency 8, with no failure injected.
3. Start the same run again, and wait until at least 4 executions are running.
4. Run `kubectl delete pod -l app.kubernetes.io/component=worker --grace-period=0 --force`, which sends no warning and gives no time to shut down.
5. Poll until the run finishes, then count final states, attempts and evaluations.

All numbers come from the public API. No script touched the database.

## Results

| Measure | Baseline, before the kill | After the kill |
| --- | --- | --- |
| Run duration | 27.58 s | 43.15 s |
| Added time | | 15.57 s |
| Executions running when killed | | 8 |
| First retried execution appeared | | 15.5 s after the kill |
| Executions retried | 0 | 8 |
| Completed | 24 of 24 | 24 of 24 |
| Failed | 0 | 0 |
| Evaluations | | 24 |
| Duplicate evaluations | | 0 |

The hypothesis held.

| Objective | Target | Measured | Result |
| --- | --- | --- | --- |
| SLO-4, execution durability | 100% reach exactly one final state | 24 of 24, no duplicates | Met |
| SLO-5, recovery from a lost worker | Within 30 s | 15.5 s | Met |

Recovery took about as long as the lease, which is what the design predicts. The added time, 15.57 s, is almost exactly the recovery time, so the rest of the run lost nothing beyond the wait for the lease to expire.

No pod restarted during the whole test session, because deleted workers were replaced rather than restarted. `kubectl get pods` showed 0 restarts on all 16 pods afterwards.

## The alert

`ExecutionsLost` went to firing. Prometheus showed it active straight after the experiment, in `ALERTS{alertstate="firing"}`. The failure was therefore visible to an operator as well as recovered from.

## Conclusion

Losing every worker at once cost one lease period and nothing else. The same behaviour is covered on Compose by `tests/e2e/test_chaos.py`, which kills the workers with `SIGKILL` and runs in CI.

The cost of this design is that the recovery time equals the lease. A shorter lease recovers faster, but a worker that stalls for longer than the lease loses its work to a second attempt. Fencing on the final write keeps that safe, so the only cost is wasted effort. Fifteen seconds is three missed heartbeats, which tolerates a slow moment without treating it as a death.
