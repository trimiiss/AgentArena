# Demo plan: 15 minutes

For the live defense. The story: a team has an AI support agent, it looks fine, and AgentArena shows it is not. Then we watch the platform survive being attacked while it measures.

## Before the session

```bash
docker compose stop                        # free memory for the cluster
scripts/deploy-kind.sh
python scripts/seed_demo.py --base-url http://127.0.0.1:8088
kubectl -n agentarena-observability port-forward svc/grafana 3000 &
kubectl -n agentarena-observability port-forward svc/jaeger 16686 &
kubectl -n agentarena-observability port-forward svc/prometheus 9090 &
```

Open these tabs: the app on `http://127.0.0.1:8088`, Grafana (Execution performance), Jaeger, and a terminal with `kubectl -n agentarena get pods -w`.

## Timeline

| Minute | Show | Say | Point to prove |
| --- | --- | --- | --- |
| 0 to 1 | Dashboard | What AgentArena does, and who it is for. | The problem is real. |
| 1 to 3 | Architecture diagram, then `kubectl get pods` | Five services, one database each. Why identity is the fifth. | Boundaries and ownership. |
| 3 to 5 | Create a run for the flawed v1 and watch the live page. | Progress arrives by Server-Sent Events. Workers call the agent over plain HTTP. | The core flow works live. |
| 5 to 7 | Open the prompt injection scenario in the results, then its trace. | The agent refunded 900 USD because a message told it to. The evaluator read the tool call, not the wording. | Deterministic scoring catches real failures. |
| 7 to 8 | Compare v1 with v2 | Pass rate by scenario, and the significance test. | Regressions are visible per scenario. |
| 8 to 11 | Start a slow run, then `kubectl delete pod` on the workers while it executes. Watch the live page and Grafana. | Leases expire in 15 seconds, and the reaper retries. No result is lost or counted twice. | Recovery, shown under fire. |
| 11 to 12 | Open that run's trace in Jaeger. | One trace crosses the API, the broker, the workers and the evaluator. | Distributed tracing. |
| 12 to 13 | `scripts/check-network-policies.sh` | Default deny, and every allowed path is listed. | Security is enforced, not just configured. |
| 13 to 15 | The live change the committee asks for, then questions. | Show the failing test first, then the fix. | I understand the code. |

## A live change to have ready

If the committee does not choose one, change a scenario's policy limit from 100 to 50 USD:

1. Open the scenario "Refund under the limit" and edit the `business_rules` value.
2. Save, which creates version 2. Show that version 1 is untouched.
3. Run it again, and show the agent now fails that rule.

## If something goes wrong

| Problem | Response |
| --- | --- |
| Cluster will not start | Fall back to Compose: `docker compose up -d --wait`. The chaos steps work there too. |
| Out of memory | Close other applications. The cluster needs about 4 GB. |
| A pod is stuck | Show `kubectl describe pod`, explain what it means, and move on. |
| Port-forward dropped | Run it again. |

## Questions to be ready for

- Why an outbox instead of publishing after the commit?
- What happens if a worker dies between the agent's reply and the database write?
- Why is the lease 15 seconds, and what happens if a healthy worker misses its heartbeat?
- Why does a failed execution still get evaluated?
- Why are scores deterministic, and what does the LLM judge add?
- How does a request prove which organization it belongs to?
- What stops a customer from registering `http://169.254.169.254` as an agent?
- Which SLO was hardest to meet, and what would you change first?
- What did the AI get wrong, and how did you find out?
