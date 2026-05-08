# Performance Baseline

Generated from `tests/perf/artifacts/latest/summary.json`.

## Profiled Journeys

| Journey | Priority | Perf Scenarios | User Smoke | Live |
|---|---:|---:|---:|---:|
| onboarding-terminal | primary | 1 | 0 | 0 |
| capture-refresh | primary | 4 | 0 | 0 |
| web-review | primary | 8 | 1 | 0 |
| tui-review | primary | 3 | 1 | 0 |
| cli-review-batch | primary | 3 | 0 | 0 |
| provenance-graph-blame | primary | 7 | 0 | 0 |

## Scenario Baselines

| Scenario | Journey(s) | Domain | Target | p50 ms | p95 ms | Cold ms | Peak RSS MB | Py Heap MB | RSS Delta MB | Budget p95 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| blame-cli-smoke | provenance-graph-blame | core | cli.blame | 236.56 | 239.25 | 235.85 | 46.75 | - | - | 400.00 |
| blame-core-smoke | provenance-graph-blame | core | graph_api.load_blame | 28.19 | 29.97 | 29.97 | 91.23 | 0.52 | 0.08 | 200.00 |
| bucket-status-smoke | cli-review-batch | cli | cli.bucket_status | 173.07 | 173.83 | 171.88 | 46.89 | - | - | - |
| graph-cli-smoke | provenance-graph-blame | core | cli.graph | 289.39 | 292.33 | 292.33 | 47.06 | - | - | 500.00 |
| graph-core-smoke | provenance-graph-blame | core | graph.load | 73.95 | 75.06 | 75.06 | 91.73 | 0.56 | 0.08 | 300.00 |
| inverse-blame-smoke | provenance-graph-blame | core | graph_api.load_inverse_blame | 25.00 | 25.62 | 25.10 | 94.09 | 0.82 | 0.34 | 250.00 |
| scan-project-smoke | capture-refresh | watcher | ingest.scan_project | 248.27 | 254.90 | 254.90 | 190.22 | 0.58 | 0.05 | 500.00 |
| status-smoke | cli-review-batch, onboarding-terminal | cli | cli.status | 184.32 | 187.80 | 184.24 | 50.47 | - | - | 250.00 |
| trace-query-smoke | cli-review-batch | cli | cli.trace_query | 585.84 | 714.30 | 456.01 | 58.83 | - | - | - |
| tui-navigation-smoke | tui-review | tui | tui.navigation | 782.63 | 782.63 | 782.63 | 141.94 | 22.92 | 17.00 | 1205.82 |
| tui-review-actions-smoke | tui-review | tui | tui.review_actions | 1505.88 | 1505.88 | 1505.88 | 183.58 | 36.73 | 21.25 | 2024.40 |
| tui-startup-smoke | tui-review | tui | tui.startup | 153.64 | 153.64 | 153.64 | 190.05 | 13.34 | 4.98 | 250.00 |
| viewer-flatten-tree-smoke | web-review | viewer | viewer.flattenTree | 0.16 | 0.17 | 0.17 | 278.86 | - | 0.02 | - |
| viewer-graph-view-smoke | web-review | viewer | viewer.GraphView | 14.39 | 14.92 | 146.41 | 374.66 | - | 0.05 | - |
| viewer-review-view-smoke | web-review | viewer | viewer.ReviewView | 47.72 | 50.01 | 283.10 | 409.11 | - | 1.30 | - |
| watcher-active-smoke | capture-refresh | watcher | watcher.run_once | 219.17 | 233.55 | 219.17 | 190.25 | 0.56 | 0.02 | 500.00 |
| watcher-quiet-smoke | capture-refresh | watcher | watcher.run_once | 8.27 | 9.35 | 8.33 | 190.20 | 0.20 | 0.00 | 150.00 |
| web-blame-smoke | provenance-graph-blame | web | web.api_blame | 33.32 | 34.38 | 34.38 | 190.27 | 0.67 | 0.02 | 100.00 |
| web-context-smoke | web-review | web | web.api_context | 4.22 | 4.26 | 4.26 | 190.28 | 0.50 | 0.00 | 20.00 |
| web-detail-smoke | web-review | web | web.api_trace_detail | 19.25 | 20.65 | 19.25 | 190.30 | 0.76 | 0.02 | 60.00 |
| web-graph-smoke | provenance-graph-blame | web | web.api_graph | 88.63 | 89.74 | 84.97 | 190.30 | 0.72 | 0.00 | 220.00 |
| web-refresh-smoke | capture-refresh | web | web.api_refresh | 253.22 | 257.80 | 249.44 | 190.30 | 0.76 | 0.00 | 500.00 |
| web-stats-smoke | web-review | web | web.api_stats | 20.03 | 20.79 | 20.03 | 190.30 | 0.76 | 0.00 | 60.00 |
| web-trace-tree-smoke | web-review | web | web.api_trace_tree | 20.56 | 21.23 | 21.23 | 190.31 | 0.76 | 0.00 | 60.00 |
| web-traces-smoke | web-review | web | web.api_traces | 20.03 | 21.22 | 20.08 | 190.31 | 0.76 | 0.00 | 60.00 |

## Viewer-Specific Baselines

| Scenario | Initial p95 ms | Update p95 ms | Peak Heap Used MB | Retained Heap MB |
|---|---:|---:|---:|---:|
| viewer-flatten-tree-smoke | 0.17 | 0.17 | 65.02 | 0.42 |
| viewer-graph-view-smoke | 146.41 | 14.92 | 95.15 | 0.23 |
| viewer-review-view-smoke | 283.10 | 50.01 | 143.84 | 0.27 |

## Mapped But Not Yet Profiled

| Journey | Why Not In Perf Baseline Yet |
|---|---|
| quality-and-export | The old root assess/export commands are no longer part of the current local flow. Dataset-specific quality and export/consume paths should get fresh perf scenarios once the dataset workflow hardens. |
| push-and-publish | The old root push perf target has been removed with the staged-trace push flow. A fresh dataset publish perf target should be added once the local dataset publication contract is stable. |
| dataset-import-and-consume | Mapped explicitly from the docs but not part of the representative perf harness yet because the hot path is remote-I/O dominated and downstream consumption is outside the app's local optimization boundary. |
| security-and-health | These workflows matter for correctness and release health, but they are integration-heavy and not the first-line representative profiling targets for UI/provenance optimization. |

## Notes

- Dataset publish performance is intentionally unprofiled until the local dataset publication contract replaces the old root push path.
- `tmux` and `agent-browser` remain separate opt-in regression smokes; they are not included in the perf latency tables because they are environment-heavy.
- Budgets in `tests/perf/budgets.toml` are current smoke-lane regression thresholds, not ideal targets.
