import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import { captureStatus, maybeShowStartupStatus } from "./bootstrap/status";
import { compactToolInput, sendBridgeEvent, sessionInfo } from "./capture/bridge";
import { runOpenTraces, textResult, truncateText } from "./tools/opentraces";

const SearchParams = Type.Object({
  query: Type.String({ description: "Natural-language or lexical query over local OpenTraces bucket traces" }),
  limit: Type.Optional(Type.Number({ description: "Maximum candidates to return" })),
  remote_bucket: Type.Optional(Type.Boolean({ description: "Read from configured remote bucket as well as local" })),
});

const TraceParams = Type.Object({
  trace_ref: Type.String({ description: "Trace id, trace unit, map node, or ot:// reference" }),
  include_map: Type.Optional(Type.Boolean()),
});

const EmptyParams = Type.Object({});
const RepairParams = Type.Object({ repair: Type.Optional(Type.Boolean()) });
const DatasetParams = Type.Object({ action: Type.Optional(Type.String()), name: Type.Optional(Type.String()), workflow: Type.Optional(Type.String()) });
const CapsuleParams = Type.Object({ trace_ref: Type.Optional(Type.String()), action: Type.Optional(Type.String({ description: "preview or export" })) });

function registerOpenTracesTools(pi: ExtensionAPI) {
  pi.registerTool({
    name: "ot_search",
    label: "OpenTraces Search",
    description: "Search prior captured OpenTraces bucket traces without leaving Pi.",
    promptSnippet: "Search OpenTraces for prior agent work and trace evidence.",
    promptGuidelines: ["Use ot_search when the user asks about previous work, traces, captured sessions, or examples from the local OpenTraces bucket."],
    parameters: SearchParams,
    async execute(_toolCallId, params: any, signal, _onUpdate, ctx) {
      const args = ["trace", "query", "--lex", String(params.query), "--limit", String(params.limit ?? 5), "--json"];
      if (params.remote_bucket) args.splice(args.length - 1, 0, "--remote-bucket");
      const result = await runOpenTraces(pi, args, ctx, { timeout: 20_000 });
      return textResult("OpenTraces search", result);
    },
  });

  pi.registerTool({
    name: "ot_trace",
    label: "OpenTraces Trace",
    description: "Resolve one OpenTraces trace into a compact evidence card.",
    parameters: TraceParams,
    async execute(_toolCallId, params: any, _signal, _onUpdate, ctx) {
      const get = await runOpenTraces(pi, ["trace", "get", String(params.trace_ref), "--json"], ctx, { timeout: 20_000 });
      if (!params.include_map || !get.ok) return textResult("OpenTraces trace", get);
      const map = await runOpenTraces(pi, ["trace", "map", String(params.trace_ref), "--json"], ctx, { timeout: 20_000 });
      return {
        content: [{ type: "text", text: truncateText(`OpenTraces trace\n\n${get.text}\n\nTrace map\n\n${map.text}`) }],
        details: { trace: get.details, map: map.details },
        isError: !get.ok || !map.ok,
      };
    },
  });

  pi.registerTool({
    name: "ot_standup",
    label: "OpenTraces Standup",
    description: "Build a daily standup packet from recent captured traces.",
    parameters: EmptyParams,
    async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
      // v1 stays on stable JSON surfaces: a bounded recent trace query is the
      // standup input packet. Rendering/narrative workflows can sit above this.
      const result = await runOpenTraces(pi, ["trace", "query", "--lex", "recent work", "--limit", "10", "--json"], ctx, { timeout: 30_000 });
      return textResult("OpenTraces standup", result);
    },
  });

  pi.registerTool({
    name: "ot_capsule",
    label: "OpenTraces Capsule",
    description: "Preview or export an OpenTraces capsule for the current or selected trace."},{
    parameters: CapsuleParams,
    async execute(_toolCallId, params: any, _signal, _onUpdate, ctx) {
      const action = String(params.action ?? "preview");
      let args = ["bucket", "status", "--json"];
      if (params.trace_ref) {
        args = action === "export"
          ? ["capsule", "export", String(params.trace_ref), "--json"]
          : ["capsule", "preview", String(params.trace_ref), "--json"];
      }
      const result = await runOpenTraces(pi, args, ctx, { timeout: 30_000 });
      return textResult("OpenTraces capsule", result);
    },
  });

  pi.registerTool({
    name: "ot_dataset",
    label: "OpenTraces Dataset",
    description: "List, create, run, or check workflow-built OpenTraces datasets.",
    parameters: DatasetParams,
    async execute(_toolCallId, params: any, _signal, _onUpdate, ctx) {
      const action = String(params.action ?? "list");
      let args = ["dataset", "list", "--json"];
      if (action === "status" && params.name) args = ["dataset", "status", String(params.name), "--json"];
      else if (action === "run" && params.name) args = ["dataset", "run", String(params.name), "--dry-run", "--json"];
      else if (action === "new" && params.name && params.workflow) args = ["dataset", "new", String(params.name), "--workflow", String(params.workflow), "--json"];
      const result = await runOpenTraces(pi, args, ctx, { timeout: 30_000 });
      return textResult("OpenTraces dataset", result);
    },
  });

  pi.registerTool({
    name: "ot_capture_status",
    label: "OpenTraces Capture Status",
    description: "Show OpenTraces Pi package, CLI, capture, raw-body, sidecar, and bucket setup status.",
    parameters: RepairParams,
    async execute(_toolCallId, params: any, _signal, _onUpdate, ctx) {
      const result = await captureStatus(pi, ctx, Boolean(params.repair));
      return textResult("OpenTraces capture status", result);
    },
  });
}

function registerCommands(pi: ExtensionAPI) {
  pi.registerCommand("ot-search", {
    description: "Search captured OpenTraces bucket traces: /ot-search <query>",
    handler: async (args, ctx) => showCommandResult(ctx, await runOpenTraces(pi, ["trace", "query", "--lex", args || "recent work", "--json"], ctx)),
  });
  pi.registerCommand("ot-trace", {
    description: "Resolve one OpenTraces trace: /ot-trace <trace-id>",
    handler: async (args, ctx) => showCommandResult(ctx, await runOpenTraces(pi, ["trace", "get", args.trim(), "--json"], ctx)),
  });
  pi.registerCommand("ot-standup", {
    description: "Build a daily standup packet from local traces.",
    handler: async (_args, ctx) => showCommandResult(ctx, await runOpenTraces(pi, ["trace", "query", "--lex", "recent work", "--limit", "10", "--json"], ctx, { timeout: 30_000 })),
  });
  pi.registerCommand("ot-capsule", {
    description: "OpenTraces capsule helper. Pass a trace id to preview a capsule.",
    handler: async (args, ctx) => {
      const traceRef = args.trim();
      const argv = traceRef ? ["capsule", "preview", traceRef, "--json"] : ["bucket", "status", "--json"];
      return showCommandResult(ctx, await runOpenTraces(pi, argv, ctx));
    },
  });
  pi.registerCommand("ot-dataset", {
    description: "List OpenTraces datasets.",
    handler: async (_args, ctx) => showCommandResult(ctx, await runOpenTraces(pi, ["dataset", "list", "--json"], ctx)),
  });
  pi.registerCommand("ot-capture-status", {
    description: "Show OpenTraces Pi capture/setup status.",
    handler: async (_args, ctx) => showCommandResult(ctx, await captureStatus(pi, ctx, false)),
  });
  pi.registerCommand("ot-setup", {
    description: "Guided minimal/local OpenTraces setup for Pi.",
    handler: async (_args, ctx) => {
      const status = await captureStatus(pi, ctx, false);
      if (!ctx.hasUI) return showCommandResult(ctx, status);
      const details: any = status.details;
      const missingProject = Array.isArray(details.steps) && details.steps.some((s: any) => s.name === "project_capture" && s.state !== "ok");
      ctx.ui.notify("OpenTraces setup is local-first. Auth, bucket remote, and optional security tools stay terminal follow-ups.", "info");
      if (missingProject) {
        const ok = await ctx.ui.confirm("Enable OpenTraces Pi capture for this project?", "Runs `opentraces init --agent pi --start-fresh` locally. Raw provider bodies remain default-off.");
        if (ok) {
          const init = await runOpenTraces(pi, ["init", "--agent", "pi", "--start-fresh"], ctx, { timeout: 30_000, parseJson: true });
          return showCommandResult(ctx, init);
        }
      }
      return showCommandResult(ctx, status);
    },
  });
}

async function showCommandResult(ctx: ExtensionContext, result: { ok: boolean; text: string; details: Record<string, unknown> }) {
  const text = result.text || JSON.stringify(result.details, null, 2);
  if (ctx.hasUI) ctx.ui.notify(truncateText(text, 4000), result.ok ? "info" : "warning");
  else console.log(truncateText(text));
}

function registerCapture(pi: ExtensionAPI) {
  pi.on("session_start", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "session_start", data: { reason: (event as any).reason } }, 500);
    await maybeShowStartupStatus(pi, ctx);
  });
  pi.on("session_shutdown", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "session_shutdown", final: true, data: { reason: (event as any).reason } }, 1000);
  });
  pi.on("agent_start", async (_event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "agent_start", data: {} }, 150);
  });
  pi.on("agent_end", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "agent_end", data: { message_count: Array.isArray((event as any).messages) ? (event as any).messages.length : undefined } }, 1000);
  });
  pi.on("context", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "context", data: { messages: (event as any).messages } }, 150);
  });
  pi.on("before_provider_request", async (event, ctx) => {
    const payload: any = (event as any).payload ?? {};
    await sendBridgeEvent(pi, ctx, {
      event: "provider_request",
      data: {
        completeness: "full",
        system: payload.system ?? payload.systemPrompt,
        messages: payload.messages,
        tools: payload.tools,
        runtime_state: { model: (ctx as any).model ? `${(ctx as any).model.provider}/${(ctx as any).model.id}` : undefined },
      },
    }, 150);
  });
  pi.on("model_select", async (event, ctx) => {
    const model: any = (event as any).model;
    await sendBridgeEvent(pi, ctx, { event: "model_change", data: { provider: model?.provider, model: model?.id, source: (event as any).source } }, 150);
  });
  pi.on("thinking_level_select", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "thinking_change", data: { level: (event as any).level } }, 150);
  });
  pi.on("tool_call", async (event, ctx) => {
    const anyEvent: any = event as any;
    if (String(anyEvent.toolName ?? "").startsWith("ot_")) return undefined;
    await sendBridgeEvent(pi, ctx, {
      event: "tool_pre",
      tool_call_id: String(anyEvent.toolCallId ?? ""),
      data: { tool_name: anyEvent.toolName, input: compactToolInput(anyEvent.input) },
    }, 150);
    return undefined;
  });
  pi.on("tool_result", async (event, ctx) => {
    const anyEvent: any = event as any;
    if (String(anyEvent.toolName ?? "").startsWith("ot_")) return undefined;
    await sendBridgeEvent(pi, ctx, {
      event: "tool_post",
      tool_call_id: String(anyEvent.toolCallId ?? ""),
      data: {
        tool_name: anyEvent.toolName,
        input: compactToolInput(anyEvent.input),
        result: { is_error: Boolean(anyEvent.isError), details: anyEvent.details, content_count: Array.isArray(anyEvent.content) ? anyEvent.content.length : undefined },
      },
    }, 150);
    return undefined;
  });
  pi.on("session_compact", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "compaction", data: { from_extension: (event as any).fromExtension, compaction_entry: (event as any).compactionEntry } }, 150);
  });
  pi.on("session_tree", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "tree", leaf_id: (event as any).newLeafId, data: { old_leaf_id: (event as any).oldLeafId, new_leaf_id: (event as any).newLeafId, from_extension: (event as any).fromExtension } }, 150);
  });
  pi.on("user_bash", async (event, ctx) => {
    await sendBridgeEvent(pi, ctx, { event: "user_bash", data: { command: (event as any).command, cwd: (event as any).cwd, exclude_from_context: (event as any).excludeFromContext } }, 150);
    return undefined;
  });
}

export default function opentracesPiExtension(pi: ExtensionAPI) {
  registerOpenTracesTools(pi);
  registerCommands(pi);
  registerCapture(pi);
}
