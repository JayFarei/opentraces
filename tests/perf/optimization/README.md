# GNHF Performance Optimization

This directory is the operating packet for a `gnhf`-driven performance campaign against the committed perf harness in `tests/perf/`.

The goal is to run up to 30 small optimization attempts that improve representative user-facing latency and memory without weakening the harness, widening budgets, or regressing the real surfaces covered by `tmux` and `agent-browser`.

## Documents

- [Program plan](../../../kb/plans/052-gnhf-performance-optimization-program.md): why this exists and what "good" looks like.
- [GNHF objective](./gnhf-objective.md): the text to feed into `gnhf`.
- [Eval rubric](./eval-rubric.md): how to score wins, losses, and regressions.
- [Runbook](./runbook.md): exact setup, run, and verification commands.
- [Iteration review template](./iteration-review-template.md): the review form for each attempt or batch.
- [Assessment report](./assessment-2026-04-20.md): branch review covering perf materiality, regression risk, and maintainability.

## Source Of Truth

- [Perf baseline](../BASELINE.md)
- [Representative journeys](../journeys.toml)
- [Budgets](../budgets.toml)

## Primary Target Set

These are the first-line hotspots for the 30-iteration campaign. Budgets are regression thresholds; the numbers below are optimization starting points.

| Scenario | Baseline | Memory Baseline | Why It Matters | Initial Goal |
|---|---:|---:|---|---|
| `tui-review-actions-smoke` | `p95 1505.88 ms` | `Py Heap 36.73 MB`, `RSS Delta 21.25 MB` | Slow staging, undo, modal, and preview actions are immediately user-visible in the TUI. | Reduce `p95` by 35%; reduce Python heap by 25%. |
| `tui-navigation-smoke` | `p95 782.63 ms` | `Py Heap 22.92 MB`, `RSS Delta 17.00 MB` | Navigation lag makes the core review loop feel heavy. | Reduce `p95` by 30%; reduce Python heap by 20%. |
| `viewer-review-view-smoke` | `initial p95 283.10 ms`, `update p95 50.01 ms` | `Peak Heap 143.84 MB` | First render and update cost dominate the web review experience on larger traces. | Reduce initial render by 30%; reduce peak heap by 25%. |
| `viewer-graph-view-smoke` | `initial p95 146.41 ms`, `update p95 14.92 ms` | `Peak Heap 95.15 MB` | Graph rendering is a visible hotspot when exploring attribution paths. | Reduce initial render by 25%; reduce peak heap by 20%. |
| `web-refresh-smoke` | `p95 257.80 ms` | `Py Heap 0.76 MB` | Refresh drives the web review loop and reflects watcher/scan costs. | Reduce `p95` by 30%. |
| `scan-project-smoke` | `p95 254.90 ms` | `Py Heap 0.58 MB` | Scan cost impacts watcher throughput and refresh responsiveness. | Reduce `p95` by 30%. |
| `graph-cli-smoke` | `p95 259.37 ms` | `Peak RSS 42.58 MB` | Graph generation is one of the heavier provenance commands. | Reduce `p95` by 25%. |
| `assess-local-smoke` | `p95 269.37 ms` | `Peak RSS 65.45 MB` | Local quality assessment is a measurable CLI bottleneck. | Reduce `p95` by 25%; reduce RSS by 15%. |
| `watcher-active-smoke` | `p95 233.55 ms` | `Py Heap 0.56 MB` | Active watcher ticks gate background responsiveness. | Reduce `p95` by 25%. |

## Non-Negotiables

- Do not widen `tests/perf/budgets.toml` as part of the campaign.
- Do not shrink fixtures or weaken representative journeys to make metrics look better.
- Do not disable `tests/perf`, `tmux`, or `agent-browser` smokes to land an optimization.
- Prefer the smallest verifiable change that moves one or more primary metrics.

## Suggested Campaign Shape

1. Attack the TUI review loop first: `tui-review-actions-smoke`, `tui-navigation-smoke`, `tui-startup-smoke`.
2. Move to web/viewer render and refresh costs.
3. Then target watcher/scan and provenance CLI hotspots.
4. Use the review template after each iteration or every 3-5 iterations, depending on how noisy the results are.
