# Thermo-Nuclear Review Register

Scope: current branch `feat/buckets-trails-milestone` against `main`.
Starting diff: 793 files changed, 151820 insertions, 11174 deletions.
Review standard: strict maintainability review for structural regressions, missed simplifications, spaghetti branching, abstraction/type-boundary problems, file-size explosions, and misplaced logic.

Status: complete; no open blocker/high finding remains after the branch-vs-main scan, bucket-layout cleanup, and otbox direct-journey fix.

## Review Focus

- Find structural simplifications that delete complexity rather than rearrange it.
- Treat files crossing 1000 lines because of this branch as presumptive decomposition blockers unless justified.
- Flag feature-specific conditionals scattered through shared paths.
- Prefer direct, typed, canonical-layer code over wrappers, casts, generic magic, and duplicated helpers.
- Require every blocker to have either a fix, a documented waiver with evidence, or a `BLOCKED:` entry in the run log.

## Findings

| ID | Severity | Path | Finding | Required remedy | Status | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| TN-001 | Medium | `src/opentraces/core/bucket_store.py` | The bucket store had pure layout/path-contract helpers mixed into a 3.3k-line persistence/projection/maintenance module. This was not a behavior bug, but it made the Plan 080 layout contract harder to inspect and worsened the large-file pressure. | Extract the pure path helpers to a dedicated owner module while preserving the existing `bucket_store` import surface. | Resolved | Added `src/opentraces/core/bucket_layout.py`; `bucket_store.py` now imports/re-exports those helpers. `py_compile` passed for bucket modules. Focused verification: `49 passed, 4 skipped, 2 xfailed in 24.78s` for `tests/core/test_bucket_store.py tests/test_bucket_self_sufficient.py tests/test_bucket_remote_symmetric.py tests/perf/test_bucket_performance_gates.py`. |
| TN-002 | Medium | Multiple: `bucket_store.py`, `trace_index.py`, `cli/trail.py`, `core/datasets.py`, `core/bursts.py`, `cli/ctx.py`, `core/trails/sync.py`, `capture/claude_code/context_tree_capture.py` | The branch introduces several >1k-line modules. This is the main thermo-nuclear maintainability risk. The scan found cohesive ownership boundaries rather than scattered feature checks: Click command groups stay in their command modules; Trace Index owns SQLite projection/query; Datasets owns the local HF-shaped dataset store; Context Tree capture owns JSONL reconstruction; Trail sync owns survival-state synchronization. `bucket_store.py` had the clearest decomposable pure-layout cluster and was split under TN-001. | Decompose any obvious pure/helper cluster; otherwise document the cohesion reason and keep high-risk future splits behind tests. | Resolved / waived as non-blocking | Size scan: `git diff --name-only --diff-filter=AM main...HEAD` plus line-count comparison. AST scan highlighted long functions/classes; no unresolved new high-risk command or projection branch was found after targeted source inspection. |
| TN-003 | Medium | `tests/perf`, event-log/bucket hot paths | The branch changes event-log, bucket, trace-query, and otbox paths where hidden O(N) regressions are plausible. | Run live perf gates instead of trusting committed artifacts; fix any meaningful slowdown. | Resolved | `Codex 5h.used_pct=8`, `source=api`, `stale=false`, `error=null` before perf work. Full smoke perf: `.venv/bin/python -m pytest tests/perf -q --perf-artifacts-dir runs/land-mega-branch-main/perf-artifacts-smoke` -> `24 passed, 8 skipped in 53.46s`. Post-refactor bucket perf/focused tests passed as noted in TN-001. |
| TN-004 | High | `tests/otbox/cli.py`, `tests/otbox/catalogue/journeys/agent-session-trail-explain-happy.toml` | Direct `./otbox journey agent-session-trail-explain-happy` ignored the journey's single `from_checkpoints` pin and ran against the current box. In this worktree that expanded `{trace_id}` / `{step_index}` to empty strings and failed the canonical gold journey with rc=2. | Make direct single-journey execution fork the declared checkpoint when exactly one `from_checkpoints` pin is present; preserve `--box` as the explicit override. | Resolved | First run failed with `--trace "" --step ""`; after the CLI fix, `./otbox journey agent-session-trail-explain-happy` -> PASS with `base: c-captured-real-session (cache hit)` and all 8 assertions OK. Focused otbox/bucket tests passed: `134 passed, 2 xfailed in 101.46s`. |

## Review Evidence

- Branch head verified as `0aca134fd11a5b2447926650ec5e059e63b150aa`; final `git merge-base --is-ancestor 0aca134fd11a5b2447926650ec5e059e63b150aa main` exits 0.
- Diff scale: `793 files changed, 151820 insertions(+), 11174 deletions(-)`.
- Otbox inventory count currently matches the handoff baseline: 122 catalogue journey TOMLs and 15 simulated-user scenario TOMLs.
- Structural scans run:
  - `git diff --dirstat=files,0 main...HEAD`
  - `git diff --numstat main...HEAD | sort -k1,1nr | head -80`
  - line-count crossing scan for changed text/Python/CLI/doc files
  - AST function/class size and branch-count scan for changed Python files
  - targeted source outline review of `bucket_store.py`, `trace_index.py`, `cli/trail.py`, `cli/ctx.py`, `core/datasets.py`, `core/bursts.py`, `core/trails/sync.py`, and `capture/claude_code/context_tree_capture.py`

Current approval bar status: no open blocker/high maintainability finding remains.

## Completion Bar

- No open blocker or high-severity maintainability finding remains.
- Any large-file waiver includes the reason the file should stay cohesive.
- Any intentional branch complexity is located behind a clear owner abstraction or documented as the simplest available shape.
- Final entry links each addressed finding to the diff and verification command that proved behavior did not regress.
