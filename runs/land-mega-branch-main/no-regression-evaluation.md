# No-Regression Evaluation

Source handoff: /tmp/opentraces-otbox-scenarios-handoff-2026-05-21.md

This checklist is the no-regression surface for landing the mega branch. Refresh the inventory from the repo before final landing; the `/tmp` handoff is the baseline, but the repo is the source of truth if accepted changes intentionally move counts.

## Baseline From Handoff

- Catalogue journey TOMLs: 122 under `tests/otbox/catalogue/journeys/*.toml`.
- Simulated-user scenario TOMLs: 15 under `tests/otbox/simulated_users/scenarios/*.toml`.
- Known stale doc: `.agents/skills/otbox/SKILL.md` may mention 59 catalogue journeys and 5 gold-tier journeys; do not treat that cached count as current truth.
- Default CI-safe simulated-user scenario: `echo-meta`.
- Canonical gold journey example: `agent-session-trail-explain-happy`.

## Inventory Refresh Commands

```bash
find tests/otbox/catalogue/journeys -maxdepth 1 -name '*.toml' -type f | wc -l
find tests/otbox/simulated_users/scenarios -maxdepth 1 -name '*.toml' -type f | wc -l
find tests/otbox/catalogue/journeys -maxdepth 1 -name '*.toml' -type f -exec basename {} .toml \; | sort
find tests/otbox/simulated_users/scenarios -maxdepth 1 -name '*.toml' -type f -exec basename {} .toml \; | sort
sed -n '1,220p' tests/otbox/catalogue/journey-inventory.md
```

## User-Facing Otbox Checks

```bash
./otbox list
make otbox-inventory
./otbox journey agent-session-trail-explain-happy
make capture-refresh SCENARIO=echo-meta
```

## Usage Gate Before Expensive Work

Run before long test suites, agent fan-out, or other expensive Codex work:

```bash
lazyusage usage-check codex --json-only
```

If `lazyusage` is unavailable on `PATH`, run from the source checkout:

```bash
cd /Users/jayfarei/src/tries/2026-02-05-test-usage-via-cli
bun run dev usage-check codex --json-only
```

Parse the Codex 5-hour metric:

```jq
.services[] | select(.name == "codex") | .metrics[] | select(.name == "5h")
```

Use `used_pct` as the hard signal. Treat `source == "fallback"`, `stale == true`, or `error != null` as unreliable and do not start large work or spawn extra agents. Stop and sleep when `used_pct >= 95`; warn and avoid new large work when `used_pct >= 90`; proceed below 90 and re-check before each long-running step.

## Performance Checks

Use the broad perf suite when feasible:

```bash
.venv/bin/python -m pytest tests/perf -q
```

If that is too broad for the current step, run the relevant scoped performance tests and record why the scope is sufficient in `runs/land-mega-branch-main/log.md`.

## Acceptance Criteria

- The catalogue journey and simulated-user scenario counts match the handoff baseline, or any intentional change is documented in the run log with the exact diff and rationale.
- `tests/otbox/catalogue/journey-inventory.md` is current after `make otbox-inventory`.
- `./otbox list` reflects the same scenario categories described in the handoff.
- `./otbox journey agent-session-trail-explain-happy` passes or any failure is root-caused, fixed, and rerun.
- `make capture-refresh SCENARIO=echo-meta` passes or any failure is root-caused, fixed, and rerun.
- Performance validation passes without a meaningful regression, or any regression is root-caused, fixed, and rerun.
- Latest reliable lazyusage Codex `5h.used_pct` reading is below 90 before expensive validation work starts.
- Final landing evidence includes the command exit summaries in `runs/land-mega-branch-main/log.md`.

## Live Evidence

- 2026-05-21T20:04:39Z fallback lazyusage command succeeded from `/Users/jayfarei/src/tries/2026-02-05-test-usage-via-cli`: Codex `5h.used_pct=7`, `source=api`, `stale=false`, `error=null`.
- 2026-05-21T20:08:07Z fallback lazyusage command succeeded: Codex `5h.used_pct=8`, `source=api`, `stale=false`, `error=null`.
- Inventory count refresh: `find tests/otbox/catalogue/journeys -maxdepth 1 -name '*.toml' -type f | wc -l` -> `122`; `find tests/otbox/simulated_users/scenarios -maxdepth 1 -name '*.toml' -type f | wc -l` -> `15`.
- Perf smoke: `.venv/bin/python -m pytest tests/perf -q --perf-artifacts-dir runs/land-mega-branch-main/perf-artifacts-smoke` -> `24 passed, 8 skipped in 53.46s`.
- Focused post-refactor bucket/perf verification: `.venv/bin/python -m pytest tests/core/test_bucket_store.py tests/test_bucket_self_sufficient.py tests/test_bucket_remote_symmetric.py tests/perf/test_bucket_performance_gates.py -q --perf-artifacts-dir runs/land-mega-branch-main/perf-artifacts-bucket-refactor` -> `49 passed, 4 skipped, 2 xfailed in 24.78s`.
- 2026-05-21T20:15:57Z fallback lazyusage command succeeded: Codex `5h.used_pct=10`, `source=api`, `stale=false`, `error=null`.
- `./otbox list` completed and listed the expected 122 journey catalogue.
- `make otbox-inventory` -> `125 public (105 owned, 20 unowned), 32 hidden`, `jtbd: drift check OK`, and left `tests/otbox/catalogue/journey-inventory.md` unchanged.
- `./otbox journey agent-session-trail-explain-happy` initially failed because the direct journey command used a stale current box instead of the TOML's `from_checkpoints` pin. After fixing `tests/otbox/cli.py`, the same command passed with `base: c-captured-real-session (cache hit)` and all 8 assertions OK.
- `make capture-refresh SCENARIO=echo-meta` -> JSON `status: "ok"`, `turn_count: 3`, using the in-tree echo binary; no tracked capture artifacts changed.
- Focused bucket + otbox pytest after the direct-journey fix: `.venv/bin/python -m pytest tests/core/test_bucket_store.py tests/test_bucket_self_sufficient.py tests/test_bucket_remote_symmetric.py tests/otbox/test_otbox_slice.py tests/otbox/test_matrix.py tests/otbox/test_agent_session_slice.py tests/test_otbox_journey_assertions.py tests/otbox/test_capture_refresh_cli.py -q` -> `134 passed, 2 xfailed in 101.46s`.
- 2026-05-21T20:18:09Z fallback lazyusage command succeeded before the broader focused suites: Codex `5h.used_pct=11`, `source=api`, `stale=false`, `error=null`.
- Focused Trace Trails + Context Tree/OTLP pytest: `.venv/bin/python -m pytest tests/integration/test_trace_trails_full_stack_demo.py tests/integration/test_trace_trails_installed_runtime_uat.py tests/integration/test_trace_trails_portrayal.py tests/integration/test_trace_trails_corpus.py tests/test_otlp_capture.py tests/test_cli_ctx.py tests/test_context_tree_json_envelope_contract.py tests/test_context_tree_layer_builders.py tests/test_context_tree_models.py tests/test_context_tree_resume.py -q` -> `144 passed in 68.54s`.
- Final merge evidence: `git merge --ff-only feat/buckets-trails-milestone` fast-forwarded `main` to `6c0c2bafc8b5518e2ef51e32dc41336bcb558f84`; `git merge-base --is-ancestor 0aca134fd11a5b2447926650ec5e059e63b150aa main` exits 0; final status checks reported a clean `main` worktree ahead of `origin/main`.
- 2026-05-21T20:23:53Z final fallback lazyusage command succeeded: Codex `5h.used_pct=12`, `source=api`, `stale=false`, `error=null`.
