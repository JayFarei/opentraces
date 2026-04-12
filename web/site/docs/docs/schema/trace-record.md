# TraceRecord

The top-level record. One per JSONL line, one per agent session.

## Identification

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | yes | Schema version, e.g. `"0.3.0"` |
| `trace_id` | string (UUID) | yes | Unique identifier for this trace |
| `session_id` | string | yes | Agent session reference |
| `content_hash` | string | no | SHA-256 of the serialized record, populated when written |
| `execution_context` | string | no | `"devtime"` (code-editing agent) or `"runtime"` (action-trajectory / RL agent). Null for pre-0.2 traces. |
| `lifecycle` | string | no | `"provisional"` (session ended, not yet tied to a revision) or `"final"` (post-commit hook correlated this trace to a commit). Defaults to `"provisional"`. Added 0.3.0 (RFC #25). |
| `git_links` | array\<GitLink\> | no | Evidence-graded links to commits/revisions this trace contributed to. A trace may link to many commits (rebase, squash, long session); a commit may link to many traces (cherry-pick, composition). Added 0.3.0. See [Outcome & Attribution](/docs/schema/outcome-attribution) for the evidence-tier taxonomy and `GitLink` fields. |

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
    "repository_url": "https://github.com/owner/repo",
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

## Intent

Optional session-level summary, parallel in placement to `task` and `outcome`. Populated by the `on_stop` hook, `opentraces enrich`, a configured post-processor, or hand-edited by the user. All fields are optional — traces captured before enrichment ran load with `intent` absent.

```json
{
  "intent": {
    "title": "Refactor the login validator",
    "summary": "Inspected the email validator and replaced the regex with a standards-compliant check; added regression tests.",
    "source": "llm_hook",
    "model": "anthropic/claude-sonnet-4-5"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | no | Short session label, ~12 words |
| `summary` | string | no | One- to three-sentence description of the session |
| `source` | `"llm_hook"` \| `"post_processor"` \| `"user"` | no | How the block was produced. Closed enum — filter on this to keep or discard machine-generated intent. |
| `model` | string | no | Model id that produced this intent (empty when `source == "user"`) |

`compute_content_hash()` excludes `intent`, so running or re-running Intent summarization never changes a trace's identity. `source == "user"` is never overwritten by enrichment, even with `--force`.

## Metadata

Open-ended object for future extensions.

## Notes

- `content_hash` is filled in when the record is serialized with `to_jsonl_line()`
- `task`, `environment`, `steps`, and the nested blocks all have defaults in the Python model
- `security.scanned` confirms the security pipeline (scan, redact, classify) was applied
- `task.repository_url` is the canonical remote URL (added 0.3.0, RFC #22). Prefer it over `repository` when normalizing across hosts.
