import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Me, Member, Organization, Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, ErrorNotice, Field, Input, KeyValues, PageHeader, Select, Spinner, Table, Td, Th } from "../components/ui";
import { dateTime, words } from "../lib/format";

const ROLES: Role[] = ["owner", "admin", "member", "viewer"];

const ROLE_HELP: Record<Role, string> = {
  owner: "Full control, including other owners.",
  admin: "Manages members and organization settings.",
  member: "Creates agents and scenarios, and starts runs.",
  viewer: "Read-only access to everything.",
};

function OrganizationCard() {
  const { session, canAdmin, rename } = useAuth();
  const [name, setName] = useState(session?.organization.name ?? "");
  const save = useMutation({
    mutationFn: () => api<Organization>("/v1/organizations/current", { method: "PATCH", body: { name } }),
    onSuccess: rename,
  });
  return (
    <Card title="Organization" subtitle="All agents, scenarios and results here are isolated from other organizations.">
      {save.error && <div className="mb-3"><ErrorNotice error={save.error} /></div>}
      <form
        className="flex max-w-md items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <div className="flex-1">
          <Field label="Name">
            <Input required minLength={2} disabled={!canAdmin} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
        </div>
        {canAdmin && (
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
        )}
      </form>
      <div className="mt-4">
        <KeyValues
          items={[
            ["Slug", <span key="slug" className="font-mono text-xs">{session?.organization.slug}</span>],
            ["Created", dateTime(session?.organization.created_at)],
            ["Organization ID", <span key="id" className="font-mono text-xs">{session?.organization.id}</span>],
            ["Your role", words(session?.role ?? "")],
          ]}
        />
      </div>
    </Card>
  );
}

function MembersCard() {
  const { session, canAdmin } = useAuth();
  const client = useQueryClient();
  const members = useQuery({ queryKey: ["members"], queryFn: () => api<Member[]>("/v1/organizations/current/members") });
  const [form, setForm] = useState<{ email: string; role: Role }>({ email: "", role: "member" });
  const refresh = () => void client.invalidateQueries({ queryKey: ["members"] });

  const add = useMutation({
    mutationFn: () => api("/v1/organizations/current/members", { method: "POST", body: form }),
    onSuccess: () => {
      setForm({ email: "", role: "member" });
      refresh();
    },
  });
  const change = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: Role }) => api(`/v1/organizations/current/members/${userId}`, { method: "PATCH", body: { role } }),
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: (userId: string) => api(`/v1/organizations/current/members/${userId}`, { method: "DELETE" }),
    onSettled: refresh,
  });
  const error = add.error ?? change.error ?? remove.error;

  return (
    <Card title="Members">
      {error && <div className="mb-3"><ErrorNotice error={error} /></div>}
      {members.isLoading ? (
        <Spinner />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Email</Th>
              <Th>Role</Th>
              <Th align="right">Joined</Th>
              {canAdmin && <Th />}
            </tr>
          </thead>
          <tbody>
            {(members.data ?? []).map((member) => (
              <tr key={member.user_id}>
                <Td>{member.display_name}</Td>
                <Td>{member.email}</Td>
                <Td>
                  {canAdmin ? (
                    <Select aria-label={`Role for ${member.display_name}`} className="w-28" value={member.role} onChange={(event) => change.mutate({ userId: member.user_id, role: event.target.value as Role })}>
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {words(role)}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    words(member.role)
                  )}
                </Td>
                <Td align="right">{dateTime(member.joined_at)}</Td>
                {canAdmin && (
                  <Td align="right">
                    {member.user_id !== session?.user.id && (
                      <Button
                        variant="ghost"
                        onClick={() => {
                          if (window.confirm(`Remove ${member.display_name} from this organization?`)) remove.mutate(member.user_id);
                        }}
                      >
                        Remove
                      </Button>
                    )}
                  </Td>
                )}
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {canAdmin && (
        <form
          className="mt-4 flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <div className="w-64">
            <Field label="Add member" hint="They need an AgentArena account first.">
              <Input type="email" required placeholder="name@example.com" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
            </Field>
          </div>
          <div className="w-32">
            <Field label="Role" hint=" ">
              <Select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value as Role })}>
                {ROLES.map((role) => (
                  <option key={role} value={role}>
                    {words(role)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="pb-5">
            <Button type="submit" variant="primary" loading={add.isPending}>
              Add
            </Button>
          </div>
        </form>
      )}
      <dl className="mt-4 grid grid-cols-1 gap-1 border-t border-line pt-3 text-xs sm:grid-cols-2">
        {ROLES.map((role) => (
          <div key={role} className="flex gap-1.5">
            <dt className="font-medium text-ink-2">{words(role)}:</dt>
            <dd className="text-ink-3">{ROLE_HELP[role]}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function WorkspacesCard() {
  const client = useQueryClient();
  const { session, switchOrganization } = useAuth();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/v1/auth/me") });
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api<Organization>("/v1/organizations", { method: "POST", body: { name } }),
    onSuccess: () => {
      setName("");
      void client.invalidateQueries({ queryKey: ["me"] });
    },
  });
  return (
    <Card title="Your organizations">
      {create.error && <div className="mb-3"><ErrorNotice error={create.error} /></div>}
      <ul className="mb-3 divide-y divide-line">
        {(me.data?.organizations ?? []).map(({ organization, role }) => (
          <li key={organization.id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span className="min-w-0 truncate">
              <span className="font-medium text-ink">{organization.name}</span>
              <span className="text-ink-3"> · {words(role)}</span>
            </span>
            {organization.id === session?.organization.id ? (
              <span className="text-xs text-ink-3">Current</span>
            ) : (
              <Button onClick={() => void switchOrganization(organization.id)}>Switch</Button>
            )}
          </li>
        ))}
      </ul>
      <form
        className="flex max-w-md items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <div className="flex-1">
          <Field label="Create another organization">
            <Input required minLength={2} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
        </div>
        <Button type="submit" loading={create.isPending}>
          Create
        </Button>
      </form>
    </Card>
  );
}

export function SettingsPage() {
  return (
    <>
      <PageHeader title="Organization settings" />
      <div className="space-y-3">
        <OrganizationCard />
        <MembersCard />
        <WorkspacesCard />
      </div>
    </>
  );
}
