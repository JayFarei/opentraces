# TraceRecord

The top-level record. One per JSONL line, one per agent session.

## Fields

### Identification

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | yes | Schema version, e.g. `"0.1.0"` |
| `trace_id` | string (UUID) | yes | Unique identifier for this trace |
| `session_id` | string (UUID) | yes | Agent session reference |
| `content_hash` | string | yes | SHA-256 of trace content for dedup |

### Timestamps

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp_start` | string (ISO8601) | yes | Session start time |
| `timestamp_end` | string (ISO8601) | yes | Session end time |

### Task

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

### Agent

```json
{
  "agent": {
    "name": "claude-code",
    "version": "1.0.83",
    "model": "anthropic/claude-sonnet-4-20250514"
  }
}
```

Model identifiers follow the `provider/model-name` convention from Agent Trace / models.dev.

### Environment

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

### System Prompts

Deduplicated into a top-level lookup table. Steps reference by hash.

```json
{
  "system_prompts": {
    "sp_a1b2c3": "You are Claude Code..."
  }
}
```

### Tool Definitions

Complete set of available tools at session level.

```json
{
  "tool_definitions": [
    {
      "name": "bash",
      "description": "Execute shell commands",
      "parameters": {}
    }
  ]
}
```

### Dependencies

Package/library names referenced during the session. Extracted from `package.json`, `Gemfile`, `requirements.txt`, `pyproject.toml`, or tool call arguments.

```json
{
  "dependencies": ["stripe", "prisma", "next"]
}
```

### Metrics

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

### Security

```json
{
  "security": {
    "tier": "automated",
    "flags_reviewed": 3,
    "redactions_applied": 1
  }
}
```

### Metadata

Open-ended object for future extensions.

```json
{
  "metadata": {}
}
```
