import type {
  AppContext,
  TraceListItem,
  TraceRecord,
  RedactionPreview,
} from "../types/trace";

const API_BASE = ""; // proxied by Vite dev server

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(`${String(res.status)} ${path}: ${body}`);
  }
  return res.json() as Promise<T>;
}

interface RawTrace {
  trace_id: string;
  task: string;
  agent: string;
  model: string | null;
  steps: number;
  security_flags: number;
  _stage: string;
  status: string;
  timestamp: string;
  tool_calls: number;
  project: string;
}

const VALID_STAGES = new Set(["inbox", "staged", "pushed", "rejected"]);

function mapTrace(raw: RawTrace): TraceListItem {
  const rawStage = raw._stage ?? "inbox";
  return {
    trace_id: raw.trace_id,
    task_description: raw.task ?? "",
    agent_name: raw.agent ?? "unknown",
    model: raw.model ?? "unknown",
    step_count: raw.steps ?? 0,
    flag_count: raw.security_flags ?? 0,
    stage: (VALID_STAGES.has(rawStage) ? rawStage : "inbox") as TraceListItem["stage"],
    timestamp: raw.timestamp ?? "",
  };
}

export async function fetchTraces(): Promise<TraceListItem[]> {
  const raw = await request<RawTrace[]>("/api/traces");
  return raw.map(mapTrace);
}

export async function fetchAppContext(): Promise<AppContext> {
  return request<AppContext>("/api/context");
}

export async function fetchTrace(traceId: string): Promise<TraceRecord> {
  return request<TraceRecord>(`/api/trace/${traceId}/detail`);
}

export async function commitTrace(traceId: string): Promise<void> {
  await request<unknown>(`/api/trace/${traceId}/commit`, { method: "POST" });
}

export async function rejectTrace(traceId: string): Promise<void> {
  await request<unknown>(`/api/trace/${traceId}/reject`, { method: "POST" });
}

export async function redactStep(
  traceId: string,
  stepIndex: number,
): Promise<void> {
  await request<unknown>(
    `/api/trace/${traceId}/step/${String(stepIndex)}/redact`,
    { method: "POST" },
  );
}

export async function commitTraces(
  traceIds: string[],
  message: string,
): Promise<{ commit_id: string }> {
  return request<{ commit_id: string }>("/api/commit", {
    method: "POST",
    body: JSON.stringify({ trace_ids: traceIds, message }),
  });
}

export async function pushCommit(
  commitId?: string,
): Promise<{ hf_commit_sha: string }> {
  return request<{ hf_commit_sha: string }>("/api/push", {
    method: "POST",
    body: JSON.stringify(commitId ? { commit_id: commitId } : {}),
  });
}

export async function setRemote(
  remote: string,
): Promise<{ status: string; remote: string }> {
  return request<{ status: string; remote: string }>("/api/remote", {
    method: "POST",
    body: JSON.stringify({ remote }),
  });
}

export async function fetchRedactionPreview(
  traceId: string,
  tier: number,
): Promise<RedactionPreview> {
  return request<RedactionPreview>(
    `/api/trace/${traceId}/redaction-preview?tier=${String(tier)}`,
  );
}
