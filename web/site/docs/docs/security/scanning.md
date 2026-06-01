# Scanning & Redaction

Scanning happens only when a caller opts into tools. The public entrypoints are:

- Python: `opentraces.security.sanitize_record(record, tools=[...])`
- Python config mode: `sanitize_record(record, cfg=cfg)`
- CLI: `opentraces security sanitize --tools ...`
- CLI config mode: `opentraces security sanitize --use-config`

Callers must pass either an explicit tool list or a config. Config mode runs
only tools whose `cfg.security.<tool>.enabled` flag is true.

## Tool Kinds

| Kind | Examples | Behavior |
|------|----------|----------|
| Detector | `regex`, `entropy`, `trufflehog`, `privacy_filter`, `llm_pii`, `business_logic` | Emits redactable spans |
| Transformer | `path_anonymizer`, `capsule_scope` | Rewrites the record without span findings |
| Judge | `classifier` | Emits a verdict without mutating content |

## Field Context

Detector tools receive a field-type hint so they can be stricter on inputs and
less noisy on tool output:

| Field type | Typical use |
|------------|-------------|
| `tool_input` | shell commands, file writes, API payloads |
| `tool_result` | command output and observations |
| `reasoning` | agent reasoning text |
| `general` | prompts, summaries, snippets, row text |

CLI example:

```bash
printf '%s\n' '{"text":"curl -H Authorization: Bearer sk-demo"}' \
  | opentraces security sanitize --tools regex --field-type tool_input
```

## Patch And Bucket Evidence

Schema `0.6.0` removed `Outcome.patch`. A workflow that needs to sanitize
patch content should read from `TraceRecord.patches[]` and the bucket Trail
companion (`trail.jsonl.gz`) instead of expecting a single unified diff field.

Raw bucket evidence is retained by default. Sanitized dataset rows are a
workflow projection over that evidence; they do not rewrite the original agent
transcript or the raw capture bucket unless the workflow explicitly writes a
new sanitized artifact.

## Redaction Shape

Detectors replace matched spans with redaction markers. The exact marker can
vary by tool and field:

```text
Before: export OPENAI_API_KEY=sk-abc123...
After:  export OPENAI_API_KEY=[REDACTED]
```

Path anonymization is a transformer:

```text
Before: /Users/alice/src/client-project/
After:  /Users/[REDACTED]/src/client-project/
```

## Custom Strings

Custom redaction strings can be configured for workflows that use config mode:

```bash
opentraces config set custom_redact_strings INTERNAL_API_KEY --append
opentraces config set custom_redact_strings corp-secret-prefix- --append
```

Custom strings are literal matches wherever they appear in scanned content.
