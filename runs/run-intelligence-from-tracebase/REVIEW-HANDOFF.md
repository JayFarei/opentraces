# Review handoff — Trace Intelligence (feat/run-intelligence-from-tracebase)

You are reviewing a feature **in full before merging**. This document is everything you need to do an adversarial, merge-gate review. Be skeptical: confirm claims against the code, do not take this summary as ground truth.

## What the feature is

A **Trace Intelligence** layer: three deterministic, derive-on-demand detectors that read how an agent run went, extracted (as ideas, not code) from `ssreeni1/tracebase`. All read-side, no LLM, no schema change, nothing persisted; each emits a frozen JSON envelope and extends the existing `trace` CLI group.

1. **Context waste** (`core/context_waste.py`) — `trace map|get --waste` → `opentraces.context_waste.v1`. Detects oversized tool output (>=12000 chars), same file read 3+ times in 20 min, search commands (`rg|grep|find|ag|ack`) repeated 5+ times in 10 min.
2. **Run signals** (`core/run_intel.py`) — `trace map|get --run-intel` → `{status,trace_id,signals,counts}`. Deterministic `resteer`/`recovery`/`loop`/`failure` annotations.
3. **Run compare** (`core/trace_compare.py`) — `trace compare <a> <b>` → `opentraces.trace_compare.v1`. `{a,b,delta}` triples over Metrics + deterministic quality persona scores + burst/error/security signals.

## Where it is

- **Branch:** `feat/run-intelligence-from-tracebase` (worktree at `/Users/jayfarei/src/tries/community-traces-run-intel`, venv installed there). Base: `main` @ `cf4988aaf3`.
- **New files:** `src/opentraces/core/{context_waste,run_intel,trace_compare}.py`; `tests/core/test_{context_waste,trace_compare,run_intel}.py`; `tests/cli/test_{context_waste,trace_compare,run_intel}_cli.py`.
- **Modified:** `src/opentraces/cli/trace.py` (flags + `trace compare` command), `README.md`, `skill/SKILL.md`, `CLAUDE.md`, `web/site/docs/docs/cli/commands.md`, `web/site/public/llms.txt`.
- **Context (separate `closedtraces` repo, `kb/`):** scout brief `kb/br/67-tracebase-local-trace-inspection.md`, plan `kb/plans/086-run-intelligence-from-tracebase.md`.
- **Process + evidence (on `main`, `runs/`):** `GOAL.md`, `log.md`, and `eval/` (rubric scorecard + HF-shaped eval dataset).

## What was verified (re-verify; don't trust)

- **New tests:** 40 pass — `cd /Users/jayfarei/src/tries/community-traces-run-intel && source .venv/bin/activate && python -m pytest tests/core/test_context_waste.py tests/core/test_trace_compare.py tests/core/test_run_intel.py tests/cli/test_context_waste_cli.py tests/cli/test_trace_compare_cli.py tests/cli/test_run_intel_cli.py -q`.
- **Regression gate (stash-based, identical command both runs):** baseline (changes stashed) `2429 passed, 1 failed`; final (changes restored) `2466 passed, 1 failed`. The single failure, `tests/release/test_product_surface_uat_matrix.py::test_product_surface_matrix_evidence_targets_exist`, is byte-identical in both runs → pre-existing, unrelated. **Caveat to re-check:** the gate ran at the 37-new-test point; 3 further run-intel tests were added afterward during the eval-fix pass (all 40 green now). Re-run the full subset to reconfirm: `python -m pytest tests/ -q --ignore=tests/perf --ignore=tests/test_hatch_build.py --ignore=tests/otbox --ignore=tests/integration --ignore=tests/e2e`. (Full `pytest tests/ -q` hangs on a pre-existing slow `otbox`/`integration` test; pytest 9 has no timeout plugin here.)
- **No schema change:** `git diff main -- packages/opentraces-schema/` must be empty; no `SCHEMA_VERSION` bump.
- **Rubric eval** (`runs/run-intelligence-from-tracebase/eval/SCORECARD.md`, dataset `run-intelligence-eval.jsonl`): context-waste P/R 1.0/1.0; run-intel P/R 1.0/0.875 (after two fixes — see below); compare deltas exact; determinism, evidence, fidelity all pass. Re-run: `python /tmp/ri_eval.py` is gone after session; the corpus + harness logic is embedded in the eval — regenerate from `eval/` if needed, or trust the committed dataset/scorecard as the recorded result.

## Design decisions to scrutinize

- **Derive-on-demand, never persisted.** Matches the project's "schema stores raw evidence, scores derived" rule. Confirm nothing writes back to any record/index.
- **No `action_type` enum widening.** Run signals are an additive sidecar, NOT new `TraceMapNode.action_type` values (which would be a breaking schema change). Confirm `packages/opentraces-schema` is untouched.
- **Frozen envelopes** `opentraces.context_waste.v1` / `opentraces.trace_compare.v1` (and the run-intel `{status,trace_id,signals,counts}` shape). These are downstream contracts in the `opentraces.*.v1` family — a field change requires a version bump. Confirm the shapes are right BEFORE merge, because they freeze on ship.
- **`get` vs `map` parity:** `--waste`/`--run-intel` route through the same impl helpers so both surfaces emit byte-identical payloads. Confirmed by CLI tests; re-check.
- **Compare pins both traces to `DEFAULT_BURST_GAP=35`** (no adaptive gap) so burst deltas are comparable. Degrades to `available:false` (never crashes) when a trace lacks a Trace Map — this was a real bug found and fixed mid-eval (`_burst_aggregate` caught a malformed-index `DatabaseError`).
- **Two run-intel precision fixes applied after the rubric eval exposed them:** (1) loop is now ONE signal per command run carrying `evidence.repeat_count` (was N-2 signals for N repeats); (2) failure prefers structured `Observation.error`/exit-code and dropped prose-prone bare substrings (`failed`/`exception`/`error:`/`timeout`) that fired on benign text.

## Known limitations (intentional — confirm acceptable)

- **Run-intel recall ceiling:** recoveries phrased outside the 7-pattern success vocabulary (e.g. "now everything works") are missed. Inherent to keyword matching; closing it needs an embedding/LLM pass, out of scope for a deterministic detector. Documented, not patched.
- **Ported thresholds** (12000 chars, 3/20min, 5/10min, loop 3/window) are tracebase constants exposed as module constants / CLI overrides. Judge whether the defaults fit opentraces.
- **Eval is 19 synthetic rows** — directional, a failure-mode probe, not a statistical benchmark.
- **Fidelity tier** reports `otel` only when `context_tree_summary.capture_methods` includes `otel`; otherwise `record`. Real but only OTel-captured traces set it.

## Suggested review checklist (merge gate)

1. **Correctness** of each detector's algorithm vs its envelope (read the three core modules end to end).
2. **Security:** `matched_text` / `reason` / `evidence` must not leak secrets — confirm they run through `_preview` (which calls `redact_index_text`) and that no raw tool output/command lands in an envelope unredacted.
3. **Envelope contracts** are final/correct (they freeze on ship).
4. **CLI:** mutual-exclusion (`--waste`/`--run-intel`/`--bursts` → exit 2), unresolved ref → exit 6, `--json` shape, get/map parity.
5. **Determinism:** run any detector twice → byte-identical.
6. **No schema change / no `SCHEMA_VERSION` bump.**
7. **Docs accuracy:** every command/flag in README, SKILL.md, commands.md, llms.txt exists in `cli/trace.py`.
8. **Regression:** the bounded subset is green and the one failure is the pre-existing unrelated one.

## How to run a deep review

`/code-review ultra` on this branch is the intended path (cloud multi-agent). It bundles the local branch and does not need a GitHub remote. For a local pass: read the three core modules, run the 6 test files, then run the bounded-subset regression command above.

## Open questions for the reviewer

- Are the frozen envelope shapes right to freeze now, or should any field be added/renamed before v1 ships?
- Are the ported thresholds the right defaults for opentraces, or should they be tuned?
- Is the keyword-recovery recall ceiling acceptable for v1, or is a follow-up (embedding/LLM recovery detection) wanted before merge?
