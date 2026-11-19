// Shapes returned by the services. The source of truth is contracts/*.openapi.yaml.

export type Role = "owner" | "admin" | "member" | "viewer";

export interface User {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  created_at: string;
}

export interface Session {
  access_token: string;
  expires_in: number;
  user: User;
  organization: Organization;
  role: Role;
}

export interface Me {
  user: User;
  organization: Organization;
  role: Role;
  organizations: { organization: Organization; role: Role }[];
}

export interface Member {
  user_id: string;
  email: string;
  display_name: string;
  role: Role;
  joined_at: string;
}

export type AgentStatus = "draft" | "active" | "unreachable" | "disabled";

export interface Credential {
  id: string;
  auth_type: "bearer" | "api_key";
  header_name: string;
  key_id: string;
  updated_at: string;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  agent_type: string;
  status: AgentStatus;
  latest_version: number;
  endpoint_url: string;
  credential: Credential | null;
  last_validated_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentVersion {
  id: string;
  version: number;
  endpoint_url: string;
  integration_type: string;
  request_timeout_seconds: number;
  default_headers: Record<string, string>;
  model_name: string | null;
  input_cost_per_1k: string | null;
  output_cost_per_1k: string | null;
  notes: string;
  created_at: string;
}

export interface ValidationResult {
  valid: boolean;
  status: AgentStatus;
  version: number;
  latency_ms: number | null;
  checks: { name: string; passed: boolean; detail: string }[];
}

export interface ScenarioSummary {
  id: string;
  name: string;
  description: string;
  scenario_type: string;
  difficulty: string;
  tags: string[];
  latest_version: number;
  parent_scenario_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScenarioVersion {
  id: string;
  version: number;
  definition: Record<string, unknown>;
  expected: Record<string, unknown>;
  criteria: Record<string, unknown>;
  change_note: string;
  created_at: string;
}

export interface Scenario extends ScenarioSummary {
  current: ScenarioVersion;
}

export interface SuiteItem {
  scenario_id: string;
  name: string;
  scenario_type: string;
  difficulty: string;
  pinned_version: number | null;
  latest_version: number;
}

export interface Suite {
  id: string;
  name: string;
  description: string;
  items: SuiteItem[];
  created_at: string;
  updated_at: string;
}

export interface Dataset {
  id: string;
  name: string;
  description: string;
  rows: Record<string, unknown>[];
}

export interface Progress {
  total: number;
  pending: number;
  dispatched: number;
  running: number;
  retry_wait: number;
  completed: number;
  failed: number;
  timed_out: number;
  cancelled: number;
  evaluated: number;
  passed: number;
}

export type RunStatus = "queued" | "running" | "evaluating" | "completed" | "failed" | "cancelling" | "cancelled";

export interface Run {
  id: string;
  name: string;
  status: RunStatus;
  agent_id: string;
  agent_name: string;
  agent_version: number;
  suite_id: string | null;
  seed: number;
  concurrency: number;
  timeout_seconds: number;
  max_retries: number;
  repetitions: number;
  judge_enabled: boolean;
  total_executions: number;
  correlation_id: string;
  trace_id: string | null;
  replay_of: string | null;
  warning: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: Progress;
}

export interface Execution {
  id: string;
  run_id: string;
  scenario_id: string;
  scenario_version: number;
  scenario_name: string;
  scenario_type: string;
  difficulty: string;
  repetition: number;
  seed: number;
  status: string;
  attempt: number;
  max_attempts: number;
  worker_id: string | null;
  error_code: string | null;
  error_message: string | null;
  latency_ms: number | null;
  queue_wait_ms: number | null;
  agent_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  passed: boolean | null;
  score: number | null;
  trace_id: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Step {
  attempt: number;
  seq: number;
  type: string;
  latency_ms: number | null;
  payload: {
    content?: string | null;
    name?: string | null;
    arguments?: Record<string, unknown> | null;
    output?: unknown;
    is_error?: boolean;
    task_status?: string | null;
    structured_output?: unknown;
    injected_failure?: boolean;
  };
}

export interface ExecutionDetail extends Execution {
  agent_id: string;
  agent_version: number;
  agent_name: string;
  run_name: string;
  steps: Step[];
}

export interface Check {
  evaluator: string;
  dimension: string;
  score: number;
  passed: boolean;
  details: Record<string, unknown>;
}

export interface Judgement {
  model: string;
  status: string;
  relevance: number | null;
  reasoning_quality: number | null;
  context_consistency: number | null;
  conversation_quality: number | null;
  error_analysis: string;
}

export interface EvaluationDetail {
  passed: boolean;
  score: number;
  dimension_scores: Record<string, number>;
  failure_categories: string[];
  evaluator_version: string;
  checks: Check[];
  judgement: Judgement | null;
}

export interface ScenarioResult {
  scenario_id: string;
  scenario_name: string;
  scenario_type: string;
  difficulty: string;
  scenario_version: number;
  total: number;
  passed: number;
  pass_rate: number;
  avg_score: number;
  failure_categories: Record<string, number>;
}

export interface Recommendation {
  category: string;
  occurrences: number;
  share: number;
  advice: string;
}

export interface BenchmarkSummary {
  verdict: "passed" | "failed";
  pass_bar: number;
  totals: { executions: number; passed: number; failed: number; pass_rate: number; avg_score: number };
  reliability: {
    completed: number;
    failed: number;
    timed_out: number;
    success_rate: number;
    failure_rate: number;
    timeout_rate: number;
    retried: number;
  };
  latency_ms: { avg: number | null; p50: number | null; p95: number | null; max: number | null };
  usage: {
    input_tokens: number;
    output_tokens: number;
    tokens_reported: boolean;
    cost_usd: number | null;
    avg_cost_usd: number | null;
  };
  throughput: {
    elapsed_seconds: number;
    executions_per_minute: number | null;
    avg_queue_wait_ms: number | null;
    p95_queue_wait_ms: number | null;
  };
  dimensions: Record<string, number>;
  by_scenario_type: Record<string, { total: number; passed: number; pass_rate: number }>;
  by_difficulty: Record<string, { total: number; passed: number; pass_rate: number }>;
  scenarios: ScenarioResult[];
  failure_categories: Record<string, number>;
  failed_executions: {
    execution_id: string;
    scenario_name: string;
    repetition: number;
    score: number;
    execution_status: string;
    error_code: string | null;
    failure_categories: string[];
  }[];
  recommendations: Recommendation[];
  reproducibility: { evaluator_versions: string[]; scenario_versions: Record<string, number> };
}

export interface Benchmark {
  run_id: string;
  run_name: string;
  agent_id: string;
  agent_name: string;
  agent_version: number;
  run_status: string;
  verdict: "passed" | "failed";
  pass_rate: number;
  final: boolean;
  summary: BenchmarkSummary;
  created_at: string | null;
}

export interface BenchmarkItem {
  run_id: string;
  run_name: string;
  agent_id: string;
  agent_name: string;
  agent_version: number;
  run_status: string;
  verdict: "passed" | "failed";
  pass_rate: number;
  created_at: string;
}

export interface ComparisonScenario {
  scenario_id: string;
  scenario_name: string;
  scenario_type: string;
  base_pass_rate: number;
  candidate_pass_rate: number;
  change: number;
  same_version: boolean;
}

export interface Comparison {
  base: { run_id: string; run_name: string; agent_name: string; agent_version: number; created_at: string };
  candidate: { run_id: string; run_name: string; agent_name: string; agent_version: number; created_at: string };
  comparison: {
    outcome: "improved" | "regressed" | "unchanged";
    pass_rate: { base: number; candidate: number; change: number; p_value: number | null; significant: boolean };
    latency_ms: Record<"avg" | "p50" | "p95", { base: number | null; candidate: number | null; change: number | null }>;
    cost_usd: { base: number | null; candidate: number | null; change: number | null };
    dimensions: { dimension: string; base: number | null; candidate: number | null; change: number | null }[];
    scenarios: ComparisonScenario[];
    regressions: ComparisonScenario[];
    improvements: ComparisonScenario[];
    shared_scenarios: number;
    only_in_base: number;
    only_in_candidate: number;
  };
}

export interface Overview {
  days: number;
  benchmarked_runs: number;
  runs_passed: number;
  runs_failed: number;
  executions: number;
  executions_passed: number;
  pass_rate: number | null;
  avg_latency_ms: number | null;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  timeouts: number;
  errors: number;
}

export interface TimePoint {
  bucket: string;
  executions: number;
  pass_rate: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  cost_usd: number | null;
}

export interface AgentPerformance {
  agent_id: string;
  agent_name: string;
  agent_version: number;
  executions: number;
  pass_rate: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  cost_usd: number | null;
  last_run_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
}
