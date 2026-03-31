export interface Field {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export interface SchemaVersion {
  version: string;
  date: string;
  summary: string;
  highlights: string[];
  models: { id: string; title: string; desc: string; fields: Field[] }[];
}

const v010: SchemaVersion = {
  version: "0.1.0",
  date: "2026-03-27",
  summary: "Initial release. 15 models covering trajectory, attribution, outcome signals, and security.",
  highlights: [
    "TraceRecord: one JSONL line per complete agent session",
    "Step: TAO-loop oriented (one LLM API call, not conversational turns)",
    "Outcome: RL-ready signals with derived/inferred/annotated confidence",
    "Attribution: embedded Agent Trace-compatible block (experimental)",
    "Sub-agent hierarchy via parent_step, agent_role, call_type",
    "System prompt deduplication by hash",
    "SecurityMetadata: auto/review mode classification",
    "Content hashing (SHA-256) for cross-upload deduplication",
    "TokenUsage with prefix reuse and cache breakdown fields",
  ],
  models: [
    {
      id: "trace-record", title: "TraceRecord",
      desc: "Root record. One per session, one JSONL line.",
      fields: [
        { name: "schema_version", type: "string", required: true, description: "e.g. \"0.1.0\"" },
        { name: "trace_id", type: "string", required: true, description: "UUID for this trace" },
        { name: "session_id", type: "string", required: true, description: "Agent's native session ID" },
        { name: "content_hash", type: "string", required: false, description: "SHA-256 for deduplication" },
        { name: "timestamp_start", type: "string", required: false, description: "ISO 8601 start" },
        { name: "timestamp_end", type: "string", required: false, description: "ISO 8601 end" },
        { name: "task", type: "Task", required: false, description: "Task metadata" },
        { name: "agent", type: "Agent", required: true, description: "Agent identity" },
        { name: "environment", type: "Environment", required: false, description: "OS, shell, VCS, languages" },
        { name: "system_prompts", type: "dict", required: false, description: "Deduplicated prompts keyed by hash" },
        { name: "tool_definitions", type: "dict[]", required: false, description: "Available tool schemas" },
        { name: "steps", type: "Step[]", required: false, description: "TAO-loop steps" },
        { name: "outcome", type: "Outcome", required: false, description: "Session outcome" },
        { name: "dependencies", type: "string[]", required: false, description: "Project dependencies" },
        { name: "metrics", type: "Metrics", required: false, description: "Aggregated metrics" },
        { name: "security", type: "SecurityMetadata", required: false, description: "Security tier and redactions" },
        { name: "attribution", type: "Attribution", required: false, description: "Code attribution (experimental)" },
        { name: "metadata", type: "dict", required: false, description: "Extensible key-value pairs" },
      ],
    },
    {
      id: "task", title: "Task",
      desc: "Task metadata for filtering and grouping.",
      fields: [
        { name: "description", type: "string", required: false, description: "What the task is" },
        { name: "source", type: "string", required: false, description: "user_prompt, cli_arg, skill, etc." },
        { name: "repository", type: "string", required: false, description: "owner/repo format" },
        { name: "base_commit", type: "string", required: false, description: "Starting commit SHA" },
      ],
    },
    {
      id: "agent", title: "Agent",
      desc: "Agent identity.",
      fields: [
        { name: "name", type: "string", required: true, description: "claude-code, cursor, codex, etc." },
        { name: "version", type: "string", required: false, description: "Agent version" },
        { name: "model", type: "string", required: false, description: "provider/model-name" },
      ],
    },
    {
      id: "environment", title: "Environment",
      desc: "Runtime context.",
      fields: [
        { name: "os", type: "string", required: false, description: "darwin, linux, etc." },
        { name: "shell", type: "string", required: false, description: "zsh, bash, etc." },
        { name: "vcs", type: "VCS", required: false, description: "type, base_commit, branch, diff" },
        { name: "language_ecosystem", type: "string[]", required: false, description: "python, typescript, etc." },
      ],
    },
    {
      id: "step", title: "Step",
      desc: "One LLM API call in the TAO loop.",
      fields: [
        { name: "step_index", type: "int", required: true, description: "Sequential index" },
        { name: "role", type: "string", required: true, description: "system | user | agent" },
        { name: "content", type: "string", required: false, description: "Message content" },
        { name: "reasoning_content", type: "string", required: false, description: "Chain-of-thought" },
        { name: "model", type: "string", required: false, description: "Model for this step" },
        { name: "system_prompt_hash", type: "string", required: false, description: "Key into system_prompts" },
        { name: "agent_role", type: "string", required: false, description: "main, explore, plan, etc." },
        { name: "parent_step", type: "int", required: false, description: "Parent step index" },
        { name: "call_type", type: "string", required: false, description: "main | subagent | warmup" },
        { name: "subagent_trajectory_ref", type: "string", required: false, description: "Sub-agent session ID" },
        { name: "tools_available", type: "string[]", required: false, description: "Available tool names" },
        { name: "tool_calls", type: "ToolCall[]", required: false, description: "Tool invocations" },
        { name: "observations", type: "Observation[]", required: false, description: "Tool results" },
        { name: "snippets", type: "Snippet[]", required: false, description: "Extracted code blocks" },
        { name: "token_usage", type: "TokenUsage", required: false, description: "Token breakdown" },
        { name: "timestamp", type: "string", required: false, description: "ISO 8601" },
      ],
    },
    {
      id: "tool-call", title: "ToolCall",
      desc: "A tool invocation within a step.",
      fields: [
        { name: "tool_call_id", type: "string", required: true, description: "ID for linking to observations" },
        { name: "tool_name", type: "string", required: true, description: "Tool name" },
        { name: "input", type: "dict", required: false, description: "Input parameters" },
        { name: "duration_ms", type: "int", required: false, description: "Wall-clock time" },
      ],
    },
    {
      id: "observation", title: "Observation",
      desc: "Tool result linked to its ToolCall.",
      fields: [
        { name: "source_call_id", type: "string", required: true, description: "Links to ToolCall" },
        { name: "content", type: "string", required: false, description: "Full output" },
        { name: "output_summary", type: "string", required: false, description: "Lightweight preview" },
        { name: "error", type: "string", required: false, description: "Error info if failed" },
      ],
    },
    {
      id: "token-usage", title: "TokenUsage",
      desc: "Per-step token breakdown.",
      fields: [
        { name: "input_tokens", type: "int", required: false, description: "Input tokens" },
        { name: "output_tokens", type: "int", required: false, description: "Output tokens" },
        { name: "cache_read_tokens", type: "int", required: false, description: "From cache" },
        { name: "cache_write_tokens", type: "int", required: false, description: "Written to cache" },
        { name: "prefix_reuse_tokens", type: "int", required: false, description: "Via prefix caching" },
      ],
    },
    {
      id: "outcome", title: "Outcome",
      desc: "Session outcome for reward modeling.",
      fields: [
        { name: "success", type: "boolean", required: false, description: "Goal achieved" },
        { name: "signal_source", type: "string", required: false, description: "Default: \"deterministic\"" },
        { name: "signal_confidence", type: "string", required: false, description: "derived | inferred | annotated" },
        { name: "description", type: "string", required: false, description: "Outcome description" },
        { name: "patch", type: "string", required: false, description: "Unified diff" },
        { name: "committed", type: "boolean", required: false, description: "Changes committed to git" },
        { name: "commit_sha", type: "string", required: false, description: "Commit SHA" },
      ],
    },
    {
      id: "attribution", title: "Attribution",
      desc: "Code attribution (experimental).",
      fields: [
        { name: "experimental", type: "boolean", required: false, description: "Always true in v0.1.0" },
        { name: "files", type: "AttributionFile[]", required: false, description: "Per-file line ranges" },
      ],
    },
    {
      id: "metrics", title: "Metrics",
      desc: "Session-level aggregates.",
      fields: [
        { name: "total_steps", type: "int", required: false, description: "Step count" },
        { name: "total_input_tokens", type: "int", required: false, description: "Sum of input tokens" },
        { name: "total_output_tokens", type: "int", required: false, description: "Sum of output tokens" },
        { name: "total_duration_s", type: "float", required: false, description: "Wall-clock seconds" },
        { name: "cache_hit_rate", type: "float", required: false, description: "0.0 to 1.0" },
        { name: "estimated_cost_usd", type: "float", required: false, description: "Estimated cost" },
      ],
    },
    {
      id: "security", title: "SecurityMetadata",
      desc: "Security mode and redaction record.",
      fields: [
        { name: "tier", type: "int", required: false, description: "1 (auto), 2 (review), 3 (review, legacy)" },
        { name: "flags_reviewed", type: "int", required: false, description: "Flags reviewed" },
        { name: "redactions_applied", type: "int", required: false, description: "Redactions applied" },
        { name: "classifier_version", type: "string", required: false, description: "Classifier version" },
      ],
    },
  ],
};

const v011: SchemaVersion = {
  version: "0.1.1",
  date: "2026-03-29",
  summary: "Patch release. Validation fixes, field documentation improvements, HuggingFace launch.",
  highlights: [
    "Security scanning and redaction pipeline hardened",
    "Schema field documentation improvements",
    "HuggingFace Hub launch partnership",
  ],
  models: v010.models.map((m) => ({
    ...m,
    fields: m.fields.map((f) =>
      f.name === "schema_version"
        ? { ...f, description: 'e.g. "0.1.1"' }
        : f.name === "experimental" && m.id === "attribution"
          ? { ...f, description: "Always true in v0.1.x" }
          : f
    ),
  })),
};

/* All versions, newest first. Add new versions here. */
export const versions: SchemaVersion[] = [v011, v010];

export const latestVersion = versions[0].version;

export function findVersion(version: string): SchemaVersion | undefined {
  const v = version === "latest" ? latestVersion : version;
  return versions.find((s) => s.version === v);
}

export function versionSlugs(): string[] {
  return ["latest", ...versions.map((v) => v.version)];
}
