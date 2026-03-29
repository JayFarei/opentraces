---
schema_version: "1.0"
title: Security Pipeline
scope: src/opentraces/security
---

# Security Pipeline

## Entities

### SecretMatch
Dataclass representing one detection hit: `pattern_name`, `matched_text`, `start`, `end`, `severity`.

### ScanResult
Aggregated result of scanning one or more fields: `matches`, `redaction_count`, `field_counts` (by field type).

### ClassifierFlag
Heuristic detection hit from Tier 2 classifier: `pattern_name`, `matched_text`, `reason`, `severity`.

### ClassifierResult
Aggregated classifier output: `flags` list, `risk_score` (0.0-1.0, clamped).

### FieldType (Enum)
Content field classification for context-aware scanning:
- `TOOL_INPUT` - Bash/Write/Edit tool inputs
- `TOOL_RESULT` - Read/Grep/Glob tool outputs
- `REASONING` - Chain-of-thought content
- `GENERAL` - Everything else

### Severity
Literal type: `"critical"`, `"high"`, `"medium"` (secrets). Classifier adds `"low"`.

## Business Rules

### Security Tiers
The system has three security tiers (configured in `config.py`):
- **Tier 1 (open)**: Minimal security, auto mode, no human review
- **Tier 2 (guarded)**: Full regex + entropy scanning, heuristic classifier, review mode
- **Tier 3 (strict)**: Everything in Tier 2 plus mandatory human review before upload

Default tier is 3 (strictest). Per-project overrides via config.

### Two-Pass Scanning Architecture
1. **Pass 1 (per-field, context-aware)**: Each field in the TraceRecord is scanned with rules appropriate to its type. Different field types get different scan configurations.
2. **Pass 2 (serialized bytes)**: The final JSONL output is scanned as raw text. Catches anything introduced during enrichment or serialization that was not present in original fields.

### Context-Aware Scanning Rules
| Field Type | Regex Scan | Entropy Scan | Rationale |
|------------|-----------|--------------|-----------|
| TOOL_INPUT | Yes | Yes | User commands may contain secrets |
| GENERAL | Yes | Yes | Free text, full scanning needed |
| TOOL_RESULT | Yes | No | Too many false positives on command output |
| REASONING | Yes | No | Hallucination risk for entropy detection |

### Tool Classification
Tools are classified for scan context:
- **Input tools** (more conservative): Bash, Write, Edit -> `TOOL_INPUT`
- **Result tools**: Read, Grep, Glob -> `TOOL_RESULT`
- **Unknown tools default to** `TOOL_INPUT` (conservative)

MCP tool names (containing `__`) are split and the last segment is used for classification.

### Secret Pattern Library
21 regex patterns covering:
- **Critical**: JWT, Anthropic/OpenAI/HuggingFace/GitHub/PyPI/NPM/AWS/Slack API keys, private keys, credit cards, SSN
- **High**: Discord webhooks, database URLs, bearer tokens
- **Medium**: IPv4/IPv6, email addresses, phone numbers

Plus Shannon entropy detection (threshold 4.5) for unknown secret formats.

### Allowlist System
Allowlists reduce false positives for:
- **Emails**: noreply@, @example.com, @test.com, @localhost
- **Decorators**: @property, @staticmethod, @pytest.mark, etc.
- **IPs**: Private/reserved ranges (10.x, 172.16-31.x, 192.168.x, 127.x, 0.0.0.0)
- **URLs**: example.com, localhost, 127.0.0.1
- **Dummy tokens**: sk-test, sk-dummy, Bearer $, Bearer <test>, etc.
- **Credit cards**: Validated with Luhn algorithm, non-Luhn matches are allowlisted
- **SSNs**: Area numbers 000, 666, >=900 are filtered. Group 00 and serial 0000 filtered.
- **Phone numbers**: Strings with <10 digits filtered

### Heuristic Classifier (Tier 2)
Pattern-based flagging beyond regex:
- **Internal hostnames**: `.internal`, `.corp`, `.local` TLDs (severity: high, score: 0.6)
- **AWS account IDs**: 12-digit numbers in ARN patterns (severity: high, score: 0.7)
- **DB connection strings**: jdbc:, mongodb+srv:// (severity: high, score: 0.7)
- **Internal URLs**: Jira, Confluence, Atlassian, Slack archives (severity: medium, score: 0.4)
- **Identifier density**: UUID/hash density exceeding threshold (severity: medium, score: 0.3-0.7)
- **Deep file paths**: Path depth exceeding threshold (severity: low, score: 0.2)

Sensitivity levels (low/medium/high) control thresholds for density and path depth.

### Username Anonymization
- Detects usernames from path patterns: `/Users/<name>/`, `/home/<name>/`, `C:\Users\<name>\`
- Replaces with 8-char SHA-256 hex prefix: `hash_username(name)[:8]`
- System accounts excluded: Shared, runner, lib, admin, root, default, Public, Guest
- Two replacement strategies:
  - **Explicit usernames** (from config or system): Full pattern set including hyphen-encoded (`-Users-name-`) and tilde (`~name`)
  - **Auto-detected usernames** (from text): Path-only patterns (more conservative, avoids ambiguous forms)
- Safety limit: Auto-detection capped at 10 usernames. Exceeding triggers a warning and fallback to explicit-only.

### Redaction
- Secrets are replaced with literal `[REDACTED]` placeholder
- Replacement is applied from end-to-start (descending position) to preserve offsets
- `apply_redactions()` modifies the TraceRecord in-place and returns total redaction count
- RedactingFilter on logging prevents secrets from leaking into opentraces' own debug logs

## Calculations

- **Shannon entropy**: `-sum((freq/len) * log2(freq/len))` for each character
- **Default entropy threshold**: 4.5
- **Risk score**: `min(1.0, max(individual_scores))` across all classifier flags (max-aggregation, clamped)
- **Severity ordering**: low=0, medium=1, high=2, critical=3

## State Machines

No explicit state machine. The security pipeline is a stateless transform applied to each TraceRecord.

## Edge Cases

1. **Span deduplication**: Regex matches track `(start, end)` spans in a set. Entropy matches check for overlap with existing spans to avoid double-counting.
2. **Decorator fast-path**: If text starts with a Python decorator pattern, the entire scan is skipped (early return).
3. **Redacting log filter**: `RedactingFilter` applies secret scanning to `record.msg` and all `record.args` (both dict and tuple forms). Never suppresses records, only sanitizes.
4. **Log filter entropy disabled**: The RedactingFilter defaults `include_entropy=False` to avoid false positives in debug output.
