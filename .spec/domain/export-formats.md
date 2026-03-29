---
schema_version: "1.0"
title: Export Formats
scope: src/opentraces/exporters
---

# Export Formats

## Entities

### FormatExporter (Protocol)
Structural typing contract for trace format exporters. Properties: `format_name`, `file_extension`, `description`. Methods:
- `export_traces(records) -> Iterator[str]`: Yields one string per output unit. Skips records that fail conversion.
- `field_coverage() -> dict[str, FieldStatus]`: Reports which TraceRecord fields are preserved.

### FieldStatus
Literal type with three values:
- `"full"` - All sub-fields preserved
- `"partial"` - Some sub-fields preserved, some dropped
- `"dropped"` - Field not included in export

### ATIFExporter
Currently the only exporter implementation. Converts TraceRecords to ATIF v1.6 JSONL.

## Business Rules

### Lossy Projection Principle
All exports are explicitly lossy. TraceRecord is the superset format, and each exporter projects it into a downstream schema that captures a subset. The `field_coverage()` method enables `opentraces export --format X --dry-run` to show what will be kept vs dropped before committing.

### ATIF v1.6 Export

**Preserved fields** (full):
- steps, tool_calls, observations, reasoning_content, agent, session_id, tool_definitions, timestamps

**Preserved fields** (partial):
- token_usage: Drops `prefix_reuse_tokens` and `cache_write_tokens`. Maps to ATIF names: `input_tokens` -> `prompt_tokens`, `output_tokens` -> `completion_tokens`, `cache_read_tokens` -> `cached_tokens`.

**Dropped fields**:
- attribution, security, environment, outcome, dependencies, system_prompts, metrics_aggregate, content_hash, snippets, hierarchy

### ATIF Field Mapping

| opentraces Field | ATIF Field |
|-----------------|------------|
| schema_version | "ATIF-v1.6" (hardcoded) |
| session_id | session_id |
| agent.name | agent.name |
| agent.version | agent.version |
| agent.model | agent.model_name |
| tool_definitions | agent.tool_definitions |
| step.step_index | step_id (renumbered from 1) |
| step.role | source |
| step.content | message |
| step.reasoning_content | reasoning_content |
| step.model | model_name |
| step.timestamp | timestamp |
| step.tool_calls[].tool_call_id | tool_calls[].tool_call_id |
| step.tool_calls[].tool_name | tool_calls[].function_name |
| step.tool_calls[].input | tool_calls[].arguments |
| step.observations[] | observation.results[] |
| observation.source_call_id | source_call_id |
| observation.content | content |
| observation.error | content (wrapped as `[error: {error}]`) |

### Step Renumbering
ATIF convention requires sequential step IDs starting at 1. The exporter renumbers regardless of source `step_index` values.

### Error Handling
- Records that fail conversion are skipped with a warning log
- Error count is tracked and logged after export completes
- The iterator continues yielding remaining records

## Calculations

None. Export is a pure field mapping operation.

## State Machines

None.

## Edge Cases

1. **Dangling tool calls in ATIF**: Observations with `error` but no `content` are mapped to `content="[error: {error}]"` to preserve the error signal in a format ATIF consumers can handle.
2. **None content steps**: Steps with `content=None` (pure tool-call steps) omit the `message` field entirely from the ATIF output.
3. **Optional agent fields**: `version` and `model_name` are only included in the ATIF agent dict if they are not None.
4. **Token usage zero check**: ATIF `metrics` block is only emitted when `input_tokens > 0 or output_tokens > 0`.
