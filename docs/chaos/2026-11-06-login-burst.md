# Experiment: a burst of logins

Date: 6 November 2026. Environment: kind cluster on the laptop described in [slo.md](../slo.md). The identity service has a 512 MiB memory limit and one replica.

## Hypothesis

If 40 failed logins for one account arrive at once from 10 parallel clients, the rate limiter answers most with 429 and the service keeps running.

## Method

`tests/e2e/test_security.py::TestSessions::test_repeated_failed_logins_are_rate_limited` sends 40 requests through the ingress, 10 at a time. The test suite ran against the cluster:

```bash
AGENTARENA_BASE_URL=http://127.0.0.1:8088 pytest tests/e2e/test_benchmark_flow.py tests/e2e/test_security.py tests/e2e/test_failure_modes.py
```

## What broke

The hypothesis failed. The ingress returned 502, and the 21 tests after it failed in setup, because the identity service was down. The pod's last state showed why:

```text
identity-service-7f79fbb46-4xkzf restarts=2 lastState=OOMKilled exit=137
```

Each Argon2 password check uses 64 MiB. The rate limiter allows a burst of 10, and the thread pool runs up to 16 tasks at once. Ten parallel checks need about 640 MiB, which is above the 512 MiB limit. The kernel killed the process.

Anyone could have crashed login for every user with a few parallel requests. Compose never showed this, because it sets no memory limits there.

## Fix

A semaphore allows at most 4 password operations at a time. Others wait their turn, so hashing memory stays at 256 MiB, whatever the load. A regression test sends 30 parallel checks and asserts that no more than 4 ever run together.

## Results

| Measure | Before the fix | After the fix |
| --- | --- | --- |
| Concurrent password operations | Up to 16 | At most 4 |
| Pod restarts in the test run | 2, both OOM kills | 0 |
| Peak memory of the pod | Above the 512 MiB limit | 338 MiB |
| OOM kills (`memory.events`) | 2 | 0 |
| Responses to the burst | 502 from the ingress | 401 and 429 |
| End-to-end tests on Kubernetes | 44 of 66 passed | 66 of 66 passed, in 90 s |

## Conclusion

A resource limit turns a slow path into a crash path. Any operation that uses a lot of memory per request needs its own concurrency limit, because a rate limit controls requests per second, not requests in flight.

This also shows why tests must run on the real deployment target. The same image passed every test on Compose, where nothing limits memory.
