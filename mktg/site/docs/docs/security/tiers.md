# Security Tiers

opentraces offers three modes for controlling what gets uploaded. Configure per-project via `opentraces init --tier` or `opentraces config set --tier`, because a personal side-project has different sensitivity than a client codebase.

## Overview

| Tier | Gate | Review | For |
|------|------|--------|-----|
| **Open** | Regex scan only | None | Open-source, benchmarks |
| **Guarded** | Classifier + escalation | Flagged traces only | Most projects (default) |
| **Strict** | Full buffer | Every trace | Sensitive codebases |

## Open (Minimal Gate)

Traces are uploaded with minimal friction, but not blindly. A baseline security check runs before any upload: regex-based scanning for high-confidence secrets (API keys, tokens, passwords) and obvious PII (emails, IP addresses). Anything flagged is auto-redacted, not escalated.

**For:** Open-source projects where the codebase is already public, benchmark runs (SWE-bench, Aider-bench), researchers who want maximum throughput.

```
  Trace captured
       |
       v
  Baseline scan (regex)
  secrets + PII auto-redact
       |
       v
  Upload to HF Hub
```

**Principle:** Even the lowest-friction tier guarantees no raw secrets or credentials leak into the public dataset.

## Guarded (Default)

Traces pass through a classifier pipeline before upload:

1. **PII detection** - Emails, API keys, tokens, credentials, internal hostnames, IP addresses, filesystem paths. Regex + lightweight classifier.
2. **Sensitive content classification** - Flags traces referencing proprietary codebases, internal tools, customer data.
3. **De-anonymization risk scoring** - Estimates how identifiable the contributor is from trace content. Stylometric and contextual signals beyond explicit PII.
4. **Escalation** - Anything flagged surfaces in the [review interface](/docs/workflow/review) before upload.

```
  Trace captured
       |
       v
  Baseline scan (regex)
       |
       v
  Classifier (content + de-anon)
       |
  +----+----+
  v         v
clean     flagged
  |         |
  |    Review (approve/redact/reject)
  |         |
  v         v
Upload    Discard
```

**Principle:** Machine classifiers handle the bulk. Humans only see edge cases the classifier is uncertain about.

## Strict (Human-in-the-Loop)

Nothing is uploaded during the session. All traces are buffered locally. After the session ends, the user manually reviews every trace.

```
  Trace captured
       |
       v
  Buffer locally (.opentraces/staging/)
       |
  Session ends
       |
       v
  Human review (opentraces review)
  per-trace approve/redact/reject
       |
  +----+----+
  v         v
Upload    Discard
```

**Principle:** Full human-in-the-loop. Nothing leaves the machine without explicit approval.

## Changing Tiers

```bash
# Per-project
opentraces config set --tier strict

# During init
opentraces init --tier open
```

See [Security Configuration](/docs/security/configuration) for advanced options like custom redaction strings and classifier sensitivity.
