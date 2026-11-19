import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, query } from "../api/client";
import type { Agent, Overview, Page, Run, ScenarioSummary, TimePoint } from "../api/types";
import { BarList, LineChart, StatTile } from "../components/charts";
import { Card, Empty, ErrorNotice, LinkButton, PageHeader, Segmented, Spinner, StateBadge, Table, Td, Th } from "../components/ui";
import { compact, milliseconds, percent, relative, usd, words } from "../lib/format";

export const RANGES = [
  { value: 1, label: "24 hours" },
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];

export function bucketLabels(points: TimePoint[], days: number): string[] {
  return points.map((point) => {
    const date = new Date(point.bucket);
    return days <= 7
      ? date.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
      : date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  });
}

function GettingStarted({ hasAgents, hasScenarios }: { hasAgents: boolean; hasScenarios: boolean }) {
  const steps = [
    { done: hasAgents, title: "Register an agent", detail: "Point AgentArena at the HTTP endpoint of the agent you want to test.", to: "/agents/new", action: "Register agent" },
    { done: hasScenarios, title: "Add scenarios", detail: "Install the starter pack, or write your own in the scenario library.", to: "/scenarios", action: "Open library" },
    { done: false, title: "Start a test run", detail: "Run the scenarios against your agent and watch the results arrive live.", to: "/runs/new", action: "New test run" },
  ];
  return (
    <Card title="Get started" subtitle="Three steps to your first benchmark.">
      <ol className="space-y-3">
        {steps.map((step, index) => (
          <li key={step.title} className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <span className="tabular mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-raised text-xs font-semibold text-ink-2">{step.done ? "✓" : index + 1}</span>
              <div>
                <p className="text-sm font-medium text-ink">{step.title}</p>
                <p className="text-sm text-ink-3">{step.detail}</p>
              </div>
            </div>
            {!step.done && <LinkButton to={step.to}>{step.action}</LinkButton>}
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function DashboardPage() {
  const [days, setDays] = useState(30);
  const bucket = days <= 7 ? "hour" : "day";
  const overview = useQuery({ queryKey: ["overview", days], queryFn: () => api<Overview>(`/v1/analytics/overview${query({ days })}`) });
  const series = useQuery({ queryKey: ["timeseries", days, bucket], queryFn: () => api<TimePoint[]>(`/v1/analytics/timeseries${query({ days, bucket })}`) });
  const failures = useQuery({ queryKey: ["failures", days], queryFn: () => api<{ category: string; count: number }[]>(`/v1/analytics/failures${query({ days })}`) });
  const runs = useQuery({ queryKey: ["runs", "recent"], queryFn: () => api<Page<Run>>("/v1/runs?limit=6"), refetchInterval: 5000 });
  const agents = useQuery({ queryKey: ["agents", "count"], queryFn: () => api<Page<Agent>>("/v1/agents?limit=1") });
  const scenarios = useQuery({ queryKey: ["scenarios", "count"], queryFn: () => api<Page<ScenarioSummary>>("/v1/scenarios?limit=1") });

  if (overview.isLoading) return <Spinner />;
  if (overview.error) return <ErrorNotice error={overview.error} />;
  const data = overview.data as Overview;
  const points = series.data ?? [];
  const isNew = (runs.data?.total ?? 0) === 0;
  const labels = bucketLabels(points, days);

  return (
    <>
      <PageHeader title="Dashboard" description="How your agents are performing, from recorded evaluations." actions={<LinkButton to="/runs/new" variant="primary">New test run</LinkButton>} />
      {isNew && (
        <div className="mb-4">
          <GettingStarted hasAgents={(agents.data?.total ?? 0) > 0} hasScenarios={(scenarios.data?.total ?? 0) > 0} />
        </div>
      )}
      <div className="mb-3">
        <Segmented label="Time range" value={days} options={RANGES} onChange={setDays} />
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Test runs" value={compact(data.benchmarked_runs)} hint="Benchmarked in this period" />
        <StatTile label="Successful runs" value={compact(data.runs_passed)} hint="Met the pass bar" />
        <StatTile label="Failed runs" value={compact(data.runs_failed)} hint="Below the pass bar" />
        <StatTile label="Agent success rate" value={percent(data.pass_rate)} hint={`${compact(data.executions_passed)} of ${compact(data.executions)} scenarios passed`} />
        <StatTile label="Average latency" value={milliseconds(data.avg_latency_ms)} hint="Agent response time per execution" />
        <StatTile label="p95 latency" value={milliseconds(data.p95_latency_ms)} hint={`p50 is ${milliseconds(data.p50_latency_ms)}`} />
        <StatTile label="Scenarios executed" value={compact(data.executions)} hint={`${compact(data.timeouts)} timeouts, ${compact(data.errors)} errors`} />
        <StatTile label="Estimated AI cost" value={usd(data.cost_usd)} hint={`${compact(data.input_tokens + data.output_tokens)} tokens reported`} />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card title="Success rate" subtitle="Share of executions that passed evaluation">
          {points.length ? (
            <LineChart title="Success rate over time" labels={labels} format={(value) => percent(value, 0)} domainMax={1} series={[{ key: "rate", label: "Success rate", color: "var(--series-1)", values: points.map((point) => point.pass_rate) }]} />
          ) : (
            <Empty title="No results in this period" />
          )}
        </Card>
        <Card title="Latency" subtitle="Agent response time per execution">
          {points.length ? (
            <LineChart
              title="Latency over time"
              labels={labels}
              format={milliseconds}
              series={[
                { key: "avg", label: "Average", color: "var(--series-1)", values: points.map((point) => point.avg_latency_ms) },
                { key: "p95", label: "p95", color: "var(--series-2)", values: points.map((point) => point.p95_latency_ms) },
              ]}
            />
          ) : (
            <Empty title="No results in this period" />
          )}
        </Card>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Card title="Recent runs" className="xl:col-span-2" action={<Link to="/runs" className="text-xs text-accent-ink hover:underline">View all</Link>}>
          {runs.data?.items.length ? (
            <Table>
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>Status</Th>
                  <Th align="right">Passed</Th>
                  <Th align="right">Started</Th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <tr key={run.id}>
                    <Td>
                      <Link to={`/runs/${run.id}`} className="font-medium hover:underline">
                        {run.name}
                      </Link>
                    </Td>
                    <Td>
                      <StateBadge state={run.status} />
                    </Td>
                    <Td align="right">
                      {run.progress.passed} / {run.total_executions}
                    </Td>
                    <Td align="right">{relative(run.created_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <Empty title="No test runs yet" />
          )}
        </Card>
        <Card title="Most common failures">
          <BarList
            rows={(failures.data ?? []).slice(0, 6).map((row) => ({ key: row.category, label: words(row.category), value: row.count, display: compact(row.count) }))}
            empty={<Empty title="No failures recorded" />}
          />
        </Card>
      </div>
    </>
  );
}
