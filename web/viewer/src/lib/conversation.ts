import type { TraceStep } from "./api";

export interface ConvEvent {
  id: string;
  kind: "user" | "agent_text" | "tool_call" | "tool_result" | "reasoning" | "system";
  text: string;
  toolName?: string;
  status?: string;
  error?: string | null;
}

export interface ConvTurn {
  role: "user" | "agent" | "system";
  events: ConvEvent[];
}

function sanitize(s: string | null | undefined): string {
  if (!s) return "";
  // Strip ANSI CSI + control chars (mirrors TUI transforms.sanitize)
  return s
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    // eslint-disable-next-line no-control-regex
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "")
    // eslint-disable-next-line no-control-regex
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
}

function formatToolCall(name: string, args: unknown): string {
  try {
    return `${name}(${JSON.stringify(args, null, 2)})`;
  } catch {
    return `${name}(${String(args)})`;
  }
}

/** Flatten steps into turns. Each role boundary opens a new turn; reasoning,
 *  tool_call, and tool_result events stay inside the agent turn that produced
 *  them, rendered in order. Nothing is redacted. */
export function toTurns(steps: TraceStep[]): ConvTurn[] {
  const turns: ConvTurn[] = [];

  const top = (): ConvTurn | undefined => turns[turns.length - 1];

  for (const step of steps || []) {
    const role: ConvTurn["role"] =
      step.role === "system" ? "system" : step.role === "user" ? "user" : "agent";
    let turn = top();
    if (!turn || turn.role !== role) {
      turn = { role, events: [] };
      turns.push(turn);
    }
    const sid = `s${step.step_index}`;

    if (step.reasoning_content) {
      turn.events.push({
        id: `${sid}-rs`, kind: "reasoning", text: sanitize(step.reasoning_content),
      });
    }

    if (step.content) {
      const kind = role === "user" ? "user" : role === "system" ? "system" : "agent_text";
      turn.events.push({ id: `${sid}-c`, kind, text: sanitize(step.content) });
    }

    const tcs = step.tool_calls ?? [];
    for (let i = 0; i < tcs.length; i++) {
      const tc = tcs[i]!;
      turn.events.push({
        id: `${sid}-tc${i}`,
        kind: "tool_call",
        toolName: tc.tool_name,
        text: sanitize(formatToolCall(tc.tool_name, tc.input)),
      });
    }

    const obs = step.observations ?? [];
    for (let i = 0; i < obs.length; i++) {
      const ob = obs[i]!;
      turn.events.push({
        id: `${sid}-ob${i}`,
        kind: "tool_result",
        toolName: ob.tool_name,
        error: ob.error,
        text: sanitize(ob.content || ob.output_summary || ""),
      });
    }
  }

  return turns;
}

export function traceMeta(trace: {
  agent?: { name?: string; model?: string };
  metrics?: {
    total_input_tokens?: number;
    total_output_tokens?: number;
    total_cache_read_tokens?: number;
    estimated_cost_usd?: number;
    total_steps?: number;
  };
  steps?: TraceStep[];
  _security_flags?: { severity: string }[];
  security?: { redactions_applied?: number };
  timestamp_start?: string;
}) {
  const steps = trace.steps || [];
  const tools = steps.reduce((a, s) => a + (s.tool_calls?.length ?? 0), 0);
  // Claude Code pushes almost all context through the prompt cache, so
  // per-step input_tokens is tiny (new material only) while
  // cache_read_tokens carries the re-used prompt. Summing just
  // total_input_tokens under-reports by 2-4 orders of magnitude. Use
  // input + cache_read so the "in" figure matches what the model saw.
  const tin = (trace.metrics?.total_input_tokens ?? 0)
            + (trace.metrics?.total_cache_read_tokens ?? 0);
  const tout = trace.metrics?.total_output_tokens ?? 0;
  const cost = trace.metrics?.estimated_cost_usd ?? 0;
  // "flags" = total items the scanner flagged (residual + auto-redacted).
  // _security_flags is the residual list — auto-redaction drives that to
  // zero on clean sessions, so summing with redactions_applied gives the
  // honest "how many items did the pipeline touch" count.
  const flags = (trace._security_flags?.length ?? 0)
              + (trace.security?.redactions_applied ?? 0);
  const started = trace.timestamp_start
    ? new Date(trace.timestamp_start).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      })
    : "";
  return {
    agent: trace.agent?.name ?? "",
    model: trace.agent?.model ?? "",
    steps: trace.metrics?.total_steps ?? steps.length,
    tools,
    flags,
    tokensIn: tin.toLocaleString(),
    tokensOut: tout.toLocaleString(),
    cost: `$${cost.toFixed(2)}`,
    started,
  };
}

export function relativeAge(iso?: string): string {
  if (!iso) return "";
  const d = Date.parse(iso);
  if (Number.isNaN(d)) return "";
  const sec = Math.max(1, Math.round((Date.now() - d) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}
