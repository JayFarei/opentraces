# Scanning & Redaction

The baseline security layer runs across all tiers. It uses pattern matching and entropy analysis to detect secrets, PII, and sensitive content before any upload.

## What Gets Scanned

### Secrets

19 regex patterns covering:

- JWT tokens
- API keys by provider prefix (OpenAI, Anthropic, Stripe, AWS, etc.)
- Database connection URLs
- Private keys (RSA, DSA, EC)
- Bearer tokens
- High-entropy strings (Shannon entropy analysis)

### PII

- Email addresses
- IP addresses (v4 and v6)
- Credit card numbers (Luhn validation)
- Social Security Numbers
- Phone numbers
- Filesystem paths that reveal usernames (`/Users/name/`, `/home/name/`)

### Allowlist

False positive suppression for common patterns that look like secrets but aren't: test fixtures, example tokens in documentation, placeholder values.

## How Redaction Works

Detected secrets and PII are replaced with typed placeholders:

```
Before: export OPENAI_API_KEY=sk-abc123...
After:  export OPENAI_API_KEY=[REDACTED:api_key]
```

```
Before: /Users/jay/src/project/...
After:  /Users/[REDACTED:username]/src/project/...
```

Redaction is applied in-place on the staged JSONL before upload. The original session files on disk are never modified.

## Context-Aware Scanning

Different trace fields receive different scanning treatment:

| Field | Scanning Level | Rationale |
|-------|---------------|-----------|
| `content` (user/agent messages) | Full scan | Most likely to contain secrets |
| `tool_calls.input` | Full scan | Commands may include tokens |
| `observations.content` | Full scan | Tool output may leak secrets |
| `task.description` | PII only | User prompts, less likely to have raw keys |
| `agent`, `environment` | Minimal | Structured metadata, low risk |

## Anonymization

Beyond secret redaction, the anonymizer handles:

- **Username hashing** - SHA-256 hash of system username
- **Path stripping** - `/Users/` and `/home/` prefixes replaced
- **macOS path handling** - Hyphen-encoded paths decoded and anonymized

## Custom Redaction

Add custom patterns via config:

```bash
opentraces config set --redact "INTERNAL_API_KEY"
opentraces config set --redact "corp-secret-prefix-"
```

Custom strings are treated as literal matches and redacted wherever they appear in trace content.
