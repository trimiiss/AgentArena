import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Page, ScenarioSummary, Suite } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, Empty, ErrorNotice, Field, Input, LinkButton, PageHeader, Spinner, Table, Td, Th } from "../components/ui";
import { relative, words } from "../lib/format";

export function SuitesPage() {
  const { canWrite } = useAuth();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const suites = useQuery({ queryKey: ["suites"], queryFn: () => api<Page<Suite>>("/v1/suites") });
  const create = useMutation({
    mutationFn: () => api<Suite>("/v1/suites", { method: "POST", body: { name } }),
    onSuccess: (suite) => {
      void client.invalidateQueries({ queryKey: ["suites"] });
      navigate(`/suites/${suite.id}`);
    },
  });

  return (
    <>
      <PageHeader title="Test suites" description="Group scenarios into a benchmark you can run again and again." />
      {canWrite && (
        <form
          className="mb-3 flex max-w-md items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div className="flex-1">
            <Field label="New suite">
              <Input required minLength={2} placeholder="Suite name" value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
          </div>
          <Button type="submit" variant="primary" loading={create.isPending}>
            Create
          </Button>
        </form>
      )}
      {create.error && <div className="mb-3"><ErrorNotice error={create.error} /></div>}
      <Card>
        {suites.error ? (
          <ErrorNotice error={suites.error} />
        ) : suites.isLoading ? (
          <Spinner />
        ) : !suites.data?.items.length ? (
          <Empty title="No test suites yet" action={<LinkButton to="/scenarios">Open scenario library</LinkButton>}>
            The starter pack includes a ready-made suite.
          </Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Description</Th>
                <Th align="right">Scenarios</Th>
                <Th align="right">Updated</Th>
              </tr>
            </thead>
            <tbody>
              {suites.data.items.map((suite) => (
                <tr key={suite.id}>
                  <Td>
                    <Link to={`/suites/${suite.id}`} className="font-medium hover:underline">
                      {suite.name}
                    </Link>
                  </Td>
                  <Td className="max-w-96 truncate text-ink-2">{suite.description || "–"}</Td>
                  <Td align="right">{suite.items.length}</Td>
                  <Td align="right">{relative(suite.updated_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}

export function SuiteDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { canWrite } = useAuth();
  const suite = useQuery({ queryKey: ["suite", id], queryFn: () => api<Suite>(`/v1/suites/${id}`) });
  const scenarios = useQuery({ queryKey: ["scenarios", "all"], queryFn: () => api<Page<ScenarioSummary>>("/v1/scenarios?limit=500") });
  const [selected, setSelected] = useState<Set<string> | null>(null);

  const current = selected ?? new Set(suite.data?.items.map((item) => item.scenario_id));
  const save = useMutation({
    mutationFn: () => {
      const pinned = new Map(suite.data?.items.map((item) => [item.scenario_id, item.pinned_version]));
      const items = [...current].map((scenario_id) => ({ scenario_id, pinned_version: pinned.get(scenario_id) ?? null }));
      return api(`/v1/suites/${id}`, { method: "PATCH", body: { items } });
    },
    onSuccess: () => {
      setSelected(null);
      void client.invalidateQueries({ queryKey: ["suite", id] });
      void client.invalidateQueries({ queryKey: ["suites"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/v1/suites/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["suites"] });
      navigate("/suites");
    },
  });

  if (suite.isLoading) return <Spinner />;
  if (suite.error) return <ErrorNotice error={suite.error} />;
  const data = suite.data as Suite;

  function toggle(scenarioId: string) {
    const next = new Set(current);
    if (next.has(scenarioId)) next.delete(scenarioId);
    else next.add(scenarioId);
    setSelected(next);
  }

  return (
    <>
      <PageHeader
        title={data.name}
        description={data.description || undefined}
        back={{ to: "/suites", label: "Test suites" }}
        actions={
          canWrite && (
            <>
              <LinkButton to={`/runs/new?suite=${id}`} variant="primary">
                Run this suite
              </LinkButton>
              <Button
                variant="danger"
                onClick={() => {
                  if (window.confirm(`Delete ${data.name}?`)) remove.mutate();
                }}
              >
                Delete
              </Button>
            </>
          )
        }
      />
      {save.error && <div className="mb-3"><ErrorNotice error={save.error} /></div>}
      <Card title={`${current.size} of ${scenarios.data?.total ?? 0} scenarios selected`} action={canWrite && selected && <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>Save changes</Button>}>
        {!scenarios.data?.items.length ? (
          <Empty title="No scenarios to add yet" action={<LinkButton to="/scenarios">Open scenario library</LinkButton>} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th />
                <Th>Scenario</Th>
                <Th>Type</Th>
                <Th>Difficulty</Th>
                <Th align="right">Version</Th>
              </tr>
            </thead>
            <tbody>
              {scenarios.data.items.map((scenario) => (
                <tr key={scenario.id}>
                  <Td>
                    <input type="checkbox" aria-label={`Include ${scenario.name}`} disabled={!canWrite} checked={current.has(scenario.id)} onChange={() => toggle(scenario.id)} />
                  </Td>
                  <Td>
                    <Link to={`/scenarios/${scenario.id}`} className="hover:underline">
                      {scenario.name}
                    </Link>
                  </Td>
                  <Td>{words(scenario.scenario_type)}</Td>
                  <Td>{words(scenario.difficulty)}</Td>
                  <Td align="right">v{scenario.latest_version}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
