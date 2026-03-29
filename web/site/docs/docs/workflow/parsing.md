# Parsing

Parsing is the ingestion step that turns raw Claude Code session logs into staged `TraceRecord` JSONL files.

## What Runs Automatically

When `opentraces init` installs the Claude Code hook, the hidden `_capture` command runs after each session ends. That capture path:

1. Finds new session files under `~/.claude/projects/`
2. Parses the raw session into a `TraceRecord`
3. Filters out trivial sessions with fewer than 2 steps or no tool calls
4. Runs the enrichment and security pipeline
5. Writes the result to `.opentraces/staging/<trace-id>.jsonl`

## Enrichment Pipeline

The current pipeline does:

1. Git detection and commit signal extraction
2. Attribution from Edit and Write tool calls
3. Dependency extraction from manifests and install commands
4. Metrics aggregation
5. Tiered security scanning and redaction
6. Tier 2 heuristic classification
7. Path anonymization

## Review Policy Interaction

`review_policy` controls where a parsed trace lands:

| Policy | Result |
|--------|--------|
| `review` | Trace lands in `Inbox` |
| `auto-ready` | Clean traces can land in `Ready` automatically |

The review surface still exists either way. `auto-ready` just reduces the amount of manual triage needed before `commit`.

## Internal Batch Commands

`discover` and `parse` are hidden internal commands used for diagnostics and batch processing. The user-facing path is the hook plus inbox workflow.

## What Gets Filtered

- Sessions with fewer than 2 steps
- Sessions with zero tool calls
- Duplicate sessions by `content_hash`

## Next Step

```bash
opentraces web
```

Use the browser inbox or `opentraces tui` to review the staged traces before committing them.
