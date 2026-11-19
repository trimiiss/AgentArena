# Plan and phase status

The project follows the course phases F0 to F7. Each row shows what the repository contains and what only the student can do. The code for all phases exists now, but each phase is still submitted and defended on its own date.

| Phase | Deadline | In the repository | Still to do by hand |
| --- | --- | --- | --- |
| F0 Project choice | Wed 11 Nov 2026 | This README | Create the private GitHub repository, add `Agonb` as a collaborator, submit the idea paragraph and link on Student Hub. |
| F1 Architecture | Wed 18 Nov | README, AGENTS.md, SLOs, C4 diagrams, ADRs, risks, contracts, services | Video, journal PDF, tag `f1`. |
| F2 Communication | Wed 25 Nov | RabbitMQ, outbox, idempotent consumers, contract tests, `docs/communication.md` | Video, journal PDF, tag `f2`. |
| F3 Containers and Kubernetes | Wed 2 Dec | Dockerfiles, Compose, manifests, `scripts/deploy-kind.sh`, `docs/rolling-update.md` | Video, journal PDF, tag `f3`. |
| F4 CI/CD | Wed 9 Dec | `.github/workflows/ci.yml`, `scripts/smoke.sh`, `docs/rollback.md` | Push to GitHub, get a green run, link it in the README, set up the ruleset, video, journal PDF, tag `f4`. |
| F5 Resilience and observability | Wed 23 Dec | Retries, breakers, Prometheus, Grafana, OpenTelemetry, Jaeger, `docs/chaos/` | Video, journal PDF, tag `f5`. |
| F6 Scaling and security | Sun 10 Jan 2027 | k6, HPA, Trivy, SBOM, NetworkPolicy, `docs/loadtest.md` | Video, journal PDF, tag `f6`. |
| F7 Report and defense | January exams | `docs/report.md`, `docs/demo.md` | Rehearse the live demo, tag `f7`. |

Every phase also has a live explanation. If you cannot explain what you submitted, that phase's explanation points are lost, even when the code works.

## Idea paragraph for F0

> AgentArena is a platform for testing and benchmarking AI agents. Teams connect an agent over HTTP and run it through scripted scenarios such as tool failures, contradictions and policy traps. Five microservices, each with its own PostgreSQL database, communicate through REST and RabbitMQ. Workers execute scenarios in parallel, and an evaluator scores each interaction deterministically. The first SLO: run submission stays at or below 300 ms at p95, at 20 requests per second, with at least 99% accepted.

## What the journals need from you

`docs/journal/fN.md` follows the course template, and its numbers come from real measurements taken while this code was built. Before submitting a journal:

1. Run the commands in its table yourself, so that you have seen each result.
2. Replace any number that differs on your machine.
3. Add your own decisions and your own experience of where the AI was wrong. The entries there describe what happened during the build, and they are a starting point, not your reflection.

## Beyond the brief

| Item | Note |
| --- | --- |
| The brief's text ended at "PHASE" | Section 14 stops after Phase 0. The course phases above were used for the remaining sequence. If the brief continues, its later phases still need checking against this work. |
| Fifth service | `identity-service` was added: see [ADR 0001](adr/0001-service-boundaries-and-identity.md). |
| LLM judge | Implemented and tested through the real SDK over a mock transport, but never called against the live API, because no key was available. |
