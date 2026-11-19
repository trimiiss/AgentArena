#!/usr/bin/env python3
"""Generates the Grafana dashboards, so every panel shares one layout and style.

    python observability/grafana/generate_dashboards.py

Writes to observability/grafana/dashboards/ for Compose and to
k8s/observability/dashboards/ for Kubernetes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGETS = (HERE / "dashboards", ROOT / "k8s" / "observability" / "dashboards")
DATASOURCE = {"type": "prometheus", "uid": "prometheus"}


def target(expr: str, legend: str = "", ref: str = "A") -> dict[str, Any]:
    return {"datasource": DATASOURCE, "expr": expr, "legendFormat": legend, "refId": ref}


def graph(title: str, exprs: list[tuple[str, str]], unit: str = "short", description: str = "") -> dict[str, Any]:
    return {
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DATASOURCE,
        "targets": [target(expr, legend, chr(65 + index)) for index, (expr, legend) in enumerate(exprs)],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "min": 0,
                "custom": {"lineWidth": 2, "fillOpacity": 8, "showPoints": "never", "spanNulls": True},
            },
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }


def stat(
    title: str, expr: str, unit: str = "short", thresholds: list[tuple[float | None, str]] | None = None
) -> dict[str, Any]:
    steps = [{"value": value, "color": color} for value, color in thresholds or [(None, "blue")]]
    return {
        "type": "stat",
        "title": title,
        "datasource": DATASOURCE,
        "targets": [target(expr)],
        "fieldConfig": {
            "defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
            "overrides": [],
        },
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"},
    }


def table(title: str, expr: str) -> dict[str, Any]:
    return {
        "type": "table",
        "title": title,
        "datasource": DATASOURCE,
        "targets": [{**target(expr), "format": "table", "instant": True}],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True},
    }


def layout(panels: list[tuple[dict[str, Any], int, int]]) -> list[dict[str, Any]]:
    """Places panels left to right, wrapping at Grafana's 24-column width."""
    placed = []
    x = y = row_height = 0
    for index, (panel, width, height) in enumerate(panels, start=1):
        if x + width > 24:
            x = 0
            y += row_height
            row_height = 0
        placed.append({**panel, "id": index, "gridPos": {"x": x, "y": y, "w": width, "h": height}})
        x += width
        row_height = max(row_height, height)
    return placed


def dashboard(uid: str, title: str, panels: list[tuple[dict[str, Any], int, int]]) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "tags": ["agentarena"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "panels": layout(panels),
    }


def p95(metric: str, by: str = "", selector: str = "") -> str:
    group = f"le, {by}" if by else "le"
    return f"histogram_quantile(0.95, sum by ({group}) (rate({metric}_bucket{selector}[1m])))"


GOOD_WHEN_LOW = [(None, "green"), (0.01, "orange"), (0.05, "red")]

SYSTEM = dashboard(
    "agentarena-system",
    "AgentArena / System health",
    [
        (stat("Targets up", "sum(up)", thresholds=[(None, "red"), (1, "green")]), 4, 5),
        (stat("Targets down", "count(up == 0) or vector(0)", thresholds=[(None, "green"), (1, "red")]), 4, 5),
        (stat("Requests per second", "sum(rate(http_requests_total[1m]))", "reqps"), 4, 5),
        (
            stat(
                "Error rate (5xx)",
                'sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(rate(http_requests_total[1m])) or vector(0)',
                "percentunit",
                GOOD_WHEN_LOW,
            ),
            4,
            5,
        ),
        (stat("Latency p95", p95("http_request_duration_seconds"), "s"), 4, 5),
        (
            stat(
                "Alerts firing",
                'count(ALERTS{alertstate="firing"}) or vector(0)',
                thresholds=[(None, "green"), (1, "red")],
            ),
            4,
            5,
        ),
        (graph("Availability by job", [("up", "{{job}} {{component}}")]), 12, 8),
        (table("Alerts firing now", 'ALERTS{alertstate="firing"}'), 12, 8),
        (
            graph(
                "Run submission SLO: p95 latency (objective 300 ms)",
                [
                    (
                        p95(
                            "http_request_duration_seconds",
                            "",
                            '{service="run-orchestrator",method="POST",route="/v1/runs"}',
                        ),
                        "p95",
                    )
                ],
                "s",
            ),
            12,
            8,
        ),
        (
            graph(
                "Run submission SLO: success rate (objective 99%)",
                [
                    (
                        'sum(rate(http_requests_total{service="run-orchestrator",method="POST",route="/v1/runs",'
                        'status=~"2.."}[1m])) / sum(rate(http_requests_total{service="run-orchestrator",'
                        'method="POST",route="/v1/runs"}[1m]))',
                        "success rate",
                    )
                ],
                "percentunit",
            ),
            12,
            8,
        ),
    ],
)

SERVICES = dashboard(
    "agentarena-services",
    "AgentArena / Microservices (RED)",
    [
        (
            graph(
                "Rate: requests per second",
                [("sum by (service) (rate(http_requests_total[1m]))", "{{service}}")],
                "reqps",
            ),
            8,
            8,
        ),
        (
            graph(
                "Errors: 5xx responses per second",
                [('sum by (service) (rate(http_requests_total{status=~"5.."}[1m]))', "{{service}}")],
                "reqps",
            ),
            8,
            8,
        ),
        (graph("Duration: p95 latency", [(p95("http_request_duration_seconds", "service"), "{{service}}")], "s"), 8, 8),
        (
            graph(
                "Slowest routes (p95)",
                [(f"topk(8, {p95('http_request_duration_seconds', 'service, route')})", "{{service}} {{route}}")],
                "s",
            ),
            12,
            8,
        ),
        (
            graph(
                "Responses by status",
                [("sum by (status) (rate(http_requests_total[1m]))", "{{status}}")],
                "reqps",
            ),
            12,
            8,
        ),
        (
            graph(
                "Calls to other services",
                [
                    (
                        "sum by (service, target, outcome) (rate(downstream_requests_total[1m]))",
                        "{{service}} to {{target}}: {{outcome}}",
                    )
                ],
                "reqps",
            ),
            8,
            8,
        ),
        (
            graph(
                "Dependency latency p95",
                [(p95("downstream_request_duration_seconds", "service, target"), "{{service}} to {{target}}")],
                "s",
            ),
            8,
            8,
        ),
        (
            graph(
                "Circuit breakers (0 closed, 1 half-open, 2 open)",
                [("max by (service, target) (circuit_breaker_state)", "{{service}} to {{target}}")],
            ),
            8,
            8,
        ),
        (
            graph(
                "Rate limited requests",
                [("sum by (service, scope) (rate(rate_limited_requests_total[1m]))", "{{service}} {{scope}}")],
                "reqps",
            ),
            12,
            7,
        ),
        (graph("Requests in flight", [("sum by (service) (http_requests_in_progress)", "{{service}}")]), 12, 7),
    ],
)

RABBITMQ = dashboard(
    "agentarena-rabbitmq",
    "AgentArena / RabbitMQ and events",
    [
        (
            stat(
                "Execution queue depth",
                'sum(rabbitmq_queue_messages_ready{queue="orchestrator.execution-jobs"}) or vector(0)',
            ),
            6,
            5,
        ),
        (
            stat(
                "Dead-lettered messages",
                'sum(rabbitmq_queue_messages{queue=~".+\\\\.dlq"}) or vector(0)',
                thresholds=[(None, "green"), (1, "red")],
            ),
            6,
            5,
        ),
        (
            stat(
                "Outbox backlog",
                "sum(outbox_pending_events) or vector(0)",
                thresholds=[(None, "green"), (100, "orange"), (500, "red")],
            ),
            6,
            5,
        ),
        (stat("Consumers", "sum(rabbitmq_queue_consumers) or vector(0)"), 6, 5),
        (
            graph(
                "Queue depth",
                [
                    ('sum by (queue) (rabbitmq_queue_messages_ready{queue!~".+retry.+"})', "{{queue}} ready"),
                    ('sum by (queue) (rabbitmq_queue_messages_unacked{queue!~".+retry.+"})', "{{queue}} in progress"),
                ],
            ),
            12,
            8,
        ),
        (
            graph(
                "Messages waiting to retry",
                [('sum by (queue) (rabbitmq_queue_messages{queue=~".+retry.+"})', "{{queue}}")],
            ),
            12,
            8,
        ),
        (
            graph(
                "Events published per second",
                [("sum by (event_type) (rate(events_published_total[1m]))", "{{event_type}}")],
                "ops",
            ),
            12,
            8,
        ),
        (
            graph(
                "Messages handled per second",
                [("sum by (queue, outcome) (rate(events_consumed_total[1m]))", "{{queue}}: {{outcome}}")],
                "ops",
            ),
            12,
            8,
        ),
        (
            graph(
                "Message errors: retried or dead-lettered",
                [
                    (
                        'sum by (queue, outcome) (rate(events_consumed_total{outcome=~"retried|dead_lettered"}[1m]))',
                        "{{queue}}: {{outcome}}",
                    )
                ],
                "ops",
            ),
            8,
            8,
        ),
        (graph("Handler duration p95", [(p95("event_handler_duration_seconds", "queue"), "{{queue}}")], "s"), 8, 8),
        (graph("Outbox publish lag p95", [(p95("outbox_publish_lag_seconds", "service"), "{{service}}")], "s"), 8, 8),
    ],
)

EXECUTION = dashboard(
    "agentarena-execution",
    "AgentArena / Execution performance",
    [
        (stat("Executions per second", "sum(rate(executions_total[1m])) or vector(0)", "ops"), 6, 5),
        (stat("Worker utilization", "sum(worker_busy_slots) / sum(worker_capacity_slots)", "percentunit"), 6, 5),
        (stat("Queue wait p95", p95("execution_queue_wait_seconds"), "s"), 6, 5),
        (
            stat(
                "Leases recovered (1h)",
                "sum(increase(execution_leases_recovered_total[1h])) or vector(0)",
                thresholds=[(None, "green"), (1, "orange")],
            ),
            6,
            5,
        ),
        (
            graph(
                "Execution throughput by result",
                [("sum by (status) (rate(executions_total[1m]))", "{{status}}")],
                "ops",
            ),
            12,
            8,
        ),
        (
            graph(
                "Workers: busy and available slots",
                [("sum(worker_busy_slots)", "busy"), ("sum(worker_capacity_slots)", "capacity")],
            ),
            12,
            8,
        ),
        (graph("Queue wait p95", [(p95("execution_queue_wait_seconds"), "p95")], "s"), 8, 8),
        (graph("Execution duration p95", [(p95("execution_duration_seconds"), "p95")], "s"), 8, 8),
        (graph("Agent call latency p95", [(p95("agent_call_duration_seconds"), "p95")], "s"), 8, 8),
        (
            graph(
                "Agent call errors",
                [('sum by (outcome) (rate(agent_calls_total{outcome!="ok"}[1m]))', "{{outcome}}")],
                "ops",
            ),
            8,
            8,
        ),
        (
            graph("Retries scheduled", [("sum by (reason) (rate(execution_retries_total[1m]))", "{{reason}}")], "ops"),
            8,
            8,
        ),
        (graph("Evaluation duration p95", [(p95("evaluation_duration_seconds"), "p95")], "s"), 8, 8),
        (
            graph("Evaluations by result", [("sum by (result) (rate(evaluations_total[1m]))", "{{result}}")], "ops"),
            12,
            7,
        ),
        (graph("Runs finished", [("sum by (status) (rate(runs_finished_total[5m]))", "{{status}}")], "ops"), 12, 7),
    ],
)

DASHBOARDS = {
    "system-health.json": SYSTEM,
    "microservices.json": SERVICES,
    "rabbitmq.json": RABBITMQ,
    "execution.json": EXECUTION,
}


def main() -> None:
    for directory in TARGETS:
        directory.mkdir(parents=True, exist_ok=True)
        for name, document in DASHBOARDS.items():
            (directory / name).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"wrote {directory.relative_to(ROOT) / name}")


if __name__ == "__main__":
    main()
