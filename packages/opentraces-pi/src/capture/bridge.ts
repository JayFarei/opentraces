import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { runOpenTraces } from "../tools/opentraces";

export type PiSidecarEvent = {
  event: string;
  session_id: string;
  session_file?: string;
  cwd: string;
  sequence?: number;
  entry_id?: string;
  leaf_id?: string;
  turn_index?: number;
  tool_call_id?: string;
  final?: boolean;
  data?: Record<string, unknown>;
};

let sequence = 0;
// Once the opentraces CLI is confirmed absent for this session we stop trying to
// spawn it on every event (an extension-only install has nothing to capture
// into). Re-probed on the next extension load.
let cliMissing = false;

function captureOptedOut(cwd: string): boolean {
  // Pi capture is global-default (opt-out), consistent with Claude/Codex. The
  // only cheap, local opt-out signal is an explicit `excluded: true` marker;
  // global vs manual tracking mode is decided authoritatively by the Python
  // bridge (which auto-enrolls under global mode and drops under manual). An
  // absent marker is NOT an opt-out: the bridge auto-enrolls it under global.
  try {
    const marker = join(cwd, ".opentraces.json");
    if (!existsSync(marker)) return false;
    const data = JSON.parse(readFileSync(marker, "utf8"));
    return data?.excluded === true;
  } catch (_error) {
    return false;
  }
}

function writePayloadFile(cwd: string, payload: PiSidecarEvent): string {
  const dir = join(cwd, ".opentraces", "pi", "tmp");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `event-${Date.now()}-${Math.random().toString(16).slice(2)}.json`);
  writeFileSync(path, `${JSON.stringify(payload)}\n`, { encoding: "utf8", mode: 0o600 });
  return path;
}

export function sessionInfo(ctx: ExtensionContext): { session_id: string; session_file?: string; leaf_id?: string } {
  const manager: any = ctx.sessionManager as any;
  const file = manager?.getSessionFile?.();
  const id = manager?.getSessionId?.() ?? file ?? `pi-ephemeral-${Date.now()}`;
  const leaf = manager?.getLeafId?.();
  return { session_id: String(id), session_file: file ? String(file) : undefined, leaf_id: leaf ? String(leaf) : undefined };
}

export async function sendBridgeEvent(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  event: Omit<PiSidecarEvent, "session_id" | "session_file" | "cwd" | "sequence" | "leaf_id"> & Partial<PiSidecarEvent>,
  timeout = 150,
): Promise<Record<string, unknown>> {
  const info = sessionInfo(ctx);
  const payload: PiSidecarEvent = {
    ...event,
    ...info,
    cwd: event.cwd ?? ctx.cwd,
    sequence: event.sequence ?? ++sequence,
    leaf_id: event.leaf_id ?? info.leaf_id,
    data: event.data ?? {},
  } as PiSidecarEvent;
  if (event.event !== "setup_status" && captureOptedOut(payload.cwd)) {
    return { ok: true, status: "capture_disabled" };
  }
  if (event.event !== "setup_status" && cliMissing) {
    return { ok: true, status: "cli_unavailable" };
  }
  let payloadFile: string | undefined;
  try {
    payloadFile = writePayloadFile(payload.cwd, payload);
    const result = await runOpenTraces(pi, ["_pi-bridge", "--payload-file", payloadFile], ctx, { timeout, parseJson: true });
    if ((result.details as any)?.status === "missing_cli") cliMissing = true;
    return result.details;
  } catch (error) {
    return { ok: false, status: "fail_open", error: String(error) };
  } finally {
    if (payloadFile) {
      try { rmSync(payloadFile, { force: true }); } catch (_error) { /* fail-open cleanup */ }
    }
  }
}

export function compactToolInput(input: unknown): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) return {};
  const source = input as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(source)) {
    if (typeof value === "string") out[key] = value.length > 4000 ? `${value.slice(0, 4000)}...[truncated]` : value;
    else if (typeof value === "number" || typeof value === "boolean" || value == null) out[key] = value;
    else if (Array.isArray(value)) out[key] = value.slice(0, 20);
    else out[key] = "[object]";
  }
  return out;
}
