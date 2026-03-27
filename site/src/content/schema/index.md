# Schema Overview

open traces uses a training-first JSONL schema where each line represents one complete agent session. The schema is informed by ATIF v1.6, ADP, and field patterns from existing HF datasets (nlile, Nebius, CoderForge).

## Design Principles

1. **Training / SFT** - Clean message sequences with role labels, tool-use structured as tool_call/tool_result pairs, and outcome signals.
2. **RL / RLHF** - Trajectory-level reward signals, step-level annotations, decision point identification.
3. **Telemetry / Analytics** - Token counts, latency, model identifiers, cache hit rates, cost estimates.
4. **Cross-agent compatibility** - Represents traces from Claude Code, Cursor, Cline, Codex CLI, OpenCode, and future agents without agent-specific fields.

## Top-Level Structure

```json
{
  "schema_version": "0.1.0",
  "trace_id": "uuid",
  "session_id": "uuid",
  "content_hash": "sha256-hex",
  "timestamp_start": "ISO8601",
  "timestamp_end": "ISO8601",
  "task": { ... },
  "agent": { ... },
  "environment": { ... },
  "system_prompts": { ... },
  "tool_definitions": [ ... ],
  "steps": [ ... ],
  "outcome": { ... },
  "dependencies": [ ... ],
  "metrics": { ... },
  "security": { ... },
  "attribution": { ... },
  "metadata": { }
}
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `steps` not `turns` | Each step is an LLM API call, not a conversational turn. Aligns with ATIF's TAO loop. |
| `role: "agent"` not `"assistant"` | Follows ATIF/community convention (`system \| user \| agent`). |
| Tool calls and observations separated | Preserves call/result separation that training pipelines depend on. |
| System prompt dedup | `system_prompt_hash` + lookup table. A 20K-token prompt repeated 92 times would be wasteful. |
| Per-step `parent_step` | Replaces session-level `sub_agents` array. Precise parent-child tree reconstruction. |
| `agent_role` per step | Labels like `main`, `explore`, `plan` for filtering by phase. |
| `content_hash` | SHA-256 for dedup at upload time (nlile pattern). |
| `reasoning_content` | Explicit chain-of-thought. Improved SWE-Bench by ~3 pts (Cognition data). |
| `outcome.committed` | Boolean: did the session's changes get committed? Cheap, deterministic quality signal. |
| `attribution` | Embedded Agent Trace block. Bridges trajectory (process) with code attribution (output). |
