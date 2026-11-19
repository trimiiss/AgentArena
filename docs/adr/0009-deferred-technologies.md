# 0009. Advanced technologies deferred until there is a need

Status: accepted

## Context

The brief says the architecture should allow LangGraph, Temporal, Neo4j, Kafka and a service mesh later, and warns against adding them for decoration. Each adds operating cost, and this system must fit in 8 GB of Docker memory.

## Decision

Adopt none of them yet. The design leaves a clear place for each, and this record states what would justify it.

| Technology | Where it would fit | Adopt when | Why not now |
| --- | --- | --- | --- |
| LangGraph | Scenario generation inside `scenario-service`, behind the variations endpoint. | Authors need generated multi-step scenarios that rule-based variations cannot produce. | Seeded rule-based variations are reproducible and need no model. |
| Temporal | Replaces the scheduler and leases in `run-orchestrator`. | Runs need human approval, or last for hours or days across many stages. | Runs take minutes. Leases recovered from a killed worker within seconds in testing. |
| Neo4j | A new service for agent and tool dependency graphs. | Agents call other agents and the product must analyse those graphs. | Today's relations are simple foreign identifiers. |
| Kafka | Next to RabbitMQ, for analytics events only. | Transcripts must be replayed into new consumers, or volume passes thousands of events per second. | Measured load is tens of events per second, and jobs suit a queue. |
| Service mesh | Mutual TLS and retries between services in Kubernetes. | Multiple teams or compliance rules require encrypted, authenticated service traffic. | NetworkPolicy and an internal token cover this deployment. A mesh adds a sidecar per pod. |
| KEDA | Scale workers on queue depth. | Workers need autoscaling in production. | The course needs one HPA, and memory is tight. It is the first of these to add: see [docs/loadtest.md](../loadtest.md). |

## Alternatives

- **Adopt some now to show breadth.** Rejected by the brief itself, and each one would need its own failure handling and monitoring.
- **Design as if they will never arrive.** Would cost little today but would scatter assumptions through the code. Keeping them behind existing boundaries costs nothing.

## Consequences

- The system stays small enough to run, test and explain on one laptop.
- Some hand-written code, such as leases and retry tiers, duplicates what those platforms offer. It is covered by tests, and its limits are recorded.
