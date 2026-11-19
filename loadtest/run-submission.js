// Measures the run submission SLO from docs/slo.md:
//   20 requests per second, p95 <= 300 ms, at least 99% accepted.
//
// k6 joins the target's Docker network, so the Windows port forwarder does not
// distort the numbers. MSYS_NO_PATHCONV=1 is only needed in Git Bash.
//
//   Kubernetes:
//     docker run --rm --network kind -v "$PWD/loadtest:/scripts" -e BASE_URL=http://agentarena-control-plane \
//       grafana/k6:1.8.2 run /scripts/run-submission.js
//   Compose:
//     docker run --rm --network agentarena_default -v "$PWD/loadtest:/scripts" -e BASE_URL=http://frontend:8080 \
//       grafana/k6:1.8.2 run /scripts/run-submission.js
//
// Options: BASE_URL, RATE, DURATION.
import { check, fail } from "k6";
import http from "k6/http";
import { Counter, Rate } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8080";
const RATE = Number(__ENV.RATE || 20);
const DURATION = __ENV.DURATION || "2m";

const accepted = new Rate("run_accepted");
const rejected = new Counter("run_rejected");

export const options = {
  scenarios: {
    submissions: {
      // A fixed arrival rate, so a slow server cannot quietly reduce the load.
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 40,
      maxVUs: 200,
    },
  },
  thresholds: {
    "http_req_duration{name:submit}": ["p(95)<300"],
    run_accepted: ["rate>=0.99"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

function post(path, body, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return http.post(`${BASE_URL}${path}`, JSON.stringify(body), { headers, tags: { name: "setup" } });
}

export function setup() {
  const stamp = Date.now();
  const session = post("/v1/auth/register", {
    email: `load-${stamp}@example.com`,
    password: "load-test-password",
    display_name: "Load Test",
    organization_name: `Load ${stamp}`,
  });
  if (session.status !== 201) fail(`register returned ${session.status}: ${session.body}`);
  const token = session.json("access_token");

  const pack = post("/v1/scenarios/starter-pack", {}, token);
  if (pack.status !== 200) fail(`starter pack returned ${pack.status}`);

  const agent = post(
    "/v1/agents",
    { name: "Load agent", agent_type: "customer_support", endpoint_url: "http://sample-agent:8000/agents/v2" },
    token,
  );
  if (agent.status !== 201) fail(`agent returned ${agent.status}`);

  // One scenario per run, so the test stresses submission and not the agent.
  const scenarios = http.get(`${BASE_URL}/v1/scenarios?q=Order%20status%20lookup`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { name: "setup" },
  });
  return { token, agentId: agent.json("id"), scenarioId: scenarios.json("items.0.id") };
}

export default function (data) {
  const response = http.post(
    `${BASE_URL}/v1/runs`,
    JSON.stringify({ agent_id: data.agentId, scenario_ids: [data.scenarioId], concurrency: 1, max_retries: 0 }),
    {
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${data.token}` },
      tags: { name: "submit" },
    },
  );
  const ok = check(response, { "run accepted": (result) => result.status === 202 });
  accepted.add(ok);
  if (!ok) rejected.add(1, { status: String(response.status) });
}
