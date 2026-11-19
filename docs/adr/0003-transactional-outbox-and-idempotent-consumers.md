# 0003. Transactional outbox and idempotent consumers

Status: accepted

## Context

A service must change its database and publish an event. Doing both directly cannot be atomic. If the commit succeeds and the publish fails, an event is lost and a run hangs. If the publish succeeds and the commit fails, consumers act on something that never happened.

## Decision

Write events to an `outbox` table in the same transaction as the state change. A relay publishes committed rows with broker confirms and marks them published. It selects with `FOR UPDATE SKIP LOCKED`, so several relays can run side by side.

Delivery is therefore at least once. Consumers record each `event_id` in `processed_messages`, inside the same transaction as their effect, so a repeated message has no effect.

## Alternatives

- **Publish after commit.** Simple, and loses events when a process dies between the two steps.
- **Two-phase commit.** Not supported by RabbitMQ, and it would couple availability of the database and the broker.
- **Change data capture with Debezium.** A sound pattern with less application code, but it adds a heavy component for this volume.

## Consequences

- A crash between the broker's confirm and the database update publishes an event twice. That is expected and handled by deduplication.
- Publishing adds up to one poll interval of latency (200 ms). The measured cost is in [docs/loadtest.md](../loadtest.md).
- The relay holds a short transaction while publishing, so each publish has a 5 second timeout. Without one, a broker outage would hold it open indefinitely.
- `outbox` and `processed_messages` grow. Published outbox rows are deleted after 7 days. Pruning `processed_messages` is a known gap, listed in [docs/risks.md](../risks.md).
