# Performance Baseline

Generated from `tests/perf/artifacts/latest/summary.json`.

## Profiled Journeys

| Journey | Priority | Perf Scenarios | User Smoke | Live |
|---|---:|---:|---:|---:|
| onboarding-terminal | primary | 1 | 0 | 0 |
| capture-refresh | primary | 3 | 0 | 0 |
| cli-review-batch | primary | 3 | 0 | 0 |
| provenance-graph-blame | primary | 4 | 0 | 0 |

## Scenario Baselines

| Scenario | Journey(s) | Domain | Target | p50 ms | p95 ms | Cold ms | Peak RSS MB | Py Heap MB | RSS Delta MB | Budget p95 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| blame-cli-smoke | provenance-graph-blame | core | cli.blame | 236.56 | 239.25 | 235.85 | 46.75 | - | - | 400.00 |
| bucket-status-smoke | cli-review-batch | cli | cli.bucket_status | 173.07 | 173.83 | 171.88 | 46.89 | - | - | - |
| graph-cli-smoke | provenance-graph-blame | core | cli.graph | 289.39 | 292.33 | 292.33 | 47.06 | - | - | 500.00 |
| graph-core-smoke | provenance-graph-blame | core | graph.load | 73.95 | 75.06 | 75.06 | 91.73 | 0.56 | 0.08 | 300.00 |
| inverse-blame-smoke | provenance-graph-blame | core | core.inverse_blame | 25.00 | 25.62 | 25.10 | 94.09 | 0.82 | 0.34 | 250.00 |
| scan-project-smoke | capture-refresh | watcher | ingest.scan_project | 248.27 | 254.90 | 254.90 | 190.22 | 0.58 | 0.05 | 500.00 |
| status-smoke | cli-review-batch, onboarding-terminal | cli | cli.status | 184.32 | 187.80 | 184.24 | 50.47 | - | - | 250.00 |
| trace-query-smoke | cli-review-batch | cli | cli.trace_query | 404.65 | 410.28 | 404.65 | 58.97 | - | - | 600.00 |
| viewer-flatten-tree-smoke | viewer-rendering | viewer | viewer.flattenTree | 0.16 | 0.17 | 0.17 | 278.86 | - | 0.02 | - |
| viewer-graph-view-smoke | viewer-rendering | viewer | viewer.GraphView | 14.39 | 14.92 | 146.41 | 374.66 | - | 0.05 | - |
| viewer-review-view-smoke | viewer-rendering | viewer | viewer.ReviewView | 47.72 | 50.01 | 283.10 | 409.11 | - | 1.30 | - |
| watcher-active-smoke | capture-refresh | watcher | watcher.run_once | 219.17 | 233.55 | 219.17 | 190.25 | 0.56 | 0.02 | 500.00 |
| watcher-quiet-smoke | capture-refresh | watcher | watcher.run_once | 8.27 | 9.35 | 8.33 | 190.20 | 0.20 | 0.00 | 150.00 |

## Viewer-Specific Baselines

| Scenario | Initial p95 ms | Update p95 ms | Peak Heap Used MB | Retained Heap MB |
|---|---:|---:|---:|---:|
| viewer-flatten-tree-smoke | 0.17 | 0.17 | 65.02 | 0.42 |
| viewer-graph-view-smoke | 146.41 | 14.92 | 95.15 | 0.23 |
| viewer-review-view-smoke | 283.10 | 50.01 | 143.84 | 0.27 |

## Mapped But Not Yet Profiled

| Journey | Why Not In Perf Baseline Yet |
|---|---|
| viewer-rendering | The legacy Flask inbox web client and Textual TUI were removed in the CLI spine simplification. The React trace-viewer SPA under web/viewer/ stays catalogued via its node-harness viewer-* scenarios until the next dataset-scoped review UI lands. |
| dataset-gates-and-consume | Standalone Quality and Export pages are decommissioned. Dataset-specific row checks, publication gates, and consumer projections should get fresh perf scenarios once the dataset workflow hardens. |
| push-and-publish | The old root push perf target has been removed with the staged-trace push flow. A fresh dataset publish perf target should be added once the local dataset publication contract is stable. |
| dataset-import-and-consume | Mapped explicitly from the docs but not part of the representative perf harness yet because the hot path is remote-I/O dominated and downstream consumption is outside the app's local optimization boundary. |
| security-and-health | These workflows matter for correctness and release health, but they are integration-heavy and not the first-line representative profiling targets for UI/provenance optimization. |

## Notes

- Dataset publish performance is intentionally unprofiled until the local dataset publication contract replaces the old root push path.
- `tmux` and `agent-browser` remain separate opt-in regression smokes; they are not included in the perf latency tables because they are environment-heavy.
- Budgets in `tests/perf/budgets.toml` are current smoke-lane regression thresholds, not ideal targets.
