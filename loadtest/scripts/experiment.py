#!/usr/bin/env python3
"""Repeatable measurements behind docs/loadtest.md and docs/chaos/.

    python scripts/experiment.py throughput --agent slow --repetitions 4 --concurrency 32
    python scripts/experiment.py kill-worker --platform kubernetes
    python scripts/experiment.py kill-worker --platform compose

Every number printed comes from the running system, through its public API.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from typing import Any

from seed_demo import Client


def new_workspace(client: Client, agent_path: str) -> tuple[str, str]:
    session = client.must(
        "POST",
        "/v1/auth/register",
        {
            "email": f"experiment-{uuid.uuid4().hex[:10]}@example.com",
            "password": "experiment-password",
            "display_name": "Experiment",
            "organization_name": f"Experiment {uuid.uuid4().hex[:6]}",
        },
    )
    client.token = session["access_token"]
    suite_id = client.must("POST", "/v1/scenarios/starter-pack")["suite_id"]
    agent = client.must(
        "POST",
        "/v1/agents",
        {
            "name": f"Experiment {agent_path}",
            "agent_type": "customer_support",
            "endpoint_url": f"http://sample-agent:8000/agents/{agent_path}",
        },
    )
    return agent["id"], suite_id


def start(client: Client, agent_id: str, suite_id: str, **options: Any) -> dict[str, Any]:
    body = {"agent_id": agent_id, "suite_id": suite_id, "timeout_seconds": 30, "seed": 1, **options}
    return client.must("POST", "/v1/runs", body)


def wait(client: Client, run_id: str, timeout: float = 600) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.must("GET", f"/v1/runs/{run_id}")
        if run["status"] in ("completed", "failed", "cancelled"):
            return run
        time.sleep(0.5)
    raise SystemExit(f"Run {run_id} did not finish: {run}")


def benchmark(client: Client, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status, body = client.call("GET", f"/v1/benchmarks/runs/{run_id}")
        if status == 200 and body["final"]:
            return body["summary"]
        time.sleep(0.5)
    raise SystemExit("Benchmark did not appear")


def seconds(run: dict[str, Any]) -> float:
    def parse(value: str) -> float:
        return time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")) + float("0." + value[20:26].rstrip("Z") or 0)

    return round(parse(run["finished_at"]) - parse(run["created_at"]), 2)


def throughput(client: Client, arguments: argparse.Namespace) -> None:
    agent_id, suite_id = new_workspace(client, arguments.agent)
    started = time.perf_counter()
    run = start(
        client,
        agent_id,
        suite_id,
        repetitions=arguments.repetitions,
        concurrency=arguments.concurrency,
    )
    finished = wait(client, run["id"])
    wall = round(time.perf_counter() - started, 2)
    summary = benchmark(client, run["id"])
    print(
        json.dumps(
            {
                "executions": finished["total_executions"],
                "status": finished["status"],
                "wall_seconds": wall,
                "executions_per_minute": summary["throughput"]["executions_per_minute"],
                "queue_wait_p95_ms": summary["throughput"]["p95_queue_wait_ms"],
                "latency_p95_ms": summary["latency_ms"]["p95"],
                "pass_rate": summary["totals"]["pass_rate"],
            },
            indent=2,
        )
    )


def kill_workers(platform: str) -> None:
    if platform == "kubernetes":
        command = [
            "kubectl",
            "-n",
            "agentarena",
            "delete",
            "pod",
            "-l",
            "app.kubernetes.io/component=worker",
            "--grace-period=0",
            "--force",
        ]
    else:
        command = ["docker", "compose", "kill", "-s", "SIGKILL", "run-orchestrator-worker"]
    subprocess.run(command, check=True, capture_output=True)


def restart_workers(platform: str) -> None:
    if platform == "compose":
        subprocess.run(["docker", "compose", "up", "-d", "run-orchestrator-worker"], check=True, capture_output=True)


def kill_worker(client: Client, arguments: argparse.Namespace) -> None:
    agent_id, suite_id = new_workspace(client, "slow")
    options = {"repetitions": 2, "concurrency": 8, "max_retries": 2}

    baseline_run = start(client, agent_id, suite_id, **options)
    baseline = wait(client, baseline_run["id"])
    print(f"baseline: {seconds(baseline)} s, {baseline['progress']['completed']} completed")

    run = start(client, agent_id, suite_id, **options)
    while client.must("GET", f"/v1/runs/{run['id']}")["progress"]["running"] < 4:
        time.sleep(0.1)
    in_flight = client.must("GET", f"/v1/runs/{run['id']}")["progress"]["running"]

    killed_at = time.monotonic()
    kill_workers(arguments.platform)
    print(f"killed the workers with {in_flight} executions running")
    time.sleep(1)
    restart_workers(arguments.platform)

    recovered_after = None
    while True:
        current = client.must("GET", f"/v1/runs/{run['id']}")
        rows = client.must("GET", f"/v1/runs/{run['id']}/executions?limit=200")["items"]
        if recovered_after is None and any(row["attempt"] > 1 for row in rows):
            recovered_after = round(time.monotonic() - killed_at, 1)
        if current["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.25)

    evaluations = client.must("GET", f"/v1/evaluations?run_id={run['id']}&limit=500")["items"]
    ids = [row["execution_id"] for row in evaluations]
    rows = client.must("GET", f"/v1/runs/{run['id']}/executions?limit=200")["items"]
    print(
        json.dumps(
            {
                "baseline_seconds": seconds(baseline),
                "with_kill_seconds": seconds(current),
                "added_seconds": round(seconds(current) - seconds(baseline), 2),
                "in_flight_when_killed": in_flight,
                "recovered_after_seconds": recovered_after,
                "retried_executions": sum(row["attempt"] > 1 for row in rows),
                "total_executions": current["total_executions"],
                "completed": current["progress"]["completed"],
                "failed": current["progress"]["failed"],
                "evaluations": len(ids),
                "duplicate_evaluations": len(ids) - len(set(ids)),
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    commands = parser.add_subparsers(dest="command", required=True)

    measure = commands.add_parser("throughput")
    measure.add_argument("--agent", default="v2")
    measure.add_argument("--repetitions", type=int, default=4)
    measure.add_argument("--concurrency", type=int, default=16)
    measure.set_defaults(handler=throughput)

    chaos = commands.add_parser("kill-worker")
    chaos.add_argument("--platform", choices=("kubernetes", "compose"), default="kubernetes")
    chaos.set_defaults(handler=kill_worker)

    arguments = parser.parse_args()
    arguments.handler(Client(arguments.base_url), arguments)


if __name__ == "__main__":
    main()
