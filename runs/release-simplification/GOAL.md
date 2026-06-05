# OpenTraces Release-Readiness Simplification — GOAL (do not stop until met)

**Mode:** ultracode dynamic workflow orchestration. Started 2026-06-02. Branch base: `main` @ `d5d6bfd9b7`.

## Objective (the contract)

Simplify and clean up the codebase and documentation for a full release of the current
version (~0.4.x) **while preserving feature accuracy around the documented release feature set.**
Do NOT remove, weaken, or silently change any documented behaviour unless proven obsolete,
duplicated, or consolidatable for a greatly improved developer experience.

## Defenses against the three failure modes

- **Agentic laziness** — every theme reaches a terminal state: verified-and-merged OR
  explicitly-abandoned-with-written-reason. Never stop midway.
- **Self-preferential bias** — no implementation agent judges its own output. Every worktree
  gets a *separate* adversarial verifier in a clean context.
- **Goal drift** — the feature map (FEATURE-MAP.md) is the non-regression contract, passed
  verbatim into every discovery / implementation / verification agent.

## Phase ledger (update as we go)

- [x] Phase 0 — Feature map (non-regression contract) → `FEATURE-MAP.json` ✓
- [x] Phase 1 — Discovery fan-out (6 areas, Sonnet) → `DISCOVERY-CANDIDATES.json` ✓
- [x] Phase 2 — Theme synthesis (Opus) → `THEMES.json` ✓ (4 themes, 13 dropped w/ reasons; gate omitted — overnight run, PR is review gate; key claims independently verified)
- [ ] Phase 3 — Implement each theme in its own git worktree (parallel, Sonnet)
- [ ] Phase 4 — Adversarial verify each worktree (separate Opus verifier, clean context; loop)
- [x] Phase 5 — Merged t1→t2→t3→t4 --no-ff into simplify/release-readiness-v04; ZERO conflicts; per-merge guards green; full suite 3436 passed (35 env-failures all triaged: perf/otbox/s7) ✓
- [x] Phase 6 — `RELEASE-READINESS-REPORT.md` written ✓
- [x] Final — **PR #11 OPEN** https://github.com/JayFarei/opentraces/pull/11 (+383/-5240). Theme worktrees removed; rs-integration kept. ✓

## DONE 2026-06-03. All completion conditions met. Net -4857 LOC, 4 themes verified+merged, 0 abandoned, docs accurate, PR open.

## Selected themes (base d5d6bfd9b701c5e0e93cf0f11a0ea7e17699a132)

| id | branch | worktree | risk |
|----|--------|----------|------|
| t1-ci-red-bar-and-doc-version-accuracy | simplify/t1-docs-version-accuracy | ../rs-t1-docs | low |
| t2-remove-dead-modules-and-dev-cruft | simplify/t2-dead-modules | ../rs-t2-dead | low |
| t3-cli-dead-commands-shims-and-drop-list | simplify/t3-cli-dead | ../rs-t3-cli | medium |
| t4-core-helper-dedup-and-dead-stubs | simplify/t4-core-dedup | ../rs-t4-core | low |

**Known overlap:** cli/trace.py ∈ t3 ∩ t4 (far-apart regions) → merge t3 before t4 in Phase 5.
**Test isolation:** PYTHONPATH=<wt>/src:<wt>/packages/opentraces-schema/src <repo>/.venv/bin/python -m pytest (proven to shadow the editable .pth, which currently points at ../pi-support). NOTE: venv editable install points at pi-support, so PYTHONPATH-pin is required for ALL test runs incl. final.
**Deferred (do NOT attempt this cycle):** all large-file splits (trace_index 4263, bucket_store 3205, trails/sync), workflow.py rename, CLI shared-option decorator, test-tree moves — recorded in THEMES.json "dropped".

## Completion condition

Complete only when: themes produced; each theme implemented-and-verified OR explicitly
abandoned with justification; verified worktrees merged into base; tests/checks rerun after
merge; documented release feature set still accurate; before/after report produced; PR opened
or branch prepared.

## Operational constraints

- Isolated subagents w/ clean contexts (not single-agent inline refactor).
- Sonnet = mechanical discovery/implementation; Opus = synthesis + adversarial verification.
- Isolated git worktrees for parallel implementation; mechanical agents avoid heavy commands.
- Verifier always fresh context, always before merge.
- Small, legible modules > clever abstractions. Preserve documented behaviour.
- Do NOT delete tests/docs just to shrink the repo. Avoid cosmetic-only churn.
- Keep changes reviewable for a PR tomorrow morning.

## Integration ledger (branch simplify/release-readiness-v04, worktree ../rs-integration)

- t1 merged --no-ff: clean. Guard: red-bar test 2 passed; --version 0.4.0. ✓
- t2 merged --no-ff: clean. Guard: 'proxy' in CAPTURE_METHOD_VALUES; force-include present; http_proxy+otc removed; CLAUDE.md doc fix present; 3645 collect. ✓
- t3 merged --no-ff: clean (−1370 LOC). Guard: all subgroups --help ok; trail blame still GROUP; no cli.publish/import_hf imports; test_publish_flow+test_cli_commands 92 passed; 3645 collect. ✓
- t4 polish re-verified PASS (wf w5nzpmtrc); merged --no-ff into integration: CLEAN (cli/trace.py auto-merged, no conflict). ✓
- Integration total: 49 files, +383/-5240 (net -4857 LOC), 17 removed, 2 added (_time.py, query_helpers.py).
- Wheel build: opentraces-0.4.0 OK, skill assets bundled, http_proxy absent. ✓
- CLI surface: all 35 top-level commands registered incl hidden hook cmds; subgroups help; trail blame still GROUP. ✓ (earlier "BROKEN" smoke was a false alarm — custom help renderer exit code, identical on base.)
- Full `pytest tests/` integration sweep: RUNNING (bg b1x2xyqgl) → triage then open PR.
- RELEASE-READINESS-REPORT.md drafted (test-result section pending).
- Minor findings accepted as-is (recorded for report): t1 well-known SKILL quick-start byte-parity (commands already documented in published skill); t3 'export' drop-list entry (harmless swallowed no-op) + already-clean docstring; t4 capture/+consumers/ _utc_now copies + search_projection datetime variant (out of core scope → follow-up).

## Working notes (live)

- Repo: 113K LOC Python, 321 test files, ~44 committed .md docs.
- Largest files (split candidates): core/trace_index.py 4263, cli/__init__.py 4171,
  core/bucket_store.py 3205, cli/installers.py 2833, cli/trail.py 2546, cli/trace.py 2377.
- Ignore hygiene already good ($tmp/, dist/, tmp/, .otbox/, __pycache__ untracked/ignored).
- Existing worktree: `pi-support` at ../pi-support. Uncommitted on main: SEARCH-EVAL.md (M),
  runs/opentraces-pi-integration/ (??) — leave untouched.
