# TraceRecord

The top-level record. One per JSONL line, one per agent trace.

## Identification

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | yes | Schema version, e.g. `"0.9.0"` |
| `trace_id` | string (UUID) | yes | Unique identifier for this trace |
| `session_id` | string | yes | Agent session reference |
| `content_hash` | string | no | SHA-256 of the serialized record, populated when written |
| `execution_context` | string | no | `"devtime"` (code-editing agent) or `"runtime"` (action-trajectory / RL agent). Null for pre-0.2 traces. |
| `lifecycle` | string | no | `"provisional"` (session ended, not yet tied to a revision) or `"final"` (post-commit hook correlated this trace to a commit). Defaults to `"provisional"`. Added 0.3.0 (RFC #25). |
| `patches` | array\<Patch\> | no | Authoritative dev-time output set. One `Patch` per tool-produced change/hunk. Added 0.6.0. |
| `git_links` | array\<GitLink\> | no | Evidence-graded links to commits/revisions this trace contributed to. A trace may link to many commits (rebase, squash, long session); a commit may link to many traces (cherry-pick, composition). Added 0.3.0. See [Outcome & Attribution](/docs/schema/outcome-attribution) for the evidence-tier taxonomy and `GitLink` fields. |
| `context_tree_summary` | object | no | Context Tree projection summary (`node_count`, `layer_count`, `active_path_leaf_id`, `capture_limitations` when present). Added 0.5.0. |
| `generation_index` | integer | no | Monotonic per-`session_id` generation counter. Generations are replacement snapshots, not stitchable supersets: later generations may carry different redactions, enrichments, or security-pipeline output. Consumers resolving "latest" should group by `session_id` and take `max(generation_index)`. Added 0.3.0. |

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
    "language_ecosystem": ["typescript", "python"],
    "resolved_dependencies": null,
    "interpreter": null,
    "arch": null,
    "platform": null,
    "abi_tag": null
  }
}
```

`resolved_dependencies` (`list<PinRecord> | null`), `interpreter`
(`Interpreter | null`), `arch`, `platform`, and `abi_tag` (all optional
strings) were added in 0.8.0. Every field defaults to `null` and is a
structural HOME for a future dependency-pin resolver, not a resolver itself;
their presence never raises `env_tier` or any other capsule trust ordinal.

```json
{
  "environment": {
    "resolved_dependencies": [
      {"name": "requests", "version": "2.31.0", "hash": null, "marker": null, "source": null}
    ],
    "interpreter": {"name": "cpython", "version": "3.11.6"},
    "arch": "arm64",
    "platform": "macosx_14_0_arm64",
    "abi_tag": "cp311"
  }
}
```

`PinRecord` requires only `name`; `version`, `hash`, `marker`, and `source` are
all optional so a resolver that knows a dependency but not its exact version
can still record it.

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

The trace-level tool schema list.

## Dependencies

Package names referenced during the trace. Extracted from manifest files or tool calls.

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
    "total_cache_read_tokens": 1650000,
    "total_cache_creation_tokens": 82000,
    "total_duration_s": 780,
    "cache_hit_rate": 0.92,
    "estimated_cost_usd": 2.4
  }
}
```

`total_cache_read_tokens` and `total_cache_creation_tokens` are session-level cache aggregates added in 0.3.0 (prompt-cache hits + writes across steps).

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
- `security.scanned` is legacy summary metadata; current per-tool details live under `metadata.security.tools_applied` and `metadata.security.tools`
- `task.repository_url` is the canonical remote URL (added 0.3.0, RFC #22). Prefer it over `repository` when normalizing across hosts.
- `Outcome.patch` was removed in 0.6.0. Use `patches[]` plus the bucket Trail companion (`trail.jsonl.gz`) for patch history and diff content.

## Patch Spine

```json
{
  "patches": [
    {
      "patch_id": "tracepatch-sha256:...",
      "file_path": "src/parser.py",
      "step_index": 7,
      "tool_call_id": "tc_123",
      "capture_method": ["hook_pretooluse", "hook_posttooluse"],
      "snapshot_before_id": "snapshot-sha256:...",
      "snapshot_after_id": "snapshot-sha256:...",
      "anchor": {
        "commit_sha": "abc123...",
        "evidence_tier": "exact_range_hash",
        "evidence_firmness": "firm_observed"
      },
      "superseded_by": [],
      "limitations": []
    }
  ]
}
```

`patches[]` is the stable join between the JSONL trace, Trace Trails, Context
Tree, and dataset workflow rows.
