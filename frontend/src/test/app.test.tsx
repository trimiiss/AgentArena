import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, query, setAccessToken } from "../api/client";
import { App } from "../App";
import { AuthProvider } from "../auth/AuthContext";
import { BarList, SegmentedBar, niceTicks } from "../components/charts";
import { ErrorNotice, StateBadge } from "../components/ui";
import { compact, duration, milliseconds, percent, usd, words } from "../lib/format";
import { SseParser } from "../lib/sse";

const session = {
  access_token: "token-1",
  expires_in: 900,
  user: { id: "u1", email: "ada@example.com", display_name: "Ada", created_at: "2026-01-01T00:00:00Z" },
  organization: { id: "o1", name: "Acme", slug: "acme", created_at: "2026-01-01T00:00:00Z" },
  role: "owner",
};

const overview = {
  days: 30,
  benchmarked_runs: 4,
  runs_passed: 3,
  runs_failed: 1,
  executions: 48,
  executions_passed: 36,
  pass_rate: 0.75,
  avg_latency_ms: 120,
  p50_latency_ms: 100,
  p95_latency_ms: 240,
  input_tokens: 1000,
  output_tokens: 200,
  cost_usd: 0.42,
  timeouts: 2,
  errors: 1,
};

type Routes = Record<string, unknown | ((init?: RequestInit) => unknown)>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi(routes: Routes, fallback = 404) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const match = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((path) => url.startsWith(path));
    if (!match) return json({ detail: "Not found" }, fallback);
    const entry = routes[match];
    const body = await (typeof entry === "function" ? (entry as (init?: RequestInit) => unknown)(init) : entry);
    return body instanceof Response ? body : json(body);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderApp(path: string): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>{children}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(<App />, { wrapper });
}

beforeEach(() => {
  setAccessToken(null);
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("authentication", () => {
  it("sends a visitor without a session to the login page", async () => {
    mockApi({ "/v1/auth/refresh": new Response(null, { status: 401 }) });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows the server's message when login fails", async () => {
    mockApi({
      "/v1/auth/refresh": () => new Response(null, { status: 401 }),
      "/v1/auth/login": () => json({ detail: "Invalid email or password", correlation_id: "abc123" }, 401),
    });
    renderApp("/login");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Invalid email or password");
    expect(alert).toHaveTextContent("abc123");
  });

  it("restores a session from the refresh cookie", async () => {
    mockApi({
      "/v1/auth/refresh": () => json(session),
      "/v1/auth/me": { ...session, organizations: [] },
      "/v1/analytics/overview": overview,
      "/v1/analytics/timeseries": [],
      "/v1/analytics/failures": [],
      "/v1/runs": { items: [], total: 0 },
      "/v1/agents": { items: [], total: 0 },
      "/v1/scenarios": { items: [], total: 0 },
    });
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });
});

describe("dashboard", () => {
  it("renders metrics from the API instead of hardcoded values", async () => {
    mockApi({
      "/v1/auth/refresh": () => json(session),
      "/v1/auth/me": { ...session, organizations: [] },
      "/v1/analytics/overview": overview,
      "/v1/analytics/timeseries": [],
      "/v1/analytics/failures": [{ category: "policy_violation", count: 7 }],
      "/v1/runs": { items: [], total: 3 },
      "/v1/agents": { items: [], total: 1 },
      "/v1/scenarios": { items: [], total: 1 },
    });
    renderApp("/");
    expect(await screen.findByText("75.0%")).toBeInTheDocument();
    expect(screen.getByText("240 ms")).toBeInTheDocument();
    expect(screen.getByText("$0.42")).toBeInTheDocument();
    expect(await screen.findByText("Policy violation")).toBeInTheDocument();
  });

  it("guides a brand new workspace", async () => {
    mockApi({
      "/v1/auth/refresh": () => json(session),
      "/v1/auth/me": { ...session, organizations: [] },
      "/v1/analytics/overview": { ...overview, executions: 0, pass_rate: null, cost_usd: null },
      "/v1/analytics/timeseries": [],
      "/v1/analytics/failures": [],
      "/v1/runs": { items: [], total: 0 },
      "/v1/agents": { items: [], total: 0 },
      "/v1/scenarios": { items: [], total: 0 },
    });
    renderApp("/");
    expect(await screen.findByText("Get started")).toBeInTheDocument();
    expect(screen.getByText("Not reported")).toBeInTheDocument();
  });
});

describe("roles", () => {
  it("hides write actions from viewers", async () => {
    mockApi({
      "/v1/auth/refresh": () => json({ ...session, role: "viewer" }),
      "/v1/auth/me": { ...session, role: "viewer", organizations: [] },
      "/v1/agents": { items: [], total: 0 },
    });
    renderApp("/agents");
    expect(await screen.findByRole("heading", { name: "Agents" })).toBeInTheDocument();
    expect(await screen.findByText("No agents yet")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Register agent" })).not.toBeInTheDocument();
  });
});

describe("api client", () => {
  it("refreshes once and retries when the token has expired", async () => {
    setAccessToken("expired");
    let calls = 0;
    mockApi({
      "/v1/auth/refresh": () => json(session),
      "/v1/agents": (init?: RequestInit) => {
        calls += 1;
        const header = new Headers(init?.headers).get("Authorization");
        return header === "Bearer token-1" ? { total: 0, items: [] } : json({ detail: "Expired" }, 401);
      },
    });
    await expect(api("/v1/agents")).resolves.toEqual({ total: 0, items: [] });
    expect(calls).toBe(2);
  });

  it("makes one refresh call for parallel requests", async () => {
    setAccessToken("expired");
    let refreshes = 0;
    mockApi({
      "/v1/auth/refresh": async () => {
        refreshes += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return json(session);
      },
      "/v1/agents": (init?: RequestInit) => (new Headers(init?.headers).get("Authorization") === "Bearer token-1" ? {} : json({ detail: "Expired" }, 401)),
    });
    await Promise.all([api("/v1/agents"), api("/v1/agents"), api("/v1/agents")]);
    expect(refreshes).toBe(1);
  });

  it("turns validation errors into a readable message", async () => {
    mockApi({
      "/v1/agents": () => json({ detail: [{ loc: ["body", "name"], msg: "too short" }] }, 422),
    });
    await expect(api("/v1/agents", { method: "POST", body: {} })).rejects.toThrow("name: too short");
  });

  it("drops empty query parameters", () => {
    expect(query({ a: 1, b: "", c: null, d: undefined, e: false })).toBe("?a=1&e=false");
  });
});

describe("server-sent events", () => {
  it("handles events split across network chunks", () => {
    const events: [string, unknown][] = [];
    const parser = new SseParser((event, data) => events.push([event, data]));
    parser.feed('event: prog');
    parser.feed('ress\ndata: {"a"');
    parser.feed(': 1}\n\nevent: done\ndata: {}\n\n');
    expect(events).toEqual([
      ["progress", { a: 1 }],
      ["done", {}],
    ]);
  });

  it("ignores keep-alive comments", () => {
    const events: unknown[] = [];
    const parser = new SseParser((event) => events.push(event));
    parser.feed(": keep-alive\n\n");
    expect(events).toEqual([]);
  });
});

describe("formatting", () => {
  it("formats values for display", () => {
    expect(percent(0.756)).toBe("75.6%");
    expect(percent(null)).toBe("–");
    expect(milliseconds(1234)).toBe("1,234 ms");
    expect(milliseconds(12_340)).toBe("12.3 s");
    expect(compact(1284)).toBe("1,284");
    expect(compact(12_900)).toBe("12.9K");
    expect(usd(null)).toBe("Not reported");
    expect(usd(0.0012)).toBe("$0.0012");
    expect(words("policy_violation")).toBe("Policy violation");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:01:05Z")).toBe("1 min 5 s");
  });
});

describe("components", () => {
  it("never shows status through color alone", () => {
    render(<StateBadge state="timed_out" />);
    expect(screen.getByText("timed out")).toBeInTheDocument();
  });

  it("gives axes round numbers", () => {
    expect(niceTicks(1, 4)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(niceTicks(240, 4)).toEqual([0, 100, 200, 300]);
    expect(niceTicks(0, 4)).toEqual([0, 1]);
  });

  it("prints the value beside each bar", () => {
    render(<BarList rows={[{ key: "a", label: "Timeouts", value: 3, display: "3" }]} />);
    const row = screen.getByTitle("Timeouts: 3");
    expect(within(row).getByText("3")).toBeInTheDocument();
  });

  it("describes a segmented bar for screen readers", () => {
    render(<SegmentedBar label="Progress" total={3} parts={[{ label: "Done", value: 2, color: "red" }, { label: "Waiting", value: 1, color: "blue" }]} />);
    expect(screen.getByRole("img")).toHaveAccessibleName("Progress: Done 2, Waiting 1");
  });

  it("shows a reference id so errors can be traced in the logs", () => {
    render(<ErrorNotice error={new ApiError(500, "It broke", "ref-1")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("ref-1");
  });
});

describe("agent registration", () => {
  it("submits the form and opens the new agent", async () => {
    const created = {
      id: "a1",
      name: "Support Bot",
      description: "",
      agent_type: "customer_support",
      status: "draft",
      latest_version: 1,
      endpoint_url: "https://agent.example.com",
      credential: null,
      last_validated_at: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    const calls = mockApi({
      "/v1/auth/refresh": () => json(session),
      "/v1/auth/me": { ...session, organizations: [] },
      "/v1/agents/a1/versions": [],
      "/v1/agents/a1": created,
      "/v1/agents": (init?: RequestInit) => (init?.method === "POST" ? created : { items: [created], total: 1 }),
      "/v1/runs": { items: [], total: 0 },
      "/v1/analytics/agents": [],
    });
    renderApp("/agents/new");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Name"), "Support Bot");
    await user.type(screen.getByLabelText(/Endpoint URL/), "https://agent.example.com");
    await user.click(screen.getByRole("button", { name: "Register agent" }));
    await waitFor(() => expect(calls).toContain("POST /v1/agents"));
    expect(await screen.findByRole("heading", { name: "Support Bot" })).toBeInTheDocument();
  });
});
