# 0002. RabbitMQ as the message broker

Status: accepted

## Context

Executions are jobs: each must be handled by one worker, may run for minutes, can fail, and must not be lost. The system needs competing consumers, per-message acknowledgement, delayed retries and dead-lettering. Volume is modest, at tens of messages per second.

## Decision

Use RabbitMQ with one durable topic exchange. Main queues and DLQs are quorum queues. Each main queue has three retry queues (1 s, 5 s, 30 s) that dead-letter back to it after their TTL.

## Alternatives

- **Kafka.** Excellent for high-throughput streams and replay, but a log is a poor fit for a job queue. Per-message acknowledgement, delayed retry and competing consumers on one partition all need extra machinery. See ADR 0009 for when it becomes worthwhile.
- **NATS JetStream.** Simpler to run, with fewer established patterns for retry tiers and dead-lettering.
- **Redis Streams.** Light, but persistence and DLQ handling would be hand-built, and the system would gain a datastore with no other purpose.
- **A PostgreSQL job table only.** Workable at this scale, but the course requires a broker, and it would couple workers tightly to polling.

## Consequences

- Retry delays are fixed per tier, which avoids the head-of-line blocking of per-message TTLs.
- Quorum queues survive restarts and are ready for a multi-node cluster, at the cost of higher disk use than classic queues.
- Ordering is not guaranteed across retries. No consumer depends on it, because each event is checked against current database state.
