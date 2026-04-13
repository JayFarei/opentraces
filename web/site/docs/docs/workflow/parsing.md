# Parsing

Parsing is the ingestion step that turns raw agent session logs into staged `TraceRecord` JSONL files.

## What Runs Automatically

When `opentraces init` installs the agent session hook, the hidden `_capture` command runs after each session ends. That capture path:

1. Finds new session files under `~/.claude/projects/`
2. Parses the raw session into a `TraceRecord`
3. Filters out trivial sessions with fewer than 2 steps or no tool calls
4. Runs the enrichment and security pipeline
5. Writes the result to `.opentraces/staging/<trace-id>.jsonl`

## Enrichment Pipeline

Every parsed trace is enriched before staging:

| Step | What it does | Example output |
|------|-------------|----------------|
| Git signals | Detects repo state, extracts commit info | `committed: true`, `commit_sha: "a3f9..."`, `branch: "main"` |
| Attribution | Maps Edit and Write tool calls to file/line ranges (three-layer pipeline, see below) | `auth.py L42-67` attributed to step 4, `murmur3:...` content hash |
| Dependencies | Extracts from manifests and install commands | `["flask", "pydantic"]` from `pyproject.toml` |
| Metrics | Aggregates token counts, cost, cache rates | `cache_hit_rate: 0.91`, `estimated_cost_usd: 3.21` |
| Security scan | Regex + entropy scan, tiered redaction | API key in Bash output replaced with `[REDACTED]` |
| Classification | Tier 2 heuristic flagging for review | Internal hostname `*.corp` flagged for manual review |
| Anonymization | Strips usernames and home paths | `/Users/alice/project/` becomes `/~/project/` |

## Attribution: the three-layer pipeline

Attribution is built by three resolvers tried in priority order. The strongest available signal wins per range.

1. **PostToolUse hook** (`installers/claude_code_hooks/on_tool_use.py`). Fires after every Edit/Write, reads the file from disk, and emits a transcript event with the exact post-edit lines plus a `murmur3:<32-hex>` content hash. This is the authoritative signal — `experimental` stays `false`.
2. **Unified diff.** When no hook event covers a range, the session's unified diff is parsed to recover line numbers and content. Medium confidence.
3. **`str.find` fallback.** Last-resort textual match of tool output back to the current file content. Low confidence; the resulting `attribution.experimental` is `true`.

The PostToolUse hook is installed alongside the session-end hook by `opentraces init` (and can be reinstalled with `opentraces setup claude-code`). Its events are consumed at parse time, so the post-edit hashes travel with the trace even if the file is later reformatted. This lets the post-commit correlator match ranges across formatter churn and classify the resulting `GitLink` tier.

## Review Policy Interaction

`review_policy` controls where a parsed trace lands:

| Policy | Result |
|--------|--------|
| `review` | Trace lands in `Inbox` for manual review |
| `auto` | Clean traces are committed and pushed automatically |

The review surface still exists either way. `auto` just reduces the amount of manual triage needed, and traces with scan hits still land in the inbox.

## Parsing Existing Sessions

To import sessions that were recorded before you ran `opentraces init`, pass `--import-existing` at init time:

```bash
opentraces init --import-existing
```

This runs a one-off batch parse of all existing Claude Code sessions for the current project directory, applying the same enrichment and security pipeline as the hook.

### Internal Batch Commands

`discover` and `parse` are hidden commands available for diagnostics and manual batch processing:

```bash
opentraces discover          # list all Claude Code projects with session files
opentraces parse             # parse all unparsed sessions into staging
opentraces parse --auto      # parse and auto-approve (skip review)
opentraces parse --limit 10  # parse at most 10 sessions
```

These bypass the hook path and write directly to `.opentraces/staging/`. The user-facing path for ongoing collection is the hook plus inbox workflow.

## What Gets Filtered

- Sessions with fewer than 2 steps
- Sessions with zero tool calls
- Duplicate sessions by `content_hash`

## Next Step

```bash
opentraces web
```

Use the browser inbox or `opentraces tui` to review the staged traces before committing them.
