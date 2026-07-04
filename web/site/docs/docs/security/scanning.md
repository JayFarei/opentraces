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

## Companion Sanitization (ctx / trail)

The Context Tree (`ContextLayer` / `context_resume_packet`) and Trace Trail
(`TrailEvent`) substrate shapes are inlined into capsules and, when a workflow
reads them, into dataset rows. `companion_field_type(path)` classifies each
inlined ctx/trail leaf onto the existing four-member field-type taxonomy (plus
an `is_path_leaf` flag), so a `cwd` leaf gets path-anonymized, a chat message
gets routed as NER-relevant prose, and a tool-registry description is treated
as tool input, instead of the whole substrate falling into one generic
`general` bucket. `sanitize_companion_dict` (a sibling of the record-level
`sanitize_dict`) is the walker that applies this per-leaf. The default
companion floor mirrors the dataset row floor: `regex`, `entropy`,
`business_logic`, plus `path_anonymizer` running in its tail-consuming mode
over every string leaf.

## Dataset Required Tools And Provenance

A dataset carries a resolved security policy in its manifest, seeded from its
workflow's `security:` contract. The policy's required tools must run for a row
to be publishable: `opentraces dataset publish --check-only` blocks any row that
does not satisfy them (block reason `required_security_tools_missing`).

Row provenance records the policy tools applied per append (a `security_policy`
block on the row), and `opentraces dataset run` exposes the resolved policy in
the run packet (`run_packet.json` has a `security` block) so the executor knows
which tools are required and enabled. Inspect or adjust a dataset's policy with
`opentraces dataset security <name>`.

## Redaction Shape

Detectors replace matched spans with redaction markers. The exact marker can
vary by tool and field:

```text
Before: export OPENAI_API_KEY=sk-abc123...
After:  export OPENAI_API_KEY=[REDACTED]
```

Path anonymization is a transformer. The default (record-level) mode hashes
just the username segment to an unambiguous, idempotent `[ot-user-<8hex>]`
marker:

```text
Before: /Users/alice/src/client-project/
After:  /Users/[ot-user-3f2a9c1d]/src/client-project/
```

A tail-consuming mode (`consume_tail=True`, used by capsules and the ctx/trail
companion sanitizer) collapses the whole home-path tail instead of just the
username, so no downstream path structure leaks:

```text
Before: /Users/alice/secret/.env
After:  /Users/[ot-user-3f2a9c1d]
```

The marker's leading `[` cannot start a detected username, so a second pass
over already-anonymized text is a no-op.

## Custom Strings

Custom redaction strings can be configured for workflows that use config mode:

```bash
opentraces config set custom_redact_strings INTERNAL_API_KEY --append
opentraces config set custom_redact_strings corp-secret-prefix- --append
```

Custom strings are literal matches wherever they appear in scanned content.
