# Commands

Complete reference for every opentraces CLI command.

## Overview

| Command | Description |
|---------|-------------|
| `opentraces login` | Authenticate with HuggingFace Hub |
| `opentraces logout` | Clear stored credentials |
| `opentraces init` | Initialize opentraces in the current project |
| `opentraces discover` | List available agent sessions |
| `opentraces parse` | Parse sessions into enriched JSONL traces |
| `opentraces review` | Review staged traces before upload |
| `opentraces push` | Upload traces to HuggingFace Hub |
| `opentraces status` | Show project status |
| `opentraces remote` | Show configured remote dataset |
| `opentraces log` | List uploaded traces grouped by date |
| `opentraces config show` | Display current configuration |
| `opentraces config set` | Update configuration |
| `opentraces import` | Import traces from other formats |
| `opentraces export` | Export traces to other formats |
| `opentraces migrate` | Run schema migrations |
| `opentraces capabilities` | Machine-discoverable feature list |
| `opentraces introspect` | Full API schema for agent integration |

## Authentication

### `opentraces login`

Authenticate with HuggingFace Hub via OAuth device code flow.

```bash
opentraces login
opentraces login --token   # paste token directly
```

| Flag | Default | Description |
|------|---------|-------------|
| `--token` | off | Use token paste instead of browser OAuth |

### `opentraces logout`

Clear stored HuggingFace credentials.

```bash
opentraces logout
```

## Project Setup

### `opentraces init`

Initialize opentraces in the current project directory. Creates `.opentraces/config.yml` and `.opentraces/staging/`.

```bash
opentraces init
opentraces init --tier open      # minimal gate
opentraces init --tier guarded   # classifier + escalation (default)
opentraces init --tier strict    # full human review
```

| Flag | Default | Description |
|------|---------|-------------|
| `--tier` | interactive prompt | Security tier: `open`, `guarded`, or `strict` |

### `opentraces config show`

Display current configuration with secrets masked.

```bash
opentraces config show
```

### `opentraces config set`

Update configuration settings. `--exclude` and `--redact` are append-only.

```bash
opentraces config set --tier guarded
opentraces config set --exclude /path/to/sensitive/project
opentraces config set --redact "INTERNAL_API_KEY"
```

| Flag | Description |
|------|-------------|
| `--tier` | Security tier (`open`, `guarded`, `strict`) |
| `--project` | Project path for per-project config |
| `--exclude` | Project path to exclude (appends) |
| `--redact` | Custom redaction string (appends) |
| `--pricing-file` | Path to custom pricing table |
| `--classifier-sensitivity` | `low`, `medium`, or `high` |

## Ingestion

### `opentraces discover`

List available agent sessions across projects.

```bash
opentraces discover
```

Scans `~/.claude/projects/` for session files and reports what's available per project.

### `opentraces parse`

Parse agent sessions into enriched JSONL traces. Runs security scanning, enrichment (git signals, attribution, dependencies, metrics), and stages results locally.

```bash
opentraces parse
opentraces parse --auto      # auto-approve for open tier
opentraces parse --limit 5   # parse at most 5 sessions
```

| Flag | Default | Description |
|------|---------|-------------|
| `--auto` | off | Auto-approve, skip interactive review |
| `--limit` | 0 (all) | Max sessions to parse |

## Review

### `opentraces review`

Interactive review interface for staged traces. Required for guarded (flagged traces) and strict (all traces) tiers.

```bash
opentraces review
opentraces review --web            # local web UI
opentraces review --web --port 8080
opentraces review --tui            # terminal UI
```

| Flag | Default | Description |
|------|---------|-------------|
| `--web` | off | Launch local web UI |
| `--port` | 5050 | Port for web review server |
| `--tui` | off | Launch TUI review interface |

## Upload

### `opentraces push`

Upload staged traces to HuggingFace Hub as sharded JSONL.

```bash
opentraces push
opentraces push --approved-only
opentraces push --private
opentraces push --repo user/custom-dataset
```

| Flag | Default | Description |
|------|---------|-------------|
| `--approved-only` | off | Only push approved traces |
| `--private` | off | Force private visibility |
| `--public` | off | Force public visibility |
| `--publish` | off | Change existing private dataset to public |
| `--gated` | off | Enable gated access (auto-approve) |
| `--repo` | `{username}/opentraces` | Target HF dataset repo |

### `opentraces status`

Show project status: staged traces, push history, security tier, authentication.

```bash
opentraces status
```

### `opentraces remote`

Show the configured remote dataset URL.

```bash
opentraces remote
```

### `opentraces log`

List uploaded traces grouped by date.

```bash
opentraces log
```

## Import & Export

### `opentraces import`

Import traces from other formats into staging.

```bash
opentraces import --from dataclaw /path/to/traces
```

| Flag | Default | Description |
|------|---------|-------------|
| `--from` | required | Source format (currently: `dataclaw`) |
| `--max-records` | unlimited | Maximum records to import |

### `opentraces export`

Export staged traces to other formats.

```bash
opentraces export --format atif
opentraces export --format atif --output /path/to/output
opentraces export --format atif --dry-run
```

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | required | Target format (currently: `atif`) |
| `--output` | stdout | Output file path |
| `--trace-id` | all | Specific trace IDs to export |
| `--dry-run` | off | Preview without writing |

## Utilities

### `opentraces migrate`

Check schema version and run migrations if needed.

```bash
opentraces migrate
```

### `opentraces capabilities`

Machine-discoverable feature and version info.

```bash
opentraces capabilities
opentraces capabilities --json
```

### `opentraces introspect`

Full API schema for agent integration. Returns the complete command tree with parameters and types.

```bash
opentraces introspect
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | OK |
| 2 | Usage error |
| 3 | Missing configuration |
| 4 | Network error |
| 5 | Data corruption |
| 7 | Lock/busy |

## JSON Output

Every command emits structured JSON with `next_steps` and `next_command` fields:

```json
{
  "status": "ok",
  "data": { },
  "next_steps": ["Review 1 flagged trace"],
  "next_command": "opentraces review"
}
```

On error:

```json
{
  "status": "error",
  "error": {
    "code": "NOT_AUTHENTICATED",
    "kind": "auth",
    "message": "No HF token",
    "hint": "Run: opentraces login",
    "retryable": false
  }
}
```
