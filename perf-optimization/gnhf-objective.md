# Objective: Reduce Representative Latency And Memory Without Regressions

You are optimizing `opentraces.ai`, a Python CLI + watcher + Textual TUI + Flask web backend + React viewer. Your job is to make one small, measurable performance improvement per successful iteration while preserving behavior, correctness, and representative workload coverage.

Read these files at the start of each iteration:

- `tests/perf/BASELINE.md`
- `tests/perf/journeys.toml`
- `perf-optimization/eval-rubric.md`
- `perf-optimization/runbook.md`

Treat `tests/perf/BASELINE.md` as the authoritative starting baseline. The current primary hotspots are:

- `tui-review-actions-smoke`
- `tui-navigation-smoke`
- `viewer-review-view-smoke`
- `viewer-graph-view-smoke`
- `web-refresh-smoke`
- `scan-project-smoke`
- `graph-cli-smoke`
- `assess-local-smoke`
- `watcher-active-smoke`

Optimization priorities, in order:

1. Reduce visible TUI interaction latency and TUI memory growth.
2. Reduce web viewer initial render cost and JS heap growth.
3. Reduce refresh / scan / watcher active tick cost.
4. Reduce heavy provenance CLI costs such as graph and assess.
5. Preserve or improve all other profiled journeys; do not "win" by shifting cost to another representative path.

Hard constraints:

- No budget widening in `tests/perf/budgets.toml`.
- No deleting, weakening, or bypassing representative perf scenarios.
- No shrinking fixtures or data volumes just to improve numbers.
- No disabling `tests/perf`, `tmux`, or `agent-browser` regression smokes.
- No behavior regressions, correctness regressions, or silently reduced functionality.

For each iteration:

1. Pick the next smallest change that can plausibly improve one primary target.
2. Measure before/after with the shortest relevant validation path first.
3. If the change affects a representative surface, run the full perf smoke lane before declaring success.
4. Record the exact scenarios improved or regressed, with real numbers.
5. If the change does not produce a defensible improvement, revert it or mark the iteration unsuccessful.

Preferred kinds of improvements:

- eliminating repeated filesystem, graph, or trace-tree work
- caching or memoization with clear invalidation and measured wins
- reducing redundant re-renders or recomputation in TUI/web/viewer flows
- moving expensive work off the hot path
- batching, lazy loading, or narrowing work to the active selection
- replacing quadratic or repeated traversal patterns with bounded work

Avoid:

- broad speculative rewrites without measurement
- "cleanup" changes that do not move a representative metric
- cosmetic changes, docs-only changes, or refactors that do not advance the objective
- instrumentation churn unless it directly unlocks the next optimization step

A successful iteration should usually satisfy all of these:

- at least one primary metric improves materially
- no primary metric regresses
- no non-targeted representative metric regresses meaningfully
- the relevant validation commands pass

Program-level success means:

- weighted latency score improves by at least 40% across the primary latency set
- weighted memory score improves by at least 25% across the primary memory set
- `pytest -q tests/perf --perf-lane smoke` is green
- `OT_TUI_SMOKE=1 OT_WEB_SMOKE=1 ./.venv/bin/pytest -q tests/e2e/test_tui_tmux_smoke.py tests/e2e/test_web_agent_browser_smoke.py` is green

Stop early if the program-level success condition is met, or if the remaining plausible changes are likely to trade away correctness or representativeness for marginal gains.
