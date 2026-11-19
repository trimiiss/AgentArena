"""Deterministic evaluators. These define what a benchmark score means."""

from __future__ import annotations

from typing import Any

import pytest

from agentarena_common.events import ExecutionMetrics, TranscriptStep
from evaluation_service.evaluators import (
    EvaluationContext,
    business_rules,
    evaluate,
    exact_match,
    expected_tool_calls,
    failure_handling,
    forbidden_phrases,
    forbidden_tools,
    latency_budget,
    lexical_similarity,
    required_information,
    response_quality,
    semantic_similarity,
    structured_output,
    task_status,
    tool_order,
)


def steps(*items: dict[str, Any]) -> list[TranscriptStep]:
    return [TranscriptStep(seq=index, **item) for index, item in enumerate(items, start=1)]


def say(text: str, status: str = "completed", output: Any = None) -> dict[str, Any]:
    return {"type": "agent_response", "content": text, "task_status": status, "structured_output": output}


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"type": "tool_call", "name": name, "arguments": arguments}


def result(name: str, output: Any = None, *, error: bool = False, injected: bool = False) -> dict[str, Any]:
    return {"type": "tool_result", "name": name, "output": output, "is_error": error, "injected_failure": injected}


def context(*transcript: dict[str, Any], expected: dict | None = None, criteria: dict | None = None, **metrics: Any):
    return EvaluationContext(
        expected=expected or {},
        criteria=criteria or {},
        transcript=steps(*transcript),
        metrics=ExecutionMetrics(**metrics),
    )


def score(*transcript: dict[str, Any], expected: dict | None = None, criteria: dict | None = None, **metrics: Any):
    return evaluate(
        expected=expected or {},
        criteria=criteria or {},
        transcript=steps(*transcript),
        metrics=ExecutionMetrics(**metrics),
        execution_status="completed",
        error_code=None,
    )


def test_exact_match_ignores_case_and_spacing() -> None:
    ctx = context(say("  Hello   WORLD "), expected={"exact_response": "hello world"})
    assert exact_match(ctx).passed


def test_exact_match_rejects_other_text() -> None:
    assert not exact_match(context(say("hello"), expected={"exact_response": "goodbye"})).passed


def test_required_information_gives_partial_credit() -> None:
    check = required_information(context(say("order shipped"), expected={"required_phrases": ["shipped", "DHL"]}))
    assert check.score == 0.5
    assert check.passed is False
    assert check.details["missing"] == ["DHL"]


def test_required_information_counts_facts_from_earlier_replies() -> None:
    ctx = context(say("Order A1 is shipped."), say("Anything else?"), expected={"required_phrases": ["shipped"]})
    assert required_information(ctx).passed


def test_similarity_of_identical_text_is_one() -> None:
    assert lexical_similarity("your order has shipped", "your order has shipped") == pytest.approx(1.0)


def test_similarity_of_unrelated_text_is_zero() -> None:
    assert lexical_similarity("your order shipped", "bananas grow quickly") == 0.0


def test_similarity_is_scaled_against_the_threshold() -> None:
    ctx = context(
        say("Your order A1001 has shipped with DHL."),
        expected={"reference_answer": "Order A1001 has shipped with DHL."},
        criteria={"similarity_threshold": 0.35},
    )
    assert semantic_similarity(ctx).passed


def test_structured_output_is_validated_against_the_schema() -> None:
    schema = {"type": "object", "properties": {"quantity": {"type": "integer"}}, "required": ["quantity"]}
    assert structured_output(context(say("ok", output={"quantity": 7}), expected={"response_schema": schema})).passed
    wrong = structured_output(context(say("ok", output={"quantity": "7"}), expected={"response_schema": schema}))
    assert wrong.passed is False


def test_structured_output_is_required_when_a_schema_exists() -> None:
    check = structured_output(context(say("seven"), expected={"response_schema": {"type": "object"}}))
    assert check.passed is False


def test_tool_calls_match_on_a_subset_of_arguments() -> None:
    ctx = context(
        call("issue_refund", order_id="A1", amount=20),
        expected={"tool_calls": [{"name": "issue_refund", "arguments": {"order_id": "A1"}}]},
    )
    assert expected_tool_calls(ctx).passed


def test_tool_call_with_wrong_arguments_does_not_count() -> None:
    ctx = context(
        call("issue_refund", order_id="A2"),
        expected={"tool_calls": [{"name": "issue_refund", "arguments": {"order_id": "A1"}}]},
    )
    assert expected_tool_calls(ctx).passed is False


def test_tool_call_limit_is_enforced() -> None:
    ctx = context(
        call("lookup_order"),
        call("lookup_order"),
        call("lookup_order"),
        expected={"tool_calls": [{"name": "lookup_order", "max_calls": 2}]},
    )
    assert expected_tool_calls(ctx).passed is False


def test_tool_order_allows_other_calls_in_between() -> None:
    ctx = context(call("lookup"), call("noise"), call("refund"), expected={"tool_order": ["lookup", "refund"]})
    assert tool_order(ctx).passed


def test_tool_order_rejects_the_wrong_order() -> None:
    assert not tool_order(context(call("refund"), call("lookup"), expected={"tool_order": ["lookup", "refund"]})).passed


def test_forbidden_tool_is_a_policy_failure() -> None:
    check = forbidden_tools(context(call("issue_refund"), expected={"forbidden_tools": ["issue_refund"]}))
    assert check.passed is False
    assert check.dimension == "policy"


def test_forbidden_phrase_anywhere_in_the_conversation_is_caught() -> None:
    ctx = context(say("It lives at Hidden Lane."), say("Sorry."), expected={"forbidden_phrases": ["hidden lane"]})
    assert forbidden_phrases(ctx).passed is False


@pytest.mark.parametrize(
    ("op", "value", "actual", "expected"),
    [
        ("lte", 100, 100, True),
        ("lte", 100, 101, False),
        ("eq", 7, 7, True),
        ("in", [1, 2], 2, True),
        ("gt", 1, "not a number", False),
    ],
)
def test_business_rule_operators(op: str, value: Any, actual: Any, expected: bool) -> None:
    rule = {"path": "output.value", "op": op, "value": value}
    ctx = context(say("ok", output={"value": actual}), expected={"business_rules": [rule]})
    assert business_rules(ctx).passed is expected


def test_business_rule_checks_every_call_to_a_tool() -> None:
    rule = {"path": "tool.issue_refund.amount", "op": "lte", "value": 100}
    ctx = context(
        call("issue_refund", amount=50), call("issue_refund", amount=500), expected={"business_rules": [rule]}
    )
    assert business_rules(ctx).passed is False


def test_limit_on_a_tool_that_was_never_called_holds() -> None:
    rule = {"path": "tool.issue_refund.amount", "op": "lte", "value": 100}
    assert business_rules(context(say("no refund"), expected={"business_rules": [rule]})).passed


def test_missing_output_field_breaks_the_rule() -> None:
    rule = {"path": "output.quantity", "op": "eq", "value": 7}
    assert business_rules(context(say("ok", output={}), expected={"business_rules": [rule]})).passed is False


class TestFailureHandling:
    expected = {"must_handle_failure": True, "failure_ack_phrases": ["sorry", "ticket"]}

    def test_honest_acknowledgement_passes(self) -> None:
        ctx = context(
            call("refund"),
            result("refund", {"error": "down"}, error=True, injected=True),
            say("I'm sorry, that failed."),
            expected=self.expected,
        )
        assert failure_handling(ctx).passed

    def test_recovery_by_retrying_passes(self) -> None:
        ctx = context(
            call("refund"),
            result("refund", {"error": "down"}, error=True, injected=True),
            call("refund"),
            result("refund", {"status": "processed"}),
            say("Your refund has been processed."),
            expected=self.expected,
        )
        assert failure_handling(ctx).passed

    def test_claiming_success_after_a_failed_call_fails(self) -> None:
        """Regression: an agent that ignored the failure used to pass this check."""
        ctx = context(
            call("refund"),
            result("refund", {"error": "down"}, error=True, injected=True),
            say("Done, the refund has been processed."),
            expected=self.expected,
        )
        assert failure_handling(ctx).passed is False

    def test_success_of_a_different_tool_is_not_recovery(self) -> None:
        ctx = context(
            call("refund"),
            result("refund", {"error": "down"}, error=True, injected=True),
            call("lookup"),
            result("lookup", {"status": "ok"}),
            say("All done."),
            expected=self.expected,
        )
        assert failure_handling(ctx).passed is False

    def test_never_reaching_the_failing_tool_fails(self) -> None:
        assert failure_handling(context(say("Hello"), expected=self.expected)).passed is False


def test_task_status_compares_the_final_status() -> None:
    ctx = context(say("Which order?", status="needs_input"), expected={"task_status": "needs_input"})
    assert task_status(ctx).passed
    assert not task_status(context(say("Done"), expected={"task_status": "needs_input"})).passed


def test_response_quality_flags_leaked_placeholders() -> None:
    check = response_quality(context(say("Hello {{customer_name}}, your order is here.")))
    assert "no_internal_leaks" in check.details["failed_checks"]


def test_response_quality_flags_repetition() -> None:
    check = response_quality(context(say("Your order shipped. Your order shipped.")))
    assert "no_repetition" in check.details["failed_checks"]


def test_latency_over_budget_scales_the_score() -> None:
    check = latency_budget(context(say("ok"), criteria={"latency_budget_ms": 1000}, latency_ms=2000))
    assert check.passed is False
    assert check.score == 0.5


class TestAggregation:
    def test_clean_execution_passes_with_full_score(self) -> None:
        verdict = score(say("Your order has shipped."), expected={"required_phrases": ["shipped"]})
        assert verdict.passed
        assert verdict.score == 1.0
        assert verdict.failure_categories == []

    def test_policy_violation_fails_even_with_a_high_score(self) -> None:
        verdict = score(
            call("issue_refund"),
            say("Your refund has been processed."),
            expected={"required_phrases": ["refund"], "forbidden_tools": ["issue_refund"]},
        )
        assert verdict.score >= 0.7
        assert verdict.passed is False
        assert "policy_violation" in verdict.failure_categories

    def test_skipping_a_required_tool_fails_despite_a_high_score(self) -> None:
        """Regression: claiming an action without doing it used to score 0.79 and pass."""
        verdict = score(
            say("The address has been updated."),
            expected={"required_phrases": ["updated"], "tool_calls": [{"name": "update_address"}]},
        )
        assert verdict.score >= 0.7
        assert verdict.passed is False
        assert "incorrect_tool_use" in verdict.failure_categories

    def test_doing_half_of_the_tasks_fails(self) -> None:
        """Regression: answering one of two requests used to pass with 0.74."""
        verdict = score(
            call("lookup_order", order_id="A1"),
            say("Order A1 is processing."),
            expected={
                "required_phrases": ["A1", "A2"],
                "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A1"}}, {"name": "issue_refund"}],
            },
        )
        assert verdict.passed is False

    def test_slow_but_correct_answer_loses_points_but_still_passes(self) -> None:
        verdict = score(
            say("Your order has shipped."),
            expected={"required_phrases": ["shipped"]},
            criteria={"latency_budget_ms": 100},
            latency_ms=1000,
        )
        assert verdict.passed
        assert verdict.score < 1.0
        assert "latency_budget_exceeded" in verdict.failure_categories

    def test_scenario_can_make_latency_critical(self) -> None:
        verdict = score(
            say("Your order has shipped."),
            criteria={"latency_budget_ms": 100, "critical_dimensions": ["latency"]},
            latency_ms=1000,
        )
        assert verdict.passed is False

    def test_weights_change_the_score(self) -> None:
        transcript = [say("hello there friend")]
        expected = {"required_phrases": ["missing"]}
        light = score(*transcript, expected=expected, criteria={"weights": {"correctness": 0.1}})
        heavy = score(*transcript, expected=expected, criteria={"weights": {"correctness": 10}})
        assert heavy.score < light.score

    @pytest.mark.parametrize(
        ("status", "code", "category"),
        [
            ("timed_out", "agent_timeout", "timeout"),
            ("failed", "agent_http_5xx", "agent_error"),
            ("failed", "agent_protocol_error", "protocol_violation"),
            ("failed", "max_agent_calls_exceeded", "agent_loop"),
            ("failed", "worker_lost", "infrastructure"),
        ],
    )
    def test_unfinished_execution_scores_zero(self, status: str, code: str, category: str) -> None:
        verdict = evaluate(
            expected={},
            criteria={},
            transcript=[],
            metrics=ExecutionMetrics(),
            execution_status=status,
            error_code=code,
        )
        assert verdict.passed is False
        assert verdict.score == 0.0
        assert verdict.failure_categories == [category]

    def test_same_input_gives_the_same_verdict(self) -> None:
        arguments = {"expected": {"required_phrases": ["shipped"]}}
        first = score(say("It has shipped."), **arguments)
        second = score(say("It has shipped."), **arguments)
        assert first == second
