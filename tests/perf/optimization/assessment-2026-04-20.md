# Performance Branch Assessment

Date: 2026-04-20
Branch assessed: `gnhf/objective-reduce-rep-9c3113`
Conclusion: do not merge as-is

## Summary

The branch contains real performance wins on representative paths, but the end state is not strong enough to justify merge. It still misses the campaign's own success threshold, carries at least one representative regression, and introduces scan-scope correctness risks in the ingest path.

## Material Wins

- `scan-project-smoke`: `254.90 ms -> 143.57 ms` (`-43.7%`)
- `web-refresh-smoke`: `257.80 ms -> 146.60 ms` (`-43.1%`)
- `graph-cli-smoke`: `259.37 ms -> 179.43 ms` (`-30.8%`)
- `web-graph-smoke`: `89.74 ms -> 24.64 ms` (`-72.5%`)
- `viewer-review-view-smoke` peak heap: `143.84 MB -> 127.28 MB` (`-11.5%`)
- `tui-review-actions-smoke`: `1505.88 ms -> 1343.71 ms` (`-10.8%`)

These gains line up with the `capture-refresh`, `provenance-graph-blame`, and `web-review` journeys in `tests/perf/journeys.toml`.

## Reasons Not To Merge

### 1. End-state score is still below the program bar

Using the current end-state artifact against the rubric in `tests/perf/optimization/eval-rubric.md`, the branch lands at roughly:

- Weighted latency score: `0.15`
- Weighted memory score: `0.17`

The documented success thresholds are `>= 0.40` latency and `>= 0.25` memory.

### 2. Representative regressions remain

- `assess-local-smoke`: `269.37 ms -> 292.13 ms` (`+8.5%`)
- `tui-navigation-smoke`: `782.63 ms -> 792.36 ms` (`+1.2%`)

That means the branch is not a clean net improvement even by its own evaluation contract.

### 3. Ingest correctness became more fragile

The sweep-scoped sharing added for performance introduces behavior risk:

- `StateManager.reload_if_changed()` does not clear in-memory state when `state.json` disappears, so stale state can survive a mid-scan reset and be written back later.
- `scan_project()` now snapshots `review_policy` once per sweep, so policy changes during the scan are ignored until the next sweep.
- `GitSignalsCache` caches VCS metadata for the whole sweep, so later sessions can inherit stale `base_commit`, `branch`, or `diff`.

Relevant files:

- `src/opentraces/core/state.py`
- `src/opentraces/core/ingest.py`
- `src/opentraces/enrichment/git_signals.py`

### 4. The perf rollup artifact is not fully objective

`tests/perf/artifacts/latest/summary.json` is incrementally mutated rather than rebuilt from a clean directory each run. That means stale entries can survive across runs and weaken any branch-level rollup conclusions unless the artifacts directory is cleaned first.

Relevant files:

- `tests/perf/harness/measure.py`
- `tests/perf/conftest.py`

## Harness Caveat

The RSS polling change in `tests/perf/harness/measure.py` improves harness runtime substantially, but it is a harness improvement, not a product win. It should not be counted as a user-facing performance gain.

## Validation Run

Commands run during assessment:

```bash
pytest -q tests/perf --perf-lane smoke
pytest -q tests/core/test_ingest.py tests/enrichment/test_diff_line_count.py tests/enrichment/test_enrichment.py tests/tui/test_tui_layout.py
OT_TUI_SMOKE=1 OT_WEB_SMOKE=1 pytest -q tests/e2e/test_tui_tmux_smoke.py tests/e2e/test_web_agent_browser_smoke.py
```

Observed results:

- `tests/perf` smoke: passed
- targeted correctness tests: passed except `tests/tui/test_tui_layout.py::test_snapshot_initial_layout`
- user smokes: passed

## Recommended Next Step

If this work is revisited, fix the scan-scope correctness issues first, then rerun the perf harness from a clean artifacts directory before making another merge decision.
