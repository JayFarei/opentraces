# TraceRecord

The top-level record. One per JSONL line, one per agent session.

## Identification

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | yes | Schema version, e.g. `"0.1.1"` |
| `trace_id` | string (UUID) | yes | Unique identifier for this trace |
| `session_id` | string | yes | Agent session reference |
| `content_hash` | string | no | SHA-256 of the serialized record, populated when written |

## Timestamps

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp_start` | string (ISO8601) | no | Session start time |
| `timestamp_end` | string (ISO8601) | no | Session end time |

## Task

```json
{
  "task": {
    "description": "Fix the failing test in src/parser.ts",
    "source": "user_prompt",
    "repository": "owner/repo",
    "base_commit": "abc123def456..."
  }
}
```

## Agent

```json
{
  "agent": {
    "name": "claude-code",
    "version": "1.0.83",
    "model": "anthropic/claude-sonnet-4-20250514"
  }
}
```

Model identifiers follow the `provider/model-name` convention.

## Environment

```json
{
  "environment": {
    "os": "darwin",
    "shell": "zsh",
    "vcs": {
      "type": "git",
      "base_commit": "abc123...",
      "branch": "main",
      "diff": "unified diff string or null"
    },
    "language_ecosystem": ["typescript", "python"]
  }
}
```

## System Prompts

Deduplicated into a top-level lookup table. Steps reference prompts by hash.

```json
{
  "system_prompts": {
    "sp_a1b2c3": "You are Claude Code..."
  }
}
```

## Tool Definitions

The session-level tool schema list.

## Dependencies

Package names referenced during the session. Extracted from manifest files or tool calls.

```json
{
  "dependencies": ["stripe", "prisma", "next"]
}
```

## Metrics

```json
{
  "metrics": {
    "total_steps": 42,
    "total_input_tokens": 1800000,
    "total_output_tokens": 34000,
    "total_duration_s": 780,
    "cache_hit_rate": 0.92,
    "estimated_cost_usd": 2.4
  }
}
```

## Security

```json
{
  "security": {
    "scanned": true,
    "flags_reviewed": 3,
    "redactions_applied": 1,
    "classifier_version": "0.1.0"
  }
}
```

## Metadata

Open-ended object for future extensions.

## Notes

- `content_hash` is filled in when the record is serialized with `to_jsonl_line()`
- `task`, `environment`, `steps`, and the nested blocks all have defaults in the Python model
- `security.scanned` confirms the security pipeline (scan, redact, classify) was applied
