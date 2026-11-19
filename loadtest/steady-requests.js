// Constant read traffic for rolling updates and rollbacks. Reports every failure,
// because the question during a rollout is simply how many requests broke.
//
//   docker run --rm --network kind -v "$PWD/loadtest:/scripts" -e BASE_URL=http://agentarena-control-plane \
//     -e DURATION=90s grafana/k6:1.8.2 run /scripts/steady-requests.js
import { check, fail } from "k6";
import http from "k6/http";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8080";
const server_errors = new Counter("server_errors_5xx");
const connection_errors = new Counter("connection_errors");

export const options = {
  scenarios: {
    steady: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 50),
      timeUnit: "1s",
      duration: __ENV.DURATION || "90s",
      preAllocatedVUs: 30,
      maxVUs: 100,
    },
  },
  thresholds: {
    server_errors_5xx: ["count==0"],
    connection_errors: ["count==0"],
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

export function setup() {
  const stamp = Date.now();
  const response = http.post(
    `${BASE_URL}/v1/auth/register`,
    JSON.stringify({
      email: `steady-${stamp}@example.com`,
      password: "steady-test-password",
      display_name: "Steady",
      organization_name: `Steady ${stamp}`,
    }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (response.status !== 201) fail(`register returned ${response.status}`);
  return { token: response.json("access_token") };
}

export default function (data) {
  const response = http.get(`${BASE_URL}/v1/runs/stats`, { headers: { Authorization: `Bearer ${data.token}` } });
  if (response.status === 0) connection_errors.add(1);
  if (response.status >= 500) server_errors.add(1);
  check(response, { "status is 200": (result) => result.status === 200 });
}
