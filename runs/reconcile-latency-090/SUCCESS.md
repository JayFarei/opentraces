# Plan 090 — Post-Commit Reconcile Latency: Conditions of Success

Branch: `fix/reconcile-latency-090` (worktree `community-traces-reconcile-latency`, based on
`main` @ 1c25ddd4cd — includes the shipped memory fix `8b71cc7f1e`).

This is the gate. Every condition below must be GREEN before merging back to `main`.
Each maps to a requirement (R1-R7) from `kb/plans/090-post-commit-reconcile-latency.md`.

## Design decision (locked in U1)

**Approach A' — single batch-summary search event carrying per-patch results.**

Replace the N per-commit `git_anchor_search_completed` events (one per patch) with **one**
`git_anchor_search_completed` event per (commit, reconcile-run) whose payload carries the full
per-patch result list:

```
payload = {
  "search_head": {algo, hex},          # the reconciled commit
  "algorithms_attempted": [...],
  "searched": N, "anchored": K, "unknown": M,
  "results": [
    {"trace_patch_id", "trace_id", "step_index", "generation_index",
     "result": "anchored"|"unknown", "created_anchor_ids": [...]},
    ...
  ],
  "schema_version": "opentraces.trail.anchor_search.v2",
  "summary": true
}
```

Rationale: a single event collapses ~N subprocess-heavy blob writes into one (kills latency R1)
and grows the log by ONE event/commit instead of N (kills growth R2), while the embedded
`results` list preserves EXACT per-(patch, commit, version) dedup and the same anchor set (R5).
The K real `git_anchor_created` events are still emitted per match, byte-identical (R5).

Readers must be **dual-shape**: tolerate BOTH the legacy per-patch events (~505K already on the
live log) and the new summary events (R7). A shared `iter_anchor_searches(events)` helper expands
either shape into a uniform per-patch search-record stream that maturation/query/explain consume.

## Conditions

### C1 (R1) — Latency
- [ ] `_run-post-commit-hook` end-to-end on a synthetic ~5K-patch fixture completes in **< 15s**
      (down from ~200s). Measured by the U0 harness under wall-clock timing.
- [ ] `reconcile_commit_anchors` on the same fixture completes in **< 10s**.

### C2 (R2) — Bounded growth
- [ ] A reconcile that creates K anchors appends **exactly K + 1** events (K anchors + 1 summary).
- [ ] A 0-anchor reconcile of a fresh commit appends **exactly 1** event (the summary).
- [ ] A re-reconcile of an already-reconciled commit (same version, no new patches) appends **0**
      events (idempotent — summary present, all patches already searched).

### C3 (R5) — Anchor-set equivalence (THE correctness gate)
- [ ] Parametrized old-vs-new test: for a corpus of commits, the **set of `git_anchor_created`
      events** (by content_hash) produced by the new reconcile is **identical** to the legacy
      per-patch reconcile. Includes the late-patch scenario (a patch ingested after a commit's
      first reconcile is still anchored on re-reconcile).

### C4 (R3) — Maturation equivalence
- [ ] `MaturationSummary.searches_completed` reports the **count of patch-searches** (sum over
      summary `results`), equal to the legacy per-event count for the same input.
- [ ] `has_unsearched_recent_patches` returns the **same boolean** as legacy for the same state.

### C5 (R4) — Query + explain equivalence
- [ ] `build_trail_query_projection`: each patch row's `anchor_searches` list and `git_anchors`
      are equivalent to legacy (per-patch result, search_head, created_anchor_ids preserved).
- [ ] `explain_commit(commit)` lists the same searched patches and anchored patches as legacy.
- [ ] `explain_trace_step` renders no worse for anchored patches.

### C6 (R6) — Event-log contract intact
- [ ] `pytest tests/core/test_trail_event_log.py tests/core/test_doctor_trail_event_log.py`
      `tests/core/test_trails_event_log_bugb.py tests/core/test_inbox_window_load.py` all green.
- [ ] `verify_event_log` reports `valid` after a reconcile under the new model.
- [ ] `read_events` full-stream contract unchanged (gap-free chain from seq 1).

### C7 (R7) — Historical readability / dual-shape
- [ ] A log containing BOTH legacy per-patch search events AND new summary events reads,
      verifies, and projects correctly (maturation/query/explain produce coherent output).
- [ ] No rewrite/removal of committed events (U5 back-migration is out of scope / not done here).

### C8 — Contract + docs
- [ ] New/changed frozen envelope registered in `core/trails/contract.py` with a version bump.
- [ ] `CLAUDE.md` Trace Trails decision note updated to describe the summary recording model.
- [ ] Plan 090 audit trail appended with the U1 decision.

### C9 — No regression
- [ ] Focused Trace Trails suites green (`tests/core/test_trail_*`, reconcile/maturation tests).
- [ ] Broader `pytest tests/core -q` shows no NEW failures attributable to this change.

## Verification commands
```bash
cd /Users/jayfarei/src/tries/community-traces-reconcile-latency
.venv/bin/python -m pytest tests/core/test_reconcile_recording.py -q          # C2,C3,C4,C5
.venv/bin/python -m pytest tests/core/test_trail_event_log.py \
  tests/core/test_doctor_trail_event_log.py \
  tests/core/test_trails_event_log_bugb.py \
  tests/core/test_inbox_window_load.py -q                                       # C6
.venv/bin/python runs/reconcile-latency-090/bench_reconcile.py                  # C1,C2 perf
```

---

## RESULTS — all conditions MET (2026-06-02)

Verified by: 1275-pass broad regression (core+capture+cli+integration trail) + a 4-adversary
verification workflow (GATE VERDICT: GO, zero blockers) + the dedicated test_reconcile_recording.py.

| Cond | Requirement | Result |
|------|-------------|--------|
| C1 | hook < 15s | **MET** — 5000-patch reconcile = 0.53s (was ~200s); baseline N=600 9.2s → 0.26s |
| C2 | K+1 events/commit | **MET** — 5000-patch/3-match = 4 events appended (was ~5003); 0-anchor = 1 |
| C3 | R5 anchor-set equivalence | **MET** — content_hash set identical old-vs-new across all 6 scenarios |
| C4 | maturation equivalence | **MET** — searches_completed counts per-patch records; has_unsearched preserved |
| C5 | query/explain equivalence | **MET** — anchor_searches + explain_commit source_events preserved |
| C6 | event-log/verify contract | **MET** — 38 event-log/doctor/bugb tests green; verify ok post-reconcile |
| C7 | dual-shape / historical | **MET** — mixed legacy+summary log reads/verifies/projects; no events rewritten |
| C7b | per-trace fan-in (new) | **MET** — bucket companion + workspace export/timeline fan summary in |
| C8 | contract + docs | **MET** — ANCHOR_SEARCH_SCHEMA_VERSION registered; CLAUDE.md updated |
| C9 | no regression | **MET** — 1275 passed / 4 skipped broad; corpus --check ok (34 files) |

Headline: post-commit reconcile **~200s → 0.53s**, log growth **O(N) → O(K+1)** per commit.
