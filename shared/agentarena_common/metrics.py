"""Prometheus metrics shared by all processes.

Names follow Prometheus conventions so the same dashboards and alert rules
work for every service. Labels are bounded: routes use the matched template,
never the raw path.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0)

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests handled.", ["service", "method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency.",
    ["service", "method", "route"],
    buckets=LATENCY_BUCKETS,
)
HTTP_IN_PROGRESS = Gauge("http_requests_in_progress", "HTTP requests in flight.", ["service"])

DOWNSTREAM_REQUESTS = Counter("downstream_requests_total", "Calls to other services.", ["service", "target", "outcome"])
DOWNSTREAM_LATENCY = Histogram(
    "downstream_request_duration_seconds",
    "Latency of calls to other services.",
    ["service", "target"],
    buckets=LATENCY_BUCKETS,
)
CIRCUIT_STATE = Gauge("circuit_breaker_state", "Circuit state: 0 closed, 1 half-open, 2 open.", ["service", "target"])

EVENTS_PUBLISHED = Counter("events_published_total", "Events confirmed by the broker.", ["service", "event_type"])
EVENTS_CONSUMED = Counter(
    "events_consumed_total",
    "Messages handled, by outcome: processed, duplicate, retried, dead_lettered.",
    ["service", "queue", "event_type", "outcome"],
)
EVENT_HANDLER_SECONDS = Histogram(
    "event_handler_duration_seconds",
    "Time spent handling one message.",
    ["service", "queue"],
    buckets=LATENCY_BUCKETS,
)
OUTBOX_PENDING = Gauge("outbox_pending_events", "Rows waiting in the outbox.", ["service"])
OUTBOX_LAG = Histogram(
    "outbox_publish_lag_seconds",
    "Delay between writing an outbox row and broker confirmation.",
    ["service"],
    buckets=LATENCY_BUCKETS,
)

RATE_LIMITED = Counter("rate_limited_requests_total", "Requests rejected with 429.", ["service", "scope"])
