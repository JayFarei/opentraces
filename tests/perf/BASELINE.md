# Performance Baseline

Generated from `tests/perf/artifacts/latest/summary.json`.

## Profiled Journeys

| Journey | Priority | Perf Scenarios | User Smoke | Live |
|---|---:|---:|---:|---:|
| onboarding-terminal | primary | 1 | 0 | 0 |
| capture-refresh | primary | 4 | 0 | 0 |
| web-review | primary | 8 | 1 | 0 |
| tui-review | primary | 3 | 1 | 0 |
| cli-review-batch | primary | 2 | 0 | 0 |
| provenance-graph-blame | primary | 7 | 0 | 0 |
| quality-and-export | secondary | 2 | 0 | 0 |
| push-and-publish | primary | 1 | 1 | 1 |

## Scenario Baselines

| Scenario | Journey(s) | Domain | Target | p50 ms | p95 ms | Cold ms | Peak RSS MB | Py Heap MB | RSS Delta MB | Budget p95 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| add-all-smoke | cli-review-batch | cli | cli.add_all | 144.49 | 146.89 | 144.49 | 42.70 | - | - | 179.18 |
| assess-local-smoke | quality-and-export | cli | cli.assess_local | 266.62 | 269.37 | 269.37 | 65.45 | - | - | 327.70 |
| blame-cli-smoke | provenance-graph-blame | core | cli.blame | 212.82 | 215.30 | 212.82 | 42.48 | - | - | 280.00 |
| blame-core-smoke | provenance-graph-blame | core | graph_api.load_blame | 24.31 | 25.25 | 24.74 | 86.36 | 0.49 | 0.06 | 40.00 |
| export-agent-trace-smoke | quality-and-export | cli | cli.export_agent_trace | 144.92 | 145.78 | 144.40 | 43.19 | - | - | 182.35 |
| graph-cli-smoke | provenance-graph-blame | core | cli.graph | 258.02 | 259.37 | 256.15 | 42.58 | - | - | 340.00 |
| graph-core-smoke | provenance-graph-blame | core | graph.load | 73.20 | 74.67 | 70.51 | 86.97 | 0.55 | 0.08 | 100.00 |
| inverse-blame-smoke | provenance-graph-blame | core | graph_api.load_inverse_blame | 20.88 | 21.49 | 20.88 | 89.03 | 0.82 | 0.33 | 35.00 |
| push-smoke | push-and-publish | publish | publish.push | 2.72 | 13.23 | 13.23 | 101.09 | 0.40 | 0.03 | 25.00 |
| scan-project-smoke | capture-refresh | watcher | ingest.scan_project | 248.27 | 254.90 | 254.90 | 190.22 | 0.58 | 0.05 | 300.00 |
| status-smoke | cli-review-batch, onboarding-terminal | cli | cli.status | 161.11 | 165.22 | 160.41 | 46.92 | - | - | 201.64 |
| tui-navigation-smoke | tui-review | tui | tui.navigation | 782.63 | 782.63 | 782.63 | 141.94 | 22.92 | 17.00 | 1205.82 |
| tui-review-actions-smoke | tui-review | tui | tui.review_actions | 1505.88 | 1505.88 | 1505.88 | 183.58 | 36.73 | 21.25 | 2024.40 |
| tui-startup-smoke | tui-review | tui | tui.startup | 153.64 | 153.64 | 153.64 | 190.05 | 13.34 | 4.98 | 250.00 |
| viewer-flatten-tree-smoke | web-review | viewer | viewer.flattenTree | 0.16 | 0.17 | 0.17 | 278.86 | - | 0.02 | - |
| viewer-graph-view-smoke | web-review | viewer | viewer.GraphView | 14.39 | 14.92 | 146.41 | 374.66 | - | 0.05 | - |
| viewer-review-view-smoke | web-review | viewer | viewer.ReviewView | 47.72 | 50.01 | 283.10 | 409.11 | - | 1.30 | - |
| watcher-active-smoke | capture-refresh | watcher | watcher.run_once | 219.17 | 233.55 | 219.17 | 190.25 | 0.56 | 0.02 | 280.00 |
| watcher-quiet-smoke | capture-refresh | watcher | watcher.run_once | 8.27 | 9.35 | 8.33 | 190.20 | 0.20 | 0.00 | 50.00 |
| web-blame-smoke | provenance-graph-blame | web | web.api_blame | 33.32 | 34.38 | 34.38 | 190.27 | 0.67 | 0.02 | 46.20 |
| web-context-smoke | web-review | web | web.api_context | 4.22 | 4.26 | 4.26 | 190.28 | 0.50 | 0.00 | 5.55 |
| web-detail-smoke | web-review | web | web.api_trace_detail | 19.25 | 20.65 | 19.25 | 190.30 | 0.76 | 0.02 | 24.48 |
| web-graph-smoke | provenance-graph-blame | web | web.api_graph | 88.63 | 89.74 | 84.97 | 190.30 | 0.72 | 0.00 | 118.90 |
| web-refresh-smoke | capture-refresh | web | web.api_refresh | 253.22 | 257.80 | 249.44 | 190.30 | 0.76 | 0.00 | 314.91 |
| web-stats-smoke | web-review | web | web.api_stats | 20.03 | 20.79 | 20.03 | 190.30 | 0.76 | 0.00 | 23.80 |
| web-trace-tree-smoke | web-review | web | web.api_trace_tree | 20.56 | 21.23 | 21.23 | 190.31 | 0.76 | 0.00 | 23.49 |
| web-traces-smoke | web-review | web | web.api_traces | 20.03 | 21.22 | 20.08 | 190.31 | 0.76 | 0.00 | 25.60 |

## Viewer-Specific Baselines

| Scenario | Initial p95 ms | Update p95 ms | Peak Heap Used MB | Retained Heap MB |
|---|---:|---:|---:|---:|
| viewer-flatten-tree-smoke | 0.17 | 0.17 | 65.02 | 0.42 |
| viewer-graph-view-smoke | 146.41 | 14.92 | 95.15 | 0.23 |
| viewer-review-view-smoke | 283.10 | 50.01 | 143.84 | 0.27 |

## Mapped But Not Yet Profiled

| Journey | Why Not In Perf Baseline Yet |
|---|---|
| dataset-import-and-consume | Mapped explicitly from the docs but not part of the representative perf harness yet because the hot path is remote-I/O dominated and downstream consumption is outside the app's local optimization boundary. |
| security-and-health | These workflows matter for correctness and release health, but they are integration-heavy and not the first-line representative profiling targets for UI/provenance optimization. |

## Notes

- `push-smoke` measures local push-path overhead with a fake uploader. Real private-HF success remains an opt-in live integration test.
- `tmux` and `agent-browser` remain separate opt-in regression smokes; they are not included in the perf latency tables because they are environment-heavy.
- Budgets in `tests/perf/budgets.toml` are current smoke-lane regression thresholds, not ideal targets.
