---
schema_version: "1.0"
title: Trace Schema
scope: packages/opentraces-schema/src/opentraces_schema
---

# Trace Schema

## Entities

### TraceRecord (top-level)
The root entity. Each line in the output JSONL file is one `TraceRecord`. It bridges trajectory data (ATIF/ADP) with code attribution (Agent Trace spec), creating a combined record of process and output.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| schema_version | str | `SCHEMA_VERSION` | From `version.py`, tied to release |
| trace_id | str | (required) | Random UUID assigned at parse time |
| session_id | str | (required) | Source session file stem |
| content_hash | str or None | None | SHA-256 of record content, excludes `content_hash` and `trace_id` |
| timestamp_start | str or None | None | ISO 8601 |
| timestamp_end | str or None | None | ISO 8601 |
| task | Task | Task() | Structured task metadata |
| agent | Agent | (required) | Agent identity |
| environment | Environment | Environment() | Runtime context |
| system_prompts | dict[str, str] | {} | Deduplicated by SHA-256 hash (key = hash prefix) |
| tool_definitions | list[dict] | [] | Tool schemas from session |
| steps | list[Step] | [] | TAO loop steps |
| outcome | Outcome | Outcome() | Session outcome signals |
| dependencies | list[str] | [] | Package names only (no versions) |
| metrics | Metrics | Metrics() | Aggregated token/cost/duration |
| security | SecurityMetadata | SecurityMetadata() | Tier, flags, redactions |
| attribution | Attribution or None | None | Code attribution block (experimental) |
| metadata | dict | {} | Catch-all for project name, etc. |

### Step
Represents one LLM API call (request + response) in the TAO (Thought-Action-Observation) loop. Not a conversational turn.

| Field | Type | Notes |
|-------|------|-------|
| step_index | int | Renumbered sequentially after subagent inlining |
| role | Literal["system", "user", "agent"] | Not "human"/"assistant" |
| content | str or None | Text content |
| reasoning_content | str or None | Chain-of-thought / extended thinking |
| model | str or None | Per-step model ID |
| system_prompt_hash | str or None | Key into top-level `system_prompts` map |
| agent_role | str or None | "main", "explore", "plan", etc. |
| parent_step | int or None | Step index of parent (for sub-agent hierarchy) |
| call_type | Literal["main", "subagent", "warmup"] or None | Discriminator for step origin |
| subagent_trajectory_ref | str or None | Session ID of sub-agent trajectory |
| tools_available | list[str] | Tool names present in this step |
| tool_calls | list[ToolCall] | Tool invocations |
| observations | list[Observation] | Tool results |
| snippets | list[Snippet] | Extracted code blocks |
| token_usage | TokenUsage | Per-step token breakdown |
| timestamp | str or None | ISO 8601 |

### Outcome
Session outcome signals for RL/reward modeling.

| Field | Type | Notes |
|-------|------|-------|
| success | bool or None | None = unknown |
| signal_source | str | "deterministic" default |
| signal_confidence | Literal["derived", "inferred", "annotated"] | How trustworthy the signal is |
| committed | bool | Whether session produced a git commit |
| commit_sha | str or None | Git SHA |
| patch | str or None | Unified diff |

### SecurityMetadata

| Field | Type | Notes |
|-------|------|-------|
| tier | Literal[1, 2, 3] | Default 3 (strictest) |
| flags_reviewed | int | Count of security flags reviewed |
| redactions_applied | int | Count of redactions applied |

### Attribution (experimental)
Bridges trajectory (process) and attribution (output). Records which files and line ranges were produced by the agent session.

### ToolCall / Observation
Linked by `tool_call_id` <-> `source_call_id`. Observations without matching tool calls get `error="no_result"`.

### TokenUsage
Five-field breakdown: input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, prefix_reuse_tokens.

## Business Rules

1. **Content hash determinism**: `compute_content_hash()` excludes `content_hash` and `trace_id` from the hash input so re-parsing identical content produces the same hash regardless of the random UUID assigned.
2. **JSONL serialization**: `to_jsonl_line()` computes and sets `content_hash` before serializing, guaranteeing every emitted line has its hash.
3. **Schema version pinning**: `schema_version` is imported from `version.py` and baked into every record and every Attribution block.
4. **Role vocabulary**: Steps use `"user"` and `"agent"`, not `"human"` and `"assistant"`. The conformance persona enforces this.
5. **Cache hit rate constraint**: `Metrics.cache_hit_rate` has Pydantic field validators `ge=0.0, le=1.0`.

## Calculations

- **Content hash**: `SHA-256(json.dumps(model_dump(exclude={content_hash, trace_id}), sort_keys=True, default=str))`
- **Attribution content hash**: `MD5(text)[:8]` (murmur3 stand-in)

## State Machines

None at the schema level. The schema is a pure data model. State machines exist in `state.py` (see upload-distribution domain).

## Edge Cases

1. **Encrypted thinking blocks**: `reasoning_content` may contain `"[redacted: model produced reasoning but content was withheld by provider]"` when the provider returns a signature-only thinking block. Consumers must handle this sentinel.
2. **VCS type="none"**: When the project is not a git repo, `VCS` has `type="none"` and all other fields are None.
3. **Observation error="no_result"**: Created when a tool_call has no matching tool_result in the session (dangling call). Preserves the call's existence without fake data.
