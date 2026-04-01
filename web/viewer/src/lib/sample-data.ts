/**
 * Static sample data for the viewer empty state.
 * Used when "Load Sample Data" is clicked, works without a backend.
 */

import type { SessionListItem, TraceRecord, AppContext } from "../types/trace";

// ---------------------------------------------------------------------------
// Sample sessions (what the sidebar shows)
// ---------------------------------------------------------------------------

const TASKS = [
  "Refactor auth module to use JWT tokens instead of session cookies",
  "Fix race condition in WebSocket handler causing dropped messages",
  "Add cursor-based pagination to /api/users endpoint",
  "Implement rate limiting middleware for public API",
  "Write unit tests for the payment processing service",
  "Migrate database schema from v2 to v3 with zero-downtime deploy",
  "Debug memory leak in image processing pipeline",
  "Add OpenTelemetry tracing to gRPC service calls",
];

const AGENTS = ["claude-code", "cursor", "codex-cli", "aider"];
const MODELS = [
  "anthropic/claude-sonnet-4-20250514",
  "anthropic/claude-opus-4-20250514",
  "openai/gpt-4o",
  "anthropic/claude-3-haiku",
];

function makeId(i: number): string {
  return `sample-${String(i).padStart(4, "0")}-${Math.random().toString(36).slice(2, 10)}`;
}

function pick<T>(arr: T[], i: number): T {
  return arr[i % arr.length]!;
}

export const SAMPLE_SESSIONS: SessionListItem[] = Array.from({ length: 12 }, (_, i) => ({
  trace_id: makeId(i),
  task_description: pick(TASKS, i),
  agent_name: pick(AGENTS, i),
  model: pick(MODELS, i),
  step_count: 4 + ((i * 3) % 17),
  flag_count: i % 4 === 0 ? 0 : (i % 3),
  stage: "inbox" as const,
  timestamp: `2026-03-${String(20 + (i % 8)).padStart(2, "0")}T10:00:00Z`,
}));

// ---------------------------------------------------------------------------
// Sample trace detail (what the main panel shows when a session is selected)
// ---------------------------------------------------------------------------

const TOOL_NAMES = ["Read", "Edit", "Bash", "Grep", "Glob", "Write"];

function sampleToolInput(tool: string): Record<string, unknown> {
  const inputs: Record<string, Record<string, unknown>> = {
    Read: { file_path: "/src/main.py", limit: 50 },
    Edit: { file_path: "/src/main.py", old_string: "def old_func():", new_string: "def new_func():" },
    Bash: { command: "python -m pytest tests/ -v", description: "Run tests" },
    Grep: { pattern: "def process_", path: "/src/", output_mode: "content" },
    Glob: { pattern: "**/*.py", path: "/src/" },
    Write: { file_path: "/src/new_file.py", content: "# New module\n" },
  };
  return inputs[tool as keyof typeof inputs] ?? { input: "sample" };
}

function sampleToolOutput(tool: string): string {
  const outputs: Record<string, string> = {
    Read: '     1\tdef process_request(req):\n     2\t    """Handle incoming request."""\n     3\t    validate(req)\n     4\t    return Response(status=200)\n',
    Edit: "Successfully edited /src/main.py",
    Bash: "===== 12 passed, 0 failed in 3.42s =====",
    Grep: "/src/handlers.py:15: def process_webhook(data):\n/src/utils.py:42: def process_batch(items):",
    Glob: "/src/main.py\n/src/utils.py\n/src/handlers.py\n/src/models.py",
    Write: "File written: /src/new_file.py",
  };
  return outputs[tool as keyof typeof outputs] ?? "Operation completed.";
}

function sampleContent(role: string, task: string, stepIndex: number): string {
  if (role === "user") {
    if (stepIndex === 0) return task;
    const followups = [
      "Yes, that looks correct. Please proceed.",
      "Can you also add error handling for edge cases?",
      "Good. Now run the tests to make sure nothing is broken.",
    ];
    return pick(followups, stepIndex);
  }
  if (role === "system") {
    return "You are a helpful coding assistant. Follow best practices.";
  }
  const responses = [
    "I'll start by reading the relevant files to understand the current implementation.",
    "Let me examine the code structure and identify the changes needed.",
    "I've made the changes. Let me run the tests to verify everything works correctly.",
    "The implementation looks good. Here's a summary of what I changed:\n\n1. Updated the main handler\n2. Added input validation\n3. Wrote new test cases",
    "I found a potential issue in the error handling. Let me fix that first.",
  ];
  return pick(responses, stepIndex);
}

export function buildSampleTrace(session: SessionListItem): TraceRecord {
  const numSteps = session.step_count;
  const steps = Array.from({ length: numSteps }, (_, s) => {
    let role: "user" | "agent" | "system" = s === 0 ? "user" : s % 3 === 0 ? "system" : "agent";

    const toolCalls = [];
    const observations = [];

    if (role === "agent" && s % 2 === 0) {
      const tool = pick(TOOL_NAMES, s);
      const tcId = `tc_${s}_0`;
      toolCalls.push({
        tool_call_id: tcId,
        tool_name: tool,
        input: sampleToolInput(tool),
        duration_ms: 100 + (s * 137) % 4900,
      });
      observations.push({
        source_call_id: tcId,
        content: sampleToolOutput(tool),
        output_summary: `${tool} completed successfully`,
        error: null,
      });
    }

    return {
      step_index: s,
      role,
      content: sampleContent(role, session.task_description, s),
      reasoning_content: role === "agent" && s % 3 === 1
        ? "Let me think about how to approach this. I need to first understand the current structure, then identify the specific changes needed."
        : null,
      model: role === "agent" ? session.model : null,
      system_prompt_hash: s === 0 ? "abc123" : null,
      agent_role: "main",
      parent_step: null,
      call_type: "main" as const,
      subagent_trajectory_ref: null,
      tools_available: role === "agent" ? TOOL_NAMES : [],
      tool_calls: toolCalls,
      observations,
      snippets: role === "agent" && s % 4 === 0 ? [{
        file_path: `src/module_${s}.py`,
        start_line: 1,
        end_line: 10,
        language: "python",
        text: 'def example():\n    """Sample function."""\n    return True\n',
        source_step: s,
      }] : [],
      token_usage: {
        input_tokens: 500 + (s * 317) % 4500,
        output_tokens: 100 + (s * 211) % 1900,
        cache_read_tokens: (s * 173) % 3000,
        cache_write_tokens: (s * 97) % 1000,
        prefix_reuse_tokens: 0,
      },
      timestamp: `2026-03-27T${String(10 + Math.floor(s / 4)).padStart(2, "0")}:${String((s * 7) % 60).padStart(2, "0")}:00Z`,
    };
  });

  const totalInput = steps.reduce((sum, s) => sum + s.token_usage.input_tokens, 0);
  const totalOutput = steps.reduce((sum, s) => sum + s.token_usage.output_tokens, 0);

  return {
    schema_version: "0.2.0",
    trace_id: session.trace_id,
    session_id: `session-${session.trace_id.slice(0, 8)}`,
    content_hash: null,
    execution_context: null,
    timestamp_start: session.timestamp,
    timestamp_end: `2026-03-27T10:${String(5 + numSteps).padStart(2, "0")}:00Z`,
    task: {
      description: session.task_description,
      source: "user_prompt",
      repository: `org/project-${session.trace_id.slice(-1)}`,
      base_commit: null,
    },
    agent: {
      name: session.agent_name,
      version: "1.0.0",
      model: session.model,
    },
    environment: {
      os: "darwin",
      shell: "zsh",
      vcs: { type: "git", base_commit: "abc123", branch: "main", diff: null },
      language_ecosystem: ["python", "typescript"],
    },
    system_prompts: { abc123: "You are a helpful coding assistant." },
    tool_definitions: [],
    steps,
    outcome: {
      success: true,
      signal_source: "deterministic",
      signal_confidence: "derived",
      description: "Task completed",
      patch: null,
      committed: false,
      commit_sha: null,
      terminal_state: null,
      reward: null,
      reward_source: null,
    },
    dependencies: [],
    metrics: {
      total_steps: numSteps,
      total_input_tokens: totalInput,
      total_output_tokens: totalOutput,
      total_duration_s: 30 + numSteps * 15,
      cache_hit_rate: 0.45,
      estimated_cost_usd: Math.round((totalInput * 0.000003 + totalOutput * 0.000015) * 10000) / 10000,
    },
    security: {
      scanned: false,
      flags_reviewed: 0,
      redactions_applied: 0,
      classifier_version: "0.1.0",
    },
    attribution: null,
    metadata: {},
  };
}

export const SAMPLE_CONTEXT: AppContext = {
  project_name: "sample-project",
  remote: null,
  review_policy: "review",
  push_policy: "manual",
  agents: ["claude-code"],
  authenticated: false,
  username: null,
};
