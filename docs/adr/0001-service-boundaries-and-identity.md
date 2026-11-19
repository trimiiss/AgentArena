# 0001. Service boundaries, and a fifth service for identity

Status: accepted

## Context

The brief asks for four microservices: Agent Registry, Scenario Service, Run Orchestrator and Evaluation Service. It also requires registration, login, organizations, roles and organization settings. None of the four owns those concepts. Users and organizations are not agents, scenarios, runs or evaluations.

## Decision

Add `identity-service`, with its own `identity_db`. It owns users, organizations, memberships and signing keys, and issues access tokens. The other four services keep the boundaries the brief describes.

## Alternatives

- **Put accounts in the Agent Registry.** Keeps four services, but makes one domain service the security dependency of all the others, and mixes two unrelated reasons to change.
- **Use an external identity provider.** The right answer for many real products. It adds an external dependency to a project that must run offline on a laptop.
- **A shared `users` table.** Breaks database-per-service, which the brief and the course both require.

## Consequences

- One more service to deploy, monitor and test. It is small and mostly idle after login.
- Services verify tokens locally with cached public keys, so identity being down does not stop requests that already hold a token. Only login and refresh stop.
- The course's checks treat every folder under `services/` as a full service, so it also needs a contract, manifests and a Prometheus target.
