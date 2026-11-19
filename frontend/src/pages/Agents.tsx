import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, query } from "../api/client";
import type { Agent, AgentPerformance, AgentVersion, Page, Run, ValidationResult } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, Empty, ErrorNotice, Field, Input, KeyValues, LinkButton, PageHeader, Select, Spinner, StateBadge, Table, Tag, Td, TextArea, Th } from "../components/ui";
import { dateTime, milliseconds, percent, relative, usd, words } from "../lib/format";

const AGENT_TYPES = ["customer_support", "coding", "research", "sales", "operations", "generic"];

export function AgentsPage() {
  const { canWrite } = useAuth();
  const [search, setSearch] = useState("");
  const agents = useQuery({ queryKey: ["agents", search], queryFn: () => api<Page<Agent>>(`/v1/agents${query({ q: search, limit: 200 })}`) });

  return (
    <>
      <PageHeader title="Agents" description="Every agent and version registered in this organization." actions={canWrite && <LinkButton to="/agents/new" variant="primary">Register agent</LinkButton>} />
      <div className="mb-3 max-w-xs">
        <Input aria-label="Search agents" placeholder="Search by name" value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <Card>
        {agents.error ? (
          <ErrorNotice error={agents.error} />
        ) : agents.isLoading ? (
          <Spinner />
        ) : !agents.data?.items.length ? (
          <Empty title={search ? "No agents match your search" : "No agents yet"} action={!search && canWrite ? <LinkButton to="/agents/new" variant="primary">Register agent</LinkButton> : undefined}>
            {!search && "Register an agent by giving AgentArena its HTTP endpoint."}
          </Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Status</Th>
                <Th>Endpoint</Th>
                <Th align="right">Version</Th>
                <Th align="right">Created</Th>
              </tr>
            </thead>
            <tbody>
              {agents.data.items.map((agent) => (
                <tr key={agent.id}>
                  <Td>
                    <Link to={`/agents/${agent.id}`} className="font-medium hover:underline">
                      {agent.name}
                    </Link>
                  </Td>
                  <Td>
                    <Tag>{words(agent.agent_type)}</Tag>
                  </Td>
                  <Td>
                    <StateBadge state={agent.status} />
                  </Td>
                  <Td mono className="max-w-64 truncate">
                    {agent.endpoint_url}
                  </Td>
                  <Td align="right">v{agent.latest_version}</Td>
                  <Td align="right">{relative(agent.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}

interface VersionForm {
  endpoint_url: string;
  request_timeout_seconds: number;
  model_name: string;
  input_cost_per_1k: string;
  output_cost_per_1k: string;
  notes: string;
}

function versionPayload(form: VersionForm) {
  return {
    endpoint_url: form.endpoint_url,
    request_timeout_seconds: form.request_timeout_seconds,
    model_name: form.model_name || null,
    input_cost_per_1k: form.input_cost_per_1k || null,
    output_cost_per_1k: form.output_cost_per_1k || null,
    notes: form.notes,
  };
}

function VersionFields({ form, onChange }: { form: VersionForm; onChange: (form: VersionForm) => void }) {
  const update = (field: keyof VersionForm) => (event: { target: { value: string } }) => onChange({ ...form, [field]: event.target.value });
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <Field label="Endpoint URL" hint={<>Must speak the agent protocol. Try <code className="font-mono">http://sample-agent:8000/agents/v2</code> with the bundled sample agent.</>}>
          <Input required type="url" placeholder="https://agents.example.com/support" value={form.endpoint_url} onChange={update("endpoint_url")} />
        </Field>
      </div>
      <Field label="Request timeout (seconds)">
        <Input type="number" min={1} max={300} value={form.request_timeout_seconds} onChange={(event) => onChange({ ...form, request_timeout_seconds: Number(event.target.value) })} />
      </Field>
      <Field label="Model" hint="Optional, for your own reference.">
        <Input value={form.model_name} onChange={update("model_name")} />
      </Field>
      <Field label="Input price per 1K tokens (USD)" hint="Used to estimate cost when the agent reports tokens.">
        <Input inputMode="decimal" placeholder="0.003" value={form.input_cost_per_1k} onChange={update("input_cost_per_1k")} />
      </Field>
      <Field label="Output price per 1K tokens (USD)">
        <Input inputMode="decimal" placeholder="0.015" value={form.output_cost_per_1k} onChange={update("output_cost_per_1k")} />
      </Field>
    </div>
  );
}

const EMPTY_VERSION: VersionForm = { endpoint_url: "", request_timeout_seconds: 30, model_name: "", input_cost_per_1k: "", output_cost_per_1k: "", notes: "" };

export function AgentNewPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [form, setForm] = useState({ name: "", description: "", agent_type: "customer_support" });
  const [version, setVersion] = useState(EMPTY_VERSION);
  const create = useMutation({
    mutationFn: () => api<Agent>("/v1/agents", { method: "POST", body: { ...form, ...versionPayload(version) } }),
    onSuccess: (agent) => {
      void client.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${agent.id}`);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <>
      <PageHeader title="Register agent" description="Agents stay on your infrastructure. AgentArena only calls their endpoint." back={{ to: "/agents", label: "Agents" }} />
      <form onSubmit={submit} className="max-w-2xl space-y-3">
        {create.error && <ErrorNotice error={create.error} />}
        <Card title="Details">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Name">
              <Input required minLength={2} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            </Field>
            <Field label="Type">
              <Select value={form.agent_type} onChange={(event) => setForm({ ...form, agent_type: event.target.value })}>
                {AGENT_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {words(type)}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="sm:col-span-2">
              <Field label="Description">
                <TextArea rows={2} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
              </Field>
            </div>
          </div>
        </Card>
        <Card title="Integration" subtitle="This becomes version 1.">
          <VersionFields form={version} onChange={setVersion} />
        </Card>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Register agent
        </Button>
      </form>
    </>
  );
}

function ValidationReport({ result }: { result: ValidationResult }) {
  return (
    <ul className="mt-3 space-y-1">
      {result.checks.map((check) => (
        <li key={check.name} className="flex items-start gap-2 text-sm">
          {check.passed ? <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-good" aria-hidden /> : <XCircle size={15} className="mt-0.5 shrink-0 text-critical" aria-hidden />}
          <span>
            <span className="font-medium text-ink">{words(check.name)}</span>
            <span className="text-ink-3"> {check.passed ? "passed" : "failed"}{check.detail ? `: ${check.detail}` : ""}</span>
          </span>
        </li>
      ))}
      {result.latency_ms !== null && <li className="text-xs text-ink-3">Responded in {milliseconds(result.latency_ms)}.</li>}
    </ul>
  );
}

function CredentialCard({ agent }: { agent: Agent }) {
  const client = useQueryClient();
  const { canWrite } = useAuth();
  const [form, setForm] = useState({ auth_type: "bearer", header_name: "X-API-Key", secret: "" });
  const refresh = () => void client.invalidateQueries({ queryKey: ["agent", agent.id] });
  const save = useMutation({
    mutationFn: () => api(`/v1/agents/${agent.id}/credential`, { method: "PUT", body: form }),
    onSuccess: () => {
      setForm({ ...form, secret: "" });
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: () => api(`/v1/agents/${agent.id}/credential`, { method: "DELETE" }), onSuccess: refresh });

  return (
    <Card title="Credential" subtitle="Encrypted at rest and never shown again after saving.">
      {save.error && <ErrorNotice error={save.error} />}
      {agent.credential ? (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="text-ink-2">
            {agent.credential.auth_type === "bearer" ? "Bearer token" : `API key in ${agent.credential.header_name}`}, updated {relative(agent.credential.updated_at)}
          </span>
          {canWrite && (
            <Button variant="danger" onClick={() => remove.mutate()} loading={remove.isPending}>
              Remove
            </Button>
          )}
        </div>
      ) : (
        <p className="mb-3 text-sm text-ink-3">No credential stored. Requests are sent without authentication.</p>
      )}
      {canWrite && (
        <form
          className="grid grid-cols-1 gap-3 sm:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Type">
            <Select value={form.auth_type} onChange={(event) => setForm({ ...form, auth_type: event.target.value })}>
              <option value="bearer">Bearer token</option>
              <option value="api_key">API key header</option>
            </Select>
          </Field>
          {form.auth_type === "api_key" && (
            <Field label="Header name">
              <Input value={form.header_name} onChange={(event) => setForm({ ...form, header_name: event.target.value })} />
            </Field>
          )}
          <Field label="Secret">
            <Input type="password" autoComplete="off" required value={form.secret} onChange={(event) => setForm({ ...form, secret: event.target.value })} />
          </Field>
          <div className="flex items-end">
            <Button type="submit" loading={save.isPending}>
              {agent.credential ? "Replace" : "Save"}
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

function NewVersionCard({ agent }: { agent: Agent }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<VersionForm>({ ...EMPTY_VERSION, endpoint_url: agent.endpoint_url });
  const create = useMutation({
    mutationFn: () => api(`/v1/agents/${agent.id}/versions`, { method: "POST", body: versionPayload(form) }),
    onSuccess: () => {
      setOpen(false);
      void client.invalidateQueries({ queryKey: ["agent", agent.id] });
    },
  });
  if (!open) return <Button onClick={() => setOpen(true)}>New version</Button>;
  return (
    <form
      className="mt-3 space-y-3 border-t border-line pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      {create.error && <ErrorNotice error={create.error} />}
      <VersionFields form={form} onChange={setForm} />
      <Field label="What changed">
        <TextArea rows={2} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={create.isPending}>
          Create version
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function AgentDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { canWrite } = useAuth();
  const agent = useQuery({ queryKey: ["agent", id], queryFn: () => api<Agent>(`/v1/agents/${id}`) });
  const versions = useQuery({ queryKey: ["agent", id, "versions"], queryFn: () => api<AgentVersion[]>(`/v1/agents/${id}/versions`) });
  const runs = useQuery({ queryKey: ["runs", "agent", id], queryFn: () => api<Page<Run>>(`/v1/runs${query({ agent_id: id, limit: 8 })}`) });
  const performance = useQuery({ queryKey: ["agent-performance"], queryFn: () => api<AgentPerformance[]>("/v1/analytics/agents?days=90") });
  const validate = useMutation({
    mutationFn: () => api<ValidationResult>(`/v1/agents/${id}/validate`, { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["agent", id] }),
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/agents/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["agents"] });
      navigate("/agents");
    },
  });

  if (agent.isLoading) return <Spinner />;
  if (agent.error) return <ErrorNotice error={agent.error} />;
  const data = agent.data as Agent;
  const rows = (performance.data ?? []).filter((row) => row.agent_id === id);

  return (
    <>
      <PageHeader
        title={data.name}
        description={data.description || undefined}
        back={{ to: "/agents", label: "Agents" }}
        actions={
          canWrite && (
            <>
              <Button onClick={() => validate.mutate()} loading={validate.isPending}>
                Test connection
              </Button>
              <LinkButton to={`/runs/new?agent=${id}`} variant="primary">
                New test run
              </LinkButton>
            </>
          )
        }
      />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card title="Overview">
          <KeyValues
            items={[
              ["Status", <StateBadge key="status" state={data.status} />],
              ["Type", words(data.agent_type)],
              ["Latest version", `v${data.latest_version}`],
              ["Created", dateTime(data.created_at)],
              ["Last tested", dateTime(data.last_validated_at)],
              ["Endpoint", <span key="endpoint" className="font-mono text-xs">{data.endpoint_url}</span>],
            ]}
          />
          {validate.error && <div className="mt-3"><ErrorNotice error={validate.error} /></div>}
          {validate.data && <ValidationReport result={validate.data} />}
        </Card>
        <CredentialCard agent={data} />
      </div>

      <Card title="Versions" subtitle="Versions cannot be edited, so past benchmarks always mean the same thing." className="mt-3" action={canWrite && <NewVersionCard agent={data} />}>
        <Table>
          <thead>
            <tr>
              <Th>Version</Th>
              <Th>Endpoint</Th>
              <Th>Model</Th>
              <Th>Notes</Th>
              <Th align="right">Executions</Th>
              <Th align="right">Pass rate</Th>
              <Th align="right">p95</Th>
              <Th align="right">Cost</Th>
            </tr>
          </thead>
          <tbody>
            {(versions.data ?? []).map((version) => {
              const stats = rows.find((row) => row.agent_version === version.version);
              return (
                <tr key={version.id}>
                  <Td>v{version.version}</Td>
                  <Td mono className="max-w-56 truncate">{version.endpoint_url}</Td>
                  <Td>{version.model_name ?? "–"}</Td>
                  <Td className="max-w-56 truncate">{version.notes || "–"}</Td>
                  <Td align="right">{stats?.executions ?? 0}</Td>
                  <Td align="right">{percent(stats?.pass_rate)}</Td>
                  <Td align="right">{milliseconds(stats?.p95_latency_ms)}</Td>
                  <Td align="right">{stats ? usd(stats.cost_usd) : "–"}</Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Card>

      <Card title="Recent runs" className="mt-3">
        {runs.data?.items.length ? (
          <Table>
            <thead>
              <tr>
                <Th>Run</Th>
                <Th>Version</Th>
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
                  <Td>v{run.agent_version}</Td>
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
          <Empty title="This agent has not been tested yet" />
        )}
      </Card>

      {canWrite && (
        <div className="mt-6">
          <Button
            variant="danger"
            loading={remove.isPending}
            onClick={() => {
              if (window.confirm(`Delete ${data.name}? Past benchmarks are kept.`)) remove.mutate();
            }}
          >
            Delete agent
          </Button>
        </div>
      )}
    </>
  );
}
