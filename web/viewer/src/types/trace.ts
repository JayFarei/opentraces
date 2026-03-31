/* TypeScript interfaces mirroring opentraces-schema Pydantic models */

export interface Task {
  description: string | null;
  source: string | null;
  repository: string | null;
  base_commit: string | null;
}

export interface Agent {
  name: string;
  version: string | null;
  model: string | null;
}

export interface VCS {
  type: "git" | "none";
  base_commit: string | null;
  branch: string | null;
  diff: string | null;
}

export interface Environment {
  os: string | null;
  shell: string | null;
  vcs: VCS;
  language_ecosystem: string[];
}

export interface ToolCall {
  tool_call_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  duration_ms: number | null;
}

export interface Observation {
  source_call_id: string;
  content: string | null;
  output_summary: string | null;
  error: string | null;
}

export interface Snippet {
  file_path: string;
  start_line: number | null;
  end_line: number | null;
  language: string | null;
  text: string | null;
  source_step: number | null;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  prefix_reuse_tokens: number;
}

export interface Step {
  step_index: number;
  role: "system" | "user" | "agent";
  content: string | null;
  reasoning_content: string | null;
  model: string | null;
  system_prompt_hash: string | null;
  agent_role: string | null;
  parent_step: number | null;
  call_type: "main" | "subagent" | "warmup" | null;
  subagent_trajectory_ref: string | null;
  tools_available: string[];
  tool_calls: ToolCall[];
  observations: Observation[];
  snippets: Snippet[];
  token_usage: TokenUsage;
  timestamp: string | null;
}

export interface Outcome {
  success: boolean | null;
  signal_source: string;
  signal_confidence: "derived" | "inferred" | "annotated";
  description: string | null;
  patch: string | null;
  committed: boolean;
  commit_sha: string | null;
}

export interface AttributionRange {
  start_line: number;
  end_line: number;
  content_hash: string | null;
  confidence: "high" | "medium" | "low" | null;
}

export interface AttributionConversation {
  contributor: Record<string, string>;
  url: string | null;
  ranges: AttributionRange[];
}

export interface AttributionFile {
  path: string;
  conversations: AttributionConversation[];
}

export interface Attribution {
  version: string;
  experimental: boolean;
  files: AttributionFile[];
}

export interface Metrics {
  total_steps: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_duration_s: number | null;
  cache_hit_rate: number | null;
  estimated_cost_usd: number | null;
}

export interface SecurityMetadata {
  tier: 1 | 2;
  flags_reviewed: number;
  redactions_applied: number;
  classifier_version: string | null;
}

export interface TraceRecord {
  schema_version: string;
  trace_id: string;
  session_id: string;
  content_hash: string | null;
  timestamp_start: string | null;
  timestamp_end: string | null;
  task: Task;
  agent: Agent;
  environment: Environment;
  system_prompts: Record<string, string>;
  tool_definitions: Record<string, unknown>[];
  steps: Step[];
  outcome: Outcome;
  dependencies: string[];
  metrics: Metrics;
  security: SecurityMetadata;
  attribution: Attribution | null;
  metadata: Record<string, unknown>;
}

/* ── UI-specific types ── */

export interface SecurityFlag {
  step_index: number;
  field: string;
  reason: string;
}

export interface TreeNode {
  id: string;
  type: "user" | "agent" | "system" | "tool" | "subagent";
  label: string;
  depth: number;
  children: TreeNode[];
  step?: Step;
  toolCall?: ToolCall;
  observation?: Observation;
  hasFlag: boolean;
  hasRedaction: boolean;
}

export type SessionStage =
  | "inbox"
  | "committed"
  | "pushed"
  | "rejected";

export interface SessionListItem {
  trace_id: string;
  task_description: string;
  agent_name: string;
  model: string;
  step_count: number;
  flag_count: number;
  stage: SessionStage;
  timestamp: string;
}

export interface AppContext {
  project_name: string;
  remote: string | null;
  review_policy: "review" | "auto";
  push_policy: "manual" | "auto-push";
  agents: string[];
  authenticated: boolean;
  username: string | null;
}

export interface RedactionPreview {
  trace_id: string;
  tier: number;
  steps: Array<{
    step_index: number;
    redactions: Array<{
      field: string;
      before: string;
      after: string;
    }>;
  }>;
  signal_kept: number;
}
