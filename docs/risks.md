# Risks

Likelihood and impact are rated low, medium or high for a real deployment of this code. The last column is the current state of each mitigation.

| Risk | Likelihood | Impact | Mitigation | State |
| --- | --- | --- | --- | --- |
| SSRF through customer-supplied agent URLs | High | High | Public-address check at registration and before each call, no redirects, egress NetworkPolicy. | In place. A small DNS rebinding window remains in the application check. NetworkPolicy covers it in Kubernetes, but not in Compose. |
| One organization reads another's data | Medium | High | `org_id` on every row and query, 404 for foreign ids, automated cross-tenant test for each resource. | In place. Enforcement is in application code. PostgreSQL row-level security would add a second layer. |
| A hostile agent attacks the worker | Medium | Medium | Response size cap, timeouts, call limit per scenario, no code execution, non-root read-only containers. | In place. |
| An agent's output manipulates the LLM judge | Medium | Low | Judge output never affects scores. The prompt treats transcripts as untrusted data. | In place. |
| Executions are lost or counted twice | Medium | High | Outbox, idempotent consumers, leases, fencing. Failure tests kill workers and restart the broker mid-run. | In place and tested. |
| Agent credentials leak | Low | High | AES-256-GCM bound to organization and agent, never in API responses, redacted from logs. | In place. Key rotation is manual. The worker holds decrypted credentials in memory for up to 60 seconds. |
| The shared internal token leaks | Low | High | Scoped per organization, not routed by the ingress, limited by NetworkPolicy. | Accepted for now. One token is shared by all internal callers. A service mesh or per-service tokens would improve this. |
| A single PostgreSQL or RabbitMQ node fails | Medium | High | Durable storage, restart recovery, outbox buffering during broker outages. | Partly. Local deployments have no replicas or backups. Production needs a managed database and a three-node RabbitMQ cluster. |
| Deduplication and transcript tables grow without limit | High | Medium | Published outbox rows are pruned after 7 days. | Partly. `processed_messages` and `execution_steps` have no retention job yet. |
| Rate limits apply per replica, so the real limit grows with scale | Medium | Low | Ingress limit per address in front of the service limit. | Accepted. A shared store such as Redis would make it exact. |
| ingress-nginx has been retired upstream | High | Medium | Pinned to its last release for local use. Manifests use the standard Ingress resource. | Accepted for local use. Move to Gateway API before any production use. |
| Workers do not autoscale well on CPU | High | Medium | They wait on agents, so CPU is a weak signal. Measured in [loadtest.md](loadtest.md). | Open. Add KEDA on queue depth, as ADR 0009 describes. |
| A vulnerable dependency reaches production | Medium | Medium | Exact version pins, Trivy on every image, SBOM per image, base images pinned. | In place in CI. Updates are manual. A bot such as Dependabot would help. |
| Too much of the code is not understood by its owner | Medium | High | Every decision has an ADR, and journals record how each phase was checked. | Depends on the student. Each phase is defended live, so the code must be read and rehearsed, not only run. |
