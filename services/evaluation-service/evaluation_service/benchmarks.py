"""Turns individual evaluations into benchmarks, comparisons and advice."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from evaluation_service.models import Evaluation

_ADVICE: dict[str, str] = {
    "policy_violation": (
        "Policy violations are the most serious finding. Enforce limits in code around the tool, not only in the "
        "prompt, and add refusal examples for the scenarios listed."
    ),
    "unhandled_tool_failure": (
        "The agent mishandles tool errors. Treat tool errors as first-class results: retry transient failures once, "
        "then tell the user what happened and offer a next step. Never answer as if the call had worked."
    ),
    "incorrect_tool_use": (
        "Tool calls are missing or wrong. Tighten tool descriptions and argument schemas, and make the required "
        "order explicit, for example look the order up before refunding it."
    ),
    "incorrect_response": (
        "Answers miss required facts. Ground the final response in tool results and check that every required "
        "field reaches the user."
    ),
    "task_incomplete": (
        "The agent stops early or reports the wrong task status. Check that it finishes every requested task and "
        "asks for missing information instead of guessing."
    ),
    "timeout": (
        "Executions are timing out. Profile the slowest scenarios, cap retries inside the agent, and raise the run "
        "timeout only if this latency is acceptable to users."
    ),
    "agent_error": "The endpoint is returning errors. Check the agent's logs using the execution ids in this report.",
    "protocol_violation": "Responses do not match the agent protocol. Validate them against the published schema.",
    "agent_loop": "The agent exceeded its call budget. Add a stop condition so it cannot repeat the same tool call.",
    "latency_budget_exceeded": "Responses exceed the latency budget. Reduce tool round trips or use a faster model.",
    "cost_budget_exceeded": "Executions exceed their token budget. Shorten prompts and trim conversation history.",
    "poor_response_quality": "Responses leak internal text or repeat themselves. Add an output check before replying.",
    "infrastructure": "Some failures came from AgentArena itself, not the agent. Re-run before drawing conclusions.",
}


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Linear interpolation between closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _grouped_pass_rates(rows: Iterable[Evaluation], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Evaluation]] = defaultdict(list)
    for row in rows:
        groups[getattr(row, key)].append(row)
    return {
        name: {
            "total": len(items),
            "passed": sum(item.passed for item in items),
            "pass_rate": _rate(sum(item.passed for item in items), len(items)),
        }
        for name, items in sorted(groups.items())
    }


def _scenario_breakdown(rows: Iterable[Evaluation]) -> list[dict[str, Any]]:
    groups: dict[Any, list[Evaluation]] = defaultdict(list)
    for row in rows:
        groups[row.scenario_id].append(row)
    breakdown = []
    for scenario_id, items in groups.items():
        passed = sum(item.passed for item in items)
        categories = Counter(category for item in items for category in item.failure_categories)
        breakdown.append(
            {
                "scenario_id": str(scenario_id),
                "scenario_name": items[0].scenario_name,
                "scenario_type": items[0].scenario_type,
                "difficulty": items[0].difficulty,
                "scenario_version": items[0].scenario_version,
                "total": len(items),
                "passed": passed,
                "pass_rate": _rate(passed, len(items)),
                "avg_score": round(sum(item.score for item in items) / len(items), 4),
                "failure_categories": dict(categories),
            }
        )
    return sorted(breakdown, key=lambda item: (item["pass_rate"], item["scenario_name"]))


def recommendations(categories: Counter[str], total: int) -> list[dict[str, Any]]:
    """Deterministic advice, ordered by how often each failure occurred."""
    priority = {"policy_violation": 0, "unhandled_tool_failure": 1}
    ordered = sorted(categories.items(), key=lambda item: (priority.get(item[0], 2), -item[1]))
    return [
        {
            "category": category,
            "occurrences": count,
            "share": _rate(count, total),
            "advice": _ADVICE.get(category, "Review the failed executions listed in this report."),
        }
        for category, count in ordered
    ]


def compute_summary(rows: Sequence[Evaluation], pass_bar: float) -> dict[str, Any]:
    total = len(rows)
    passed = sum(row.passed for row in rows)
    completed = [row for row in rows if row.execution_status == "completed"]
    timed_out = sum(row.execution_status == "timed_out" for row in rows)
    failed = sum(row.execution_status == "failed" for row in rows)

    latencies = [row.latency_ms for row in completed]
    costs = [row.cost_usd for row in rows if row.cost_usd is not None]
    starts = [row.started_at for row in rows if row.started_at]
    ends = [row.finished_at for row in rows if row.finished_at]
    elapsed = (max(ends) - min(starts)).total_seconds() if starts and ends else 0.0

    dimension_totals: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for dimension, score in row.dimension_scores.items():
            dimension_totals[dimension].append(score)

    categories: Counter[str] = Counter(category for row in rows for category in row.failure_categories)
    pass_rate = _rate(passed, total)
    return {
        "verdict": "passed" if total and pass_rate >= pass_bar else "failed",
        "pass_bar": pass_bar,
        "totals": {
            "executions": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": pass_rate,
            "avg_score": round(sum(row.score for row in rows) / total, 4) if total else 0.0,
        },
        "reliability": {
            "completed": len(completed),
            "failed": failed,
            "timed_out": timed_out,
            "success_rate": _rate(len(completed), total),
            "failure_rate": _rate(failed, total),
            "timeout_rate": _rate(timed_out, total),
            "retried": sum(row.attempts > 1 for row in rows),
        },
        "latency_ms": {
            "avg": _round(sum(latencies) / len(latencies)) if latencies else None,
            "p50": _round(percentile(latencies, 0.5)),
            "p95": _round(percentile(latencies, 0.95)),
            "max": _round(max(latencies)) if latencies else None,
        },
        "usage": {
            "input_tokens": sum(row.input_tokens for row in rows),
            "output_tokens": sum(row.output_tokens for row in rows),
            "tokens_reported": any(row.input_tokens or row.output_tokens for row in rows),
            "cost_usd": _round(sum(costs), 6) if costs else None,
            "avg_cost_usd": _round(sum(costs) / len(costs), 6) if costs else None,
        },
        "throughput": {
            "elapsed_seconds": round(elapsed, 2),
            "executions_per_minute": round(total / elapsed * 60, 2) if elapsed > 0 else None,
            "avg_queue_wait_ms": _round(sum(row.queue_wait_ms for row in rows) / total) if total else None,
            "p95_queue_wait_ms": _round(percentile([row.queue_wait_ms for row in rows], 0.95)),
        },
        "dimensions": {
            dimension: round(sum(values) / len(values), 4) for dimension, values in sorted(dimension_totals.items())
        },
        "by_scenario_type": _grouped_pass_rates(rows, "scenario_type"),
        "by_difficulty": _grouped_pass_rates(rows, "difficulty"),
        "scenarios": _scenario_breakdown(rows),
        "failure_categories": dict(categories.most_common()),
        "failed_executions": [
            {
                "execution_id": str(row.execution_id),
                "scenario_name": row.scenario_name,
                "repetition": row.repetition,
                "score": row.score,
                "execution_status": row.execution_status,
                "error_code": row.error_code,
                "failure_categories": row.failure_categories,
            }
            for row in sorted(rows, key=lambda item: item.score)
            if not row.passed
        ][:50],
        "recommendations": recommendations(categories, total),
        "reproducibility": {
            "evaluator_versions": sorted({row.evaluator_version for row in rows}),
            "scenario_versions": {
                row.scenario_name: row.scenario_version for row in sorted(rows, key=lambda item: item.scenario_name)
            },
        },
    }


def two_proportion_p_value(pass_a: int, total_a: int, pass_b: int, total_b: int) -> float | None:
    """Two-sided z-test on pass rates. None when the samples cannot support one."""
    if min(total_a, total_b) < 5:
        return None
    pooled = (pass_a + pass_b) / (total_a + total_b)
    variance = pooled * (1 - pooled) * (1 / total_a + 1 / total_b)
    if variance <= 0:
        return None
    z = abs(pass_a / total_a - pass_b / total_b) / math.sqrt(variance)
    return round(math.erfc(z / math.sqrt(2)), 4)


def _delta(base: float | None, candidate: float | None, digits: int = 4) -> float | None:
    if base is None or candidate is None:
        return None
    return round(candidate - base, digits)


def compare(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare two benchmark summaries, matching scenarios by id."""
    base_scenarios = {item["scenario_id"]: item for item in base["scenarios"]}
    candidate_scenarios = {item["scenario_id"]: item for item in candidate["scenarios"]}
    shared = sorted(set(base_scenarios) & set(candidate_scenarios))

    scenarios = []
    regressions = []
    improvements = []
    for scenario_id in shared:
        before = base_scenarios[scenario_id]
        after = candidate_scenarios[scenario_id]
        change = round(after["pass_rate"] - before["pass_rate"], 4)
        entry = {
            "scenario_id": scenario_id,
            "scenario_name": after["scenario_name"],
            "scenario_type": after["scenario_type"],
            "base_pass_rate": before["pass_rate"],
            "candidate_pass_rate": after["pass_rate"],
            "change": change,
            "same_version": before["scenario_version"] == after["scenario_version"],
        }
        scenarios.append(entry)
        if change < 0:
            regressions.append(entry)
        elif change > 0:
            improvements.append(entry)

    base_totals = base["totals"]
    candidate_totals = candidate["totals"]
    pass_change = round(candidate_totals["pass_rate"] - base_totals["pass_rate"], 4)
    p_value = two_proportion_p_value(
        base_totals["passed"], base_totals["executions"], candidate_totals["passed"], candidate_totals["executions"]
    )
    if pass_change > 0:
        outcome = "improved"
    elif pass_change < 0:
        outcome = "regressed"
    else:
        outcome = "unchanged"

    dimensions = sorted(set(base["dimensions"]) | set(candidate["dimensions"]))
    return {
        "outcome": outcome,
        "pass_rate": {
            "base": base_totals["pass_rate"],
            "candidate": candidate_totals["pass_rate"],
            "change": pass_change,
            "p_value": p_value,
            "significant": p_value is not None and p_value < 0.05,
        },
        "latency_ms": {
            key: {
                "base": base["latency_ms"][key],
                "candidate": candidate["latency_ms"][key],
                "change": _delta(base["latency_ms"][key], candidate["latency_ms"][key], 2),
            }
            for key in ("avg", "p50", "p95")
        },
        "cost_usd": {
            "base": base["usage"]["cost_usd"],
            "candidate": candidate["usage"]["cost_usd"],
            "change": _delta(base["usage"]["cost_usd"], candidate["usage"]["cost_usd"], 6),
        },
        "dimensions": [
            {
                "dimension": dimension,
                "base": base["dimensions"].get(dimension),
                "candidate": candidate["dimensions"].get(dimension),
                "change": _delta(base["dimensions"].get(dimension), candidate["dimensions"].get(dimension)),
            }
            for dimension in dimensions
        ],
        "scenarios": sorted(scenarios, key=lambda item: item["change"]),
        "regressions": regressions,
        "improvements": improvements,
        "shared_scenarios": len(shared),
        "only_in_base": len(set(base_scenarios) - set(candidate_scenarios)),
        "only_in_candidate": len(set(candidate_scenarios) - set(base_scenarios)),
    }
