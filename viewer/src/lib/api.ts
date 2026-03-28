import type {
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

export async function fetchSessions(): Promise<SessionListItem[]> {
  return request<SessionListItem[]>("/api/sessions");
}

export async function fetchTrace(traceId: string): Promise<TraceRecord> {
  return request<TraceRecord>(`/api/traces/${traceId}`);
}

export async function stageSession(traceId: string): Promise<void> {
  await request<unknown>(`/api/sessions/${traceId}/stage`, { method: "POST" });
}

export async function unstageSession(traceId: string): Promise<void> {
  await request<unknown>(`/api/sessions/${traceId}/unstage`, { method: "POST" });
}

export async function approveSession(traceId: string): Promise<void> {
  await request<unknown>(`/api/sessions/${traceId}/approve`, { method: "POST" });
}

export async function rejectSession(traceId: string): Promise<void> {
  await request<unknown>(`/api/sessions/${traceId}/reject`, { method: "POST" });
}

export async function redactStep(
  traceId: string,
  stepIndex: number,
): Promise<void> {
  await request<unknown>(
    `/api/sessions/${traceId}/steps/${String(stepIndex)}/redact`,
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
  commitId: string,
): Promise<{ hf_commit_sha: string }> {
  return request<{ hf_commit_sha: string }>(`/api/push/${commitId}`, {
    method: "POST",
  });
}

export async function fetchRedactionPreview(
  traceId: string,
  tier: number,
): Promise<RedactionPreview> {
  return request<RedactionPreview>(
    `/api/sessions/${traceId}/redaction-preview?tier=${String(tier)}`,
  );
}
