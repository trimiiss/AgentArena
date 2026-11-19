import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, query } from "../api/client";
import type { Agent, AgentPerformance, Overview, Page, TimePoint } from "../api/types";
import { BarList, LineChart, StatTile } from "../components/charts";
import { Card, Empty, ErrorNotice, PageHeader, Segmented, Select, Spinner, Table, Td, Th } from "../components/ui";
import { compact, milliseconds, percent, relative, score, usd, words } from "../lib/format";
import { RANGES, bucketLabels } from "./Dashboard";

export function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const [agentId, setAgentId] = useState("");
  const agent_id = agentId || undefined;
  const bucket = days <= 7 ? "hour" : "day";
  const scope = [days, agentId];

  const agents = useQuery({ queryKey: ["agents", "all"], queryFn: () => api<Page<Agent>>("/v1/agents?limit=200") });
  const overview = useQuery({ queryKey: ["overview", ...scope], queryFn: () => api<Overview>(`/v1/analytics/overview${query({ days, agent_id })}`) });
  const series = useQuery({ queryKey: ["timeseries", ...scope, bucket], queryFn: () => api<TimePoint[]>(`/v1/analytics/timeseries${query({ days, agent_id, bucket })}`) });
  const dimensions = useQuery({ queryKey: ["dimensions", ...scope], queryFn: () => api<{ dimension: string; average: number; samples: number }[]>(`/v1/analytics/dimensions${query({ days, agent_id })}`) });
  const failures = useQuery({ queryKey: ["failures", ...scope], queryFn: () => api<{ category: string; count: number }[]>(`/v1/analytics/failures${query({ days, agent_id })}`) });
  const performance = useQuery({ queryKey: ["agent-performance", days], queryFn: () => api<AgentPerformance[]>(`/v1/analytics/agents${query({ days })}`) });

  if (overview.isLoading) return <Spinner />;
  if (overview.error) return <ErrorNotice error={overview.error} />;
  const data = overview.data as Overview;
  const points = series.data ?? [];
  const labels = bucketLabels(points, days);
  const rows = (performance.data ?? []).filter((row) => !agentId || row.agent_id === agentId);

  return (
    <>
      <PageHeader title="Performance analytics" description="Every number is computed from stored evaluation results." />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented label="Time range" value={days} options={RANGES} onChange={setDays} />
        <div className="w-56">
          <Select aria-label="Filter by agent" value={agentId} onChange={(event) => setAgentId(event.target.value)}>
            <option value="">All agents</option>
            {(agents.data?.items ?? []).map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Executions" value={compact(data.executions)} hint={`${compact(data.benchmarked_runs)} benchmarked runs`} />
        <StatTile label="Success rate" value={percent(data.pass_rate)} hint={`${compact(data.timeouts)} timeouts, ${compact(data.errors)} errors`} />
        <StatTile label="Latency p50 / p95" value={`${milliseconds(data.p50_latency_ms)} / ${milliseconds(data.p95_latency_ms)}`} hint={`Average ${milliseconds(data.avg_latency_ms)}`} />
        <StatTile label="Estimated cost" value={usd(data.cost_usd)} hint={`${compact(data.input_tokens)} in, ${compact(data.output_tokens)} out`} />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card title="Executions" subtitle="Volume per period">
          {points.length ? (
            <LineChart title="Executions over time" labels={labels} format={(value) => compact(value)} series={[{ key: "executions", label: "Executions", color: "var(--series-1)", values: points.map((point) => point.executions) }]} />
          ) : (
            <Empty title="No results in this period" />
          )}
        </Card>
        <Card title="Estimated cost" subtitle="Reported or calculated from token prices">
          {points.some((point) => point.cost_usd !== null) ? (
            <LineChart title="Cost over time" labels={labels} format={(value) => `$${value.toFixed(3)}`} series={[{ key: "cost", label: "Cost", color: "var(--series-1)", values: points.map((point) => point.cost_usd) }]} />
          ) : (
            <Empty title="No cost data in this period">Set token prices on an agent version, or report usage from your agent.</Empty>
          )}
        </Card>
        <Card title="Scores by dimension" subtitle="Average from 0 to 1">
          <BarList max={1} rows={(dimensions.data ?? []).map((row) => ({ key: row.dimension, label: words(row.dimension), value: row.average, display: score(row.average) }))} empty={<Empty title="No results in this period" />} />
        </Card>
        <Card title="Failure categories" subtitle="How often each failure occurred">
          <BarList rows={(failures.data ?? []).map((row) => ({ key: row.category, label: words(row.category), value: row.count, display: compact(row.count) }))} empty={<Empty title="No failures recorded" />} />
        </Card>
      </div>

      <Card title="Agent versions" subtitle="Performance of each version in this period" className="mt-3">
        {rows.length ? (
          <Table>
            <thead>
              <tr>
                <Th>Agent</Th>
                <Th align="right">Version</Th>
                <Th align="right">Executions</Th>
                <Th align="right">Pass rate</Th>
                <Th align="right">Average</Th>
                <Th align="right">p95</Th>
                <Th align="right">Cost</Th>
                <Th align="right">Last run</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.agent_id}-${row.agent_version}`}>
                  <Td>
                    <Link to={`/agents/${row.agent_id}`} className="font-medium hover:underline">
                      {row.agent_name}
                    </Link>
                  </Td>
                  <Td align="right">v{row.agent_version}</Td>
                  <Td align="right">{compact(row.executions)}</Td>
                  <Td align="right">{percent(row.pass_rate)}</Td>
                  <Td align="right">{milliseconds(row.avg_latency_ms)}</Td>
                  <Td align="right">{milliseconds(row.p95_latency_ms)}</Td>
                  <Td align="right">{usd(row.cost_usd)}</Td>
                  <Td align="right">{relative(row.last_run_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <Empty title="No results in this period" />
        )}
      </Card>
    </>
  );
}
