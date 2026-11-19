import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Bot, CheckCircle2, User, Wrench, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Check, EvaluationDetail, ExecutionDetail, Judgement, Step } from "../api/types";
import { BarList, StatTile } from "../components/charts";
import { Card, Empty, ErrorNotice, KeyValues, PageHeader, PassBadge, Segmented, Spinner, StateBadge, Tag } from "../components/ui";
import { dateTime, milliseconds, score, usd, words } from "../lib/format";

function Json({ value }: { value: unknown }) {
  return <pre className="mt-1 overflow-x-auto rounded bg-raised p-2 font-mono text-xs text-ink-2">{JSON.stringify(value, null, 2)}</pre>;
}

function StepRow({ step }: { step: Step }) {
  const { payload } = step;
  const icon = {
    user_message: User,
    agent_response: Bot,
    tool_call: Wrench,
    tool_result: Wrench,
    error: AlertTriangle,
  }[step.type] ?? Bot;
  const Icon = icon;
  const failed = payload.is_error || step.type === "error";
  const title = {
    user_message: "User",
    agent_response: "Agent",
    tool_call: `Calls ${payload.name}`,
    tool_result: `${payload.name} returned`,
    error: payload.name ? words(payload.name) : "Error",
  }[step.type];

  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      <span className={clsx("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border border-line bg-raised", failed ? "text-critical" : "text-ink-2")}>
        <Icon size={13} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold text-ink">{title}</span>
          {step.latency_ms !== null && <span className="tabular text-ink-3">{milliseconds(step.latency_ms)}</span>}
          {payload.task_status && step.type === "agent_response" && <Tag>{words(payload.task_status)}</Tag>}
          {payload.injected_failure && <Tag>Scripted failure</Tag>}
          {payload.is_error && !payload.injected_failure && <Tag>Tool error</Tag>}
        </p>
        {payload.content && <p className="mt-0.5 whitespace-pre-wrap break-words text-sm text-ink">{payload.content}</p>}
        {step.type === "tool_call" && <Json value={payload.arguments ?? {}} />}
        {step.type === "tool_result" && <Json value={payload.output} />}
        {payload.structured_output !== null && payload.structured_output !== undefined && (
          <>
            <p className="mt-1 text-xs text-ink-3">Structured output</p>
            <Json value={payload.structured_output} />
          </>
        )}
      </div>
    </li>
  );
}

function CheckRow({ check }: { check: Check }) {
  const details = Object.entries(check.details).filter(([, value]) => value !== null && !(Array.isArray(value) && value.length === 0));
  return (
    <li className="border-b border-line py-2 last:border-0">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-1.5 text-sm font-medium text-ink">
          {check.passed ? <CheckCircle2 size={14} className="text-good" aria-hidden /> : <XCircle size={14} className="text-critical" aria-hidden />}
          {words(check.evaluator)}
          <span className="sr-only">{check.passed ? "passed" : "failed"}</span>
        </span>
        <span className="tabular text-xs text-ink-2">{score(check.score)}</span>
      </div>
      {!check.passed && details.length > 0 && (
        <dl className="mt-1 space-y-0.5 text-xs">
          {details.map(([key, value]) => (
            <div key={key} className="flex gap-1.5">
              <dt className="shrink-0 text-ink-3">{words(key)}:</dt>
              <dd className="min-w-0 break-words text-ink-2">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function JudgeCard({ judgement }: { judgement: Judgement }) {
  if (judgement.status !== "completed") {
    return (
      <Card title="LLM judge" subtitle="Advisory only">
        <p className="text-sm text-ink-3">
          No review available ({words(judgement.status)}). {judgement.error_analysis}
        </p>
      </Card>
    );
  }
  const scores: [string, number | null][] = [
    ["Relevance", judgement.relevance],
    ["Reasoning", judgement.reasoning_quality],
    ["Consistency", judgement.context_consistency],
    ["Conversation", judgement.conversation_quality],
  ];
  return (
    <Card title="LLM judge" subtitle={`Advisory only, not part of the score. Model: ${judgement.model}`}>
      <BarList max={5} rows={scores.map(([label, value]) => ({ key: label, label, value: value ?? 0, display: `${value ?? "–"} / 5` }))} />
      <p className="mt-3 text-sm text-ink-2">{judgement.error_analysis}</p>
    </Card>
  );
}

export function ExecutionTracePage() {
  const { id = "" } = useParams();
  const execution = useQuery({ queryKey: ["execution", id], queryFn: () => api<ExecutionDetail>(`/v1/executions/${id}`) });
  const evaluation = useQuery({
    queryKey: ["evaluation", id],
    queryFn: () => api<EvaluationDetail>(`/v1/evaluations/executions/${id}`),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
  const attempts = useMemo(() => [...new Set(execution.data?.steps.map((step) => step.attempt))].sort((a, b) => a - b), [execution.data]);
  const [chosen, setChosen] = useState<number | null>(null);

  if (execution.isLoading) return <Spinner />;
  if (execution.error) return <ErrorNotice error={execution.error} />;
  const data = execution.data as ExecutionDetail;
  const attempt = chosen ?? attempts[attempts.length - 1] ?? 1;
  const steps = data.steps.filter((step) => step.attempt === attempt);
  const result = evaluation.data;

  return (
    <>
      <PageHeader
        title={data.scenario_name}
        description={`${data.agent_name} v${data.agent_version} · scenario v${data.scenario_version} · repetition ${data.repetition}`}
        back={{ to: `/runs/${data.run_id}`, label: data.run_name }}
        actions={
          <>
            <StateBadge state={data.status} />
            <PassBadge passed={result?.passed} />
          </>
        }
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Score" value={score(result?.score)} hint={result ? `Evaluator ${result.evaluator_version}` : "Not evaluated yet"} />
        <StatTile label="Agent time" value={milliseconds(data.latency_ms)} hint={`${data.agent_calls} agent calls`} />
        <StatTile label="Tool calls" value={`${data.tool_calls}`} hint={`Attempt ${data.attempt} of ${data.max_attempts}`} />
        <StatTile label="Tokens" value={`${data.input_tokens + data.output_tokens}`} hint={usd(data.cost_usd)} />
      </div>
      {data.error_message && <div className="mt-3"><ErrorNotice error={new Error(`${data.error_code}: ${data.error_message}`)} /></div>}

      <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <Card
            title="Execution trace"
            subtitle="Every message, tool call and result, in order"
            action={attempts.length > 1 ? <Segmented label="Attempt" value={attempt} onChange={setChosen} options={attempts.map((value) => ({ value, label: `Attempt ${value}` }))} /> : undefined}
          >
            {steps.length ? (
              <ol>
                {steps.map((step) => (
                  <StepRow key={`${step.attempt}-${step.seq}`} step={step} />
                ))}
              </ol>
            ) : (
              <Empty title="Nothing was recorded for this attempt">The worker may have stopped before it could save the transcript.</Empty>
            )}
          </Card>
        </div>
        <div className="space-y-3">
          <Card title="Evaluation" subtitle={result?.failure_categories.length ? result.failure_categories.map(words).join(", ") : undefined}>
            {result ? (
              <ul>
                {result.checks.map((check) => (
                  <CheckRow key={check.evaluator} check={check} />
                ))}
              </ul>
            ) : (
              <Empty title="Not evaluated yet" />
            )}
          </Card>
          {result?.judgement && <JudgeCard judgement={result.judgement} />}
          <Card title="Details">
            <KeyValues
              items={[
                ["Seed", data.seed],
                ["Queue wait", milliseconds(data.queue_wait_ms)],
                ["Started", dateTime(data.started_at)],
                ["Finished", dateTime(data.finished_at)],
                ["Worker", <span key="worker" className="font-mono text-xs">{data.worker_id ?? "–"}</span>],
                ["Trace ID", <span key="trace" className="font-mono text-xs">{data.trace_id ?? "–"}</span>],
              ]}
            />
          </Card>
        </div>
      </div>
    </>
  );
}
