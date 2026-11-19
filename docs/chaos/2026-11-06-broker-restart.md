# Experiment: restarting RabbitMQ during a run

Date: 6 November 2026. Environment: Docker Compose on the laptop described in [slo.md](../slo.md).

## Hypothesis

If RabbitMQ restarts while a run is executing, the run finishes with every execution evaluated exactly once, because queues are durable and clients reconnect.

## Method

`tests/e2e/test_chaos.py::test_broker_restart_loses_no_messages`:

1. Start a run of 24 executions against the slow sample agent, with concurrency 6 and up to 3 retries.
2. Wait until at least 2 executions are running.
3. Run `docker compose restart rabbitmq`.
4. Wait for the run to finish, then count evaluations per execution.

## What broke

The hypothesis failed on the first attempt, and this was the most useful result of the project. The run never finished. After 170 s it still showed 18 pending, 6 dispatched and 0 running.

Inspecting the broker showed the cause:

| Queue | Messages ready | Consumers |
| --- | --- | --- |
| `orchestrator.execution-jobs` | 16 | 0 |
| `orchestrator.scheduler` | 2 | 0 |
| `evaluation.requests` | 1 | 0 |

No message was lost. The durable queues kept everything. But nothing consumed them, because the clients reconnected without resubscribing. `consume()` fetched the queue with `get_queue(ensure=False)`, and a robust channel only restores queues that it declared itself. All 272 tests passed at that point, because none of them restarted the broker.

The logs showed a second, smaller bug. If a channel closed just after a handler succeeded, `ack()` raised, so a processed message took the retry path, and `nack()` then raised as well.

## Fix

- `consume()` now declares the queue on its own channel, so it is restored with its consumer.
- Acks and nacks go through `_settle`, which treats a closed channel as normal: the broker has already taken the message back, and deduplication absorbs the redelivery.
- Publishes have a 5 s timeout, so an outbox transaction cannot wait out a broker outage.

## Results

| Measure | Before the fix | After the fix |
| --- | --- | --- |
| Run outcome | Never finished, stopped waiting at 170 s | Completed |
| Time for the whole test | 174 s, failed | 50.8 s, passed |
| Consumers after the restart | 0 on all queues | 1 on each queue |
| Evaluations | 0 of 24 | 24 of 24, no duplicates |
| Messages lost | 0 | 0 |

## Conclusion

Durability was never the problem. Recovery was. A system can keep every message and still be down. The chaos test is now part of CI.
