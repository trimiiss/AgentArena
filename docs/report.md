# AgentArena: final report

Course 104CEC, Cloud and Edge Computing, 2026/27. All measurements were taken on one laptop in November 2026, on Docker Compose and on a local kind cluster. They show whether the design works, not what a production cluster could sustain.

## 1. The problem

Teams are putting AI agents in front of customers, and most test them by trying a few conversations by hand. That finds the obvious failures. It does not answer the questions that matter before a release. How often does the agent do the right thing? What happens when a tool fails halfway through? Does it follow policy when a user pushes back, or when a message tells it to ignore its instructions? Is the new version better, or just different?

AgentArena answers these by treating agent quality as something to measure. A developer connects an agent over HTTP and runs it through scripted scenarios: missing information, contradictions, tool outages, policy traps and prompt injection. Every interaction is scored, and the results show what failed, how fast it ran and what it cost. Two versions can then be compared scenario by scenario.

The domain also suits this course well. A benchmark is only worth anything if its results can be trusted, so the system has to run work reliably under failure, and it must not lose or double-count anything. Those are the problems distributed systems exist to solve.

## 2. What was built

The system has five microservices, a React frontend, and a standard operational stack.

| Component | Responsibility |
| --- | --- |
| identity-service | Accounts, organizations, roles, tokens |
| agent-registry | Agents, immutable versions, encrypted credentials |
| scenario-service | Scenarios, suites, datasets, seeded variations |
| run-orchestrator | Run API, scheduler, and workers that scale separately |
| evaluation-service | Scoring, benchmarks, comparisons, analytics, reports |
| Frontend | Fifteen pages, with live progress over Server-Sent Events |

Each service owns its PostgreSQL database and its migrations. Synchronous calls use REST, and everything else goes through RabbitMQ. The platform runs on Docker Compose and on Kubernetes, with Prometheus, Grafana, OpenTelemetry and Jaeger. The test suite has 354 Python tests and 19 frontend tests across unit, contract, integration, end-to-end and failure-injection levels.

An agent needs one HTTP endpoint that speaks a small, versioned protocol. Tools are simulated from the scenario, which makes tool failures scriptable and a run repeatable. No customer code ever runs inside the platform. A sample agent with a flawed and a fixed version lets the whole system be demonstrated without any LLM key.

## 3. Architecture

Requests enter through an ingress that routes by path. `POST /v1/runs` calls the Agent Registry and Scenario Service synchronously, because a run must be rejected at once if its agent is missing or belongs to another organization. It forwards the caller's token, so those services enforce tenant isolation themselves. The run, its executions and a `run.requested` event then commit in one transaction, and the API answers 202.

Everything after that is asynchronous. The scheduler fills each run's concurrency slots by writing `execution.requested` events. A worker claims a job with a row lock, starts a lease, loads the agent and scenario through internal APIs, and drives the conversation. The transcript, the final state and `evaluation.requested` commit together. The evaluation consumer scores the execution and publishes `evaluation.completed`. When every execution is scored, the scheduler finishes the run and the benchmark is stored.

Diagrams, event tables and failure tables are in [architecture.md](architecture.md) and [communication.md](communication.md).

## 4. Key decisions

Ten decisions are recorded as ADRs. Five shaped the system most.

**A fifth service.** The brief listed four services but also required login and organizations. Putting users inside a domain service would make it the security dependency of every other service, so identity became its own small service. Other services verify tokens locally with public keys, so identity being down stops new logins but not requests that already hold a token. Tokens use Ed25519, so a compromised service cannot forge tokens for another tenant.

**RabbitMQ rather than Kafka.** Executions are jobs. Each needs one worker, an acknowledgement, a delayed retry and a dead-letter path. RabbitMQ provides these directly, while Kafka would need extra machinery for each. Kafka remains the right tool if transcripts must be replayed at high volume, and ADR 0009 states that trigger.

**A transactional outbox with idempotent consumers.** Publishing after a commit loses events when a process dies between the two steps. Writing the event in the same transaction removes that window, and makes delivery at-least-once. Consumers therefore deduplicate on event id, inside the transaction that applies the effect.

**The database owns execution state.** RabbitMQ only tells workers that there is work. Workers hold 15 second leases, renewed by a heartbeat every 5 seconds. If a worker dies, a reaper retries its executions when the lease expires, regardless of how long the run's timeout is. Fencing checks on the final write discard results from a worker that lost its lease, so an execution cannot record two outcomes.

**Deterministic scores.** An LLM judge cannot give repeatable benchmarks, and it reads text written by the system being tested. Scores therefore come only from deterministic checks. The optional judge adds comments, runs after the scores commit, and cannot change them.

Technologies such as Temporal, Kafka, Neo4j, LangGraph and a service mesh were deliberately left out. ADR 0009 names where each would fit and what would justify it. Each one adds operating cost, and the brief itself warned against adding them for decoration.

## 5. Security

Tenancy is enforced on every query, and another organization's id returns 404, so ids cannot be probed. An automated test tries this for each resource. Each database role can only connect to its own database, so a cross-service query fails at the database, not just by convention.

Customer URLs are the largest risk, because the platform calls them from inside its own network. Only public addresses are allowed, checked at registration and again before each call. Redirects are not followed, and responses are capped at 1 MB. In Kubernetes, the workers' egress policy excludes private ranges as a second layer.

NetworkPolicies deny everything by default. This was tested with real connections from inside real pods, not just by reading the manifests: `scripts/check-network-policies.sh` confirmed that all 7 allowed paths connect and all 11 forbidden paths are blocked, including the cloud metadata address and the Kubernetes API.

Agent credentials are encrypted with AES-256-GCM and bound to their organization and agent, so a ciphertext copied to another row will not decrypt. They never appear in API responses. Containers run as non-root with no capabilities and read-only root filesystems, enforced by the `restricted` Pod Security Standard. CI runs on actions pinned to commit hashes, and runs Trivy from a pinned image, pinned by digest, because the Trivy GitHub Action's tags and Docker Hub images were hijacked in March 2026 (CVE-2026-33634).

## 6. Measurements

MEASUREMENTS_PLACEHOLDER

## 7. What broke

The most valuable results of this project were failures. In each case below the tests were green, and something else revealed the problem.

**The scoring passed agents that lied.** The first scoring used a weighted average. An agent that never called a required tool and claimed success scored 0.79 and passed. Another ignored a failed tool, said "Done" and scored 1.00. All 10 end-to-end tests were green. Printing the real numbers exposed it, because the run looked too easy. Requirements can now fail a scenario on their own, and failure handling reads tool results rather than wording. The flawed agent's pass rate dropped from 41.7% to 16.7%, which is the true figure.

**Consumers did not survive a broker restart.** With 272 tests green, the first chaos test froze a run. The queues had kept every message but showed 0 consumers, because the code fetched queues in a way the client library does not restore after reconnecting. The system was durable and still down.

**A burst of logins killed the identity service.** Ten parallel Argon2 checks need about 640 MiB, above the 512 MiB limit, so the pod was OOM killed. Compose has no memory limit, so this only appeared on Kubernetes. Any visitor could have caused it. A semaphore now bounds hashing, and peak memory was 338 MiB after the fix.

**NetworkPolicy exposed a hidden dependency.** The identity service fetched public keys from itself over the network. Compose allowed that silently. On Kubernetes the policy blocked it, correctly, and every authenticated identity route returned 503. The right fix was to verify locally, not to open a path.

**Silent telemetry loss.** OpenTelemetry's SQLAlchemy instrumentation refused the newest SQLAlchemy and logged one line at startup. Database spans were simply missing while everything looked healthy.

**A deployment failed and reported success.** The deploy ran through `tail`, which replaced the exit code with 0 while no pods existed. Reading the log caught it.

**The first explanation was wrong.** Registration took 21 seconds on the cluster, and Argon2 was the obvious suspect. Measuring in the pod showed 0.2 s. The real cause was the Windows TCP timeout on `localhost` trying IPv6 first. Fixing the obvious suspect would have changed nothing.

**Graceful shutdown worked by accident.** A type checker showed that an attribute used to disable uvicorn's signal handlers no longer existed. Shutdown still passed its test, because of the order handlers happened to be installed in. It now uses the supported override.

## 8. Use of AI

An AI coding agent wrote most of the code in this project, under the rules in [AGENTS.md](../AGENTS.md). The incidents above show the pattern of its mistakes. It produced plausible code quickly, and its first version often passed its own tests. The errors were mostly assumptions: that the newest library versions work together, that a green test means correct behaviour, that the obvious suspect is the cause.

What worked was treating its output as untrusted until there was evidence. That meant reading real numbers rather than pass counts, injecting failures on purpose, running on the real deployment target, and measuring before fixing. The journals record each case.

## 9. Limits and what remains

- **CI has not run on GitHub.** The workflow passes `actionlint`, and each step was run locally, but a real pipeline run needs the repository pushed.
- **The LLM judge has not called a live model.** It was tested through the real SDK over a mock transport, because no API key was available.
- **One node, one laptop.** PostgreSQL and RabbitMQ are single replicas with no backups. Production needs managed services or clusters.
- **Workers autoscale on CPU, which is a weak signal** for work that mostly waits on agents. Scaling on queue depth with KEDA is the next step.
- **Retention.** `processed_messages` and transcripts have no pruning job yet.
- **Tenancy is enforced in application code.** PostgreSQL row-level security would add a second layer.
- **Lexical similarity** is blind to synonyms, so it is advisory and never fails a scenario.

## 10. Lessons

**Green tests measure what you asserted, not what you wanted.** Most serious bugs here passed every test at the time. Reading actual output and asking whether it made sense found more than coverage did.

**Failure paths need their own tests.** Durability, recovery and capacity are separate properties. The queues never lost a message, and the system was still down. The only way to learn this is to break things on purpose.

**Test on the real target.** Limits and policies change behaviour. Two serious bugs existed only because Kubernetes enforced what Compose did not.

**Measure before fixing.** The 21 second delay had nothing to do with its obvious suspect.

**Make failure visible.** A log line nobody reads, a masked exit code and a silent retry all hid real problems. A system that fails loudly is much easier to trust.
