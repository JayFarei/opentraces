# CLI Reference

## Commands

### `opentraces publish`

Parse, enrich, and publish traces to Hugging Face Hub.

```bash
opentraces publish [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--tier` | `automated` | Security tier: `danger`, `automated`, or `manual` |
| `--dataset` | auto | Target HF dataset repo (default: `{username}/opentraces-{agent}`) |
| `--json` | off | Emit structured JSON output for agent consumption |
| `--dry-run` | off | Parse and enrich without uploading |

### `opentraces review`

Interactive review interface for flagged or buffered traces.

```bash
opentraces review [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--web` | off | Launch local web UI instead of CLI review |
| `--session` | all | Review a specific session ID |

### `opentraces status`

Show pending traces, published counts, and configuration.

```bash
opentraces status [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--json` | off | Structured JSON output |

### `opentraces auth`

Authenticate with Hugging Face Hub.

```bash
opentraces auth
```

### `opentraces config`

Configure per-project settings.

```bash
opentraces config [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--tier` | - | Set default security tier for this project |
| `--dataset` | - | Set target dataset repo |

### `opentraces capabilities`

Machine-discoverable feature and version info.

```bash
opentraces capabilities --json
```

### `opentraces introspect`

Full API schema for agent integration.

```bash
opentraces introspect --json
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

## JSON Output Format

When `--json` is passed, every command emits:

```json
{
  "status": "ok",
  "data": { ... },
  "next_steps": ["Review 1 flagged trace"],
  "next_command": "opentraces review"
}
```

On error:

```json
{
  "status": "error",
  "error": {
    "code": 4,
    "kind": "network",
    "message": "HF Hub unreachable",
    "hint": "Check your internet connection or HF_TOKEN",
    "retryable": true
  }
}
```
