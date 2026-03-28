# Parsing

`opentraces parse` scans agent sessions, normalizes them into the opentraces schema, runs security scanning, enriches with git signals, and stages results locally.

## Discover First

```bash
opentraces discover
```

Lists available sessions across your projects. Shows session counts per project directory.

## Parse

```bash
opentraces parse
```

For each discovered session:

1. Detects the agent type (Claude Code for v0.1)
2. Parses raw session files into structured `TraceRecord` objects
3. Runs security scanning based on your configured [tier](/docs/security/tiers)
4. Enriches with git signals (base commit, branch, diff)
5. Extracts [attribution](/docs/schema/outcome-attribution) from Edit tool calls
6. Calculates metrics (token counts, duration, cost estimates)
7. Stages enriched traces in `.opentraces/staging/`

## Options

```bash
# Auto-approve for open-tier projects
opentraces parse --auto

# Limit how many sessions are parsed
opentraces parse --limit 5
```

| Flag | Default | Description |
|------|---------|-------------|
| `--auto` | off | Skip interactive review, auto-approve |
| `--limit` | 0 (all) | Max sessions to parse |

## What Gets Enriched

| Signal | Source | Description |
|--------|--------|-------------|
| `content_hash` | SHA-256 of trace | Dedup at upload time |
| `attribution` | Edit tool calls + patch | File/line ranges produced by the agent |
| `outcome.committed` | Git history | Whether session changes were committed |
| `outcome.commit_sha` | Git history | Specific commit hash |
| `environment.vcs` | Git state | Base commit, branch, diff |
| `dependencies` | Package files | Libraries referenced during the session |
| `metrics` | Token counts | Total tokens, duration, cache hit rate, cost |

## Filtering

Not all sessions become traces. The parser filters out:

- Sessions with fewer than 2 steps (trivial interactions)
- Sessions with zero tool calls (pure conversation)
- Duplicate sessions (SHA-256 content hash dedup)
