# Assess

Trace quality scoring against the current downstream-facing rubrics runs inside dataset workflows. In 0.4 the standalone `opentraces assess` verb has been retired; assessment is a step in `opentraces dataset run` and the resulting scorecard is embedded into the dataset card on `opentraces dataset publish`.

## Publication Integration

`opentraces dataset publish` always carries the latest assessment for the dataset. Workflows can opt the assessment into a stricter publication gate by declaring it; rows without a clean assessment verdict will be filtered out at publish time.

## Scoring Model

Assessment is deterministic by default. The core score is computed from Python checks over each `TraceRecord` (and its dataset row projections), without external calls or randomness.

An optional LLM judge can add qualitative scoring; workflows may configure it via the bundled `setup llm-review` provider.

## Personas

Every trace is scored across five consumer-facing personas:

| Persona | What it checks |
|---------|----------------|
| Conformance | Schema correctness and structural completeness |
| Training | SFT-readiness: dialogue quality, tool-call structure, usable reasoning |
| RL | Outcome and reward-signal usefulness |
| Analytics | Metrics, timing, cost, and observability coverage |
| Domain | Metadata that makes the trace discoverable and reusable |

## Remote Datasets

To assess a dataset already on Hugging Face, the recommended path is to import its rows into a local dataset and run the workflow's assessment step against them. Direct remote assessment is no longer a standalone CLI verb.

## Typical Flows

```bash
opentraces dataset run my-dataset
opentraces dataset review my-dataset --tui
opentraces dataset publish my-dataset
```

Or, when you want a stricter publication gate, configure the workflow to require a clean Tier 2 verdict on every row before approval (see [Security Tiers](/docs/security/tiers)).
