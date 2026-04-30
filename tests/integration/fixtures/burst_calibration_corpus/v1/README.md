# Burst calibration corpus v1

Cluster H — small, hand-labeled corpus for validating burst detection heuristics
(adaptive gap, hard-split on user pivot).

Each trace under `traces/` is a single-line JSONL `TraceRecord`. The
`labels.json` file records the ground-truth burst boundaries the
detector should produce when run with the calibration settings. The
calibration test runs `detect_bursts(...)` against each trace and
checks the produced burst list matches the labels.

## Trace catalogue

| File                                       | Story                                              | Expected bursts                      |
|--------------------------------------------|----------------------------------------------------|--------------------------------------|
| `trace_001_single_burst_clean.jsonl`       | Clean rename, 8 hunks tightly packed.              | 1 burst                              |
| `trace_002_two_distinct_bursts.jsonl`      | Two unrelated tasks separated by a long gap.       | 2 bursts                             |
| `trace_003_long_burst_with_user_pivot.jsonl` | One long span with a mid-burst non-trigger pivot. | 1 burst by default, 2 with T9 on    |
| `trace_004_sparse_edits.jsonl`             | 3 edits across 200 steps.                          | 1 burst (adaptive gap widens)        |
| `trace_005_dense_loop.jsonl`               | 30 edits in 50 steps.                              | 1 burst                              |

## Why "default vs T9" matters

T9 (`hard_split_on_user_pivot`) is opt-in. Real-world traces (entry #6
in the integration regression) routinely incorporate mid-burst user
redirections into a single commit, which makes default-on T9 split
genuine single-burst sessions into many fragments. The calibration
corpus exercises both modes so callers can pick the heuristic that
matches their downstream consumer.

## Regenerating

The corpus is produced by `tests/integration/harness/burst_calibration_corpus.py`.
Re-run it after intentional label changes; commit both the trace
JSONL and the regenerated labels.json.
