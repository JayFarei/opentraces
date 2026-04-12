# Security Tiers

opentraces runs a layered security pipeline on every trace. Tiers 1a and 1b are always on and in-process. Tiers 1.5, 1.8, and 2 are optional and must be explicitly enabled. Tier 3 is human review.

The pipeline's internal version is exposed as `SECURITY_VERSION` (currently `0.4.0`, bumped when detection logic changes).

## Tier Model

| Tier | Name | Status | What it does |
|------|------|--------|--------------|
| 1a | Regex patterns | always on, in-process | 28+ built-in detectors for known secret formats (API keys, tokens, private keys) |
| 1b | Shannon entropy | always on, in-process | Flags high-entropy strings that look like secrets without matching a known pattern |
| 1.5 | TruffleHog | optional, opt-in | 800+ third-party detectors, run locally with `--verify_secrets=false` (no outbound probes) |
| 1.8 | LLM PII detection | optional | Per-field entity detection (PERSON, EMAIL, API_KEY, INTERNAL_URL, IP_ADDR, USER_PATH, CREDENTIAL, ORG_NAME, EMPLOYEE_ID, PHONE, SENSITIVE_DATA), SHA-256 cached |
| 2 | LLM session review | optional | Whole-session semantic verdict across all tiers (see below) |
| 3 | Human review | always available | TUI / web inbox / `opentraces session` |

### Tier 1.5 — TruffleHog

TruffleHog is **disabled by default**. Enable it with:

```bash
opentraces setup trufflehog            # install + enable
opentraces setup trufflehog --verify   # verify existing binary + enable
opentraces setup trufflehog --disable  # turn it off
```

Once enabled in config (`security.trufflehog.enabled = true`), a missing binary is a **hard error**, not a silent skip. `opentraces doctor` reports this as `ENABLED-BUT-MISSING` and exits `3`.

For ad-hoc bypass on a single push, use the one-shot override:

```bash
opentraces push --no-trufflehog
```

Verification of detected secrets against third-party APIs is forced off (`--verify_secrets=false`) — nothing is sent outbound.

### Tier 1.8 — LLM PII detection

Optional per-field PII detector. Entities must appear verbatim in the input; hallucinated entities are rejected. Redactions produce named placeholders via the EntityMap (see below) rather than opaque `[REDACTED]` markers.

### Tier 2 — LLM session review

Runs over the whole session transcript after all field-level tiers. Transcript is chunked at 400k chars; per-chunk verdicts are aggregated pessimistically (`shareable`: `no` > `manual_review` > `yes`; `missed_sensitive_data`: `yes` > `maybe` > `no`). Cached on `sha256(content + model + prompt_version + context)`.

Verdict shape:

```json
{
  "shareable": "yes" | "no" | "manual_review",
  "missed_sensitive_data": "yes" | "no" | "maybe",
  "flagged_parts": [{"reason": "...", "evidence": "..."}],
  "summary": "..."
}
```

Run manually:

```bash
opentraces review-llm                         # ollama, default model
opentraces review-llm --provider anthropic    # cloud model
opentraces review-llm --dry-run               # just estimate cost
```

Gate `push` on a clean verdict for every committed trace:

```bash
opentraces push --llm-review
```

`push --llm-review` aborts (exit `3`) unless every committed trace has `metadata.llm_review.status == "complete"` with `shareable != "no"` and `missed_sensitive_data != "yes"`.

### Tier 3 — Human review

Unchanged. Use `opentraces web`, `opentraces tui`, or `opentraces session` to inspect and edit staged traces. Traces can also enter the `BLOCKED` state (`block_reason = parse_error | trufflehog_finding | ...`) and require human action.

## EntityMap — named placeholders

Instead of `[REDACTED]`, the Tier 1.8 PII detector registers each hit into an `EntityMap` that emits stable named placeholders — `[PERSON_1]`, `[EMAIL_2]`, `[API_KEY_3]` — so the same entity always renders identically within a session. `USER_PATH` entities normalize paths in place (`/Users/rlamers/` → `/Users/user/`) rather than producing a numbered placeholder, preserving structure for trace analysis.

`EntityMap` supports `save(path)` / `load(path)` round-tripping via JSON; a persistent file is not yet exposed as a CLI flag, but reviewers see named placeholders in TUI and web today whenever Tier 1.8 runs.

This makes redacted traces readable to both humans and agents while still stripping sensitive content.

## Review Policy

`opentraces init` still sets a project-level review policy:

| Policy | Values | What it controls |
|--------|--------|------------------|
| `review_policy` | `review`, `auto` | Whether traces need manual review or are committed automatically |

```bash
opentraces init --review-policy review
opentraces init --review-policy auto
```

In `review` mode, every trace lands in the inbox. In `auto` mode, clean traces are committed automatically; traces with scan hits or `BLOCKED` status still land in the inbox.

## Review Flow

```text
Trace captured
  -> Tier 1a (regex) + Tier 1b (entropy) — always on
  -> Tier 1.5 TruffleHog — if enabled
  -> Tier 1.8 LLM PII — if enabled
  -> Tier 2 LLM session review — if enabled (or run later via review-llm)
  -> Inbox (review mode) or auto-committed (auto mode)
  -> session commit / reject / redact
  -> opentraces push [--llm-review]
```

## Changing Settings

```bash
opentraces config set --redact "ACME_INTERNAL_TOKEN"
opentraces config set --classifier-sensitivity high
opentraces setup trufflehog
opentraces setup trufflehog --disable
```

See [Security Configuration](/docs/security/configuration) for the config file shape and [Scanning & Redaction](/docs/security/scanning) for the field-by-field security pipeline.
