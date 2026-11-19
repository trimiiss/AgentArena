# 0004. The database owns execution state, and workers hold leases

Status: accepted

## Context

An execution can be interrupted at any point: a worker can crash, a pod can be evicted, or the broker can restart. Agents can take minutes, so waiting for an overall timeout before noticing a dead worker would stall runs. A slow worker that is presumed dead must not overwrite the result of its replacement.

## Decision

Each execution row is the single source of truth. RabbitMQ only tells workers that there is something to do.

- **Claim.** A worker takes a row lock and moves the execution from `dispatched` to `running` only if the attempt number matches.
- **Lease.** The claim sets `lease_expires_at` 15 seconds ahead, and a heartbeat renews it every 5 seconds.
- **Recovery.** The scheduler finds expired leases and schedules a new attempt, or fails the execution if none remain.
- **Fencing.** The final write checks `status`, `worker_id` and `attempt`. A worker that lost its lease discards its result.

Agent failures that are worth retrying use the same path: the row moves to `retry_wait` with a backoff, and the scheduler dispatches it when due.

## Alternatives

- **Rely on broker redelivery alone.** A redelivered message cannot tell a dead worker from a slow one, so work could run twice at once, and the broker's view of "in progress" cannot be queried per execution.
- **Lease as long as the run timeout.** Simpler, with no heartbeat, but recovery could take five minutes instead of 15 seconds.
- **Temporal.** Solves this exactly and adds a large platform. See ADR 0009.

## Consequences

- A killed worker recovers in about 15 seconds regardless of the run timeout. Measured in [docs/chaos](../chaos/).
- Heartbeats add one small update per running execution every 5 seconds.
- An agent may receive the same scenario twice after a crash, once from each attempt. The protocol documents this, and tool calls are simulated, so nothing external happens twice.
- If a lease expires during a long database outage, a healthy worker can lose its work. That is safe, because fencing discards the result, but it costs a retry.
