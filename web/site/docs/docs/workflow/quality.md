# Assess

`opentraces assess` scores trace quality against the current downstream-facing rubrics.

```bash
opentraces assess
opentraces assess --judge --judge-model sonnet
opentraces assess --dataset owner/team-traces
opentraces assess --explain
```

Local mode assesses staged traces first. If nothing is staged yet, it falls back to the local trace store so you can still inspect quality before deciding what to upload.

## Push Integration

`opentraces push` runs assessment by default and embeds the resulting scorecard into the dataset card. Use `--no-assess` when you want to skip that pass for a particular push.

## Scoring Model

Assessment is deterministic by default. The core score is computed from Python checks over the `TraceRecord` structure, without external calls or randomness.

An optional LLM judge can add qualitative scoring:

```bash
opentraces assess --judge
opentraces assess --judge --judge-model haiku
opentraces assess --judge --judge-model sonnet
opentraces assess --judge --judge-model opus
```

## Personas

Every trace is scored across five consumer-facing personas:

| Persona | What it checks |
|---------|----------------|
| Conformance | Schema correctness and structural completeness |
| Training | SFT-readiness: dialogue quality, tool-call structure, usable reasoning |
| RL | Outcome and reward-signal usefulness |
| Analytics | Metrics, timing, cost, and observability coverage |
| Domain | Metadata that makes the trace discoverable and reusable |

Run `opentraces assess --explain` for the full glossary and threshold details exposed by the CLI.

## Remote Datasets

To assess a dataset already on Hugging Face:

```bash
opentraces assess --dataset owner/team-traces
```

This is independent of the current local inbox.

## Typical Flows

```bash
opentraces add --all
opentraces assess
opentraces push
```

Or, when you want a stricter push gate:

```bash
opentraces llm-review --scope staged
opentraces push --llm-review
```
