const API = "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${path}: ${body}`);
  }
  return res.json() as Promise<T>;
}

async function pingLifecycle(clientId: string): Promise<void> {
  await fetch(`${API}/api/_lifecycle/ping`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId }),
  });
}

async function quitLifecycle(clientId: string): Promise<void> {
  await fetch(`${API}/api/_lifecycle/quit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId }),
  });
}

function disconnectLifecycle(clientId: string): void {
  const path = `${API}/api/_lifecycle/disconnect?client_id=${encodeURIComponent(clientId)}`;
  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    navigator.sendBeacon(path, "");
    return;
  }
  void fetch(path, { method: "POST", keepalive: true }).catch(() => {});
}

export interface TraceListItem {
  trace_id: string;
  task: string;
  agent: string;
  model: string | null;
  steps: number;
  tool_calls: number;
  timestamp: string;
  status: string;
  _stage: "inbox" | "staged" | "pushed" | "rejected" | "blocked";
  security_flags: number;
  generation_index: number;
  project: string;
  llm_review?: { status?: string; shareable?: boolean };
}

export interface AppContext {
  project_name: string;
  remote: string | null;
  review_policy: string;
  push_policy: string;
  authenticated: boolean;
  username: string | null;
  security?: {
    trufflehog: { enabled: boolean; version: string | null };
    llm_review: { enabled: boolean; model: string };
  };
}

export interface TraceStep {
  step_index: number;
  role: "user" | "agent" | "system";
  content: string | null;
  reasoning_content?: string | null;
  model?: string | null;
  tool_calls?: { tool_call_id?: string; tool_name: string; input: unknown; duration_ms?: number }[];
  observations?: { source_call_id?: string; tool_name?: string; content?: string; output_summary?: string; error?: string | null }[];
  timestamp?: string;
  parent_step?: number | null;
  call_type?: "main" | "subagent" | "warmup" | null;
  subagent_trajectory_ref?: string | null;
}

export interface TraceRecord {
  trace_id: string;
  generation_index?: number;
  task: { description: string };
  agent: { name: string; model: string };
  steps: TraceStep[];
  timestamp_start: string;
  metrics: {
    total_steps: number;
    total_input_tokens: number;
    total_output_tokens: number;
    total_cache_read_tokens?: number;
    total_cache_creation_tokens?: number;
    estimated_cost_usd: number;
  };
  security?: {
    scanned?: boolean;
    flags_reviewed?: number;
    redactions_applied?: number;
    classifier_version?: string;
  };
  metadata?: {
    security?: {
      trufflehog?: {
        status: "clean" | "findings";
        version: string;
        scanned_at: string;
        findings_count: number;
      };
      trufflehog_findings?: { detector: string; verified: boolean; line: number; source_file: string }[];
    };
    llm_review?: {
      status?: string;
      shareable?: string;
      missed_sensitive_data?: string;
      model?: string;
    };
  };
  block_reason?: string | null;
  _security_flags?: { type: string; reason: string; severity: string }[];
  _stage?: string;
}

export interface TraceTreeNode {
  id: string;
  parent_id: string | null;
  kind: "step" | "tool_call" | "observation" | "subagent_ref" | "compaction";
  step_index: number | null;
  timestamp: string | null;
  preview: string;
  label: string | null;
  on_active_path: boolean;
  entity_ref: Record<string, unknown> | null;
  role?: "user" | "agent" | "system" | null;
  children: TraceTreeNode[];
}

export interface ResumeResponse {
  argv: string[];
  new_session_id: string;
  truncated_at_line: number;
  parent_session_id: string;
  parent_step_id: string;
}

export interface GraphTrace {
  id: string;
  trace_id: string;
  canonical: boolean;
  lines: number;
  short_name: string | null;
  changes?: string;
  fns?: string;
  info?: string;
}

export interface GraphCommit {
  id: string;
  sha: string;
  msg: string;
  timestamp: string;
  pct: number;
  coverage_role: "green" | "yellow" | "red";
  traces: GraphTrace[];
}

export interface BlameSession {
  id: string;
  trace_id: string;
  name: string;
  lines: number;
  pct: string;
  model: string;
  entities: string;
  canonical: boolean;
}

export interface BlamePayload {
  sha: string;
  id: string;
  msg: string;
  pct: number;
  coverage: string;
  attributed: string;
  fileCount: number;
  sessions: BlameSession[];
  files: { path: string; total: number; attr: number }[];
}

export interface InverseBlame {
  trace: { id: string; trace_id: string; name: string; lines: number; model: string };
  commits: { id: string; sha: string; msg: string; linesInCommit: number; totalCommitLines: number; pct: string }[];
  files: { path: string; lines: number }[];
}

export const api = {
  traces: () => req<TraceListItem[]>("/api/traces"),
  context: () => req<AppContext>("/api/context"),
  lifecyclePing: (clientId: string) => pingLifecycle(clientId),
  lifecycleDisconnect: (clientId: string) => disconnectLifecycle(clientId),
  quit: (clientId: string) => quitLifecycle(clientId),
  trace: (id: string) => req<TraceRecord>(`/api/trace/${id}/detail`),
  traceTree: (id: string) => req<{ tree: TraceTreeNode[] }>(`/api/traces/${id}/tree`),
  resumeTrace: (id: string, atStep: string) =>
    req<ResumeResponse>(`/api/traces/${id}/resume`, {
      method: "POST",
      body: JSON.stringify({ at_step: atStep }),
    }),
  graph: (params: { limit?: number; page?: number; trace?: string; entities?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.page) q.set("page", String(params.page));
    if (params.trace) q.set("trace", params.trace);
    if (params.entities === false) q.set("entities", "0");
    return req<{ commits: GraphCommit[] }>(`/api/graph?${q.toString()}`);
  },
  blame: (sha: string) => req<BlamePayload>(`/api/blame/${sha}`),
  inverseBlame: (traceId: string) => req<InverseBlame>(`/api/trace/${traceId}/commits`),
  addTrace: (id: string) => req<unknown>(`/api/trace/${id}/add`, { method: "POST" }),
  unstage: (id: string) => req<unknown>(`/api/trace/${id}/unstage`, { method: "POST" }),
  reject: (id: string) => req<unknown>(`/api/trace/${id}/reject`, { method: "POST" }),
  push: (opts: { commitId?: string; llmReview?: boolean } = {}) => {
    const body: Record<string, unknown> = {};
    if (opts.commitId) body.commit_id = opts.commitId;
    if (opts.llmReview) body.llm_review = true;
    return req<{
      status?: string; count?: number; message?: string; error?: string;
      log?: string; stage?: string; trace_ids?: string[];
    }>("/api/push", { method: "POST", body: JSON.stringify(body) });
  },
  addBatch: (ids: string[], message: string) =>
    req<{ commit_id: string | null }>("/api/add", {
      method: "POST", body: JSON.stringify({ trace_ids: ids, message }),
    }),
  refresh: () =>
    req<{ status: string; new: number; updated: number; skipped: number; errors: number; total: number }>(
      "/api/refresh", { method: "POST" },
    ),
};
