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
        { name: "tier", type: "int", required: false, description: "1 (auto), 2 (review)" },
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

const v020: SchemaVersion = {
  version: "0.2.0",
  date: "2026-03-31",
  summary: "Runtime agent support. Adds execution_context discriminator and runtime outcome signals for action-trajectory agents.",
  highlights: [
    "execution_context: devtime vs runtime session discriminator",
    "Outcome.terminal_state: goal_reached / interrupted / error / abandoned",
    "Outcome.reward: numeric reward signal from RL environments",
    "Outcome.reward_source: identifies the reward provider",
    "Quality engine: 5-persona rubrics with fidelity-aware scoring",
    "Hermes parser: import community traces from HuggingFace Hub",
  ],
  models: v011.models.map((m) => ({
    ...m,
    fields: m.fields.map((f) => {
      if (f.name === "schema_version") return { ...f, description: 'e.g. "0.2.0"' };
      return f;
    }).concat(
      m.id === "trace-record"
        ? [{ name: "execution_context", type: "string | null", required: false, description: '"devtime" (code-editing agent) or "runtime" (action-trajectory / RL agent). Null for pre-0.2 traces.' }]
        : m.id === "outcome"
          ? [
              { name: "terminal_state", type: "string | null", required: false, description: '"goal_reached", "interrupted", "error", or "abandoned". Meaningful for runtime agents.' },
              { name: "reward", type: "float | null", required: false, description: "Numeric reward signal from an RL environment or evaluator." },
              { name: "reward_source", type: "string | null", required: false, description: 'Canonical values: "rl_environment", "judge", "human_annotation", "orchestrator".' },
            ]
          : []
    ),
  })),
};

const v030: SchemaVersion = {
  version: "0.3.0",
  date: "2026-04-16",
  summary: "Commit-anchored evidence tiers (GitLink), lifecycle, and richer Attribution. New notes, blame, setup git, and export --format agent-trace CLI surfaces, plus a flat git-style command restructure.",
  highlights: [
    "GitLink: evidence-graded link from trace to commit (tool_emitted | tool_emitted_with_divergence | overlapping | orphan)",
    "TraceRecord.lifecycle: provisional (pre-correlation) | final (revision-anchored)",
    "Attribution.revision pins a block to a commit; unaccounted_files surfaces Bash-applied edits",
    "AttributionRange.original captures pre-divergence state when a formatter rewrote agent output",
    "AttributionRange.change_type: addition | modification | deletion; per-range contributor override",
    "AttributionConversation.ids (provider-native msg ids) and .related (plan / issue / pr links)",
    "Task.repository_url: canonical remote URL alongside owner/repo",
    "AttributionRange.content_hash format now murmur3:<32-hex> (replaces md5-truncated-8) for cross-tool line-range matching; top-level TraceRecord.content_hash remains SHA-256 for dedup",
    "Post-commit hook correlates trace to revision; PostToolUse hook captures diff as it happens",
    "opentraces notes <ref>, opentraces blame <file>:<line>, opentraces setup git, list --by-commit",
    "opentraces export --format agent-trace; show --markdown (prompt-injection-safe)",
  ],
  models: v020.models.map((m) => {
    if (m.id === "trace-record") {
      return {
        ...m,
        fields: [
          ...m.fields.map((f) =>
            f.name === "schema_version"
              ? { ...f, description: 'e.g. "0.3.0"' }
              : f.name === "content_hash"
                ? { ...f, description: "SHA-256 hex of the serialized record, used for cross-contributor dedup at upload time. Unchanged by 0.3.0." }
                : f
          ),
          { name: "lifecycle", type: "string", required: false, description: '"provisional" (pre-commit-correlation) or "final" (revision-anchored). Default provisional.' },
          { name: "git_links", type: "GitLink[]", required: false, description: "Evidence-graded links to commits/revisions this trace contributed to." },
        ],
      };
    }
    if (m.id === "task") {
      return {
        ...m,
        fields: [
          ...m.fields,
          { name: "repository_url", type: "string", required: false, description: "Canonical remote URL, e.g. https://github.com/org/repo" },
        ],
      };
    }
    if (m.id === "attribution") {
      return {
        ...m,
        fields: [
          ...m.fields,
          { name: "revision", type: "dict", required: false, description: "Pins this block to a revision. Keys: vcs_type ('git'|'jj'), revision." },
          { name: "unaccounted_files", type: "string[]", required: false, description: "Files changed at commit time with no tracked Edit/Write source (e.g. Bash sed edits). Low confidence." },
        ],
      };
    }
    return m;
  }).concat([
    {
      id: "git-link", title: "GitLink",
      desc: "Evidence-graded link between a trace and a commit/revision. A trace can link to many commits (rebase, squash, long session); a commit can link to many traces (cherry-pick, composition).",
      fields: [
        { name: "vcs_type",         type: "string",  required: true,  description: '"git" or "jj".' },
        { name: "revision",         type: "string",  required: true,  description: "Commit SHA or jj change id." },
        { name: "repo_url",         type: "string",  required: false, description: "Canonical remote URL." },
        { name: "branch",           type: "string",  required: false, description: "Branch at correlation time." },
        { name: "tier",             type: "string",  required: true,  description: '"tool_emitted" (Edit hashes match committed hunks), "tool_emitted_with_divergence" (file overlap but bytes diverge), "overlapping" (file-set overlap, no hash match), or "orphan".' },
        { name: "commit_reachable", type: "boolean", required: false, description: "Computed lazily on read; false if commit was force-pushed away." },
        { name: "content_alive",    type: "boolean", required: false, description: "Computed lazily on read; false if agent's hashes no longer appear at HEAD." },
      ],
    },
    {
      id: "attribution-range", title: "AttributionRange",
      desc: "A range of lines attributed to an agent conversation.",
      fields: [
        { name: "start_line",    type: "int",    required: true,  description: "First attributed line (1-indexed)." },
        { name: "end_line",      type: "int",    required: true,  description: "Last attributed line (inclusive)." },
        { name: "content_hash",  type: "string", required: false, description: "murmur3:<32-hex> for cross-refactor tracking." },
        { name: "confidence",    type: "string", required: false, description: "high | medium | low." },
        { name: "change_type",   type: "string", required: false, description: '"addition", "modification", or "deletion". Default "addition".' },
        { name: "original",      type: "dict",   required: false, description: "Pre-divergence state when a formatter/human rewrote agent output. Keys: start_line, end_line, content_hash." },
        { name: "contributor",   type: "dict",   required: false, description: "Per-range contributor override (used when the enclosing conversation is 'mixed')." },
      ],
    },
    {
      id: "attribution-conversation", title: "AttributionConversation",
      desc: "Links attributed code ranges to the conversation that produced them.",
      fields: [
        { name: "contributor", type: "dict",                required: false, description: "e.g. {type: 'ai', model_id: 'anthropic/claude-sonnet-4'}" },
        { name: "url",         type: "string",              required: false, description: "opentraces://trace_id/step_N" },
        { name: "ids",         type: "dict",                required: false, description: "Provider-native conversation ids. e.g. {anthropic: 'msg_01xyz', openai: ['resp_1', 'resp_2']}" },
        { name: "related",     type: "dict[]",              required: false, description: "Links to broader resources. Each entry: {type, url}. e.g. {type: 'plan', url: 'opentraces://t/plan_3'}" },
        { name: "ranges",      type: "AttributionRange[]",  required: false, description: "Attributed line ranges." },
      ],
    },
  ]),
};

/* All versions, newest first. Add new versions here. */
export const versions: SchemaVersion[] = [v030, v020, v011, v010];

export const latestVersion = versions[0].version;

export function findVersion(version: string): SchemaVersion | undefined {
  const v = version === "latest" ? latestVersion : version;
  return versions.find((s) => s.version === v);
}

export function versionSlugs(): string[] {
  return ["latest", ...versions.map((v) => v.version)];
}
