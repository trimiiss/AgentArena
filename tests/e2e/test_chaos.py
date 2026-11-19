"""Failure injection against the Compose stack. These stop and kill real containers.

Run them on their own, because they disturb anything else using the stack:

    pytest tests/e2e/test_chaos.py -m chaos
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import Api

pytestmark = pytest.mark.chaos

ROOT = Path(__file__).resolve().parents[2]


def compose(*arguments: str) -> None:
    subprocess.run(["docker", "compose", *arguments], cwd=ROOT, check=True, capture_output=True, timeout=120)


def wait_until(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {what}")


@pytest.fixture()
def healed() -> Iterator[None]:
    """Whatever a test breaks, the stack is healthy again afterwards."""
    yield
    compose("up", "-d", "--wait", "--wait-timeout", "90")


@pytest.fixture()
def setup(api: Api) -> tuple[Api, str]:
    return api, api.starter_pack()["suite_id"]


def assert_exactly_once(api: Api, run_id: str, expected: int) -> None:
    """Every execution has a single outcome and a single evaluation."""
    evaluations = api.ok(api.get("/v1/evaluations", params={"run_id": run_id, "limit": 500}))
    ids = [row["execution_id"] for row in evaluations["items"]]
    assert len(ids) == expected
    assert len(set(ids)) == expected


def test_killed_worker_loses_no_work(setup: tuple[Api, str], healed: None) -> None:
    api, suite = setup
    agent = api.create_agent(f"Chaos {uuid.uuid4().hex[:6]}", "slow")
    run = api.start_run(agent["id"], suite, repetitions=2, max_retries=2, concurrency=8)

    def executing() -> bool:
        return api.ok(api.get(f"/v1/runs/{run['id']}"))["progress"]["running"] >= 4

    wait_until(executing, 30, "executions to start")
    compose("kill", "-s", "SIGKILL", "run-orchestrator-worker")
    time.sleep(2)
    compose("up", "-d", "run-orchestrator-worker")

    finished = api.wait_for_run(run["id"], timeout=150)
    assert finished["status"] == "completed"
    assert finished["progress"]["completed"] == 24
    assert_exactly_once(api, run["id"], 24)
    rows = api.ok(api.get(f"/v1/runs/{run['id']}/executions"))["items"]
    assert any(row["attempt"] > 1 for row in rows), "killed executions should have been retried"


def test_evaluator_outage_only_delays_results(setup: tuple[Api, str], healed: None) -> None:
    api, suite = setup
    agent = api.create_agent(f"Chaos {uuid.uuid4().hex[:6]}", "v2")
    compose("stop", "evaluation-service-consumer")
    run = api.start_run(agent["id"], suite)

    def evaluating() -> bool:
        return api.ok(api.get(f"/v1/runs/{run['id']}"))["status"] == "evaluating"

    wait_until(evaluating, 30, "the run to wait for evaluations")
    assert api.ok(api.get(f"/v1/runs/{run['id']}"))["progress"]["evaluated"] == 0

    compose("start", "evaluation-service-consumer")
    finished = api.wait_for_run(run["id"], timeout=60)
    assert finished["status"] == "completed"
    assert finished["progress"]["evaluated"] == 12
    assert_exactly_once(api, run["id"], 12)


def test_broker_restart_loses_no_messages(setup: tuple[Api, str], healed: None) -> None:
    api, suite = setup
    agent = api.create_agent(f"Chaos {uuid.uuid4().hex[:6]}", "slow")
    run = api.start_run(agent["id"], suite, repetitions=2, max_retries=3, concurrency=6)

    def executing() -> bool:
        return api.ok(api.get(f"/v1/runs/{run['id']}"))["progress"]["running"] >= 2

    wait_until(executing, 30, "executions to start")
    compose("restart", "rabbitmq")

    finished = api.wait_for_run(run["id"], timeout=170)
    assert finished["status"] == "completed"
    assert finished["progress"]["evaluated"] == 24
    assert_exactly_once(api, run["id"], 24)


def test_graceful_stop_lets_running_work_finish(setup: tuple[Api, str], healed: None) -> None:
    api, suite = setup
    agent = api.create_agent(f"Chaos {uuid.uuid4().hex[:6]}", "slow")
    run = api.start_run(agent["id"], suite, max_retries=0, concurrency=4)

    def executing() -> bool:
        return api.ok(api.get(f"/v1/runs/{run['id']}"))["progress"]["running"] >= 2

    wait_until(executing, 30, "executions to start")
    compose("stop", "run-orchestrator-worker")
    compose("up", "-d", "run-orchestrator-worker")

    finished = api.wait_for_run(run["id"], timeout=120)
    rows = api.ok(api.get(f"/v1/runs/{run['id']}/executions"))["items"]
    assert finished["progress"]["completed"] == 12, [row["error_code"] for row in rows if row["error_code"]]
    assert all(row["attempt"] == 1 for row in rows), "draining must not need retries"


def test_missing_dependency_fails_fast_and_recovers(setup: tuple[Api, str], healed: None) -> None:
    api, suite = setup
    agent = api.create_agent(f"Chaos {uuid.uuid4().hex[:6]}", "v2")
    compose("stop", "scenario-service")
    body = {"agent_id": agent["id"], "suite_id": suite}

    responses = [api.post("/v1/runs", json=body) for _ in range(4)]
    assert all(response.status_code == 503 for response in responses)
    assert responses[-1].headers["retry-after"]

    started = time.perf_counter()
    assert api.post("/v1/runs", json=body).status_code == 503
    assert time.perf_counter() - started < 1, "an open breaker must fail fast"

    compose("start", "scenario-service")

    def accepted() -> bool:
        return api.post("/v1/runs", json=body).status_code == 202

    wait_until(accepted, 60, "the breaker to close")
