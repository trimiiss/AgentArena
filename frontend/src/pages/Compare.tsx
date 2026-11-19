import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, api, query } from "../api/client";
import type { BenchmarkItem, Comparison, Page } from "../api/types";
import { Dumbbell, StatTile } from "../components/charts";
import { Card, Empty, ErrorNotice, Field, PageHeader, Select, Spinner, StatusBadge, Table, Td, Th } from "../components/ui";
import { dateTime, milliseconds, percent, score, usd, words } from "../lib/format";

function runLabel(item: BenchmarkItem): string {
  return `${item.agent_name} v${item.agent_version} · ${dateTime(item.created_at)} · ${percent(item.pass_rate, 0)}`;
}

function signed(value: number | null, format: (value: number) => string): string {
  if (value === null) return "–";
  return `${value > 0 ? "+" : ""}${format(value)}`;
}

const OUTCOMES = {
  improved: { tone: "good", label: "Improved" },
  regressed: { tone: "critical", label: "Regressed" },
  unchanged: { tone: "neutral", label: "No change in pass rate" },
} as const;

function Significance({ pValue, significant }: { pValue: number | null; significant: boolean }) {
  if (pValue === null) return <>Too few executions to test whether this is real.</>;
  const shown = pValue < 0.001 ? "p < 0.001" : `p = ${pValue}`;
  return significant ? <>Statistically significant ({shown}).</> : <>Could be chance ({shown}). Add repetitions to be sure.</>;
}

export function ComparePage() {
  const [params] = useSearchParams();
  const benchmarks = useQuery({ queryKey: ["benchmarks"], queryFn: () => api<Page<BenchmarkItem>>("/v1/benchmarks?limit=200") });
  const [base, setBase] = useState(params.get("base") ?? "");
  const [candidate, setCandidate] = useState("");
  const items = benchmarks.data?.items ?? [];
  const baseId = base || items[1]?.run_id || "";
  const candidateId = candidate || items[0]?.run_id || "";

  const comparison = useQuery({
    queryKey: ["comparison", baseId, candidateId],
    queryFn: () => api<Comparison>(`/v1/comparisons/runs${query({ base_run_id: baseId, candidate_run_id: candidateId })}`),
    enabled: Boolean(baseId && candidateId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });

  if (benchmarks.isLoading) return <Spinner />;
  if (benchmarks.error) return <ErrorNotice error={benchmarks.error} />;
  if (items.length < 2) {
    return (
      <>
        <PageHeader title="Compare versions" description="See what changed between two benchmarks." />
        <Card>
          <Empty title="You need two finished runs to compare">Run the same suite against two versions of an agent.</Empty>
        </Card>
      </>
    );
  }

  const data = comparison.data;
  const result = data?.comparison;
  const outcome = result ? OUTCOMES[result.outcome] : null;

  return (
    <>
      <PageHeader title="Compare versions" description="Scenarios are matched between runs, so you can see exactly what improved or regressed." actions={outcome && <StatusBadge tone={outcome.tone} label={outcome.label} />} />
      <div className="mb-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Field label="Base">
          <Select value={baseId} onChange={(event) => setBase(event.target.value)}>
            {items.map((item) => (
              <option key={item.run_id} value={item.run_id}>
                {runLabel(item)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Candidate">
          <Select value={candidateId} onChange={(event) => setCandidate(event.target.value)}>
            {items.map((item) => (
              <option key={item.run_id} value={item.run_id}>
                {runLabel(item)}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {comparison.error && <ErrorNotice error={comparison.error} />}
      {comparison.isLoading && <Spinner />}
      {result && data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Pass rate change" value={signed(Math.round(result.pass_rate.change * 1000) / 10, (value) => `${value} pts`)} hint={`${percent(result.pass_rate.base, 0)} to ${percent(result.pass_rate.candidate, 0)}`} />
            <StatTile label="Scenarios improved" value={`${result.improvements.length}`} hint={`Of ${result.shared_scenarios} shared`} />
            <StatTile label="Scenarios regressed" value={`${result.regressions.length}`} hint={`Of ${result.shared_scenarios} shared`} />
            <StatTile label="p95 latency change" value={signed(result.latency_ms.p95.change, milliseconds)} hint={`${milliseconds(result.latency_ms.p95.base)} to ${milliseconds(result.latency_ms.p95.candidate)}`} />
          </div>
          <p className="mt-2 text-sm text-ink-2">
            <Significance pValue={result.pass_rate.p_value} significant={result.pass_rate.significant} />
            {(result.only_in_base > 0 || result.only_in_candidate > 0) && ` ${result.only_in_base + result.only_in_candidate} scenarios appear in only one run and are left out.`}
          </p>

          <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3">
            <Card title="Pass rate by scenario" subtitle="Worst change first" className="xl:col-span-2">
              <Dumbbell
                baseLabel={`Base: v${data.base.agent_version}`}
                candidateLabel={`Candidate: v${data.candidate.agent_version}`}
                rows={result.scenarios.map((item) => ({ key: item.scenario_id, label: item.scenario_name, base: item.base_pass_rate, candidate: item.candidate_pass_rate }))}
              />
            </Card>
            <div className="space-y-3">
              <Card title="Scores by dimension">
                <Table>
                  <thead>
                    <tr>
                      <Th>Dimension</Th>
                      <Th align="right">Base</Th>
                      <Th align="right">Candidate</Th>
                      <Th align="right">Change</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.dimensions.map((item) => (
                      <tr key={item.dimension}>
                        <Td>{words(item.dimension)}</Td>
                        <Td align="right">{score(item.base)}</Td>
                        <Td align="right">{score(item.candidate)}</Td>
                        <Td align="right">{signed(item.change, (value) => value.toFixed(2))}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Card>
              <Card title="Latency and cost">
                <Table>
                  <thead>
                    <tr>
                      <Th>Metric</Th>
                      <Th align="right">Base</Th>
                      <Th align="right">Candidate</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {(["avg", "p50", "p95"] as const).map((key) => (
                      <tr key={key}>
                        <Td>{key === "avg" ? "Average" : key}</Td>
                        <Td align="right">{milliseconds(result.latency_ms[key].base)}</Td>
                        <Td align="right">{milliseconds(result.latency_ms[key].candidate)}</Td>
                      </tr>
                    ))}
                    <tr>
                      <Td>Cost</Td>
                      <Td align="right">{usd(result.cost_usd.base)}</Td>
                      <Td align="right">{usd(result.cost_usd.candidate)}</Td>
                    </tr>
                  </tbody>
                </Table>
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  );
}
