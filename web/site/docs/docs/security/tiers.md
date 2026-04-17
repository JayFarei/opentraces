# Security Tiers

opentraces applies layered security scanning before traces are staged or pushed. The current pipeline version is `SECURITY_VERSION = 0.3.0`.

Tip: run `opentraces doctor --security` to see the exact tiers, versions, and commands active in your current install.

## Current User-Facing Tiers

The current 0.3 CLI surfaces these layers:

| Tier | Name | Status | What it does |
|------|------|--------|--------------|
| 1a | Regex patterns | always on | Built-in secret detectors for known token and key formats |
| 1b | Shannon entropy | always on | Flags high-entropy strings that look like secrets |
| 1.5 | TruffleHog | optional | Runs TruffleHog locally for broader secret detection |
| 2 | LLM trace review | optional, on demand | Semantic review over the whole trace transcript |
| 3 | Human review | always available | Web inbox, TUI, and CLI review before upload |

## Tier 1a And 1b

Regex and entropy scanning are always on. They run locally during processing and rewrite sensitive content before traces surface in the inbox.

## Tier 1.5: TruffleHog

Enable Tier 1.5 with:

```bash
opentraces setup trufflehog
opentraces setup trufflehog --enable
opentraces setup trufflehog --disable
```

Current behavior:

- TruffleHog is opt-in
- it runs locally with `verify_secrets = false`
- findings are redacted in place
- findings force human review before upload
- `opentraces push --no-trufflehog` skips it for one push only

Use `opentraces doctor --security` to confirm whether the binary is installed and enabled.

## Tier 2: LLM Trace Review

Configure the reviewer once:

```bash
opentraces setup llm-review
```

Then run it on demand:

```bash
opentraces llm-review
opentraces llm-review --scope inbox
opentraces llm-review --scope staged
opentraces llm-review --trace 8a3f1c
opentraces llm-review --dry-run
```

Use `opentraces push --llm-review` when you want upload to require a clean Tier 2 verdict on every staged trace.

## Tier 3: Human Review

Human review is always available through:

```bash
opentraces web
opentraces tui
opentraces list --stage inbox
opentraces show <trace-id>
opentraces redact <trace-id>
```

This is the final check for project-specific context, sensitive business details, and traces that are technically safe but not worth publishing.

## Review Policy

Each repo carries a review policy in `.opentraces.json`:

```bash
opentraces setup review-policy --review
opentraces setup review-policy --auto
```

| Policy | Effect |
|--------|--------|
| `review` | Every trace lands in Inbox for manual review |
| `auto` | Safe traces are auto-approved into `staged` |

`auto` does not push automatically. Upload remains explicit.

## What Can Still Block

The user-facing pipeline is designed to redact and route most issues into review, but some failures still stop upload:

- parse errors
- missing required integrations you explicitly enabled
- `push --llm-review` when staged traces lack a clean Tier 2 verdict

Use `opentraces doctor` for pipeline failures and `opentraces list --stage blocked` for traces that still need intervention.
