# Trace Intelligence Eval Scorecard

This fixture is a small, synthetic failure-mode probe for the deterministic
Trace Intelligence surfaces:

- `opentraces trace get <trace-id> --waste --json`
- `opentraces trace get <trace-id> --run-intel --json`
- `opentraces trace compare <trace-a> <trace-b> --json`

It is not a statistical benchmark. The 19 labeled rows are designed to make the
detectors easy to inspect: threshold boundaries, benign prose that should not
count as failure, bare authorization that should not count as a resteer,
distinct commands that should not count as a loop, one out-of-vocabulary
recovery miss, and one `fidelity=otel` row.

## Scores

| Feature | Precision | Recall | F1 | Notes |
|---|---:|---:|---:|---|
| Context waste | 1.000 | 1.000 | 1.000 | Large output, repeated reads, repeated searches. |
| Run intelligence | 1.000 | 0.875 | 0.933 | One documented recovery-recall miss. |
| Trace compare | n/a | n/a | n/a | Expected metric deltas match exactly. |

## Residual Caveat

`adv-recover-novocab` is labeled as a real recovery, but the deterministic
detector only sees the prior failure. The recovery phrase is intentionally
outside the fixed success vocabulary. Closing that miss would require a semantic
or LLM pass, which is out of scope for this deterministic fixture.

## How To Inspect

```bash
jq '{dataset_rows, context_waste, run_intel, compare}' examples/trace-intelligence/eval/scorecard.json
head -n 2 examples/trace-intelligence/eval/run-intelligence-eval.jsonl | jq -c .
```
