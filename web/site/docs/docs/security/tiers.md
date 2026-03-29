# Security Modes

opentraces applies the same security pipeline to every trace: scan, redact, and classify. The review policy controls whether traces require manual approval before publishing.

## Review Policy

`opentraces init` sets a project-level review policy:

| Policy | Values | What it controls |
|--------|--------|------------------|
| `review_policy` | `review`, `auto` | Whether traces require manual review or are committed automatically |

```bash
opentraces init --review-policy review
opentraces init --review-policy auto
```

In `review` mode, all traces land in the inbox for manual commit and push. In `auto` mode, clean traces are committed automatically, while traces with scan hits still land in the inbox for review.

## Security Pipeline

Every trace goes through the full security pipeline:

1. **Scan** - Two-pass secret detection (field-level + serialized)
2. **Redact** - Automatic replacement of detected secrets
3. **Classify** - Heuristic flagging of internal hostnames, AWS ARNs, database URIs, deep file paths
4. **Anonymize** - Path anonymization to remove usernames

The `security.scanned` field on each trace confirms the pipeline ran. `security.redactions_applied` and `security.flags_reviewed` record what was found.

## Review Flow

```text
Trace captured
  -> parsed, scanned, redacted, classified
  -> Inbox (review mode) or auto-committed (auto mode)
  -> session commit / reject / redact
  -> push
```

```bash
opentraces web
opentraces tui
opentraces session list --stage inbox
opentraces session commit <trace-id>
opentraces commit --all
opentraces push
```

## Changing Settings

```bash
opentraces config set --redact "ACME_INTERNAL_TOKEN"
opentraces config set --classifier-sensitivity high
```

See [Security Configuration](/docs/security/configuration) for the config file shape and [Scanning & Redaction](/docs/security/scanning) for the field-by-field security pipeline.
