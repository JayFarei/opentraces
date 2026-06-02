# Run-intelligence rubric evaluation

Corpus: 19 labeled traces written into a real OT project bucket, indexed via `trace query --force-rebuild`, scored by invoking the actual CLI (`trace get --waste`, `trace get --run-intel`, `trace compare`). Dataset artifact: `run-intelligence-eval.jsonl`. Includes adversarial cases (threshold boundaries, authorization-not-resteer, "failed" in prose, distinct-command non-loop, out-of-vocab recovery).

Caveat: 19 synthetic rows is directional, not statistical — a failure-mode probe, not a benchmark.

## Scores (0-5)

| Feature | Dimension | Before fixes | After fixes |
|---|---|---|---|
| Context-waste | Precision | 5 (1.00) | 5 (1.00) |
| Context-waste | Recall | 5 (1.00) | 5 (1.00) |
| Run-intel | Precision | 1 (0.455) | **5 (1.00)** |
| Run-intel | Recall | 3 (0.833) | **4 (0.875)** |
| Trace compare | Delta correctness | 5 | 5 |
| Cross-cutting | Determinism | 5 | 5 |
| Cross-cutting | Evidence/actionability | 5 | 5 |
| Cross-cutting | Fidelity-awareness | 5 | 5 |

Run-intel F1: 0.588 -> **0.933**.

## The two fixes

1. **Loop signal: cluster-then-emit-once.** Was emitting one signal per repeat past the 3rd (5 greps -> 3 loop signals). Now segments each command fingerprint's occurrences into windowed runs and emits ONE signal per run, anchored at the occurrence that crossed the threshold, carrying `evidence: {repeat_count, first_step, last_step}`. (`core/run_intel.py::_loop_signals`.)

2. **Failure precision: structured-first, restricted text fallback.** Now prefers `Observation.error` (authoritative), then `is_error:true` / `exit code [1-9]`, and the text fallback is restricted to failure-SPECIFIC phrases (`command failed`, `traceback`, `N failed`, `fatal:`, `segmentation fault`, `build/test failed`). Dropped the prose-prone bare words `failed` / `exception` / `error:` / `timeout` that fired on benign text. Eliminated the "the legacy approach failed to scale" false positive.

## Residual (intentionally not chased)

- `adv-recover-novocab`: a real recovery phrased "now everything works fine" is missed — it is not in the 7-pattern success vocabulary. This is the inherent recall ceiling of keyword matching; closing it needs an embedding/LLM pass, which is out of scope for a deterministic detector. Documented, not patched.

Pinned by 3 new unit tests: `test_loop_emits_one_signal_with_repeat_count`, `test_structured_observation_error_is_failure`, `test_benign_failed_in_prose_is_not_a_failure` (40 new tests total, all green).
