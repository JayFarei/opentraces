# Eval Rubric

This rubric is the decision contract for the 30-iteration `gnhf` optimization run.

## Evaluation Principles

- Optimize representative user journeys, not isolated helper functions.
- Use the measurement model already established by the harness.
- Do not accept gains that come from weakening tests, shrinking fixtures, or reducing functionality.
- Prefer small, attributable wins over large speculative rewrites.

## Metric Model

Use the right metric for the right surface.

| Surface | Primary Metric | Secondary Metric | Notes |
|---|---|---|---|
| In-process Python perf scenarios | `p95_ms` | `python_heap_peak_mb`, `rss_delta_mb` | Ignore absolute peak RSS as a primary signal here; it is too process-shared to be objective. |
| Subprocess CLI scenarios | `p95_ms` | `peak_rss_mb` | These are isolated child processes, so RSS is meaningful. |
| Viewer scenarios | `initial_p95_ms` and `update_p95_ms` | `peak_heap_used_mb`, `retained_heap_mb` | Treat initial render as the user-facing priority. |
| TUI scenarios | `p95_ms` | `python_heap_peak_mb`, `rss_delta_mb` | TUI interaction smoothness is the first-line outcome. |

## Primary Scoring Set

### Latency

| Scenario | Weight | Baseline |
|---|---:|---:|
| `tui-review-actions-smoke` | 5 | `1505.88 ms` |
| `tui-navigation-smoke` | 4 | `782.63 ms` |
| `viewer-review-view-smoke` initial render | 4 | `283.10 ms` |
| `web-refresh-smoke` | 3 | `257.80 ms` |
| `scan-project-smoke` | 3 | `254.90 ms` |
| `graph-cli-smoke` | 3 | `259.37 ms` |
| `assess-local-smoke` | 3 | `269.37 ms` |
| `watcher-active-smoke` | 2 | `233.55 ms` |
| `viewer-graph-view-smoke` initial render | 2 | `146.41 ms` |

### Memory

| Scenario | Weight | Baseline |
|---|---:|---:|
| `viewer-review-view-smoke` peak heap | 5 | `143.84 MB` |
| `tui-review-actions-smoke` Python heap | 4 | `36.73 MB` |
| `tui-navigation-smoke` Python heap | 3 | `22.92 MB` |
| `viewer-graph-view-smoke` peak heap | 3 | `95.15 MB` |
| `tui-startup-smoke` Python heap | 2 | `13.34 MB` |
| `assess-local-smoke` peak RSS | 2 | `65.45 MB` |
| `graph-cli-smoke` peak RSS | 1 | `42.58 MB` |

## Score Formula

For each metric in the primary set:

`improvement = (baseline - candidate) / baseline`

Weighted program score:

`weighted_score = sum(weight * improvement) / sum(weight)`

Interpretation:

- `0.00` means no net change from baseline.
- `0.10` means a weighted 10% improvement.
- `-0.05` means a weighted 5% regression.

## Success Thresholds

### Per iteration

Count an iteration as a keepable optimization only if:

- it improves at least one primary metric by `>= 5%`, and
- it does not regress any other primary metric by more than `2%`, and
- all relevant validation commands pass.

If an iteration is measurement-only or cleanup-only, it should normally be marked unsuccessful unless it directly unlocks the next optimization step and does not worsen metrics.

### Program level

The full 30-iteration campaign is a success when all of these are true:

- weighted latency score is `>= 0.40`
- weighted memory score is `>= 0.25`
- no primary scenario is slower or heavier than baseline at the end state
- no non-targeted representative scenario regresses by more than `5%`
- perf smoke and user smokes are green

## Guardrails

Every accepted optimization must preserve these:

1. `./.venv/bin/pytest -q tests/perf --perf-lane smoke`
2. `OT_TUI_SMOKE=1 OT_WEB_SMOKE=1 ./.venv/bin/pytest -q tests/e2e/test_tui_tmux_smoke.py tests/e2e/test_web_agent_browser_smoke.py`
3. Focused correctness tests for the touched surface

Disallowed shortcuts:

- updating `tests/perf/budgets.toml` to hide regressions
- changing perf fixtures so they no longer represent the documented journeys
- deleting assertions or coverage to make the loop pass
- accepting "maybe faster" changes without measured before/after evidence

## Stop Conditions

Stop the `gnhf` run early if any of these become true:

- the program-level success thresholds are already met
- three consecutive attempts fail to produce a keepable improvement
- remaining ideas require budget widening, fixture weakening, or functionality tradeoffs
- the perf harness or user smokes are unstable for environmental reasons and the run is no longer producing objective signal
