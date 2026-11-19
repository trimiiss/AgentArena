import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, query } from "../api/client";
import type { Dataset, Page, Scenario, ScenarioSummary, ScenarioVersion } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, Empty, ErrorNotice, Field, Input, LinkButton, PageHeader, Select, Spinner, Table, Tag, Td, TextArea, Th } from "../components/ui";
import { dateTime, relative, words } from "../lib/format";

export const SCENARIO_TYPES = ["customer_support", "multi_turn", "tool_use", "error_handling", "conflicting_information", "missing_information", "policy_compliance", "concurrent_tasks"];
const DIFFICULTIES = ["easy", "medium", "hard"];
const STRATEGIES = ["typos", "lowercase", "uppercase", "polite", "urgent", "filler", "no_punctuation"];

const TEMPLATE = {
  definition: { system_prompt: "You are a helpful support agent.", turns: [{ role: "user", content: "Where is my order A1001?" }], tools: [], context: {}, max_agent_calls: 8 },
  expected: { task_status: "completed", required_phrases: [], forbidden_phrases: [], tool_calls: [], forbidden_tools: [] },
  criteria: { pass_threshold: 0.7, latency_budget_ms: 5000 },
};

export function ScenariosPage() {
  const { canWrite } = useAuth();
  const client = useQueryClient();
  const [filters, setFilters] = useState({ q: "", scenario_type: "", difficulty: "" });
  const scenarios = useQuery({ queryKey: ["scenarios", filters], queryFn: () => api<Page<ScenarioSummary>>(`/v1/scenarios${query({ ...filters, limit: 500 })}`) });
  const pack = useMutation({
    mutationFn: () => api("/v1/scenarios/starter-pack", { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["scenarios"] });
      void client.invalidateQueries({ queryKey: ["suites"] });
    },
  });
  const filtered = Boolean(filters.q || filters.scenario_type || filters.difficulty);

  return (
    <>
      <PageHeader
        title="Scenario library"
        description="Versioned test cases, each with expected behaviour and evaluation criteria."
        actions={
          canWrite && (
            <>
              <Button onClick={() => pack.mutate()} loading={pack.isPending}>
                Install starter pack
              </Button>
              <LinkButton to="/scenarios/new" variant="primary">
                New scenario
              </LinkButton>
            </>
          )
        }
      />
      {pack.error && <div className="mb-3"><ErrorNotice error={pack.error} /></div>}
      <div className="mb-3 flex flex-wrap gap-2">
        <div className="w-60">
          <Input aria-label="Search scenarios" placeholder="Search by name" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} />
        </div>
        <div className="w-52">
          <Select aria-label="Filter by type" value={filters.scenario_type} onChange={(event) => setFilters({ ...filters, scenario_type: event.target.value })}>
            <option value="">All types</option>
            {SCENARIO_TYPES.map((type) => (
              <option key={type} value={type}>
                {words(type)}
              </option>
            ))}
          </Select>
        </div>
        <div className="w-40">
          <Select aria-label="Filter by difficulty" value={filters.difficulty} onChange={(event) => setFilters({ ...filters, difficulty: event.target.value })}>
            <option value="">All difficulties</option>
            {DIFFICULTIES.map((level) => (
              <option key={level} value={level}>
                {words(level)}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <Card>
        {scenarios.error ? (
          <ErrorNotice error={scenarios.error} />
        ) : scenarios.isLoading ? (
          <Spinner />
        ) : !scenarios.data?.items.length ? (
          <Empty title={filtered ? "No scenarios match these filters" : "No scenarios yet"}>{!filtered && "Install the starter pack for a complete customer-support benchmark, or write your own."}</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Difficulty</Th>
                <Th>Tags</Th>
                <Th align="right">Version</Th>
                <Th align="right">Updated</Th>
              </tr>
            </thead>
            <tbody>
              {scenarios.data.items.map((scenario) => (
                <tr key={scenario.id}>
                  <Td>
                    <Link to={`/scenarios/${scenario.id}`} className="font-medium hover:underline">
                      {scenario.name}
                    </Link>
                  </Td>
                  <Td>{words(scenario.scenario_type)}</Td>
                  <Td>{words(scenario.difficulty)}</Td>
                  <Td>
                    <span className="flex flex-wrap gap-1">
                      {scenario.tags.slice(0, 3).map((tag) => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                    </span>
                  </Td>
                  <Td align="right">v{scenario.latest_version}</Td>
                  <Td align="right">{relative(scenario.updated_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function JsonField({ label, hint, value, onChange, rows = 12 }: { label: string; hint: string; value: string; onChange: (value: string) => void; rows?: number }) {
  let error: string | undefined;
  try {
    JSON.parse(value);
  } catch (failure) {
    error = `Invalid JSON: ${(failure as Error).message}`;
  }
  return (
    <Field label={label} hint={hint} error={error}>
      <TextArea rows={rows} spellCheck={false} value={value} onChange={(event) => onChange(event.target.value)} className="font-mono text-xs" />
    </Field>
  );
}

// Plain-English editing. The JSON text stays the single source of truth, so saving, versioning and
// the Advanced JSON boxes keep working. Each guided field reads from the text and writes back to it.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Obj = Record<string, any>;

function parseObj(text: string): Obj | null {
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function setKey(text: string, onChange: (text: string) => void, key: string, value: unknown) {
  const current = parseObj(text);
  if (current) onChange(pretty({ ...current, [key]: value }));
}

function LinesField({ label, hint, value, onChange }: { label: string; hint: string; value: string[]; onChange: (lines: string[]) => void }) {
  const [text, setText] = useState(value.join("\n"));
  return (
    <Field label={label} hint={hint}>
      <TextArea
        rows={3}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          onChange(event.target.value.split("\n").map((line) => line.trim()).filter(Boolean));
        }}
      />
    </Field>
  );
}

function Advanced({ children }: { children: ReactNode }) {
  return (
    <details className="mt-4 rounded-md border border-line px-3 py-2">
      <summary className="cursor-pointer text-xs font-medium text-ink-2">Advanced: edit as JSON</summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}

function Broken() {
  return <p className="text-sm text-critical-ink">The JSON below is not valid, so the guided fields are paused. Fix it under Advanced.</p>;
}

function InputEditor({ text, onChange }: { text: string; onChange: (text: string) => void }) {
  const def = parseObj(text);
  const tools: Obj[] = Array.isArray(def?.tools) ? def.tools : [];
  const turns: Obj[] = Array.isArray(def?.turns) ? def.turns : [];
  return (
    <>
      {!def ? (
        <Broken />
      ) : (
        <div className="space-y-4">
          <Field label="Instructions for the agent" hint="The rules and role the agent is given before the customer speaks.">
            <TextArea rows={5} value={def.system_prompt ?? ""} onChange={(event) => setKey(text, onChange, "system_prompt", event.target.value)} />
          </Field>
          <div>
            <p className="mb-1 text-xs font-medium text-ink-2">What the customer says</p>
            <p className="mb-2 text-xs text-ink-3">
              Use <code>{"{{name}}"}</code> to leave a blank that a dataset or variation fills in later.
            </p>
            <div className="space-y-2">
              {turns.map((turn, index) => (
                <div key={index} className="flex gap-2">
                  <TextArea
                    aria-label={`Customer message ${index + 1}`}
                    rows={2}
                    value={turn.content ?? ""}
                    onChange={(event) => setKey(text, onChange, "turns", turns.map((item, at) => (at === index ? { ...item, content: event.target.value } : item)))}
                  />
                  {turns.length > 1 && (
                    <Button onClick={() => setKey(text, onChange, "turns", turns.filter((_, at) => at !== index))}>Remove</Button>
                  )}
                </div>
              ))}
            </div>
            <Button className="mt-2" onClick={() => setKey(text, onChange, "turns", [...turns, { role: "user", content: "" }])}>
              Add another message
            </Button>
          </div>
          <Field label="Most times the agent may reply" hint="A safety limit so a confused agent cannot loop forever.">
            <Input type="number" min={1} max={50} value={def.max_agent_calls ?? 8} onChange={(event) => setKey(text, onChange, "max_agent_calls", Number(event.target.value))} />
          </Field>
          <div>
            <p className="mb-1 text-xs font-medium text-ink-2">Fake tools the agent can use</p>
            {tools.length === 0 ? (
              <p className="text-sm text-ink-3">None. The agent has no tools in this scenario.</p>
            ) : (
              <ul className="space-y-1 text-sm text-ink-2">
                {tools.map((tool) => (
                  <li key={tool.name}>
                    <span className="font-medium text-ink">{tool.name}</span>: {tool.description || "no description"}.{" "}
                    {tool.failure ? "It is set to fail on purpose, to test error handling." : `It has ${Array.isArray(tool.responses) ? tool.responses.length : 0} scripted answer(s).`}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-xs text-ink-3">To add or change tools and their scripted answers, use Advanced below.</p>
          </div>
        </div>
      )}
      <Advanced>
        <JsonField label="Definition" hint="Turns, tools with mock responses, context and the call limit." value={text} onChange={onChange} rows={16} />
      </Advanced>
    </>
  );
}

const TASK_STATUSES: [string, string][] = [
  ["completed", "Finished the job"],
  ["needs_input", "Asked the customer for missing information"],
  ["failed", "Could not do it"],
  ["in_progress", "Still working"],
];

function ExpectedEditor({ text, onChange }: { text: string; onChange: (text: string) => void }) {
  const exp = parseObj(text);
  const calls: Obj[] = Array.isArray(exp?.tool_calls) ? exp.tool_calls : [];
  const set = (key: string, value: unknown) => setKey(text, onChange, key, value);
  return (
    <>
      {!exp ? (
        <Broken />
      ) : (
        <div className="space-y-4">
          <Field label="How the task should end">
            <Select value={exp.task_status ?? ""} onChange={(event) => set("task_status", event.target.value || null)}>
              <option value="">Do not check</option>
              {TASK_STATUSES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <LinesField label="The reply must contain" hint="One phrase per line. Missing any of them fails the scenario." value={exp.required_phrases ?? []} onChange={(lines) => set("required_phrases", lines)} />
            <LinesField label="The reply must not contain" hint="One phrase per line. For example a promise the agent may not make." value={exp.forbidden_phrases ?? []} onChange={(lines) => set("forbidden_phrases", lines)} />
            <LinesField label="Tools the agent must never use" hint="One tool name per line." value={exp.forbidden_tools ?? []} onChange={(lines) => set("forbidden_tools", lines)} />
            <LinesField label="If a tool fails, the reply should say something like" hint="One phrase per line, such as “sorry” or “try again”." value={exp.failure_ack_phrases ?? []} onChange={(lines) => set("failure_ack_phrases", lines)} />
          </div>
          <label className="flex items-center gap-2 text-sm text-ink-2">
            <input type="checkbox" checked={Boolean(exp.must_handle_failure)} onChange={(event) => set("must_handle_failure", event.target.checked)} />
            A tool fails on purpose, and the agent must handle it honestly
          </label>
          <div>
            <p className="mb-1 text-xs font-medium text-ink-2">Tools the agent should call</p>
            {calls.length === 0 ? (
              <p className="text-sm text-ink-3">No tool calls are checked.</p>
            ) : (
              <ul className="space-y-1">
                {calls.map((call, index) => (
                  <li key={index} className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
                    <span className="font-medium text-ink">{call.name}</span>
                    {call.arguments && Object.keys(call.arguments).length > 0 && <span className="text-ink-3">with {Object.entries(call.arguments).map(([key, value]) => `${key} = ${String(value)}`).join(", ")}</span>}
                    <label className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={Boolean(call.required)}
                        onChange={(event) => set("tool_calls", calls.map((item, at) => (at === index ? { ...item, required: event.target.checked } : item)))}
                      />
                      required
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-xs text-ink-3">Required calls decide pass or fail. To add a call or set its arguments, use Advanced below.</p>
          </div>
        </div>
      )}
      <Advanced>
        <JsonField label="Expected outcome" hint="Phrases, tool calls, forbidden tools, business rules and failure handling." value={text} onChange={onChange} rows={14} />
      </Advanced>
    </>
  );
}

const DIMENSION_LABELS: [string, string][] = [
  ["correctness", "Correct answer"],
  ["task_completion", "Finished the task"],
  ["policy", "Followed the rules"],
  ["tool_use", "Used tools well"],
  ["error_handling", "Handled failures"],
  ["response_quality", "Clear, polite reply"],
  ["latency", "Fast enough"],
  ["cost", "Cheap enough"],
];

function CriteriaEditor({ text, onChange }: { text: string; onChange: (text: string) => void }) {
  const criteria = parseObj(text);
  const set = (key: string, value: unknown) => setKey(text, onChange, key, value);
  const weights: Obj = criteria?.weights ?? {};
  const critical: string[] = criteria?.critical_dimensions ?? [];
  const optionalNumber = (key: string, label: string, hint: string) => (
    <Field label={label} hint={hint}>
      <Input type="number" min={0} step="any" placeholder="No limit" value={criteria?.[key] ?? ""} onChange={(event) => set(key, event.target.value === "" ? null : Number(event.target.value))} />
    </Field>
  );
  return (
    <>
      {!criteria ? (
        <Broken />
      ) : (
        <div className="space-y-4">
          <Field label="Score needed to pass (%)" hint="The agent passes when its overall score reaches this.">
            <Input type="number" min={0} max={100} value={Math.round((criteria.pass_threshold ?? 0.7) * 100)} onChange={(event) => set("pass_threshold", Number(event.target.value) / 100)} />
          </Field>
          <div>
            <p className="mb-1 text-xs font-medium text-ink-2">How much each thing counts</p>
            <p className="mb-2 text-xs text-ink-3">Higher means it matters more. 0 means ignore it. Tick “must pass” if failing it should fail the whole scenario.</p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {DIMENSION_LABELS.map(([key, label]) => (
                <div key={key} className="flex items-center justify-between gap-2 text-sm text-ink-2">
                  <span>{label}</span>
                  <span className="flex items-center gap-3">
                    <Input
                      aria-label={`${label} weight`}
                      type="number"
                      min={0}
                      step="any"
                      className="w-20"
                      value={weights[key] ?? 0}
                      onChange={(event) => set("weights", { ...weights, [key]: Number(event.target.value) })}
                    />
                    <label className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={critical.includes(key)}
                        onChange={(event) => set("critical_dimensions", event.target.checked ? [...critical, key] : critical.filter((item) => item !== key))}
                      />
                      must pass
                    </label>
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {optionalNumber("latency_budget_ms", "Slowest acceptable reply (ms)", "1000 ms is one second.")}
            {optionalNumber("cost_budget_usd", "Most it may cost (USD)", "Leave empty for no limit.")}
            {optionalNumber("token_budget", "Most tokens it may use", "Leave empty for no limit.")}
          </div>
        </div>
      )}
      <Advanced>
        <JsonField label="Criteria" hint="Pass threshold, dimension weights and budgets." value={text} onChange={onChange} rows={10} />
      </Advanced>
    </>
  );
}

function VariationsCard({ scenario }: { scenario: Scenario }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api<Page<Dataset>>("/v1/datasets") });
  const [form, setForm] = useState({ count: 5, seed: 42, dataset_id: "", strategies: ["typos", "polite", "urgent"] });
  const generate = useMutation({
    mutationFn: () =>
      api<{ created: ScenarioSummary[] }>(`/v1/scenarios/${scenario.id}/variations`, {
        method: "POST",
        body: { count: form.count, seed: form.seed, dataset_id: form.dataset_id || null, strategies: form.dataset_id ? [] : form.strategies },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["scenarios"] });
      navigate("/scenarios");
    },
  });

  return (
    <Card title="Generate variations" subtitle="Same seed, same result, so generated scenarios stay reproducible.">
      {generate.error && <div className="mb-3"><ErrorNotice error={generate.error} /></div>}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Count">
          <Input type="number" min={1} max={100} value={form.count} onChange={(event) => setForm({ ...form, count: Number(event.target.value) })} />
        </Field>
        <Field label="Seed">
          <Input type="number" value={form.seed} onChange={(event) => setForm({ ...form, seed: Number(event.target.value) })} />
        </Field>
      </div>
      <div className="mt-3">
        <Field label="Fill from dataset" hint="Replaces {{placeholders}} with each row. Without a dataset, the strategies below rewrite the user's messages.">
          <Select value={form.dataset_id} onChange={(event) => setForm({ ...form, dataset_id: event.target.value })}>
            <option value="">No dataset</option>
            {(datasets.data?.items ?? []).map((dataset) => (
              <option key={dataset.id} value={dataset.id}>
                {dataset.name} ({dataset.rows.length} rows)
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {!form.dataset_id && (
        <fieldset className="mt-3">
          <legend className="mb-1 text-xs font-medium text-ink-2">Strategies</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {STRATEGIES.map((strategy) => (
              <label key={strategy} className="flex items-center gap-1.5 text-sm text-ink-2">
                <input
                  type="checkbox"
                  checked={form.strategies.includes(strategy)}
                  onChange={(event) => setForm({ ...form, strategies: event.target.checked ? [...form.strategies, strategy] : form.strategies.filter((item) => item !== strategy) })}
                />
                {words(strategy)}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <Button className="mt-3" onClick={() => generate.mutate()} loading={generate.isPending} disabled={!form.dataset_id && !form.strategies.length}>
        Generate
      </Button>
    </Card>
  );
}

export function ScenarioEditorPage() {
  const { id } = useParams();
  const isNew = !id;
  const scenario = useQuery({ queryKey: ["scenario", id], queryFn: () => api<Scenario>(`/v1/scenarios/${id}`), enabled: !isNew });
  if (!isNew && scenario.isLoading) return <Spinner />;
  if (scenario.error) return <ErrorNotice error={scenario.error} />;
  return <ScenarioForm key={scenario.data?.current.id ?? "new"} scenario={scenario.data} />;
}

function ScenarioForm({ scenario }: { scenario?: Scenario }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { canWrite } = useAuth();
  const [meta, setMeta] = useState({
    name: scenario?.name ?? "",
    description: scenario?.description ?? "",
    scenario_type: scenario?.scenario_type ?? "customer_support",
    difficulty: scenario?.difficulty ?? "medium",
    tags: scenario?.tags.join(", ") ?? "",
  });
  const [definition, setDefinition] = useState(pretty(scenario?.current.definition ?? TEMPLATE.definition));
  const [expected, setExpected] = useState(pretty(scenario?.current.expected ?? TEMPLATE.expected));
  const [criteria, setCriteria] = useState(pretty(scenario?.current.criteria ?? TEMPLATE.criteria));
  const [note, setNote] = useState("");
  const versions = useQuery({ queryKey: ["scenario", scenario?.id, "versions"], queryFn: () => api<ScenarioVersion[]>(`/v1/scenarios/${scenario?.id}/versions`), enabled: Boolean(scenario) });

  const save = useMutation({
    mutationFn: async () => {
      const content = { definition: JSON.parse(definition), expected: JSON.parse(expected), criteria: JSON.parse(criteria) };
      const fields = { ...meta, tags: meta.tags.split(",").map((tag) => tag.trim()).filter(Boolean) };
      if (!scenario) return api<Scenario>("/v1/scenarios", { method: "POST", body: { ...fields, ...content } });
      await api(`/v1/scenarios/${scenario.id}`, { method: "PATCH", body: fields });
      const changed = pretty(scenario.current.definition) !== definition || pretty(scenario.current.expected) !== expected || pretty(scenario.current.criteria) !== criteria;
      if (changed) await api(`/v1/scenarios/${scenario.id}/versions`, { method: "POST", body: { ...content, change_note: note } });
      return scenario;
    },
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ["scenarios"] });
      void client.invalidateQueries({ queryKey: ["scenario", saved.id] });
      setNote("");
      if (!scenario) navigate(`/scenarios/${saved.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/scenarios/${scenario?.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["scenarios"] });
      navigate("/scenarios");
    },
  });

  return (
    <>
      <PageHeader title={scenario ? scenario.name : "New scenario"} description={scenario ? `Version ${scenario.latest_version}. Saving changes creates a new version.` : undefined} back={{ to: "/scenarios", label: "Scenario library" }} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <div className="space-y-3 xl:col-span-2">
          {save.error && <ErrorNotice error={save.error} />}
          <Card title="Details">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Field label="Name">
                  <Input value={meta.name} onChange={(event) => setMeta({ ...meta, name: event.target.value })} />
                </Field>
              </div>
              <Field label="Type">
                <Select value={meta.scenario_type} onChange={(event) => setMeta({ ...meta, scenario_type: event.target.value })}>
                  {SCENARIO_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {words(type)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Difficulty">
                <Select value={meta.difficulty} onChange={(event) => setMeta({ ...meta, difficulty: event.target.value })}>
                  {DIFFICULTIES.map((level) => (
                    <option key={level} value={level}>
                      {words(level)}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="sm:col-span-2">
                <Field label="Description">
                  <TextArea rows={2} value={meta.description} onChange={(event) => setMeta({ ...meta, description: event.target.value })} />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Field label="Tags" hint="Separate with commas.">
                  <Input value={meta.tags} onChange={(event) => setMeta({ ...meta, tags: event.target.value })} />
                </Field>
              </div>
            </div>
          </Card>
          <Card title="Input" subtitle="What the agent receives, including simulated tools and scripted failures.">
            <InputEditor text={definition} onChange={setDefinition} />
          </Card>
          <Card title="Expected behaviour" subtitle="Required items here decide pass or fail.">
            <ExpectedEditor text={expected} onChange={setExpected} />
          </Card>
          <Card title="Scoring" subtitle="How the agent's reply is graded.">
            <CriteriaEditor text={criteria} onChange={setCriteria} />
          </Card>
          {canWrite && (
            <div className="flex flex-wrap items-end gap-2">
              {scenario && (
                <div className="w-72">
                  <Field label="What changed">
                    <Input value={note} onChange={(event) => setNote(event.target.value)} />
                  </Field>
                </div>
              )}
              <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>
                {scenario ? "Save" : "Create scenario"}
              </Button>
              {scenario && (
                <Button
                  variant="danger"
                  loading={remove.isPending}
                  onClick={() => {
                    if (window.confirm("Delete this scenario? Past runs keep their exact version.")) remove.mutate();
                  }}
                >
                  Delete
                </Button>
              )}
            </div>
          )}
        </div>
        {scenario && (
          <div className="space-y-3">
            <Card title="Version history">
              <ol className="space-y-2">
                {(versions.data ?? []).map((version) => (
                  <li key={version.id} className="text-sm">
                    <span className="font-medium text-ink">v{version.version}</span>
                    <span className="text-ink-3"> · {dateTime(version.created_at)}</span>
                    {version.change_note && <p className="text-ink-2">{version.change_note}</p>}
                  </li>
                ))}
              </ol>
            </Card>
            {canWrite && <VariationsCard scenario={scenario} />}
          </div>
        )}
      </div>
    </>
  );
}
