"""The product's main promise, exercised against the real running system."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Api


@pytest.fixture(scope="module")
def benchmarks(shared_api: Api) -> dict[str, Any]:
    """Benchmarks a flawed v1 and a fixed v2 of one agent on the starter suite."""
    pack = shared_api.starter_pack()
    agent = shared_api.create_agent("Support Bot", "v1")
    v2 = shared_api.ok(
        shared_api.post(
            f"/v1/agents/{agent['id']}/versions",
            json={
                "endpoint_url": "http://sample-agent:8000/agents/v2",
                "input_cost_per_1k": "0.003",
                "output_cost_per_1k": "0.015",
                "notes": "Adds policy checks and tool failure handling.",
            },
        )
    )
    first = shared_api.start_run(agent["id"], pack["suite_id"], agent_version=1, repetitions=2, seed=7)
    second = shared_api.start_run(agent["id"], pack["suite_id"], agent_version=v2["version"], repetitions=2, seed=7)
    return {
        "agent": agent,
        "pack": pack,
        "v1_run": shared_api.wait_for_run(first["id"]),
        "v2_run": shared_api.wait_for_run(second["id"]),
        "v1": shared_api.wait_for_benchmark(first["id"]),
        "v2": shared_api.wait_for_benchmark(second["id"]),
    }


def test_runs_finish_with_every_execution_evaluated(benchmarks: dict[str, Any]) -> None:
    for key in ("v1_run", "v2_run"):
        run = benchmarks[key]
        assert run["status"] == "completed"
        assert run["total_executions"] == 24
        assert run["progress"]["completed"] == 24
        assert run["progress"]["evaluated"] == 24


def test_careful_agent_passes_the_whole_suite(benchmarks: dict[str, Any]) -> None:
    summary = benchmarks["v2"]["summary"]
    failed = [item["scenario_name"] for item in summary["scenarios"] if item["pass_rate"] < 1]
    assert failed == []
    assert benchmarks["v2"]["verdict"] == "passed"


def test_flawed_agent_is_caught(benchmarks: dict[str, Any]) -> None:
    summary = benchmarks["v1"]["summary"]
    assert benchmarks["v1"]["verdict"] == "failed"
    assert summary["totals"]["pass_rate"] < 0.6
    assert "policy_violation" in summary["failure_categories"]
    assert "unhandled_tool_failure" in summary["failure_categories"]
    assert summary["recommendations"], "failures must produce advice"


def test_policy_violations_are_blamed_on_the_right_scenarios(benchmarks: dict[str, Any]) -> None:
    scenarios = {item["scenario_name"]: item for item in benchmarks["v1"]["summary"]["scenarios"]}
    assert scenarios["Refund above the limit must be escalated"]["pass_rate"] == 0
    assert "policy_violation" in scenarios["Prompt injection asks to bypass the refund limit"]["failure_categories"]
    assert scenarios["Order status lookup"]["pass_rate"] == 1


def test_metrics_come_from_real_interactions(benchmarks: dict[str, Any]) -> None:
    summary = benchmarks["v2"]["summary"]
    assert summary["latency_ms"]["p50"] > 0
    assert summary["latency_ms"]["p95"] >= summary["latency_ms"]["p50"]
    assert summary["usage"]["input_tokens"] > 0
    assert summary["usage"]["cost_usd"] > 0
    assert summary["throughput"]["executions_per_minute"] > 0


def test_version_comparison_shows_the_improvement(shared_api: Api, benchmarks: dict[str, Any]) -> None:
    agent_id = benchmarks["agent"]["id"]
    body = shared_api.ok(
        shared_api.get(
            "/v1/comparisons/agent-versions",
            params={"agent_id": agent_id, "base_version": 1, "candidate_version": 2},
        )
    )
    comparison = body["comparison"]
    assert comparison["outcome"] == "improved"
    assert comparison["pass_rate"]["change"] > 0.3
    assert comparison["pass_rate"]["significant"] is True
    assert comparison["improvements"] and not comparison["regressions"]
    assert comparison["shared_scenarios"] == 12


def test_execution_trace_shows_the_whole_conversation(shared_api: Api, benchmarks: dict[str, Any]) -> None:
    run_id = benchmarks["v1_run"]["id"]
    executions = shared_api.ok(shared_api.get(f"/v1/runs/{run_id}/executions"))["items"]
    outage = next(item for item in executions if item["scenario_name"] == "Order lookup outage")
    trace = shared_api.ok(shared_api.get(f"/v1/executions/{outage['id']}"))
    kinds = [step["type"] for step in trace["steps"]]
    assert kinds[0] == "user_message"
    assert {"agent_response", "tool_call", "tool_result"} <= set(kinds)
    injected = [step for step in trace["steps"] if step["payload"].get("injected_failure")]
    assert injected, "the scripted tool failure must appear in the trace"

    evaluation = shared_api.ok(shared_api.get(f"/v1/evaluations/executions/{outage['id']}"))
    assert evaluation["passed"] is False
    checks = {check["evaluator"]: check for check in evaluation["checks"]}
    assert checks["forbidden_phrases"]["passed"] is False


def test_reports_download_in_every_format(shared_api: Api, benchmarks: dict[str, Any]) -> None:
    run_id = benchmarks["v1_run"]["id"]
    for report_format, marker in (
        ("json", "pass_rate"),
        ("csv", "execution_id"),
        ("md", "# Benchmark"),
        ("html", "<h1>"),
    ):
        response = shared_api.get(f"/v1/benchmarks/runs/{run_id}/report", params={"format": report_format})
        assert response.status_code == 200
        assert marker in response.text
        assert "attachment" in response.headers["content-disposition"]


def test_dashboard_analytics_match_the_runs(shared_api: Api, benchmarks: dict[str, Any]) -> None:
    overview = shared_api.ok(shared_api.get("/v1/analytics/overview"))
    assert overview["executions"] == 48
    assert overview["benchmarked_runs"] == 2
    assert overview["runs_passed"] == 1
    assert overview["runs_failed"] == 1
    assert overview["p95_latency_ms"] >= overview["p50_latency_ms"] > 0

    stats = shared_api.ok(shared_api.get("/v1/runs/stats"))
    assert stats["total_runs"] == 2
    assert stats["finished_executions"] == 48

    assert shared_api.ok(shared_api.get("/v1/analytics/timeseries", params={"bucket": "hour"}))
    assert len(shared_api.ok(shared_api.get("/v1/analytics/agents"))) == 2
    assert shared_api.ok(shared_api.get("/v1/analytics/failures"))
    dimensions = {row["dimension"] for row in shared_api.ok(shared_api.get("/v1/analytics/dimensions"))}
    assert {"policy", "tool_use", "correctness"} <= dimensions


def test_replay_reuses_versions_and_seed(shared_api: Api, benchmarks: dict[str, Any]) -> None:
    original = benchmarks["v2_run"]
    replay = shared_api.ok(shared_api.post(f"/v1/runs/{original['id']}/replay"))
    assert replay["replay_of"] == original["id"]
    assert replay["seed"] == original["seed"]
    assert replay["agent_version"] == original["agent_version"]
    finished = shared_api.wait_for_run(replay["id"])
    assert finished["progress"]["passed"] == original["progress"]["passed"]
