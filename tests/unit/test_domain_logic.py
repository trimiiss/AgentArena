from __future__ import annotations

import copy
import csv
import io
import random
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from agent_registry.crypto import CredentialCipher
from agent_registry.schemas import VersionConfig
from evaluation_service.benchmarks import compare, compute_summary, percentile, recommendations, two_proportion_p_value
from evaluation_service.reports import _csv_safe, report_csv, report_html
from scenario_service import starter_pack
from scenario_service.schemas import EvaluationCriteria, ExpectedOutcome, ScenarioDefinition, VariationRequest
from scenario_service.variations import apply_strategy, perturbations, substitute

KEY = "bG9jYWwtZGV2LWNyZWRlbnRpYWxzLWtleS0wMDAwMDA="


def evaluation(**overrides: Any) -> SimpleNamespace:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    values = {
        "execution_id": uuid.uuid4(),
        "scenario_id": uuid.UUID(int=1),
        "scenario_name": "Scenario",
        "scenario_type": "tool_use",
        "scenario_version": 1,
        "difficulty": "easy",
        "repetition": 1,
        "seed": 1,
        "execution_status": "completed",
        "error_code": None,
        "passed": True,
        "score": 1.0,
        "dimension_scores": {"policy": 1.0},
        "failure_categories": [],
        "evaluator_version": "1.0",
        "latency_ms": 100.0,
        "queue_wait_ms": 10.0,
        "agent_calls": 1,
        "tool_calls": 0,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.01,
        "attempts": 1,
        "started_at": now,
        "finished_at": now + timedelta(seconds=30),
    }
    return SimpleNamespace(**{**values, **overrides})


class TestCredentialCipher:
    org = uuid.uuid4()
    agent = uuid.uuid4()

    def cipher(self) -> CredentialCipher:
        return CredentialCipher(KEY, "k1")

    def test_round_trip(self) -> None:
        blob = self.cipher().encrypt("secret", org_id=self.org, agent_id=self.agent)
        assert self.cipher().decrypt(blob, org_id=self.org, agent_id=self.agent) == "secret"

    def test_plaintext_is_not_visible_in_the_stored_value(self) -> None:
        assert b"secret" not in self.cipher().encrypt("secret", org_id=self.org, agent_id=self.agent)

    def test_same_secret_encrypts_differently_each_time(self) -> None:
        cipher = self.cipher()
        first = cipher.encrypt("secret", org_id=self.org, agent_id=self.agent)
        assert first != cipher.encrypt("secret", org_id=self.org, agent_id=self.agent)

    def test_ciphertext_cannot_move_to_another_organization(self) -> None:
        blob = self.cipher().encrypt("secret", org_id=self.org, agent_id=self.agent)
        with pytest.raises(ValueError):
            self.cipher().decrypt(blob, org_id=uuid.uuid4(), agent_id=self.agent)

    def test_tampering_is_detected(self) -> None:
        blob = bytearray(self.cipher().encrypt("secret", org_id=self.org, agent_id=self.agent))
        blob[-1] ^= 1
        with pytest.raises(ValueError):
            self.cipher().decrypt(bytes(blob), org_id=self.org, agent_id=self.agent)

    def test_short_keys_are_refused(self) -> None:
        with pytest.raises(RuntimeError):
            CredentialCipher("c2hvcnQ=", "k1")


class TestAgentConfigValidation:
    def test_secrets_cannot_be_smuggled_into_plain_headers(self) -> None:
        with pytest.raises(ValidationError):
            VersionConfig(endpoint_url="https://example.com/a", default_headers={"Authorization": "Bearer x"})

    def test_header_names_are_checked(self) -> None:
        with pytest.raises(ValidationError):
            VersionConfig(endpoint_url="https://example.com/a", default_headers={"bad header\r\n": "x"})


class TestVariations:
    definition = {"turns": [{"role": "user", "content": "Where is my order A1001? Please answer quickly."}]}

    def test_same_seed_gives_the_same_variants(self) -> None:
        first = perturbations(self.definition, ["typos", "polite"], 6, seed=42)
        assert first == perturbations(self.definition, ["typos", "polite"], 6, seed=42)

    def test_different_seeds_give_different_variants(self) -> None:
        first = perturbations(self.definition, ["typos"], 6, seed=1)
        assert first != perturbations(self.definition, ["typos"], 6, seed=2)

    def test_typos_keep_identifiers_intact(self) -> None:
        rng = random.Random(3)
        for _ in range(50):
            assert "A1001" in apply_strategy(self.definition, "typos", rng)["turns"][0]["content"]

    def test_the_original_is_not_modified(self) -> None:
        original = copy.deepcopy(self.definition)
        apply_strategy(self.definition, "uppercase", random.Random(1))
        assert self.definition == original

    def test_strategies_rotate(self) -> None:
        labels = [label for label, _ in perturbations(self.definition, ["typos", "polite"], 4, seed=1)]
        assert labels == ["typos", "polite", "typos", "polite"]

    def test_placeholders_are_filled_everywhere(self) -> None:
        value = {"a": "Hi {{name}}", "b": ["{{ name }}"], "c": {"d": "{{name}}!"}}
        assert substitute(value, {"name": "Arta"}) == {"a": "Hi Arta", "b": ["Arta"], "c": {"d": "Arta!"}}

    def test_a_placeholder_alone_keeps_its_type(self) -> None:
        assert substitute({"total": "{{total}}"}, {"total": 25.5}) == {"total": 25.5}

    def test_unknown_placeholders_are_left_visible(self) -> None:
        assert substitute("{{missing}}", {}) == "{{missing}}"

    def test_a_source_is_required(self) -> None:
        with pytest.raises(ValidationError):
            VariationRequest(strategies=[])


class TestStarterPack:
    def test_every_scenario_type_is_covered(self) -> None:
        types = {spec["scenario_type"] for spec in starter_pack.SCENARIOS}
        assert types == {
            "customer_support",
            "multi_turn",
            "tool_use",
            "error_handling",
            "conflicting_information",
            "missing_information",
            "policy_compliance",
            "concurrent_tasks",
        }

    @pytest.mark.parametrize("spec", starter_pack.SCENARIOS, ids=lambda spec: spec["name"])
    def test_definitions_satisfy_the_schemas(self, spec: dict[str, Any]) -> None:
        ScenarioDefinition.model_validate(spec["definition"])
        ExpectedOutcome.model_validate(spec["expected"])
        EvaluationCriteria.model_validate(spec.get("criteria", {}))


class TestBenchmarks:
    def test_percentile_interpolates(self) -> None:
        assert percentile([1, 2, 3, 4], 0.5) == 2.5
        assert percentile([10], 0.95) == 10
        assert percentile([], 0.95) is None

    def test_percentile_of_a_known_series(self) -> None:
        assert percentile(list(range(1, 101)), 0.95) == pytest.approx(95.05)

    def test_rates_add_up(self) -> None:
        rows = [
            evaluation(),
            evaluation(passed=False, score=0.2),
            evaluation(
                passed=False, score=0, execution_status="timed_out", latency_ms=0, failure_categories=["timeout"]
            ),
            evaluation(
                passed=False, score=0, execution_status="failed", latency_ms=0, failure_categories=["agent_error"]
            ),
        ]
        summary = compute_summary(rows, 0.8)
        assert summary["totals"]["passed"] == 1
        assert summary["totals"]["pass_rate"] == 0.25
        assert summary["reliability"]["success_rate"] == 0.5
        assert summary["reliability"]["timeout_rate"] == 0.25
        assert summary["reliability"]["failure_rate"] == 0.25
        assert summary["verdict"] == "failed"

    def test_failed_executions_do_not_flatter_latency(self) -> None:
        rows = [evaluation(latency_ms=500.0), evaluation(execution_status="failed", latency_ms=0.0, passed=False)]
        assert compute_summary(rows, 0.8)["latency_ms"]["avg"] == 500

    def test_unreported_cost_stays_empty(self) -> None:
        summary = compute_summary([evaluation(cost_usd=None, input_tokens=0, output_tokens=0)], 0.8)
        assert summary["usage"]["cost_usd"] is None
        assert summary["usage"]["tokens_reported"] is False

    def test_throughput_uses_wall_clock_time(self) -> None:
        summary = compute_summary([evaluation(), evaluation()], 0.8)
        assert summary["throughput"]["executions_per_minute"] == 4

    def test_empty_run_fails_rather_than_passes(self) -> None:
        assert compute_summary([], 0.8)["verdict"] == "failed"

    def test_policy_advice_comes_first(self) -> None:
        advice = recommendations({"incorrect_response": 10, "policy_violation": 1}, 11)  # type: ignore[arg-type]
        assert advice[0]["category"] == "policy_violation"

    def test_significance_needs_enough_samples(self) -> None:
        assert two_proportion_p_value(1, 2, 2, 2) is None

    def test_large_real_difference_is_significant(self) -> None:
        assert two_proportion_p_value(10, 100, 90, 100) < 0.001

    def test_no_difference_is_not_significant(self) -> None:
        assert two_proportion_p_value(50, 100, 50, 100) == 1.0

    def test_comparison_finds_regressions_per_scenario(self) -> None:
        first, second = uuid.UUID(int=1), uuid.UUID(int=2)
        base = compute_summary(
            [
                evaluation(scenario_id=first, scenario_name="A", passed=True),
                evaluation(scenario_id=second, scenario_name="B", passed=False),
            ],
            0.8,
        )
        candidate = compute_summary(
            [
                evaluation(scenario_id=first, scenario_name="A", passed=False),
                evaluation(scenario_id=second, scenario_name="B", passed=True),
            ],
            0.8,
        )
        result = compare(base, candidate)
        assert result["outcome"] == "unchanged"
        assert [item["scenario_name"] for item in result["regressions"]] == ["A"]
        assert [item["scenario_name"] for item in result["improvements"]] == ["B"]

    def test_scenarios_on_only_one_side_are_reported(self) -> None:
        base = compute_summary([evaluation(scenario_id=uuid.UUID(int=1))], 0.8)
        candidate = compute_summary([evaluation(scenario_id=uuid.UUID(int=2))], 0.8)
        result = compare(base, candidate)
        assert result["shared_scenarios"] == 0
        assert result["only_in_base"] == 1
        assert result["only_in_candidate"] == 1


class TestReports:
    def test_csv_cannot_smuggle_spreadsheet_formulas(self) -> None:
        assert _csv_safe("=HYPERLINK('http://evil')").startswith("'")
        assert _csv_safe("normal name") == "normal name"

    def test_csv_has_one_row_per_execution(self) -> None:
        rows = list(csv.reader(io.StringIO(report_csv([evaluation(), evaluation()]))))
        assert len(rows) == 3

    def test_html_escapes_user_supplied_names(self) -> None:
        name = "<script>alert(1)</script>"
        summary = compute_summary([evaluation(scenario_name=name)], 0.8)
        benchmark = SimpleNamespace(
            run_name=name,
            agent_name=name,
            agent_version=1,
            verdict="passed",
            summary=summary,
        )
        html = report_html(benchmark)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
