import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { AppLayout } from "./components/Layout";
import { Empty, LinkButton, Spinner } from "./components/ui";
import { AgentDetailPage, AgentNewPage, AgentsPage } from "./pages/Agents";
import { AnalyticsPage } from "./pages/Analytics";
import { LoginPage, RegisterPage } from "./pages/Auth";
import { ComparePage } from "./pages/Compare";
import { DashboardPage } from "./pages/Dashboard";
import { ExecutionTracePage } from "./pages/ExecutionTrace";
import { NewRunPage, RunLivePage, RunResultsPage, RunsPage } from "./pages/Runs";
import { ScenarioEditorPage, ScenariosPage } from "./pages/Scenarios";
import { SettingsPage } from "./pages/Settings";
import { SuiteDetailPage, SuitesPage } from "./pages/Suites";

function RequireSession() {
  const { session, loading } = useAuth();
  if (loading) return <Spinner label="Signing in" />;
  if (!session) return <Navigate to="/login" replace />;
  return <Outlet />;
}

function NotFound() {
  return <Empty title="Page not found" action={<LinkButton to="/">Back to dashboard</LinkButton>} />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<RequireSession />}>
        <Route element={<AppLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="agents/new" element={<AgentNewPage />} />
          <Route path="agents/:id" element={<AgentDetailPage />} />
          <Route path="scenarios" element={<ScenariosPage />} />
          <Route path="scenarios/new" element={<ScenarioEditorPage />} />
          <Route path="scenarios/:id" element={<ScenarioEditorPage />} />
          <Route path="suites" element={<SuitesPage />} />
          <Route path="suites/:id" element={<SuiteDetailPage />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="runs/new" element={<NewRunPage />} />
          <Route path="runs/:id" element={<RunLivePage />} />
          <Route path="runs/:id/results" element={<RunResultsPage />} />
          <Route path="executions/:id" element={<ExecutionTracePage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="compare" element={<ComparePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
