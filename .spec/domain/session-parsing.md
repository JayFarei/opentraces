---
schema_version: "1.0"
title: Session Parsing
scope: src/opentraces/parsers
---

# Session Parsing

## Entities

### SessionParser (Protocol)
Structural typing contract (no inheritance required). Two methods:
- `discover_sessions(projects_path) -> Iterator[Path]`
- `parse_session(session_path, byte_offset=0) -> TraceRecord | None`

Property: `agent_name: str`

### FormatImporter (Protocol)
For importing from external formats (e.g. ADP trajectories). Two methods:
- `import_traces(input_path, max_records=0) -> list[TraceRecord]`

Properties: `format_name: str`, `file_extensions: list[str]`

### ClaudeCodeParser
The primary and currently only parser implementation (761 LOC). Reads Claude Code `session.jsonl` files from `~/.claude/projects/*/`.

## Business Rules

1. **Quality gate**: Every parsed session must pass `meets_quality_threshold()` before being returned. Criteria: min 2 steps AND min 1 tool call. Sessions below threshold return `None`.

2. **Corrupted line threshold**: If >5% of JSONL lines in a session fail to parse (`CORRUPTED_LINE_THRESHOLD = 0.05`), the entire session is rejected. Individual bad lines below the threshold are skipped with a warning.

3. **Subagent depth limit**: Recursive sub-agent loading is capped at `MAX_SUBAGENT_DEPTH = 10`. Prevents stack overflow from malformed session data.

4. **Circular reference detection**: A `visited_sessions` set tracks already-parsed session IDs. If a sub-agent file references an already-visited session, it logs a warning and returns the ID without re-parsing.

5. **Subagent file skip**: During discovery, any `.jsonl` file whose path contains `/subagents/` is skipped (they are loaded recursively by the parent parser).

6. **Step renumbering**: After all steps (including inlined subagent steps) are collected, step indices are renumbered sequentially starting from 1. A `old_to_new` map is built and used to fix `parent_step` references.

7. **Byte offset resume**: `parse_session` accepts a `byte_offset` parameter for incremental processing. After seeking, the parser discards the first partial line to avoid landing mid-UTF8.

8. **User message truncation**: `task.description` is populated from the first user message, truncated to 500 characters.

9. **Observation content cap**: Tool result content is truncated to 10,000 characters. Snippet text is truncated to 5,000 characters.

10. **Role mapping**: Raw session roles (`"assistant"`) are mapped to schema roles (`"agent"`). User messages that contain only `tool_result` blocks are skipped entirely (they are observations, not steps).

## Calculations

### Warmup detection
A step is classified as `call_type="warmup"` when ALL of:
- `token_usage.output_tokens <= 10`
- No stripped content
- No tool calls
- Role is "assistant"

### OS inference
Inferred from `cwd` path prefix:
- `/Users/` -> "darwin"
- `/home/` or `/root/` -> "linux"
- `X:\` or `X:/` -> "windows"

### VCS inference
- `git_branch` present and not "HEAD" -> `VCS(type="git", branch=branch)`
- `git_branch == "HEAD"` -> `VCS(type="git")` (detached head)
- No branch -> `VCS(type="none")`

### Metrics (parser-level)
- `cache_hit_rate = cache_read_tokens / (input_tokens + cache_read_tokens)` when denominator > 0
- `total_duration_s` from first to last timestamped step

## State Machines

No explicit state machine. The parser is a single-pass transform: raw JSONL -> TraceRecord or None.

## Edge Cases

1. **Encrypted thinking**: Thinking blocks with only a `signature` field (no `thinking` text) produce the sentinel `"[redacted: model produced reasoning but content was withheld by provider]"`. This preserves the fact that reasoning occurred.

2. **Tool call duration sources**: The parser tries three keys from `toolUseResult`: `durationMs` (int), `durationSeconds` (float * 1000), `duration` (float * 1000). Falls back gracefully.

3. **Plain text user messages**: If `content` is a string instead of a list of blocks, it is wrapped directly into a Step with role="user".

4. **queue-operation lines**: Used as a fallback source for model name, tool definitions, and system prompts. Content is a JSON string that must be double-parsed.

5. **System prompt sources**: Two sources, in priority order: (a) inline from step parsing, (b) from `system_prompt_raw` in metadata extracted from queue-operation lines. The raw value can be a list of text blocks or a plain string.

6. **Unknown block types**: If >20% of content blocks have unknown types, a warning is logged suggesting Claude Code's format may have changed.

7. **Subagent meta.json matching**: Subagent files are matched by comparing the `description` field in `meta.json` against the tool call's input `description`. Falls back to the first available subagent file.

8. **Agent role mapping for subagents**: The `subagent_type` from tool input is mapped: `"Explore"` -> `"explore"`, `"Plan"` -> `"plan"`, `"general-purpose"` -> `"general"`, otherwise lowercased.
