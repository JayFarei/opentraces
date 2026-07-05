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
  summary: "Commit-anchored evidence tiers (GitLink), lifecycle, richer Attribution, and generation-indexed supersedes. New blame, graph, backfill, pull, and export --format agent-trace CLI surfaces, plus a flat git-style command restructure.",
  highlights: [
    "GitLink: evidence-graded link from trace to commit (tool_emitted | tool_emitted_with_divergence | overlapping | orphan)",
    "TraceRecord.lifecycle: provisional (pre-correlation) | final (revision-anchored)",
    "TraceRecord.generation_index: monotonic per-session replacement counter for pull + supersedes resolution",
    "Metrics.total_cache_read_tokens + total_cache_creation_tokens: session-level prompt-cache aggregates",
    "Attribution.revision pins a block to a commit; unaccounted_files surfaces Bash-applied edits",
    "AttributionRange.original captures pre-divergence state when a formatter rewrote agent output",
    "AttributionRange.change_type: addition | modification | deletion; per-range contributor override",
    "AttributionConversation.ids (provider-native msg ids) and .related (plan / issue / pr links)",
    "Task.repository_url: canonical remote URL alongside owner/repo",
    "AttributionRange.content_hash format now murmur3:<32-hex> (replaces md5-truncated-8) for cross-tool line-range matching; top-level TraceRecord.content_hash remains SHA-256 for dedup",
    "Post-commit hook correlates trace to revision; PostToolUse hook captures edits as they happen",
    "Historical 0.3 command names: blame/graph/backfill/setup git/list --by-commit; current CLI groups these under trail/dataset surfaces",
    "Historical importer/export verbs; current import/export behavior is workflow or schema-package driven",
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
          { name: "generation_index", type: "int", required: false, description: "Monotonic per-session_id generation counter. Consumers resolving 'latest' should group by session_id and take max(generation_index)." },
        ],
      };
    }
    if (m.id === "metrics") {
      return {
        ...m,
        fields: [
          ...m.fields,
          { name: "total_cache_read_tokens", type: "int", required: false, description: "Session-level prompt-cache read aggregate." },
          { name: "total_cache_creation_tokens", type: "int", required: false, description: "Session-level prompt-cache write aggregate." },
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

const v040: SchemaVersion = {
  version: "0.4.0",
  date: "2026-04-29",
  summary: "Local executable datasets, dataset workflows, dataset remotes, and the trace index / Trace Map / Candidate Packet contract. Strictly additive on top of 0.3.0; TraceRecord is unchanged.",
  highlights: [
    "DatasetManifest, DatasetSchemaRef, WorkflowRef, ExecutorConfig: local HF-shaped dataset contract driving `opentraces dataset new/run/review/publish`",
    "DatasetRemote, DatasetPublicationState, DatasetPublicationPolicy: per-dataset HF remote bindings, review/security policy, and publication status tracking",
    "DatasetCandidateQuery, DatasetSchedule, DatasetDiscoverability: remembered candidate query, scheduling cadence, and HF discoverability metadata",
    "DatasetRunRecord, DatasetRowIndexEntry: append-only run history and row provenance back to source traces",
    "TraceUnit, TraceFacet, TraceSignal: addressable bounded search documents behind `opentraces trace query` and `opentraces trace index`",
    "TraceMap, TraceMapNode, TraceMapEdge: workflow-neutral evidence graph behind `opentraces trace map`",
    "CandidatePacket: bounded search-result envelope returned by `opentraces trace query`",
    "All dataset models declare extra=\"forbid\" so unknown manifest keys fail validation",
  ],
  models: v030.models.map((m) => {
    if (m.id === "trace-record") {
      return {
        ...m,
        fields: m.fields.map((f) =>
          f.name === "schema_version" ? { ...f, description: 'e.g. "0.4.0"' } : f,
        ),
      };
    }
    return m;
  }),
};

const v050: SchemaVersion = {
  version: "0.5.0",
  date: "2026-05-18",
  summary: "Context Tree cross-reference fields. Adds Step.context_node_id and TraceRecord.context_tree_summary so consumers can join a trace step to what the model saw.",
  highlights: [
    "Step.context_node_id points at the ContextNode for that step when captured",
    "TraceRecord.context_tree_summary rolls up node/layer counts and capture limitations",
    "Context Tree data remains a substrate companion, not embedded into every JSONL row",
  ],
  models: v040.models.map((m) => {
    if (m.id === "trace-record") {
      return {
        ...m,
        fields: [
          ...m.fields.map((f) =>
            f.name === "schema_version" ? { ...f, description: 'e.g. "0.5.0"' } : f,
          ),
          { name: "context_tree_summary", type: "dict", required: false, description: "Summary of Context Tree capture: node_count, layer_count, active_path_leaf_id, capture_limitations." },
        ],
      };
    }
    if (m.id === "step") {
      return {
        ...m,
        fields: [
          ...m.fields,
          { name: "context_node_id", type: "string | null", required: false, description: "Context Tree node id for the model view at this step." },
        ],
      };
    }
    return m;
  }),
};

const v060: SchemaVersion = {
  version: "0.6.0",
  date: "2026-05-21",
  summary: "Trace patch spine. Adds TraceRecord.patches[] as the authoritative dev-time output set and removes the legacy Outcome.patch field.",
  highlights: [
    "TraceRecord.patches[] is the authoritative output spine: one Patch per tool-produced change/hunk",
    "Patch.anchor links a trace patch to Git evidence and survival tracking",
    "Outcome.patch removed; full diff/history lives in the bucket Trail companion",
    "Outcome.committed, Outcome.commit_sha, and TraceRecord.git_links remain compatibility projections derived from patch anchors",
  ],
  models: v050.models.map((m) => {
    if (m.id === "trace-record") {
      return {
        ...m,
        fields: [
          ...m.fields.map((f) =>
            f.name === "schema_version" ? { ...f, description: 'e.g. "0.6.0"' } : f,
          ),
          { name: "patches", type: "Patch[]", required: false, description: "Authoritative dev-time output set. One Patch per tool-produced change/hunk." },
        ],
      };
    }
    if (m.id === "outcome") {
      return {
        ...m,
        fields: m.fields.filter((f) => f.name !== "patch"),
      };
    }
    if (m.id === "security") {
      return {
        ...m,
        desc: "Security scan summary. Detailed tool output lives under metadata.security.",
        fields: [
          { name: "scanned", type: "boolean", required: false, description: "Whether security processing was applied to this record." },
          { name: "flags_reviewed", type: "int", required: false, description: "Number of security flags reviewed." },
          { name: "redactions_applied", type: "int", required: false, description: "Number of redactions applied." },
          { name: "classifier_version", type: "string | null", required: false, description: "Classifier tool version when classifier ran." },
        ],
      };
    }
    return m;
  }).concat([
    {
      id: "git-anchor", title: "GitAnchor",
      desc: "Typed link from a Patch to its appearance in Git.",
      fields: [
        { name: "last_searched_at", type: "string", required: true, description: "ISO8601 timestamp set after the first maturation search." },
        { name: "found", type: "boolean", required: true, description: "Whether a matching commit was found." },
        { name: "commit_sha", type: "string | null", required: false, description: "Matched commit SHA when found." },
        { name: "path", type: "string | null", required: false, description: "Path in the commit; may differ after rename." },
        { name: "blob_sha", type: "string | null", required: false, description: "Matched Git blob SHA." },
        { name: "git_patch_id", type: "string | null", required: false, description: "Git patch-id, stable across rebase." },
        { name: "evidence_tier", type: "string | null", required: false, description: "Evidence match label such as exact_range_hash, patch_id, formatter_divergent, overlapping_hunk, or orphan." },
        { name: "evidence_firmness", type: "string | null", required: false, description: "Firmness label such as firm_observed, provisional, human_asserted, or unknown." },
      ],
    },
    {
      id: "patch", title: "Patch",
      desc: "A trace-produced change. Full patch history resolves through the bucket Trail companion.",
      fields: [
        { name: "patch_id", type: "string", required: true, description: "Content-addressed trace patch id." },
        { name: "file_path", type: "string", required: true, description: "Path at creation time." },
        { name: "step_index", type: "int | null", required: false, description: "Producing step index." },
        { name: "tool_call_id", type: "string | null", required: false, description: "Producing tool call id." },
        { name: "capture_method", type: "string[]", required: false, description: "Capture methods such as hook_pretooluse, hook_posttooluse, watcher_backstop." },
        { name: "snapshot_before_id", type: "string | null", required: false, description: "Before snapshot id." },
        { name: "snapshot_after_id", type: "string | null", required: false, description: "After snapshot id." },
        { name: "anchor", type: "GitAnchor | null", required: false, description: "Git match when the patch matures into a commit." },
        { name: "superseded_by", type: "string[]", required: false, description: "Commit supersede chain after amend/rebase/squash." },
        { name: "limitations", type: "string[]", required: false, description: "Capture quality flags." },
      ],
    },
  ]),
};

const v070: SchemaVersion = {
  version: "0.7.0",
  date: "2026-06-08",
  summary: "Dataset security policy contract. A workflow declares the security posture of the rows it projects, and each dataset stores its resolved policy in the manifest. Additive only; the TraceRecord wire shape is unchanged.",
  highlights: [
    "WorkflowSecurityContract: a workflow's declared security posture (required_tools, optional_tools, default_enabled_tools, disallowed_tools, allow_disable_required)",
    "DatasetSecurityPolicy: the resolved per-dataset policy stored on the manifest, seeded from a workflow contract and pinned to the source workflow digest",
    "DatasetSecurityOverride: an explicit, recorded unsafe opt-out of a required security tool",
    "DatasetManifest.security: additive optional field, defaults to an empty policy so existing manifests load unchanged",
    "SecurityToolName Literal + SECURITY_TOOL_ORDER: canonical security tool vocabulary kept in sync with the runtime tool registry",
    "CLI: `opentraces dataset security <name>` inspects/edits a dataset's policy; `--unsafe-override` records disabling a required tool",
    "TraceRecord wire shape unchanged; migrate_record is a transparent no-op",
  ],
  models: v060.models.map((m) => {
    if (m.id === "trace-record") {
      return {
        ...m,
        fields: m.fields.map((f) =>
          f.name === "schema_version" ? { ...f, description: 'e.g. "0.7.0"' } : f,
        ),
      };
    }
    return m;
  }),
};

const v080: SchemaVersion = {
  version: "0.8.0",
  date: "2026-07-03",
  summary: "Seal family (ADR-0008), issue #200/#155 Part A. Adds a structured home for exact dependency pins and runtime interpreter identity on Environment. Purely additive; this is a home for future resolver output, not a resolver, and does not raise env_tier.",
  highlights: [
    "PinRecord: a single resolved dependency pin (name required; version, hash, marker, source all optional)",
    "Interpreter: runtime interpreter identity (name, version), both optional",
    "Environment.resolved_dependencies: list[PinRecord] | None, filled by the future #202 resolver",
    "Environment.interpreter: Interpreter | None, the interpreter the session ran on",
    "Environment.arch, Environment.platform, Environment.abi_tag: CPU/platform/ABI identity for the future L3 wheel-platform boundary",
    "Honesty boundary: all five fields are absent by default and their presence never lifts env_tier or verdict_trust; every pre-0.8.0 record validates unchanged and migrate_record is a no-op",
  ],
  models: v070.models.map((m) => {
    if (m.id === "environment") {
      return {
        ...m,
        fields: [
          ...m.fields,
          { name: "resolved_dependencies", type: "PinRecord[] | null", required: false, description: "Exact resolved dependency pins for the trace's closure. None until a resolver fills them; presence does not raise env_tier." },
          { name: "interpreter", type: "Interpreter | null", required: false, description: "Runtime interpreter identity (name/version)." },
          { name: "arch", type: "string | null", required: false, description: "CPU architecture, e.g. arm64, x86_64" },
          { name: "platform", type: "string | null", required: false, description: "Platform tag, e.g. macosx_14_0_arm64, linux" },
          { name: "abi_tag", type: "string | null", required: false, description: "Python ABI tag for the L3 wheel-platform boundary, e.g. cp311" },
        ],
      };
    }
    return m;
  }).concat([
    {
      id: "pin-record", title: "PinRecord",
      desc: "A single resolved dependency pin (name==version, optionally hashed). A structured home for a future resolver's output, not a resolver itself.",
      fields: [
        { name: "name", type: "string", required: true, description: "Dependency name" },
        { name: "version", type: "string | null", required: false, description: "Exact resolved version, e.g. 2.31.0" },
        { name: "hash", type: "string | null", required: false, description: "Artifact hash (e.g. sha256:...) for the future L3 wheel path" },
        { name: "marker", type: "string | null", required: false, description: "PEP 508 environment marker, e.g. python_version >= '3.8'" },
        { name: "source", type: "string | null", required: false, description: "Resolver/index the pin came from (routed through the redaction floor)" },
      ],
    },
    {
      id: "interpreter", title: "Interpreter",
      desc: "Runtime interpreter identity (name + version, e.g. cpython 3.11.6).",
      fields: [
        { name: "name", type: "string | null", required: false, description: "Interpreter name, e.g. cpython, pypy" },
        { name: "version", type: "string | null", required: false, description: "Interpreter version, e.g. 3.11.6" },
      ],
    },
  ]),
};

const v090: SchemaVersion = {
  version: "0.9.0",
  date: "2026-07-04",
  summary: "Dataset facet scoping (issue #212, external-review fix for PR #218). Adds an optional metadata scope refinement to a dataset's persisted candidate query and its resolved match set to each run record. Purely additive; the TraceRecord wire shape is untouched entirely, this bump only touches the local dataset-lifecycle models.",
  highlights: [
    "DatasetCandidateQuery.facets: dict[str, str], an optional persisted name=value metadata scope refinement (model / agent.name / agent.version) narrowing a dataset's candidate query at `dataset new` / schedule time, composing with the existing scope/args. Default empty (no narrowing).",
    "DatasetRunRecord.facet_resolution: dict[str, Any] | None, present only when a run's effective facet scope was non-empty: {\"facets\": {...}, \"matched_count\": int, \"matched\": [...]}. None (absent) on every unfaceted run.",
    "Existing pre-0.9.0 dataset manifests and run records validate unchanged; no migration is needed",
  ],
  models: v080.models.map((m) => {
    if (m.id === "trace-record") {
      return {
        ...m,
        fields: m.fields.map((f) =>
          f.name === "schema_version" ? { ...f, description: 'e.g. "0.9.0"' } : f,
        ),
      };
    }
    return m;
  }),
};

/* All versions, newest first. Add new versions here. */
export const versions: SchemaVersion[] = [v090, v080, v070, v060, v050, v040, v030, v020, v011, v010];

export const latestVersion = versions[0].version;

export function findVersion(version: string): SchemaVersion | undefined {
  const v = version === "latest" ? latestVersion : version;
  return versions.find((s) => s.version === v);
}

export function versionSlugs(): string[] {
  return ["latest", ...versions.map((v) => v.version)];
}
