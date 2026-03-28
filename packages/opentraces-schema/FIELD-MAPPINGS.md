# Field Mappings: opentraces -> Downstream Formats

Reference tables for converting opentraces TraceRecord to downstream schemas.
These mappings are implemented in `src/opentraces/exporters/` but documented here
for ML researchers who want to write their own converters.

## opentraces -> ATIF v1.6

ATIF (Agent Trajectory Interchange Format) is designed for SFT and RL training pipelines.
The export is lossy: opentraces fields with no ATIF equivalent are dropped.

### Root Level

| opentraces | ATIF v1.6 | Notes |
|-----------|-----------|-------|
| `schema_version` | `schema_version: "ATIF-v1.6"` | Hardcoded |
| `session_id` | `session_id` | Direct |
| `agent.name` | `agent.name` | Direct |
| `agent.version` | `agent.version` | Direct |
| `agent.model` | `agent.model_name` | Rename |
| `tool_definitions` | `agent.tool_definitions` | Direct |
| `trace_id` | - | Dropped (ATIF uses session_id only) |
| `content_hash` | - | Dropped |
| `timestamp_start` | - | Dropped (per-step timestamps preserved) |
| `timestamp_end` | - | Dropped |
| `environment` | - | Dropped |
| `outcome` | - | Dropped |
| `dependencies` | - | Dropped |
| `metrics` | - | Dropped (per-step metrics preserved) |
| `security` | - | Dropped |
| `attribution` | - | Dropped |
| `system_prompts` | - | Dropped (ATIF stores inline per step) |
| `metadata` | - | Dropped |

### Step Level

| opentraces Step | ATIF Step | Notes |
|----------------|-----------|-------|
| `step_index` | `step_id` | Renumbered sequentially from 1 at export time |
| `role` | `source` | Direct (system/user/agent are the same) |
| `content` | `message` | Direct, omitted if None |
| `reasoning_content` | `reasoning_content` | Direct |
| `model` | `model_name` | Rename |
| `timestamp` | `timestamp` | Direct |
| `system_prompt_hash` | - | Dropped |
| `agent_role` | - | Dropped |
| `parent_step` | - | Dropped |
| `call_type` | - | Dropped |
| `subagent_trajectory_ref` | - | Dropped (ATIF supports this but we don't populate it) |
| `tools_available` | - | Dropped |
| `snippets` | - | Dropped |

### Tool Calls

| opentraces ToolCall | ATIF ToolCallSchema | Notes |
|--------------------|---------------------|-------|
| `tool_call_id` | `tool_call_id` | Direct |
| `tool_name` | `function_name` | Rename |
| `input` (dict) | `arguments` (dict) | Direct (ATIF accepts dict) |
| `duration_ms` | - | Dropped |

### Observations

opentraces stores observations as a flat list on each Step. ATIF wraps them
in a singular `observation` object with a `results` array.

| opentraces Observation | ATIF ObservationResult | Notes |
|-----------------------|-----------------------|-------|
| `source_call_id` | `source_call_id` | Direct |
| `content` | `content` | Direct |
| `output_summary` | - | Dropped |
| `error` | `content` | Mapped as `[error: {value}]` string |

**Structure transformation:**
```
opentraces: step.observations = [Obs1, Obs2]
ATIF:       step.observation = {"results": [Result1, Result2]}
```

When a step has zero observations, the `observation` key is omitted entirely.

### Token Usage

| opentraces TokenUsage | ATIF MetricsSchema | Notes |
|-----------------------|-------------------|-------|
| `input_tokens` | `prompt_tokens` | Rename |
| `output_tokens` | `completion_tokens` | Rename |
| `cache_read_tokens` | `cached_tokens` | Rename |
| `cache_write_tokens` | - | Dropped (no ATIF equivalent) |
| `prefix_reuse_tokens` | - | Dropped (opentraces-only metric) |

ATIF also supports `cost_usd`, `prompt_token_ids`, `completion_token_ids`, and
`logprobs`, but these are not available from CLI-level agent traces and are
omitted from the export.

---

## opentraces -> ADP (Agent Data Protocol)

ADP is designed as a training interlingua for SFT across multiple agent harnesses.
The export flattens opentraces' hierarchical steps into ADP's alternating
action/observation list.

*Exporter implementation planned. Mapping table below is a reference for
researchers writing their own converters.*

### Core Mapping

| opentraces | ADP | Notes |
|-----------|-----|-------|
| `session_id` | `Trajectory.id` | Direct |
| Step(role=agent, tool_calls=[tc]) | `APIAction(function=tc.tool_name, kwargs=tc.input)` | Each tool call becomes a separate APIAction |
| Step(role=agent, content=code) | `CodeAction(language=..., content=...)` | Only if step contains executable code |
| Step(role=agent, content=text) | `MessageAction(content=text)` | Agent messages without tool calls |
| Observation(content=text) | `TextObservation(source="environment", content=text)` | Tool results |
| Step(role=user) | `TextObservation(source="user", content=text)` | User messages |
| `reasoning_content` | `APIAction.description` or `CodeAction.description` | Reasoning attached to the action |
| `metadata` | `Trajectory.details` | Flexible dict |

### Fields Dropped by ADP Export

All of: `attribution`, `security`, `environment`, `outcome`, `dependencies`,
`metrics`, `system_prompts`, `tool_definitions`, `content_hash`, `token_usage`,
`snippets`, `hierarchy` (parent_step, agent_role, call_type).

ADP's key strength is simplicity: 3 action types + 2 observation types cover
coding, browsing, tool use, and SWE. The trade-off is losing all the metadata
that makes opentraces traces useful for analytics, RL reward modeling,
attribution, and security auditing.

---

## opentraces -> OTel GenAI (future)

OTel GenAI Semantic Conventions represent traces as span trees, which is a
fundamentally different structure from our step arrays. Each opentraces Step
would become a span, with tool calls as child spans.

*Exporter implementation planned for v0.2.*

---

## Notes for Converter Authors

1. **step_id renumbering**: ATIF uses 1-indexed step_id. opentraces step_index
   may be 0 or 1-indexed depending on the parser. Always renumber at export time.

2. **observation wrapping**: ATIF uses singular `observation` with `results[]`.
   opentraces uses plural `observations[]`. Don't just rename the field.

3. **token_usage partial mapping**: opentraces tracks 5 token sub-fields, ATIF
   tracks 3. The two cache fields unique to opentraces (cache_write, prefix_reuse)
   are our key differentiator for cost analysis.

4. **content=None steps**: Steps that are pure tool calls (no text content) should
   omit the `message`/`content` field, not set it to empty string.

5. **dangling tool calls**: Observations with `error="no_result"` indicate tool
   calls that never received a response. Map these to a descriptive error string.
