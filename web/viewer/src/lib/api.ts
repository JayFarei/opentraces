import type {
  AppContext,
  SessionListItem,
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

interface RawSession {
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

const VALID_STAGES = new Set(["inbox", "committed", "pushed", "rejected"]);

function mapSession(raw: RawSession): SessionListItem {
  const rawStage = raw._stage ?? "inbox";
  return {
    trace_id: raw.trace_id,
    task_description: raw.task ?? "",
    agent_name: raw.agent ?? "unknown",
    model: raw.model ?? "unknown",
    step_count: raw.steps ?? 0,
    flag_count: raw.security_flags ?? 0,
    stage: (VALID_STAGES.has(rawStage) ? rawStage : "inbox") as SessionListItem["stage"],
    timestamp: raw.timestamp ?? "",
  };
}

export async function fetchSessions(): Promise<SessionListItem[]> {
  const raw = await request<RawSession[]>("/api/sessions");
  return raw.map(mapSession);
}

export async function fetchAppContext(): Promise<AppContext> {
  return request<AppContext>("/api/context");
}

export async function fetchTrace(traceId: string): Promise<TraceRecord> {
  return request<TraceRecord>(`/api/session/${traceId}/detail`);
}

export async function commitSession(traceId: string): Promise<void> {
  await request<unknown>(`/api/session/${traceId}/commit`, { method: "POST" });
}

export async function rejectSession(traceId: string): Promise<void> {
  await request<unknown>(`/api/session/${traceId}/reject`, { method: "POST" });
}

export async function redactStep(
  traceId: string,
  stepIndex: number,
): Promise<void> {
  await request<unknown>(
    `/api/session/${traceId}/step/${String(stepIndex)}/redact`,
    { method: "POST" },
  );
}

export async function commitSessions(
  sessionIds: string[],
  message: string,
): Promise<{ commit_id: string }> {
  return request<{ commit_id: string }>("/api/commit", {
    method: "POST",
    body: JSON.stringify({ session_ids: sessionIds, message }),
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
    `/api/session/${traceId}/redaction-preview?tier=${String(tier)}`,
  );
}
