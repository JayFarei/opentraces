# Schema Overview

opentraces uses a training-first JSONL schema where each line is one complete agent session. The schema is a superset of ATIF v1.6, informed by ADP and field patterns from existing HF datasets.

## Design Principles

1. **Training / SFT** - Clean message sequences with role labels, tool-use as tool_call/tool_result pairs, outcome signals.
2. **RL / RLHF** - Trajectory-level reward signals, step-level annotations, decision point identification.
3. **Telemetry** - Token counts, latency, model identifiers, cache hit rates, cost estimates.
4. **Cross-agent** - Represents traces from Claude Code, Cursor, Cline, Codex, and future agents without agent-specific fields.

## Top-Level Structure

```json
{
  "schema_version": "0.2.0",
  "trace_id": "uuid",
  "session_id": "uuid",
  "content_hash": "sha256-hex",
  "timestamp_start": "ISO8601",
  "timestamp_end": "ISO8601",
  "execution_context": "devtime",
  "task": { },
  "agent": { },
  "environment": { },
  "system_prompts": { },
  "tool_definitions": [ ],
  "steps": [ ],
  "outcome": { },
  "dependencies": [ ],
  "metrics": { },
  "security": { },
  "attribution": { },
  "metadata": { }
}
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `steps` not `turns` | Each step is an LLM API call, not a conversational turn. Aligns with ATIF's TAO loop. |
| `role: "agent"` not `"assistant"` | Follows ATIF convention (`system`, `user`, `agent`). |
| Tool calls separated from observations | Preserves call/result separation training pipelines depend on. |
| System prompt dedup | Hash-based lookup table. A 20K-token prompt repeated across steps would be wasteful. |
| `parent_step` per step | Precise parent-child tree for sub-agents, not a flat session-level array. |
| `content_hash` | SHA-256 for dedup at upload time. |
| `reasoning_content` | Explicit chain-of-thought field. Improved SWE-Bench by ~3 pts (Cognition data). |
| `outcome.committed` | Did the session's changes get committed? Cheap, deterministic quality signal. |
| `attribution` | Embedded Agent Trace block. Bridges trajectory (process) with code attribution (output). |

## Schema Package

The schema is a standalone Python package:

```bash
pip install opentraces-schema
```

```python
from opentraces_schema import TraceRecord, SCHEMA_VERSION

record = TraceRecord(
    trace_id="abc-123",
    session_id="sess-456",
    agent={"name": "claude-code", "version": "1.0.32"},
)
line = record.to_jsonl_line()
```

See [TraceRecord](/docs/schema/trace-record), [Steps](/docs/schema/steps), and [Outcome & Attribution](/docs/schema/outcome-attribution) for field-level detail.
