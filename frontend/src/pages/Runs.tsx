import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api, downloadFile, query } from "../api/client";
import type { Agent, AgentVersion, Benchmark, Execution, Page, Progress, Run, Suite } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { BarList, SegmentedBar, StatTile } from "../components/charts";
import { Button, Card, Empty, ErrorNotice, Field, Input, KeyValues, LinkButton, PageHeader, PassBadge, Select, Spinner, StateBadge, StatusBadge, Table, Tag, Td, Th } from "../components/ui";
import { compact, dateTime, duration, milliseconds, percent, relative, score, shortId, usd, words } from "../lib/format";
import { streamEvents } from "../lib/sse";

const FINISHED = new Set(["completed", "failed", "cancelled"]);

export function RunsPage() {
  const { canWrite } = useAuth();
  const [status, setStatus] = useState("");
  const runs = useQuery({ queryKey: ["runs", status], queryFn: () => api<Page<Run>>(`/v1/runs${query({ status, limit: 100 })}`), refetchInterval: 4000 });

  return (
    <>
      <PageHeader title="Test runs" description="Each run executes scenarios against one agent version." actions={canWrite && <LinkButton to="/runs/new" variant="primary">New test run</LinkButton>} />
      <div className="mb-3 w-48">
        <Select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          {["queued", "running", "evaluating", "completed", "failed", "cancelled"].map((value) => (
            <option key={value} value={value}>
              {words(value)}
            </option>
          ))}
        </Select>
      </div>
      <Card>
        {runs.error ? (
          <ErrorNotice error={runs.error} />
        ) : runs.isLoading ? (
          <Spinner />
        ) : !runs.data?.items.length ? (
          <Empty title="No test runs found" />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Run</Th>
                <Th>Agent</Th>
                <Th>Status</Th>
                <Th align="right">Executions</Th>
                <Th align="right">Passed</Th>
                <Th align="right">Duration</Th>
                <Th align="right">Started</Th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((run) => (
                <tr key={run.id}>
                  <Td>
                    <Link to={FINISHED.has(run.status) ? `/runs/${run.id}/results` : `/runs/${run.id}`} className="font-medium hover:underline">
                      {run.name}
                    </Link>
                  </Td>
                  <Td>
                    {run.agent_name} v{run.agent_version}
                  </Td>
                  <Td>
                    <StateBadge state={run.status} />
                  </Td>
                  <Td align="right">{run.total_executions}</Td>
                  <Td align="right">{run.progress.evaluated ? percent(run.progress.passed / run.progress.evaluated, 0) : "–"}</Td>
                  <Td align="right">{duration(run.started_at, run.finished_at)}</Td>
                  <Td align="right">{relative(run.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}

export function NewRunPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params] = useSearchParams();
  const agents = useQuery({ queryKey: ["agents", "all"], queryFn: () => api<Page<Agent>>("/v1/agents?limit=200") });
  const suites = useQuery({ queryKey: ["suites"], queryFn: () => api<Page<Suite>>("/v1/suites") });
  const [form, setForm] = useState({
    name: "",
    agent_id: params.get("agent") ?? "",
    agent_version: "",
    suite_id: params.get("suite") ?? "",
    repetitions: 1,
    concurrency: 8,
    timeout_seconds: 30,
    max_retries: 1,
    seed: "",
    judge_enabled: false,
  });
  // Stays the same through retries, so a repeated click cannot start a second run.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const versions = useQuery({ queryKey: ["agent", form.agent_id, "versions"], queryFn: () => api<AgentVersion[]>(`/v1/agents/${form.agent_id}/versions`), enabled: Boolean(form.agent_id) });

  useEffect(() => {
    if (!form.agent_id && agents.data?.items.length) setForm((current) => ({ ...current, agent_id: agents.data.items[0].id }));
    if (!form.suite_id && suites.data?.items.length) setForm((current) => ({ ...current, suite_id: suites.data.items[0].id }));
  }, [agents.data, suites.data, form.agent_id, form.suite_id]);

  const suite = suites.data?.items.find((item) => item.id === form.suite_id);
  const total = (suite?.items.length ?? 0) * form.repetitions;

  const start = useMutation({
    mutationFn: () =>
      api<Run>("/v1/runs", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: {
          name: form.name || null,
          agent_id: form.agent_id,
          agent_version: form.agent_version ? Number(form.agent_version) : null,
          suite_id: form.suite_id,
          repetitions: form.repetitions,
          concurrency: form.concurrency,
          timeout_seconds: form.timeout_seconds,
          max_retries: form.max_retries,
          seed: form.seed === "" ? null : Number(form.seed),
          judge_enabled: form.judge_enabled,
        },
      }),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    start.mutate();
  }

  if (agents.isLoading || suites.isLoading) return <Spinner />;
  if (!agents.data?.items.length || !suites.data?.items.length) {
    return (
      <>
        <PageHeader title="New test run" back={{ to: "/runs", label: "Test runs" }} />
        <Card>
          <Empty title="A run needs an agent and a test suite" action={<LinkButton to={agents.data?.items.length ? "/scenarios" : "/agents/new"} variant="primary">{agents.data?.items.length ? "Add scenarios" : "Register an agent"}</LinkButton>} />
        </Card>
      </>
    );
  }

  const number = (field: "repetitions" | "concurrency" | "timeout_seconds" | "max_retries") => (event: { target: { value: string } }) => setForm({ ...form, [field]: Number(event.target.value) });

  return (
    <>
      <PageHeader title="New test run" description="Agent and scenario versions are pinned when the run starts, so it can be repeated exactly." back={{ to: "/runs", label: "Test runs" }} />
      <form onSubmit={submit} className="max-w-2xl space-y-3">
        {start.error && <ErrorNotice error={start.error} />}
        <Card title="What to test">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Agent">
              <Select required value={form.agent_id} onChange={(event) => setForm({ ...form, agent_id: event.target.value, agent_version: "" })}>
                {agents.data.items.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Version">
              <Select value={form.agent_version} onChange={(event) => setForm({ ...form, agent_version: event.target.value })}>
                <option value="">Latest</option>
                {(versions.data ?? []).map((version) => (
                  <option key={version.id} value={version.version}>
                    v{version.version}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Test suite">
              <Select required value={form.suite_id} onChange={(event) => setForm({ ...form, suite_id: event.target.value })}>
                {suites.data.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} ({item.items.length})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Run name" hint="Optional.">
              <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            </Field>
          </div>
        </Card>
        <Card title="Execution" subtitle={`This run will make ${compact(total)} executions.`}>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="Repetitions">
              <Input type="number" min={1} max={100} value={form.repetitions} onChange={number("repetitions")} />
            </Field>
            <Field label="Concurrency">
              <Input type="number" min={1} max={64} value={form.concurrency} onChange={number("concurrency")} />
            </Field>
            <Field label="Timeout (seconds)">
              <Input type="number" min={1} max={300} value={form.timeout_seconds} onChange={number("timeout_seconds")} />
            </Field>
            <Field label="Retries">
              <Input type="number" min={0} max={5} value={form.max_retries} onChange={number("max_retries")} />
            </Field>
            <Field label="Seed" hint="Blank for random.">
              <Input type="number" min={0} value={form.seed} onChange={(event) => setForm({ ...form, seed: event.target.value })} />
            </Field>
          </div>
          <label className="mt-3 flex items-start gap-2 text-sm text-ink-2">
            <input type="checkbox" className="mt-1" checked={form.judge_enabled} onChange={(event) => setForm({ ...form, judge_enabled: event.target.checked })} />
            <span>
              Add LLM judge feedback
              <span className="block text-xs text-ink-3">Advisory comments only. Scores always come from deterministic evaluators. Needs a configured API key.</span>
            </span>
          </label>
        </Card>
        <Button type="submit" variant="primary" loading={start.isPending}>
          Start run
        </Button>
      </form>
    </>
  );
}

interface LiveExecution {
  id: string;
  status: string;
  attempt: number;
  scenario_name: string;
  repetition: number;
  latency_ms: number | null;
  passed: boolean | null;
  score: number | null;
  error_code: string | null;
}

function progressParts(progress: Progress) {
  return [
    { label: "Completed", value: progress.completed, color: "var(--good)" },
    { label: "Running", value: progress.running + progress.dispatched, color: "var(--accent)" },
    { label: "Retrying", value: progress.retry_wait, color: "var(--warning)" },
    { label: "Timed out", value: progress.timed_out, color: "var(--serious)" },
    { label: "Failed", value: progress.failed, color: "var(--critical)" },
    { label: "Cancelled", value: progress.cancelled, color: "var(--line-strong)" },
    { label: "Waiting", value: progress.pending, color: "var(--track)" },
  ];
}

export function RunLivePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { canWrite } = useAuth();
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api<Run>(`/v1/runs/${id}`) });
  const executions = useQuery({ queryKey: ["run", id, "executions"], queryFn: () => api<Page<Execution>>(`/v1/runs/${id}/executions?limit=500`) });
  const [live, setLive] = useState<Map<string, LiveExecution>>(new Map());
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void streamEvents(
      `/v1/runs/${id}/stream`,
      {
        onEvent: (event, data) => {
          setConnected(true);
          if (event === "progress") {
            client.setQueryData<Run>(["run", id], (current) => (current ? { ...current, ...(data as Pick<Run, "status" | "warning" | "progress">) } : current));
          } else if (event === "executions") {
            setLive((current) => {
              const next = new Map(current);
              for (const row of data as LiveExecution[]) next.set(row.id, row);
              return next;
            });
          } else if (event === "done") {
            void client.invalidateQueries({ queryKey: ["run", id] });
            void client.invalidateQueries({ queryKey: ["runs"] });
          }
        },
        onError: () => setConnected(false),
      },
      controller.signal,
    );
    return () => controller.abort();
  }, [client, id]);

  const cancel = useMutation({ mutationFn: () => api(`/v1/runs/${id}/cancel`, { method: "POST" }) });
  const replay = useMutation({
    mutationFn: () => api<Run>(`/v1/runs/${id}/replay`, { method: "POST" }),
    onSuccess: (next) => navigate(`/runs/${next.id}`),
  });

  const rows = useMemo(() => {
    const merged = new Map<string, LiveExecution>();
    for (const row of executions.data?.items ?? []) merged.set(row.id, row);
    for (const [key, row] of live) merged.set(key, { ...merged.get(key), ...row });
    return [...merged.values()].sort((a, b) => a.scenario_name.localeCompare(b.scenario_name) || a.repetition - b.repetition);
  }, [executions.data, live]);

  if (run.isLoading) return <Spinner />;
  if (run.error) return <ErrorNotice error={run.error} />;
  const data = run.data as Run;
  const { progress } = data;
  const done = progress.completed + progress.failed + progress.timed_out + progress.cancelled;
  const finished = FINISHED.has(data.status);

  return (
    <>
      <PageHeader
        title={data.name}
        description={
          <>
            {data.agent_name} v{data.agent_version} · seed {data.seed}
          </>
        }
        back={{ to: "/runs", label: "Test runs" }}
        actions={
          <>
            <StateBadge state={data.status} />
            {!finished && canWrite && data.status !== "cancelling" && (
              <Button variant="danger" onClick={() => cancel.mutate()} loading={cancel.isPending}>
                Cancel run
              </Button>
            )}
            {finished && canWrite && (
              <Button onClick={() => replay.mutate()} loading={replay.isPending}>
                Run again
              </Button>
            )}
            {(finished || progress.evaluated > 0) && (
              <LinkButton to={`/runs/${id}/results`} variant="primary">
                View results
              </LinkButton>
            )}
          </>
        }
      />
      {data.warning && <div className="mb-3"><ErrorNotice error={new Error(data.warning)} /></div>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Finished" value={`${done} / ${data.total_executions}`} hint={finished ? "All executions settled" : connected ? "Updating live" : "Connecting"} />
        <StatTile label="Evaluated" value={`${progress.evaluated}`} hint={progress.evaluated ? `${progress.passed} passed` : "Waiting for results"} />
        <StatTile label="In progress" value={`${progress.running + progress.dispatched}`} hint={`Limit of ${data.concurrency} at a time`} />
        <StatTile label="Elapsed" value={duration(data.started_at ?? data.created_at, data.finished_at)} hint={`Created ${relative(data.created_at)}`} />
      </div>
      <Card title="Progress" className="mt-3">
        <SegmentedBar label="Execution progress" total={data.total_executions} parts={progressParts(progress)} />
      </Card>
      <Card title="Executions" className="mt-3">
        <Table>
          <thead>
            <tr>
              <Th>Scenario</Th>
              <Th align="right">Rep</Th>
              <Th>Status</Th>
              <Th align="right">Attempt</Th>
              <Th align="right">Latency</Th>
              <Th>Result</Th>
              <Th align="right">Score</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <Td>
                  <Link to={`/executions/${row.id}`} className="hover:underline">
                    {row.scenario_name}
                  </Link>
                </Td>
                <Td align="right">{row.repetition}</Td>
                <Td>
                  <StateBadge state={row.status} />
                </Td>
                <Td align="right">{row.attempt}</Td>
                <Td align="right">{milliseconds(row.latency_ms)}</Td>
                <Td>{row.status === "cancelled" ? "–" : <PassBadge passed={row.passed} />}</Td>
                <Td align="right">{score(row.score)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
      <p className="mt-3 text-xs text-ink-3">
        Correlation ID <span className="font-mono">{data.correlation_id}</span>
        {data.trace_id && (
          <>
            {" "}
            · Trace <span className="font-mono">{data.trace_id}</span>
          </>
        )}
      </p>
    </>
  );
}

const REPORT_FORMATS = [
  { format: "md", label: "Markdown" },
  { format: "html", label: "HTML" },
  { format: "csv", label: "CSV" },
  { format: "json", label: "JSON" },
];

export function RunResultsPage() {
  const { id = "" } = useParams();
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api<Run>(`/v1/runs/${id}`) });
  const benchmark = useQuery({
    queryKey: ["benchmark", id],
    queryFn: () => api<Benchmark>(`/v1/benchmarks/runs/${id}`),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    refetchInterval: (state) => (state.state.data?.final ? false : 3000),
  });
  const [downloadError, setDownloadError] = useState<unknown>(null);

  if (benchmark.isLoading) return <Spinner label="Loading results" />;
  if (benchmark.error) {
    return (
      <>
        <PageHeader title="Results" back={{ to: `/runs/${id}`, label: "Live view" }} />
        <Card>
          <Empty title="No results yet">Results appear here as soon as the first execution has been evaluated.</Empty>
        </Card>
      </>
    );
  }

  const data = benchmark.data as Benchmark;
  const summary = data.summary;
  const download = (format: string) => {
    setDownloadError(null);
    downloadFile(`/v1/benchmarks/runs/${id}/report?format=${format}`, `agentarena-${shortId(id)}.${format}`).catch(setDownloadError);
  };
  const failures = Object.entries(summary.failure_categories);

  return (
    <>
      <PageHeader
        title={data.run_name}
        description={`${data.agent_name} v${data.agent_version}`}
        back={{ to: `/runs/${id}`, label: "Live view" }}
        actions={
          <>
            {data.final ? (
              <StatusBadge tone={data.verdict === "passed" ? "good" : "critical"} label={`${data.verdict}, bar is ${percent(summary.pass_bar, 0)}`} />
            ) : (
              <StatusBadge tone="info" label="partial results" spin />
            )}
            <LinkButton to={`/compare?base=${id}`}>Compare</LinkButton>
          </>
        }
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Pass rate" value={percent(summary.totals.pass_rate)} hint={`${summary.totals.passed} of ${summary.totals.executions} passed`} />
        <StatTile label="Average score" value={score(summary.totals.avg_score)} hint={`Finished without error: ${percent(summary.reliability.success_rate, 0)}`} />
        <StatTile label="Latency p50 / p95" value={`${milliseconds(summary.latency_ms.p50)} / ${milliseconds(summary.latency_ms.p95)}`} hint={`Average ${milliseconds(summary.latency_ms.avg)}`} />
        <StatTile label="Estimated cost" value={usd(summary.usage.cost_usd)} hint={summary.usage.tokens_reported ? `${compact(summary.usage.input_tokens + summary.usage.output_tokens)} tokens` : "Agent reported no tokens"} />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card title="Scores by dimension" subtitle="Average across executions, from 0 to 1">
          <BarList max={1} rows={Object.entries(summary.dimensions).map(([name, value]) => ({ key: name, label: words(name), value, display: score(value) }))} />
        </Card>
        <Card title="Reliability and throughput">
          <KeyValues
            items={[
              ["Failure rate", percent(summary.reliability.failure_rate)],
              ["Timeout rate", percent(summary.reliability.timeout_rate)],
              ["Needed a retry", `${summary.reliability.retried}`],
              ["Throughput", summary.throughput.executions_per_minute ? `${summary.throughput.executions_per_minute} per min` : "–"],
              ["Queue wait p95", milliseconds(summary.throughput.p95_queue_wait_ms)],
              ["Total time", `${summary.throughput.elapsed_seconds} s`],
            ]}
          />
        </Card>
      </div>

      {summary.recommendations.length > 0 && (
        <Card title="Recommended improvements" subtitle="Generated from the failures in this run" className="mt-3">
          <ol className="space-y-3">
            {summary.recommendations.map((item) => (
              <li key={item.category}>
                <p className="text-sm font-medium text-ink">
                  {words(item.category)} <span className="font-normal text-ink-3">· {item.occurrences} {item.occurrences === 1 ? "time" : "times"}</span>
                </p>
                <p className="text-sm text-ink-2">{item.advice}</p>
              </li>
            ))}
          </ol>
        </Card>
      )}

      <Card title="Scenario results" className="mt-3">
        <Table>
          <thead>
            <tr>
              <Th>Scenario</Th>
              <Th>Type</Th>
              <Th>Difficulty</Th>
              <Th align="right">Pass rate</Th>
              <Th align="right">Score</Th>
              <Th>Failures</Th>
            </tr>
          </thead>
          <tbody>
            {summary.scenarios.map((item) => (
              <tr key={item.scenario_id}>
                <Td>{item.scenario_name}</Td>
                <Td>{words(item.scenario_type)}</Td>
                <Td>{words(item.difficulty)}</Td>
                <Td align="right">{percent(item.pass_rate, 0)}</Td>
                <Td align="right">{score(item.avg_score)}</Td>
                <Td>
                  <span className="flex flex-wrap gap-1">
                    {Object.keys(item.failure_categories).map((category) => (
                      <Tag key={category}>{words(category)}</Tag>
                    ))}
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>

      {summary.failed_executions.length > 0 && (
        <Card title="Failed executions" subtitle={failures.length ? "Open one to see the full trace." : undefined} className="mt-3">
          <Table>
            <thead>
              <tr>
                <Th>Scenario</Th>
                <Th align="right">Rep</Th>
                <Th>Execution</Th>
                <Th align="right">Score</Th>
                <Th>Why it failed</Th>
              </tr>
            </thead>
            <tbody>
              {summary.failed_executions.map((item) => (
                <tr key={item.execution_id}>
                  <Td>
                    <Link to={`/executions/${item.execution_id}`} className="font-medium hover:underline">
                      {item.scenario_name}
                    </Link>
                  </Td>
                  <Td align="right">{item.repetition}</Td>
                  <Td>{item.execution_status === "completed" ? <span className="text-ink-3">Ran to the end</span> : <StateBadge state={item.execution_status} />}</Td>
                  <Td align="right">{score(item.score)}</Td>
                  <Td className="text-ink-2">{item.failure_categories.map(words).join(", ")}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Card title="Reports and reproducibility" className="mt-3">
        {downloadError !== null && <div className="mb-3"><ErrorNotice error={downloadError} /></div>}
        {data.final ? (
          <div className="flex flex-wrap gap-2">
            {REPORT_FORMATS.map(({ format, label }) => (
              <Button key={format} onClick={() => download(format)}>
                <Download size={14} aria-hidden /> {label}
              </Button>
            ))}
          </div>
        ) : (
          <p className="text-sm text-ink-3">Reports can be downloaded when the run finishes.</p>
        )}
        {run.data && (
          <p className="mt-3 text-xs text-ink-3">
            Seed {run.data.seed}, agent v{run.data.agent_version}, evaluator {summary.reproducibility.evaluator_versions.join(", ")}. Started {dateTime(run.data.started_at)}.
          </p>
        )}
      </Card>
    </>
  );
}
