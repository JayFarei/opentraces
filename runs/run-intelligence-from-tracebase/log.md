# Run log — run-intelligence-from-tracebase

Worktree: `/Users/jayfarei/src/tries/community-traces-run-intel` on `feat/run-intelligence-from-tracebase`.

## Attempt 1 — 2026-05-29

### Baseline (pre-edit, worktree HEAD `633106dd1c`)
`pytest tests/ -q` does NOT cleanly collect: **4 pre-existing collection errors** unrelated to this work — `tests/test_hatch_build.py` (`ModuleNotFoundError: hatchling`) and `tests/perf/{test_core_perf,test_publish_perf,test_watcher_perf}.py` (missing perf deps). These fail at import on HEAD before any edit. Baseline is therefore established over the collectable suite with `--ignore=tests/perf --ignore=tests/test_hatch_build.py`. (Baseline count recorded below once the run completes.)

### Implemented (diff summary)
- `src/opentraces/core/context_waste.py` [new] — `detect_context_waste()`; large_output>=12000 (0.8), repeated_file_read>=3/20min (0.78), repeated_search rg|grep|find|ag|ack>=5/10min (0.7); `opentraces.context_waste.v1` envelope; `fidelity` from `context_tree_summary.capture_methods` (otel|record); missing-timestamp limitation.
- `src/opentraces/core/run_intel.py` [new] — `detect_run_signals()`; resteer (is_trigger-suppressed)/recovery (gated on prior failure)/loop (3rd repeat in step/time window)/failure; additive-only, no `action_type` widening, no signal_refs persistence; `{status,trace_id,signals,counts}` envelope.
- `src/opentraces/core/trace_compare.py` [new] — `compare_traces()`; `{a,b,delta}` triples over Metrics (reuse `compute_metrics`) + quality (`assess_trace(enable_judge=False)`, `persona_scores.get()`) + burst/error/security; both traces pinned to `DEFAULT_BURST_GAP=35`; `opentraces.trace_compare.v1`.
- `src/opentraces/cli/trace.py` — `--waste`/`--run-intel` flags + `_trace_get_waste_impl`/`_trace_get_run_intel_impl` on `trace get`; same flags routed to the same helpers on `trace map` (byte-identical parity); new `trace compare <a> <b>` command (exit 6 on unresolved, `--no-quality`). Mutual-exclusion → exit 2.
- `CLAUDE.md` — trace-surface bullet extended.
- 6 new test files (tests/core/ + tests/cli/).

### Bug found + fixed (evidence-driven)
While capturing a real `trace compare` envelope in the worktree cwd, `core.trace_index.get_trace_map` raised `sqlite3.DatabaseError: database disk image is malformed` (a real but corrupt local index). `_burst_aggregate` only handled `None`, so the whole compare crashed. Fixed: wrapped `get_trace_map`/`detect_bursts` in `try/except Exception → return None`, honoring the vet's "degrade-never-crash" requirement (burst block → `available:false`, zero deltas). Re-captured envelope cleanly.

### Evidence observed
- New-test run: `37 passed` (tests/core/test_context_waste.py 12, test_trace_compare.py 7, test_run_intel.py 10; tests/cli/ context_waste 4, trace_compare 3, run_intel 2 — counts include parity + determinism + mutual-exclusion + exit-6).
- Real envelopes captured for `--run-intel` (failure+resteer+recovery, counts match signal count), `--waste` with **`fidelity: "otel"`** (synthesized via `context_tree_summary.capture_methods=["otel"]`), and `trace compare` (`total_input_tokens` delta -600, `quality:null` under `--no-quality`). R7 otel branch exercised deterministically without live capture.
- get-vs-map parity asserted byte-identical in CLI tests; determinism (run twice) asserted in both core and CLI tests.
- No-schema-change: `git status --porcelain packages/opentraces-schema/` is empty; changed files limited to CLAUDE.md, cli/trace.py, 3 new core modules, 6 new test files.

### Regression gate — PASS (stash-based, identical command both runs)
The full `pytest tests/ -q` hangs on a slow/env-gated test under `tests/integration`/`tests/otbox` (pytest 9, no timeout plugin), so the objective gate ran over the collectable subset `--ignore=tests/perf --ignore=tests/test_hatch_build.py --ignore=tests/otbox --ignore=tests/integration --ignore=tests/e2e` (2479 collected). Baseline was taken by `git stash push -u` (true pre-edit state), final after `git stash pop`:

- **Baseline (pre-edit):** `1 failed, 2429 passed, 14 skipped, 2 xfailed` (617.90s)
- **Final (with feature):** `1 failed, 2466 passed, 14 skipped, 2 xfailed` (737.05s)
- Gate: final passed `2466` >= baseline passed `2429`. Delta `+37` = exactly the new tests. **No regression.**
- The single failure, `tests/release/test_product_surface_uat_matrix.py::test_product_surface_matrix_evidence_targets_exist`, is byte-identical in both runs (fails with the feature stashed away) → pre-existing, unrelated to this change.

### Docs propagation (/docs-update, additive CLI surface)
- `README.md` — Concepts table row + new "### Run intelligence" subsection under `## Trace`.
- `skill/SKILL.md` — top-line command list, the Trace Retrieval bash block, and a new "### Run intelligence" subsection.
- `web/site/docs/docs/cli/commands.md` — command table row (`trace compare`) + new "## Run Intelligence" section.
- `CLAUDE.md` — trace-surface bullet (done earlier).
- `web/site/public/llms.txt` — regenerated via `generate-llms-txt.sh` (run-intel content present).
- Marketing site components (Hero/Features/etc.) intentionally NOT touched: they don't enumerate trace-analysis subcommands; the `kb/web/hub` visualiser is the right surface for these and is handled via a separate designer prompt.

### Status: COMPLETE. All of R1-R7 met; gate green; docs propagated.

## Attempt 2 — 2026-05-29 — rubric eval + run-intel fixes

Built a 19-trace labeled corpus through OT (real project bucket + index + CLI), emitted an HF-shaped eval dataset (`eval/run-intelligence-eval.jsonl`), scored precision/recall + a rubric (`eval/SCORECARD.md`, `eval/scorecard.json`). The eval exposed two run-intel weaknesses, both fixed in `core/run_intel.py`:

1. **Loop multiplicity** — emitted N-2 signals for N repeats. Now clusters occurrences into windowed runs and emits ONE signal per run with `evidence.repeat_count` (`_loop_signals`).
2. **Failure precision** — loose substrings (`failed`/`exception`/`error:`/`timeout`) fired on benign prose. Now structured-first (`Observation.error`, `is_error`, exit code) with a restricted failure-specific text fallback.

Result: run-intel precision 0.455 -> 1.0, recall 0.833 -> 0.875, F1 0.588 -> 0.933. Context-waste and compare unchanged at 1.0 / all-correct. Cross-cutting (determinism, evidence, fidelity, compare) all 5/5. Residual: one out-of-vocabulary recovery FN, the inherent keyword ceiling — documented, not chased. +3 unit tests pin the fixes (40 new tests total, all green). Changes stay additive / deterministic / no-schema-change; the `evidence` key was added to the run-intel signal shape pre-ship (no migration).
