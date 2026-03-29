# Scanning & Redaction

The security pipeline is context-aware and runs in two passes:

1. Scan the trace record field-by-field using the field type to decide whether entropy analysis is enabled.
2. Scan the final serialized JSONL bytes to catch anything introduced during enrichment or serialization.

## What Gets Scanned

| Field | Context | Notes |
|-------|---------|-------|
| `system_prompts` | General | Full scan |
| `task.description` | General | Full scan |
| `steps[].content` | General | Full scan |
| `steps[].reasoning_content` | Reasoning | Regex only, no entropy |
| `steps[].tool_calls[].input` | Tool input | Full scan for input-like tools, regex-only for result-like tools |
| `steps[].observations[].content` | Tool result | Regex only, no entropy |
| `steps[].observations[].output_summary` | Tool result | Regex only, no entropy |
| `steps[].observations[].error` | Tool result | Regex only, no entropy |
| `steps[].snippets[].text` | General | Full scan |
| `outcome.patch` | General | Full scan |
| `environment.vcs.diff` | General | Full scan, truncated before storage when the repo diff is very large |

The scanner also applies a second pass over the serialized JSONL output so redaction does not depend on field shape alone.

## What Gets Redacted

Detected secrets and path fragments are replaced with `[REDACTED]` or hashed path segments, depending on the detector:

```text
Before: export OPENAI_API_KEY=sk-abc123...
After:  export OPENAI_API_KEY=[REDACTED]
```

```text
Before: /Users/jay/src/project/...
After:  /Users/[REDACTED]/src/project/...
```

The staged JSONL is rewritten in place. Raw session files on disk are not modified.

## Tier 2 Classifier

Tier 2 adds a heuristic classifier on top of scanning and redaction. It flags:

- internal hostnames
- AWS account IDs in ARNs
- database connection strings
- internal collaboration URLs
- dense UUID / hash sequences
- deep file paths that may reveal internal structure

## Custom Redaction

```bash
opentraces config set --redact "INTERNAL_API_KEY"
opentraces config set --redact "corp-secret-prefix-"
```

Custom redaction strings are treated as literal matches wherever they appear in trace content.
