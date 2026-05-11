# Parsing

Parsing is the ingestion step that turns raw agent logs or imported datasets into local `TraceRecord` JSONL files under `~/.opentraces/projects/<slug>/traces/`.

## What Runs Automatically

When `opentraces init` installs the Claude Code hook, capture runs automatically after each session ends. The capture path:

1. Finds new Claude Code session files under `~/.claude/projects/`
2. Parses the raw session into a `TraceRecord`
3. Filters out trivial traces with fewer than 2 steps or no tool calls
4. Runs the enrichment and security pipeline
5. Records Trace Patch events into the append-only Trace Trails event log under `refs/opentraces/local/events/v1` (consumed by `opentraces trail`)
6. Writes the result into the project's machine-local trace store
7. Updates local state so the trace surfaces as `inbox`, `staged`, `rejected`, `pushed`, or `blocked`

## Enrichment Pipeline

Every parsed trace is enriched before staging:

| Step | What it does | Example output |
|------|-------------|----------------|
| Git signals | Detects repo state and later correlates commits back to traces | active branch, git links, lifecycle |
| Attribution | Maps Edit and Write tool calls to file and line ranges when possible | `auth.py L42-67` attributed to step 4 |
| Dependencies | Extracts from manifests and install commands | `["flask", "pydantic"]` from `pyproject.toml` |
| Metrics | Aggregates token counts, cost, cache rates | `cache_hit_rate: 0.91`, `estimated_cost_usd: 3.21` |
| Security scan | Regex + entropy scan, optional TruffleHog, redaction | sensitive strings rewritten before review |
| Trace Trails | Records Trace Patches and reconciles Git Anchors after each commit | event log under `refs/opentraces/local/events/v1`, surfaced via `opentraces trail` |
| Anonymization | Normalizes usernames and local paths | `/Users/alice/project/` becomes a sanitized path |

## Attribution: the three-layer pipeline

Attribution is built by three resolvers tried in priority order. The strongest available signal wins per range.

1. **PostToolUse hook** (`src/opentraces/capture/claude_code/hooks/on_tool_use.py`). Fires after every Edit/Write, reads the file from disk, and emits a transcript event with the exact post-edit lines plus a `murmur3:<32-hex>` content hash. This is the authoritative signal — `experimental` stays `false`.
2. **Unified diff.** When no hook event covers a range, the trace's unified diff is parsed to recover line numbers and content. Medium confidence.
3. **`str.find` fallback.** Last-resort textual match of tool output back to the current file content. Low confidence; the resulting `attribution.experimental` is `true`.

The PostToolUse hook is installed alongside the trace-end capture hook by `opentraces init` (and can be reinstalled with `opentraces setup claude-code`). Its events are consumed at parse time, so the post-edit hashes travel with the trace even if the file is later reformatted. This lets the post-commit correlator match ranges across formatter churn and classify the resulting `GitLink` tier.

After the post-commit hook runs, a separate Trace Trail reconciler creates Git Anchor events for the new commit (best-effort, non-fatal). Anchor identity has two tiers: an exact whitespace-collapsed range hash, and a structural-match fallback (line similarity ≥ 0.85) for format-then-commit cases. See [`opentraces trail`](/docs/cli/commands#trail-commands) in the CLI reference.

## Review Policy Interaction

`review_policy` controls where a parsed trace lands:

| Policy | Result |
|--------|--------|
| `review` | Trace lands in `Inbox` for manual review |
| `auto` | Clean traces are auto-approved into `staged` |

The review surface still exists either way. `blocked` traces and traces with findings still need human attention.

## Parsing Existing Traces

To import traces that were recorded before you ran `opentraces init`, pass `--import-existing` at init time:

```bash
opentraces init --import-existing
```

This runs a one-off batch parse of all existing Claude Code traces for the current project directory, applying the same enrichment and security pipeline as the hook.

For dataset imports instead of live capture, use the dataset workflow surface to seed an ad-hoc dataset from a JSONL file:

```bash
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
```

Ad-hoc datasets skip workflow execution; review and publish flows still apply.

For HuggingFace dataset import via the older `hermes` adapter, the `FormatImporter` protocol is still registered (see [Supported Agents](/docs/cli/supported-agents)). The user-facing `opentraces pull` verb is no longer part of the public CLI; consume registered importers through dataset workflows or the schema package directly.

## What Gets Filtered

- Traces with fewer than 2 steps
- Traces with zero tool calls
- Duplicate traces by `content_hash`
- Parse outcomes with errors are marked `blocked`

## Next Step

```bash
opentraces trace query --since 1d
opentraces dataset run my-dataset
opentraces dataset review my-dataset --tui
```

Use `trace query` to search retained traces, then drive dataset workflows to synthesize rows for review and publication.
