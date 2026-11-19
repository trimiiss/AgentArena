"""Downloadable benchmark reports."""

from __future__ import annotations

import csv
import html
import io
from typing import Any

from evaluation_service.models import Benchmark, Evaluation


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} ms"


def _usd(value: float | None) -> str:
    return "not reported" if value is None else f"${value:.4f}"


def report_json(benchmark: Benchmark) -> dict[str, Any]:
    return {
        "run_id": str(benchmark.run_id),
        "run_name": benchmark.run_name,
        "agent_id": str(benchmark.agent_id),
        "agent_name": benchmark.agent_name,
        "agent_version": benchmark.agent_version,
        "run_status": benchmark.run_status,
        "verdict": benchmark.verdict,
        "pass_rate": benchmark.pass_rate,
        "generated_at": benchmark.created_at.isoformat(),
        **benchmark.summary,
    }


def report_csv(rows: list[Evaluation]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "execution_id",
            "scenario_name",
            "scenario_type",
            "difficulty",
            "scenario_version",
            "repetition",
            "seed",
            "execution_status",
            "error_code",
            "passed",
            "score",
            "failure_categories",
            "latency_ms",
            "queue_wait_ms",
            "agent_calls",
            "tool_calls",
            "attempts",
            "input_tokens",
            "output_tokens",
            "cost_usd",
        ]
    )
    for row in sorted(rows, key=lambda item: (item.scenario_name, item.repetition)):
        writer.writerow(
            [
                row.execution_id,
                _csv_safe(row.scenario_name),
                row.scenario_type,
                row.difficulty,
                row.scenario_version,
                row.repetition,
                row.seed,
                row.execution_status,
                row.error_code or "",
                row.passed,
                row.score,
                ";".join(row.failure_categories),
                row.latency_ms,
                row.queue_wait_ms,
                row.agent_calls,
                row.tool_calls,
                row.attempts,
                row.input_tokens,
                row.output_tokens,
                "" if row.cost_usd is None else row.cost_usd,
            ]
        )
    return buffer.getvalue()


def _csv_safe(value: str) -> str:
    """Spreadsheets run cells that start with these characters as formulas."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def report_markdown(benchmark: Benchmark) -> str:
    summary = benchmark.summary
    totals = summary["totals"]
    reliability = summary["reliability"]
    latency = summary["latency_ms"]
    usage = summary["usage"]
    throughput = summary["throughput"]
    lines = [
        f"# Benchmark report: {benchmark.run_name}",
        "",
        f"- Agent: {benchmark.agent_name} v{benchmark.agent_version}",
        f"- Verdict: **{benchmark.verdict.upper()}** (pass bar {_percent(summary['pass_bar'])})",
        f"- Run status: {benchmark.run_status}",
        f"- Generated: {benchmark.created_at.isoformat()}",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Executions | {totals['executions']} |",
        f"| Passed | {totals['passed']} ({_percent(totals['pass_rate'])}) |",
        f"| Average score | {totals['avg_score']:.2f} |",
        f"| Completed without error | {_percent(reliability['success_rate'])} |",
        f"| Failure rate | {_percent(reliability['failure_rate'])} |",
        f"| Timeout rate | {_percent(reliability['timeout_rate'])} |",
        f"| Latency avg / p50 / p95 | {_ms(latency['avg'])} / {_ms(latency['p50'])} / {_ms(latency['p95'])} |",
        f"| Tokens in / out | {usage['input_tokens']} / {usage['output_tokens']} |",
        f"| Estimated cost | {_usd(usage['cost_usd'])} |",
        f"| Throughput | {throughput['executions_per_minute'] or 'n/a'} executions/min |",
        "",
        "## Scores by dimension",
        "",
        "| Dimension | Score |",
        "| --- | --- |",
    ]
    lines += [f"| {name.replace('_', ' ')} | {score:.2f} |" for name, score in summary["dimensions"].items()]
    lines += ["", "## Scenarios", "", "| Scenario | Type | Pass rate | Failures |", "| --- | --- | --- | --- |"]
    for item in summary["scenarios"]:
        failures = ", ".join(item["failure_categories"]) or "none"
        lines.append(
            f"| {item['scenario_name']} | {item['scenario_type']} | {_percent(item['pass_rate'])} | {failures} |"
        )
    if summary["recommendations"]:
        lines += ["", "## Recommended improvements", ""]
        lines += [
            f"{index}. **{item['category'].replace('_', ' ')}** ({item['occurrences']}): {item['advice']}"
            for index, item in enumerate(summary["recommendations"], start=1)
        ]
    lines += ["", "## Reproducing this run", ""]
    versions = summary["reproducibility"]["scenario_versions"]
    lines += [f"- {name}: version {version}" for name, version in versions.items()]
    return "\n".join(lines) + "\n"


def report_html(benchmark: Benchmark) -> str:
    """A self-contained page. All values are escaped, since names come from users."""
    summary = benchmark.summary
    totals = summary["totals"]
    latency = summary["latency_ms"]
    escape = html.escape
    rows = "".join(
        f"<tr><td>{escape(item['scenario_name'])}</td><td>{escape(item['scenario_type'])}</td>"
        f"<td>{_percent(item['pass_rate'])}</td>"
        f"<td>{escape(', '.join(item['failure_categories']) or 'none')}</td></tr>"
        for item in summary["scenarios"]
    )
    advice = "".join(
        f"<li><strong>{escape(item['category'].replace('_', ' '))}</strong>: {escape(item['advice'])}</li>"
        for item in summary["recommendations"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{escape(benchmark.run_name)}</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1b1f24}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border-bottom:1px solid #d8dee4;padding:.5rem;text-align:left}}
.kpis{{display:flex;gap:2rem;flex-wrap:wrap}}.kpis b{{display:block;font-size:1.5rem}}
</style></head><body>
<h1>{escape(benchmark.run_name)}</h1>
<p>{escape(benchmark.agent_name)} v{benchmark.agent_version}. Verdict: <strong>{escape(benchmark.verdict)}</strong>.</p>
<div class="kpis">
<div><b>{_percent(totals["pass_rate"])}</b>pass rate</div>
<div><b>{totals["executions"]}</b>executions</div>
<div><b>{_ms(latency["p50"])}</b>p50 latency</div>
<div><b>{_ms(latency["p95"])}</b>p95 latency</div>
<div><b>{_usd(summary["usage"]["cost_usd"])}</b>estimated cost</div>
</div>
<h2>Scenarios</h2>
<table><thead><tr><th>Scenario</th><th>Type</th><th>Pass rate</th><th>Failures</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>Recommended improvements</h2><ol>{advice or "<li>No failures to address.</li>"}</ol>
</body></html>
"""
