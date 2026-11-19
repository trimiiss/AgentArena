from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from agentarena_common.metrics import LATENCY_BUCKETS

LONG_BUCKETS = (*LATENCY_BUCKETS, 20.0, 30.0, 60.0, 120.0, 300.0)

WORKER_CAPACITY = Gauge("worker_capacity_slots", "Concurrent executions this worker can run.", ["service"])
WORKER_BUSY = Gauge("worker_busy_slots", "Executions running on this worker right now.", ["service"])

EXECUTIONS = Counter("executions_total", "Execution attempts finished, by result.", ["service", "status"])
EXECUTION_DURATION = Histogram(
    "execution_duration_seconds", "Wall-clock time of one execution attempt.", ["service"], buckets=LONG_BUCKETS
)
QUEUE_WAIT = Histogram(
    "execution_queue_wait_seconds",
    "Time between dispatch and a worker starting the execution.",
    ["service"],
    buckets=LONG_BUCKETS,
)
AGENT_CALLS = Counter("agent_calls_total", "Calls to agents under test, by outcome.", ["service", "outcome"])
AGENT_CALL_DURATION = Histogram(
    "agent_call_duration_seconds", "Latency of one call to an agent.", ["service"], buckets=LONG_BUCKETS
)
EXECUTION_RETRIES = Counter("execution_retries_total", "Attempts scheduled for retry.", ["service", "reason"])
LEASES_RECOVERED = Counter(
    "execution_leases_recovered_total", "Executions recovered by the reaper.", ["service", "reason"]
)
CONFIG_CACHE = Counter("worker_config_cache_total", "Worker config cache lookups.", ["service", "kind", "result"])
RUNS_FINISHED = Counter("runs_finished_total", "Runs reaching a final state.", ["service", "status"])
RUNS_CREATED = Counter("runs_created_total", "Runs accepted by the API.", ["service"])
