import clsx from "clsx";
import { Activity, BarChart3, Bot, FlaskConical, GitCompare, LayoutDashboard, Layers, LogOut, Menu, Moon, Play, Settings, Sun, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Me } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const NAVIGATION: { heading: string; items: { to: string; label: string; icon: typeof Bot; end?: boolean }[] }[] = [
  { heading: "Overview", items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard, end: true }] },
  {
    heading: "Build",
    items: [
      { to: "/agents", label: "Agents", icon: Bot },
      { to: "/scenarios", label: "Scenarios", icon: FlaskConical },
      { to: "/suites", label: "Test suites", icon: Layers },
    ],
  },
  {
    heading: "Evaluate",
    items: [
      { to: "/runs", label: "Test runs", icon: Play },
      { to: "/analytics", label: "Analytics", icon: BarChart3 },
      { to: "/compare", label: "Compare versions", icon: GitCompare },
    ],
  },
  { heading: "Organization", items: [{ to: "/settings", label: "Settings", icon: Settings }] },
];

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? "light");
  return [
    theme,
    () => {
      const next = theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try {
        localStorage.setItem("aa-theme", next);
      } catch {
        // The choice just won't persist, for example in a private window.
      }
      setTheme(next);
    },
  ];
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-2">
      <span className="flex size-6 items-center justify-center rounded-md bg-accent text-white">
        <Activity size={14} aria-hidden />
      </span>
      <span className="text-sm font-semibold tracking-tight text-ink">AgentArena</span>
    </div>
  );
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="mt-4 space-y-4">
      {NAVIGATION.map((group) => (
        <div key={group.heading}>
          <p className="px-2 pb-1 text-xs font-medium text-ink-3">{group.heading}</p>
          <ul className="space-y-0.5">
            {group.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  onClick={onNavigate}
                  className={({ isActive }) => clsx("flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition", isActive ? "bg-accent-wash font-medium text-accent-ink" : "text-ink-2 hover:bg-raised hover:text-ink")}
                >
                  <item.icon size={15} aria-hidden />
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function OrganizationSwitcher() {
  const { session, switchOrganization } = useAuth();
  const navigate = useNavigate();
  const { data } = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/v1/auth/me") });
  const organizations = data?.organizations ?? [];
  if (organizations.length < 2) {
    return <p className="truncate px-2 text-xs text-ink-3">{session?.organization.name}</p>;
  }
  return (
    <select
      aria-label="Organization"
      value={session?.organization.id}
      onChange={(event) => {
        void switchOrganization(event.target.value).then(() => navigate("/"));
      }}
      className="w-full rounded-md border border-line-strong bg-surface px-2 py-1 text-xs text-ink"
    >
      {organizations.map(({ organization }) => (
        <option key={organization.id} value={organization.id}>
          {organization.name}
        </option>
      ))}
    </select>
  );
}

function SidebarFooter() {
  const { session, logout } = useAuth();
  const [theme, toggleTheme] = useTheme();
  return (
    <div className="mt-auto space-y-2 border-t border-line pt-3">
      <OrganizationSwitcher />
      <div className="flex items-center gap-1 px-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-ink">{session?.user.display_name}</p>
          <p className="truncate text-xs text-ink-3">{session?.role}</p>
        </div>
        <button type="button" onClick={toggleTheme} aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"} className="rounded p-1.5 text-ink-2 hover:bg-raised">
          {theme === "dark" ? <Sun size={15} aria-hidden /> : <Moon size={15} aria-hidden />}
        </button>
        <button type="button" onClick={() => void logout()} aria-label="Sign out" className="rounded p-1.5 text-ink-2 hover:bg-raised">
          <LogOut size={15} aria-hidden />
        </button>
      </div>
    </div>
  );
}

export function AppLayout() {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-surface p-3 lg:flex">
        <Brand />
        <Navigation />
        <SidebarFooter />
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-surface p-3">
            <div className="flex items-center justify-between">
              <Brand />
              <button type="button" aria-label="Close menu" onClick={() => setOpen(false)} className="rounded p-1.5 text-ink-2 hover:bg-raised">
                <X size={16} aria-hidden />
              </button>
            </div>
            <Navigation onNavigate={() => setOpen(false)} />
            <SidebarFooter />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-2 border-b border-line bg-surface px-4 py-2 lg:hidden">
          <button type="button" aria-label="Open menu" onClick={() => setOpen(true)} className="rounded p-1.5 text-ink-2 hover:bg-raised">
            <Menu size={18} aria-hidden />
          </button>
          <Brand />
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-5 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function AuthLayout({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex justify-center">
          <Brand />
        </div>
        <div className="rounded-lg border border-line bg-surface p-6">
          <h1 className="text-lg font-semibold tracking-tight text-ink">{title}</h1>
          <p className="mb-4 text-sm text-ink-2">{subtitle}</p>
          {children}
        </div>
      </div>
    </div>
  );
}
