# Trace Intelligence Eval

## Task

Inspect a synthetic labeled evaluation packet for the Trace Intelligence
detectors: context waste, run-intelligence signals, and trace comparison.

## Inputs

- `run-intelligence-eval.jsonl` - 19 synthetic labeled rows.
- `scorecard.json` - machine-readable aggregate scores.
- `SCORECARD.md` - human-readable scorecard and residual caveats.

## Run

Inspect the committed public fixture:

```bash
jq '{dataset_rows, context_waste, run_intel, compare}' examples/trace-intelligence/eval/scorecard.json
wc -l examples/trace-intelligence/eval/run-intelligence-eval.jsonl
head -n 2 examples/trace-intelligence/eval/run-intelligence-eval.jsonl | jq -c '{trace_id,note,gt_waste,pred_waste,signals_correct,waste_correct}'
```

Equivalent live surfaces, when you have retained traces:

```bash
opentraces trace get <trace-id> --waste --json
opentraces trace get <trace-id> --run-intel --json
opentraces trace compare <trace-a> <trace-b> --json
```

## Expected Output

The packet documents context-waste F1 of `1.0` and run-intelligence F1 of
`0.933`, with one intentionally documented recovery-recall miss
(`adv-recover-novocab`).
