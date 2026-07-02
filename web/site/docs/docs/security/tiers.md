# Security Tools

Security in opentraces is a flat optional tool registry. Tools are not enabled
by default for per-record sanitization. A bucket flow or workflow opts in to
the tools it wants, either explicitly with `--tools` or by enabling tools in
config and running with `--use-config`.

Current pipeline version: `SECURITY_VERSION = 0.7.0`.

```bash
opentraces security tools list
opentraces security tools info regex --json
opentraces doctor --security
```

## Registered Tools

| Tool | Kind | Default | Notes |
|------|------|---------|-------|
| `regex` | detector | off | Built-in token/key patterns |
| `entropy` | detector | off | High-entropy secret-like strings |
| `trufflehog` | detector | off | Optional deep secret detector, configured by `opentraces setup trufflehog` |
| `privacy_filter` | detector | off | Optional `openai/privacy-filter` NER detector, configured by `opentraces setup privacy-filter` |
| `llm_pii` | detector | off | Advanced per-field LLM PII detector; configure `security.llm_pii` directly before enabling |
| `business_logic` | detector | off | Promotes internal-hostname / internal-url / db-connection-string / aws-account-id heuristics to redactable spans; always-on inside the capsule REDACTION_FLOOR |
| `path_anonymizer` | transformer | off | Rewrites usernames in filesystem paths |
| `capsule_scope` | transformer | off | Applies field-path exclusion of prompt-bearing fields (the "this never leaves" guarantee); default-disabled in normal ingest |
| `classifier` | judge | off | Heuristic sensitivity verdict without mutating the record |

Canonical order is deterministic:

```text
regex -> entropy -> trufflehog -> privacy_filter -> llm_pii -> business_logic -> path_anonymizer -> capsule_scope -> classifier
```

Explicit `--tools` requests are sorted into that order so a judge never sees
pre-redaction text because of caller ordering.

## Running Sanitization

`security sanitize` reads JSON from stdin and requires one selection mode:

```bash
printf '%s\n' '{"text":"OPENAI_API_KEY=sk-demo"}' \
  | opentraces security sanitize --tools regex

printf '%s\n' '{"row":{"path":"/Users/alice/project"}}' \
  | opentraces security sanitize --tools path_anonymizer

printf '%s\n' '{"record":{...}}' \
  | opentraces security sanitize --use-config
```

Payload shapes:

| Shape | Behavior |
|-------|----------|
| `{"text": "..."}` | Runs detector tools over one string and returns sanitized text + findings |
| `{"row": {...}}` | Runs detector tools over string leaves in a JSON row |
| `{"record": {...TraceRecord...}}` | Runs the full record path and stamps metadata |

## Metadata Contract

Sanitized records get a compact tool report under `metadata.security`:

```json
{
  "metadata": {
    "security": {
      "tools_applied": ["regex", "entropy"],
      "tools": {
        "regex": { "findings": 1 },
        "entropy": { "findings": 0 }
      }
    }
  }
}
```

If no tools run, `tools_applied` is `[]` and stale per-tool metadata is
cleared.

## Setup Commands

```bash
opentraces setup trufflehog
opentraces setup trufflehog --enable
opentraces setup trufflehog --disable
opentraces setup privacy-filter --enable --install-deps
opentraces setup privacy-filter --disable
```

LLM-backed row review is intentionally separate from the inline tool registry.
It is a session or row reviewer used by dataset publication gates, not a
per-record sanitize tool, and it now lives on the dataset surface itself:
`opentraces dataset review <name>` (plus `dataset review approve|reject|reset`)
and `opentraces dataset publish <name>`. The old `setup llm-review` command is
hidden but still callable.

## Bucket Security Policies

`bucket security` is a scoped front-end that bundles the registry tools above
into a few named policies for the private bucket. `security tools list|info` and
`security sanitize` stay the generic registry surface; `bucket security` just
applies a named bundle of the same `cfg.security.<tool>.enabled` flags.

```bash
opentraces bucket security status
opentraces bucket security policy --policy recommended
opentraces bucket security policy --tool regex --enable
opentraces bucket security policy --tool entropy --disable
```

| Policy | Tools |
|--------|-------|
| `off` | (nothing) |
| `basic` | regex, entropy |
| `recommended` | regex, entropy, business_logic, path_anonymizer, classifier |
| `strict` | regex, entropy, trufflehog, privacy_filter, business_logic, path_anonymizer, classifier |

`bucket security status` inspects the active policy and tools. `bucket security
policy --policy` accepts only `off|basic|recommended|strict`. This bucket policy
vocabulary is unrelated to the `dataset run --privacy-tier off|low|medium|high`
field.

## Review And Publication

Dataset review remains explicit:

```bash
opentraces dataset review my-dataset --json
opentraces dataset review approve my-dataset <row-id>
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset
```

Rows that have not been approved are not published. Workflows that require
additional privacy checks should run `security sanitize` or configure
`llm-review` before approval.
