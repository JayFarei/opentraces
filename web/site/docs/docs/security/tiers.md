# Security Modes

opentraces separates review policy, push policy, and trace security tier.

## Project Policies

`opentraces init` and `opentraces config set` work with two project-level policies:

| Policy | Values | What it controls |
|--------|--------|------------------|
| `review_policy` | `review`, `auto-ready` | Whether a parsed trace lands in `Inbox` or `Ready` |
| `push_policy` | `manual`, `auto-push` | Metadata for downstream automation; the built-in CLI still uses explicit `commit` and `push` |

```bash
opentraces init --review-policy review --push-policy manual
opentraces init --review-policy auto-ready --push-policy manual
```

`review_policy` is the current user-facing replacement for the older `--mode` flag. `--mode` remains as a legacy alias only.

## Security Tier

The trace-level `security.tier` field records which security pass was used while processing a trace.

| Tier | Pipeline | Typical use |
|------|----------|-------------|
| `1` | Scan + redact | Open or lower-risk work where automatic redaction is acceptable |
| `2` | Scan + redact + classifier | Guarded work that wants automatic redaction plus heuristic flagging |
| `3` | Human review first | Strict review flow; rely on the inbox before upload |

The pipeline is tiered, but the review surface is always the same: browser inbox, TUI inbox, or `session` subcommands.

## Review Flow

```text
Trace captured
  -> parsed and staged locally
  -> Inbox or Ready depending on review_policy and tier
  -> session approve / reject / redact
  -> commit
  -> push
```

```bash
opentraces web
opentraces tui
opentraces session list --stage inbox
opentraces session approve <trace-id>
opentraces commit --all
opentraces push
```

## Changing Settings

```bash
opentraces config set --tier 2
opentraces config set --tier 3
opentraces config set --redact "ACME_INTERNAL_TOKEN"
opentraces config set --classifier-sensitivity high
```

See [Security Configuration](/docs/security/configuration) for the config file shape and [Scanning & Redaction](/docs/security/scanning) for the field-by-field security pipeline.
