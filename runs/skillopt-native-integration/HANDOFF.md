# HANDOFF , SkillOpt native integration (read this first)

You are picking up a multi-session effort to integrate the **SkillOpt** paper
(arXiv 2605.23904, a text-space optimizer for agent skills) natively into
opentraces. This file is the orientation; `log.md` (same dir) is the append-only
attempt log; `GOAL.md` (same dir) is the exact `/goal` text to re-paste.

## Where everything lives

| Thing | Path |
|---|---|
| Worktree (all code work) | `/Users/jayfarei/src/tries/community-traces-skillopt` |
| Branch | `feat/skillopt-native-slice` (worktree of the main opentraces repo) |
| venv | `<worktree>/.venv` (has `pytest-timeout` installed) |
| Run log | `<worktree>/runs/skillopt-native-integration/log.md` |
| This handoff + goal | `<worktree>/runs/skillopt-native-integration/{HANDOFF,GOAL}.md` |
| Research brief | `kb/br/66-skillopt-text-space-skill-optimizer.md` (closedtraces repo, NOT in the worktree) |
| Plan (current goal) | `kb/plans/084-skillopt-paper-fidelity-and-online-harness.md` |
| Paper text (re-extract if gone) | was `/tmp/skillopt.txt`; re-run `pdftotext -layout` on the arXiv PDF |
| Original offline prototype | `/tmp/skillopt-proto/` (ephemeral; superseded by the shipped engine) |

`kb/` is a SEPARATE git repo (closedtraces) nested in the main checkout; the
worktree does not contain `kb/`. The brief + plan are committed there.

## What is DONE (the prior 4-slice goal, COMPLETE)

Shipped under `src/opentraces/consumers/skill_opt/` (engine.py, proposers.py,
runner.py, rerollout.py) + `consumers/contract.py` + workflow template
`src/opentraces/workflow_templates/skill-opt-v1/` + CLI `workflow optimize` in
`src/opentraces/cli/workflow.py`. Also migrated the branch/PR consumer into
`consumers/branch_pr/` (shim left at `core/branch_context.py`).

- Slice 1: offline edit-engine (4-op patch grammar, marker-protected slow-update
  region, budget schedules, strict gate, rejected buffer, export) + scored-rollout
  workflow + `consumers/` package + contract.
- Slice 2: real `outcome_reward` (committed/success/Trail survival) + reward-aware
  reflection + full Appendix C.2 LLM proposer chain behind a deterministic fake
  client + rejected-buffer feedback. Real bucket: uncommitted 0.00 < committed 0.80.
- Slice 3: live re-rollout gate (`rerollout.py`); proven with real `claude` 2.1.152
  (incumbent skill reward 0.0 vs candidate 1.0 -> gate accepts).
- Slice 4: versioned promotion + skill-version lineage (`lineage.jsonl`) + README.

Tests: `tests/test_skill_opt.py` (38). Bounded regression last green at 738 passed.

## The PAPER-FIDELITY AUDIT (why there is more work)

A line-by-line check against Algorithm 1 found the current optimizer is a faithful
OFFLINE adaptation, not a line-for-line reproduction. Faithful: strict gate,
bounded edits, score cache, rejected buffer, C.2 chain, protected region,
failure/success split, schedules, export. Divergences (the new goal closes them):
no 3-way train/sel/TEST split, run-global (not epoch-local) buffer, no `Bm`
minibatching, slow update not the longitudinal `e>=2` comparison, no optimizer
meta-skill, no autonomous schedule, and no live per-step rollout sampling.

## What we are DOING NOW (current goal = plan 084)

`kb/plans/084-skillopt-paper-fidelity-and-online-harness.md`, three phases:
- **Phase 1 (start here)** , offline fidelity, NO agent: 3-way split + reported
  held-out `Dtest`, epoch-local feedback buffer vs run-global audit log, `Bm`
  minibatches, longitudinal `e>=2` slow/meta update, optimizer meta-skill,
  autonomous LR schedule. All deterministic-testable.
- **Phase 2** , a `Harness` protocol (`collect_rollouts` + `score`) with
  `BucketHarness` refactored out, plus a TEST-SIDE `OtboxReRolloutRunner` /
  `OtboxHarness` (forks `c-installed-source`, injects skill as `CLAUDE.md`, runs
  `tests/otbox/simulated_users/runner.py::run_simulated_session`, scores via the
  captured trace's `outcome_reward`). Prove the online loop deterministically in
  CI with the synthetic echo/fake-claude binary as a new otbox journey.
- **Phase 3** , a small held-out verifiable scenario suite + ONE budgeted real
  otbox-driven optimization run committed as gold evidence.

`src/` must NEVER import `tests/`; the otbox runner lives under `tests/otbox/`.

## How to run

```bash
cd /Users/jayfarei/src/tries/community-traces-skillopt && . .venv/bin/activate
python -m pytest tests/test_skill_opt.py -q                       # the slice tests
# Bounded regression (the FULL `pytest tests/ -q` HANGS on env-bound
# integration/otbox/perf tests + 4 missing-dep collection errors; use a subset):
python -m pytest tests/test_skill_opt.py tests/cli tests/quality \
  tests/core/test_branch_context_gh.py tests/core/test_branch_context_render.py \
  -q -p no:cacheprovider --timeout=120
# Real bucket dry-run (the project has 4 real traces; reward separates them):
opentraces workflow optimize --dry-run --project 2026-03-27-community-traces-hf-24eb286b --out /tmp/run
# otbox: read tests/otbox/README.md; run_simulated_session drives claude/codex/echo.
```

## Gotchas learned

- `EnterWorktree` picks the repo of the session cwd, which is `kb/` (a nested
  repo), so it made a worktree on the wrong repo. The worktree here was made
  manually with `git worktree add` on the MAIN opentraces repo. Operate via
  absolute paths.
- otbox / `run_simulated_session` subprocesses inherit `HOME` (env var), so test
  isolation works across the subprocess boundary; in-process `OPENTRACES_DIR`
  monkeypatch alone would NOT reach a subprocess.
- Trail `survival_state` resolves to `None` on this machine because the project is
  not in the opted-in registry (`_iter_opted_in_projects()` is empty); reward
  falls back to committed/success, which still separates. Not a bug.
- The offline gate (`score_skill_on_rows`) is a reward-weighted proxy; the live
  gate (`rerollout.make_rerollout_gate`) is the real re-roll. Phase 2/3 make the
  online loop real.

## Git state

Nothing pushed. The slice 1-4 work + this run dir are committed on
`feat/skillopt-native-slice`; the brief + plan are committed in the `kb`
(closedtraces) repo. Re-paste `GOAL.md` into `/goal` to resume.
