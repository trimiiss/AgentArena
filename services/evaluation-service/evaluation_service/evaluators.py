"""Deterministic evaluators: the primary and only source of benchmark scores.

Each evaluator answers one narrow question, and only runs when the scenario
defines what it needs. Given the same transcript and expectations they always
produce the same result, which is what makes benchmarks comparable over time.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import jsonschema

from agentarena_common.events import ExecutionMetrics, TranscriptStep

EVALUATOR_VERSION = "1.0"

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "we",
        "were",
        "will",
        "with",
        "you",
        "your",
    ]
)
_LEAK_PATTERNS = re.compile(r"(traceback \(most recent call last\)|\{\{[^}]*\}\}|undefined|\bnull\b|nan\b)", re.I)

DEFAULT_WEIGHTS: dict[str, float] = {
    "task_completion": 3,
    "correctness": 3,
    "tool_use": 2,
    "policy": 3,
    "error_handling": 2,
    "response_quality": 1,
    "latency": 0.5,
    "cost": 0.5,
}


# These checks measure quality on a sliding scale, so they lower the score
# without failing the scenario. Every other check states something the scenario
# requires, and failing one fails the scenario, whatever the average says.
ADVISORY_EVALUATORS = frozenset({"semantic_similarity", "response_quality", "latency_budget", "cost_budget"})


@dataclass(frozen=True)
class CheckResult:
    evaluator: str
    dimension: str
    score: float
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.evaluator not in ADVISORY_EVALUATORS


@dataclass
class EvaluationContext:
    expected: dict[str, Any]
    criteria: dict[str, Any]
    transcript: list[TranscriptStep]
    metrics: ExecutionMetrics

    @cached_property
    def agent_steps(self) -> list[TranscriptStep]:
        return [step for step in self.transcript if step.type == "agent_response"]

    @cached_property
    def final_step(self) -> TranscriptStep | None:
        return self.agent_steps[-1] if self.agent_steps else None

    @property
    def final_response(self) -> str:
        return (self.final_step.content or "") if self.final_step else ""

    @cached_property
    def agent_text(self) -> str:
        """Everything the agent said. A fact counts wherever it was stated."""
        return "\n".join(step.content or "" for step in self.agent_steps)

    @cached_property
    def tool_calls(self) -> list[TranscriptStep]:
        return [step for step in self.transcript if step.type == "tool_call"]

    @cached_property
    def tool_results(self) -> list[TranscriptStep]:
        return [step for step in self.transcript if step.type == "tool_result"]


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def contains(haystack: str, needle: str) -> bool:
    return normalize(needle) in normalize(haystack)


def _ratio(hits: int, total: int) -> float:
    return hits / total if total else 1.0


def _terms(text: str) -> Counter[str]:
    tokens = [token for token in _TOKEN.findall(text.lower()) if token not in _STOP_WORDS]
    bigrams = [f"{left} {right}" for left, right in zip(tokens, tokens[1:], strict=False)]
    return Counter(tokens + bigrams)


def lexical_similarity(left: str, right: str) -> float:
    """Cosine similarity over word and bigram counts.

    This is a lexical proxy for meaning: fast, offline and deterministic, but
    blind to synonyms. An embedding backend can replace it without touching the
    callers, at the cost of a model dependency.
    """
    a, b = _terms(left), _terms(right)
    if not a or not b:
        return 0.0
    dot = sum(count * b[term] for term, count in a.items() if term in b)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def task_finished(ctx: EvaluationContext) -> CheckResult | None:
    answered = bool(ctx.final_response.strip()) or ctx.final_step is not None and bool(ctx.final_step.structured_output)
    return CheckResult(
        "task_finished",
        "task_completion",
        1.0 if answered else 0.0,
        answered,
        {"reason": "Agent gave a final answer" if answered else "Agent gave no final answer"},
    )


def task_status(ctx: EvaluationContext) -> CheckResult | None:
    expected = ctx.expected.get("task_status")
    if not expected:
        return None
    actual = ctx.final_step.task_status if ctx.final_step else None
    passed = actual == expected
    return CheckResult(
        "task_status", "task_completion", 1.0 if passed else 0.0, passed, {"expected": expected, "actual": actual}
    )


def exact_match(ctx: EvaluationContext) -> CheckResult | None:
    expected = ctx.expected.get("exact_response")
    if expected is None:
        return None
    passed = normalize(ctx.final_response) == normalize(expected)
    return CheckResult(
        "exact_match",
        "correctness",
        1.0 if passed else 0.0,
        passed,
        {"expected": expected, "actual": ctx.final_response},
    )


def required_information(ctx: EvaluationContext) -> CheckResult | None:
    phrases = ctx.expected.get("required_phrases") or []
    if not phrases:
        return None
    missing = [phrase for phrase in phrases if not contains(ctx.agent_text, phrase)]
    return CheckResult(
        "required_information",
        "correctness",
        _ratio(len(phrases) - len(missing), len(phrases)),
        not missing,
        {"required": phrases, "missing": missing},
    )


def semantic_similarity(ctx: EvaluationContext) -> CheckResult | None:
    reference = ctx.expected.get("reference_answer")
    if not reference:
        return None
    threshold = ctx.criteria.get("similarity_threshold", 0.35)
    similarity = lexical_similarity(ctx.final_response, reference)
    return CheckResult(
        "semantic_similarity",
        "correctness",
        min(1.0, similarity / threshold) if threshold else similarity,
        similarity >= threshold,
        {"similarity": round(similarity, 4), "threshold": threshold, "method": "lexical_cosine"},
    )


def structured_output(ctx: EvaluationContext) -> CheckResult | None:
    schema = ctx.expected.get("response_schema")
    if not schema:
        return None
    output = ctx.final_step.structured_output if ctx.final_step else None
    if output is None:
        return CheckResult("structured_output", "correctness", 0.0, False, {"error": "No structured output returned"})
    try:
        jsonschema.validate(output, schema)
    except jsonschema.ValidationError as exc:
        return CheckResult(
            "structured_output", "correctness", 0.0, False, {"error": exc.message, "path": list(exc.absolute_path)}
        )
    except jsonschema.SchemaError as exc:
        return CheckResult("structured_output", "correctness", 0.0, False, {"error": f"Invalid schema: {exc.message}"})
    return CheckResult("structured_output", "correctness", 1.0, True, {})


def _dig(value: Any, parts: list[str]) -> tuple[bool, Any]:
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return False, None
    return True, value


def _resolve(ctx: EvaluationContext, path: str) -> list[Any]:
    """``output.a.b`` reads structured output. ``tool.name.arg`` reads every call."""
    head, _, rest = path.partition(".")
    parts = rest.split(".") if rest else []
    if head == "output":
        output = ctx.final_step.structured_output if ctx.final_step else None
        found, value = _dig(output, parts)
        return [value] if found else []
    if head == "tool" and parts:
        name, *argument_path = parts
        values = []
        for call in ctx.tool_calls:
            if call.name == name:
                found, value = _dig(call.arguments or {}, argument_path)
                if found:
                    values.append(value)
        return values
    return []


_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "contains": lambda a, b: b in a,
    "exists": lambda a, b: True,
}


def _rule_holds(rule: dict[str, Any], values: list[Any]) -> bool:
    if not values:
        # A limit on a tool that was never called has nothing to break.
        return rule["path"].startswith("tool.") and rule["op"] != "exists"
    compare = _OPERATORS[rule["op"]]
    try:
        return all(compare(value, rule.get("value")) for value in values)
    except TypeError:
        return False


def business_rules(ctx: EvaluationContext) -> CheckResult | None:
    rules = ctx.expected.get("business_rules") or []
    if not rules:
        return None
    broken = [
        {"path": rule["path"], "op": rule["op"], "value": rule.get("value"), "description": rule.get("description", "")}
        for rule in rules
        if not _rule_holds(rule, _resolve(ctx, rule["path"]))
    ]
    return CheckResult(
        "business_rules", "correctness", _ratio(len(rules) - len(broken), len(rules)), not broken, {"broken": broken}
    )


def _arguments_match(expected: dict[str, Any], actual: dict[str, Any] | None) -> bool:
    actual = actual or {}
    return all(actual.get(key) == value for key, value in expected.items())


def expected_tool_calls(ctx: EvaluationContext) -> CheckResult | None:
    expected = ctx.expected.get("tool_calls") or []
    if not expected:
        return None
    problems: list[str] = []
    required = [spec for spec in expected if spec.get("required", True)]
    hits = 0
    for spec in expected:
        matching = [
            call
            for call in ctx.tool_calls
            if call.name == spec["name"] and _arguments_match(spec.get("arguments") or {}, call.arguments)
        ]
        limit = spec.get("max_calls")
        if limit is not None and len(matching) > limit:
            problems.append(f"{spec['name']} called {len(matching)} times, limit is {limit}")
        if spec.get("required", True):
            if matching:
                hits += 1
            else:
                problems.append(f"Missing call to {spec['name']} with {spec.get('arguments') or 'any arguments'}")
    return CheckResult(
        "expected_tool_calls",
        "tool_use",
        _ratio(hits, len(required)),
        not problems,
        {"problems": problems, "calls": [call.name for call in ctx.tool_calls]},
    )


def tool_order(ctx: EvaluationContext) -> CheckResult | None:
    order = ctx.expected.get("tool_order") or []
    if not order:
        return None
    names = iter(call.name for call in ctx.tool_calls)
    passed = all(any(name == wanted for name in names) for wanted in order)
    return CheckResult(
        "tool_order",
        "tool_use",
        1.0 if passed else 0.0,
        passed,
        {"expected_order": order, "actual": [call.name for call in ctx.tool_calls]},
    )


def forbidden_tools(ctx: EvaluationContext) -> CheckResult | None:
    forbidden = ctx.expected.get("forbidden_tools") or []
    if not forbidden:
        return None
    used = sorted({call.name or "" for call in ctx.tool_calls} & set(forbidden))
    return CheckResult("forbidden_tools", "policy", 0.0 if used else 1.0, not used, {"violations": used})


def forbidden_phrases(ctx: EvaluationContext) -> CheckResult | None:
    phrases = ctx.expected.get("forbidden_phrases") or []
    if not phrases:
        return None
    found = [phrase for phrase in phrases if contains(ctx.agent_text, phrase)]
    return CheckResult("forbidden_phrases", "policy", 0.0 if found else 1.0, not found, {"violations": found})


def failure_handling(ctx: EvaluationContext) -> CheckResult | None:
    """After a scripted tool failure the agent must recover, or say so honestly.

    Recovery is judged from the tool results rather than the wording, so an
    agent that ignores a failed call and announces success cannot pass.
    """
    if not ctx.expected.get("must_handle_failure"):
        return None
    injected = [step for step in ctx.tool_results if step.injected_failure]
    if not injected:
        return CheckResult(
            "failure_handling",
            "error_handling",
            0.0,
            False,
            {"reason": "The failing tool was never called, so the failure was not handled"},
        )
    last_failure = injected[-1]
    recovered = any(
        step.name == last_failure.name and step.seq > last_failure.seq and not step.is_error
        for step in ctx.tool_results
    )
    phrases = ctx.expected.get("failure_ack_phrases") or []
    acknowledged = [phrase for phrase in phrases if contains(ctx.final_response, phrase)]
    passed = recovered or bool(acknowledged)
    return CheckResult(
        "failure_handling",
        "error_handling",
        1.0 if passed else 0.0,
        passed,
        {"injected_failures": len(injected), "recovered": recovered, "acknowledged_with": acknowledged},
    )


def response_quality(ctx: EvaluationContext) -> CheckResult | None:
    """Cheap, deterministic signals. Nuanced quality is left to the optional judge."""
    text = ctx.final_response.strip()
    if ctx.final_step is not None and not text and ctx.final_step.structured_output is not None:
        return None
    sentences = [normalize(part) for part in re.split(r"[.!?\n]+", text) if part.strip()]
    checks = {
        "not_empty": bool(text),
        "reasonable_length": 5 <= len(text) <= 4000,
        "no_repetition": len(sentences) == len(set(sentences)),
        "no_internal_leaks": _LEAK_PATTERNS.search(text) is None,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return CheckResult(
        "response_quality",
        "response_quality",
        _ratio(len(checks) - len(failed), len(checks)),
        not failed,
        {"failed_checks": failed},
    )


def latency_budget(ctx: EvaluationContext) -> CheckResult | None:
    budget = ctx.criteria.get("latency_budget_ms", 5000)
    latency = ctx.metrics.latency_ms
    passed = latency <= budget
    return CheckResult(
        "latency_budget",
        "latency",
        1.0 if passed else budget / latency,
        passed,
        {"latency_ms": round(latency, 2), "budget_ms": budget},
    )


def cost_budget(ctx: EvaluationContext) -> CheckResult | None:
    token_limit = ctx.criteria.get("token_budget")
    cost_limit = ctx.criteria.get("cost_budget_usd")
    if token_limit is None and cost_limit is None:
        return None
    tokens = ctx.metrics.input_tokens + ctx.metrics.output_tokens
    cost = ctx.metrics.cost_usd
    over = []
    if token_limit is not None and tokens > token_limit:
        over.append("tokens")
    if cost_limit is not None and cost is not None and cost > cost_limit:
        over.append("cost")
    return CheckResult(
        "cost_budget",
        "cost",
        0.0 if over else 1.0,
        not over,
        {"tokens": tokens, "token_budget": token_limit, "cost_usd": cost, "cost_budget_usd": cost_limit, "over": over},
    )


EVALUATORS: tuple[Callable[[EvaluationContext], CheckResult | None], ...] = (
    task_finished,
    task_status,
    exact_match,
    required_information,
    semantic_similarity,
    structured_output,
    business_rules,
    expected_tool_calls,
    tool_order,
    forbidden_tools,
    forbidden_phrases,
    failure_handling,
    response_quality,
    latency_budget,
    cost_budget,
)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    score: float
    dimension_scores: dict[str, float]
    failure_categories: list[str]
    checks: list[CheckResult]


_CATEGORY_BY_DIMENSION = {
    "task_completion": "task_incomplete",
    "correctness": "incorrect_response",
    "tool_use": "incorrect_tool_use",
    "policy": "policy_violation",
    "error_handling": "unhandled_tool_failure",
    "response_quality": "poor_response_quality",
    "latency": "latency_budget_exceeded",
    "cost": "cost_budget_exceeded",
}

_INFRASTRUCTURE_ERRORS = {"worker_lost", "dispatch_lost", "dependency_unavailable", "config_not_found"}


def _execution_failure_category(status: str, error_code: str | None) -> str:
    if status == "timed_out":
        return "timeout"
    if error_code in _INFRASTRUCTURE_ERRORS:
        return "infrastructure"
    if error_code == "agent_protocol_error":
        return "protocol_violation"
    if error_code == "max_agent_calls_exceeded":
        return "agent_loop"
    return "agent_error"


def evaluate(
    *,
    expected: dict[str, Any],
    criteria: dict[str, Any],
    transcript: list[TranscriptStep],
    metrics: ExecutionMetrics,
    execution_status: str,
    error_code: str | None,
) -> Verdict:
    """Score one execution. An execution that failed to finish scores zero."""
    if execution_status != "completed":
        category = _execution_failure_category(execution_status, error_code)
        check = CheckResult(
            "execution_outcome",
            "task_completion",
            0.0,
            False,
            {"execution_status": execution_status, "error_code": error_code},
        )
        return Verdict(False, 0.0, {"task_completion": 0.0}, [category], [check])

    ctx = EvaluationContext(expected=expected, criteria=criteria, transcript=transcript, metrics=metrics)
    checks = [result for evaluator in EVALUATORS if (result := evaluator(ctx)) is not None]

    grouped: dict[str, list[CheckResult]] = {}
    for check in checks:
        grouped.setdefault(check.dimension, []).append(check)
    dimension_scores = {
        dimension: round(sum(check.score for check in items) / len(items), 4) for dimension, items in grouped.items()
    }

    weights = {**DEFAULT_WEIGHTS, **(criteria.get("weights") or {})}
    total_weight = sum(weights.get(dimension, 1.0) for dimension in dimension_scores)
    weighted = sum(score * weights.get(dimension, 1.0) for dimension, score in dimension_scores.items())
    score = round(weighted / total_weight, 4) if total_weight else 0.0

    critical = criteria.get("critical_dimensions", ["policy"])
    critical_failed = any(dimension_scores.get(dimension, 1.0) < 1.0 for dimension in critical)
    requirement_failed = any(check.blocking and not check.passed for check in checks)
    passed = score >= criteria.get("pass_threshold", 0.7) and not critical_failed and not requirement_failed

    categories = sorted({_CATEGORY_BY_DIMENSION[check.dimension] for check in checks if not check.passed})
    return Verdict(passed, score, dimension_scores, categories, checks)


def transcript_json(transcript: list[TranscriptStep]) -> str:
    return json.dumps([step.model_dump(mode="json", exclude_none=True) for step in transcript], indent=1)
