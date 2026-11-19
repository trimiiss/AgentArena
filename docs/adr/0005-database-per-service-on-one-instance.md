# 0005. Database per service, on one PostgreSQL instance locally

Status: accepted

## Context

Each service must own its data. Running five PostgreSQL servers on a laptop, next to RabbitMQ, the observability stack and a Kubernetes cluster, would exhaust the 8 GB that Docker Desktop has here.

## Decision

Run one PostgreSQL instance locally with five databases. Each service has its own role, owns only its database, and PUBLIC has no connect privilege. A role that tries another service's database gets `permission denied`, and `tests/e2e/test_data_ownership.py` checks every pair.

## Alternatives

- **One server per service.** The strongest isolation, and the shape to use in production, at several times the memory.
- **One database with a schema per service.** Lighter, but isolation is weaker and it is too easy to write a cross-schema join.
- **One shared database.** Ruled out by the brief.

## Consequences

- Ownership is enforced by the database, not by convention. A cross-service join is impossible, so no code can drift into one.
- The instance is a shared point of failure and shared load locally. Moving a service to its own server only changes its connection string.
- Reports that span services cannot use SQL. Data needed later travels in events, which is why `evaluation.requested` carries the agent and scenario names.
