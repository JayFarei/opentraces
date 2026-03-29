# Security Modes

opentraces offers two modes for controlling what gets uploaded. Configure per-project via `opentraces init --mode` or `opentraces config set --mode`, because a personal side-project has different sensitivity than a client codebase.

## Overview

| Mode | Pipeline | Review | For |
|------|----------|--------|-----|
| **Auto** | Scan + redact + push | None | Open-source, benchmarks, personal projects |
| **Review** (default) | Buffer locally | Every trace | Client work, sensitive codebases |

## Auto

Traces are scanned, redacted, and pushed automatically after each session. A security pipeline runs before any upload: regex-based scanning for high-confidence secrets (API keys, tokens, passwords) and PII (emails, IP addresses, filesystem paths). Anything flagged is auto-redacted.

**For:** Open-source projects where the codebase is already public, benchmark runs, researchers who want maximum throughput, personal projects where you trust the content.

```
  Trace captured
       |
       v
  Scan (regex + entropy)
  secrets + PII auto-redact
       |
       v
  Push to HF Hub
```

**Principle:** Automated scanning catches known secret patterns. No human bottleneck.

## Review (Default)

Nothing is uploaded automatically. All traces are buffered locally in `.opentraces/staging/`. You review every trace before pushing, using the TUI (`opentraces review`) or local web UI (`opentraces review --web`).

Scanning and redaction still run during review, the same regex and entropy checks as auto mode. The difference is that you approve each trace before it leaves your machine.

```
  Trace captured
       |
       v
  Buffer locally (.opentraces/staging/)
       |
  opentraces review
       |
       v
  Scan + redact + human review
  per-trace approve/redact/reject
       |
  +----+----+
  v         v
Push     Discard
```

**Principle:** Full human-in-the-loop. Nothing leaves the machine without explicit approval.

## Changing Modes

```bash
# Per-project
opentraces config set --mode review

# During init
opentraces init --mode auto
```

See [Security Configuration](/docs/security/configuration) for advanced options like custom redaction patterns and classifier sensitivity.
