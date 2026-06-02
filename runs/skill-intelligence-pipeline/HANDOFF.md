# HANDOFF: Skill Intelligence Pipeline Closeout

Read this first when resuming the Skill Intelligence work.

## Workspace

| Thing | Path |
|---|---|
| Worktree | `/Users/jayfarei/src/tries/community-traces-skillopt` |
| Branch | `feat/skillopt-native-slice` |
| venv | `/Users/jayfarei/src/tries/community-traces-skillopt/.venv` |
| Goal | `runs/skill-intelligence-pipeline/GOAL.md` |
| Plan | `runs/skill-intelligence-pipeline/PLAN.md` |
| Run log | `runs/skill-intelligence-pipeline/log.md` |
| Warm-up prompt | `runs/skill-intelligence-pipeline/CONTEXT_WARMUP_PROMPT.md` |
| Corpus audit | `runs/skill-intelligence-pipeline/corpus-audit.json` |
| Current report | `runs/skill-intelligence-pipeline/case-study/ci-echo/skill-update-report-v1.json` |

Use the repo venv for all Python commands:

```bash
cd /Users/jayfarei/src/tries/community-traces-skillopt
.venv/bin/python -m pytest ...
```

## Current State

The missing skill invocation count has been repaired. The system now has enough
retained OpenTraces data to continue SkillOpt work from Trace Index
`skill_invocation` units instead of raw regex scans.

Facts verified on 2026-05-27:

- raw sessions scanned by repair: 366;
- parser skips: 26, all `below parse quality gate`;
- ingest errors: 0;
- retained trace files after additive repairs: 1027;
- Trace Index default latest-generation query: 212 `skill_invocation` units
  across 116 traces;
- latest units by agent: `codex-cli=158`, `claude-code=54`;
- top skills: `otbox=29`, `goal-forge=22`, `tdd=16`,
  `architecture-patterns=16`, `docs-update=14`;
- wildcard/glob false positives were fixed; no `skill.name="*"` rows remain;
- `corpus-audit.json` reports `total_skill_invocation_units=212`;
- Skill Intelligence dry-run selected `goal-forge`;
- dry-run split counts are `Dtrain=14`, `Dsel=4`, `Dtest=12`;
- current report has `dtest_score=1.0` and `promotion_state="default_off"`.

## Code Changes To Understand

Relevant current diffs include:

- `src/opentraces/capture/codex_cli/parse.py`: detects skill body reads from
  skill `SKILL.md` paths and rejects wildcard/glob skill names.
- `src/opentraces/core/skill_detection.py`: shared detector for Claude metadata,
  Codex explicit skill calls, and retained Codex tool-call body-read evidence.
- `src/opentraces/core/ingest.py`: explicit bulk repair controls plus
  supersession metadata on new generations.
- `src/opentraces/core/workflow.py`: agent normalization now dedupes.
- `src/opentraces/cli/__init__.py`: repeated `init --import-existing` can
  re-import; repeated `init --agent codex-cli` can merge agents; hidden
  `_scan --trace-record-only` supports fast repair.
- `tests/otbox/simulated_users/runner.py`: otbox setup installs the OpenTraces
  skill namespace for Claude/Codex harnesses.

The full run history is in `runs/skill-intelligence-pipeline/log.md`; Attempt 8
contains the retained-corpus repair facts.

## Product Direction

Build Skill Intelligence as an OpenTraces-native consumer:

```text
retained TraceRecords
  -> core skill detection
  -> Trace Index skill_invocation units
  -> skill-episodes-v1
  -> skill-eval-tasks-v1 with Dtrain/Dsel/Dtest
  -> skill-rollouts-v1
  -> SkillOpt Harness
  -> skill-update-report-v1
```

Do not create a second skill ledger. Do not auto-promote skills. Keep otbox
internal and default-off.

## Immediate Next Action

Start with Phase 1 from `PLAN.md`: make repair/reparse idempotent or safely
bounded.

Reason: the final corpus is usable because latest-generation queries collapse
additive generations, but repeated repair still grows retained trace files. Fix
that before running long closeout loops.

Expected first patch:

- add a repair mode or ingest policy that refreshes latest generation when source
  bytes are unchanged and the caller explicitly requests repair/reparse;
- preserve additive new-generation behavior for real resumed sessions after
  terminal statuses;
- keep `metadata.supersedes`, `metadata.supersedes_reason`, and
  `metadata.superseded_trace_ids` for genuine new generations;
- add focused tests in `tests/core/test_ingest.py` and CLI coverage where
  appropriate.

## Verification Commands

Focused checks already used successfully:

```bash
.venv/bin/python -m ruff check src/opentraces/core/workflow.py \
  src/opentraces/core/ingest.py src/opentraces/cli/__init__.py \
  src/opentraces/capture/codex_cli/parse.py src/opentraces/core/skill_detection.py \
  tests/cli/test_cli_init.py tests/capture/test_parser_codex_cli_advanced.py \
  tests/core/test_skill_detection.py tests/core/test_trace_index_plan056.py \
  tests/core/test_ingest.py tests/otbox/simulated_users/runner.py \
  tests/otbox/simulated_users/test_runner.py

.venv/bin/python -m pytest tests/cli/test_cli_init.py::test_init_import_existing_flag_imports_backlog \
  tests/cli/test_cli_init.py::test_reinit_import_existing_reparses_existing_project \
  tests/cli/test_cli_init.py::test_reinit_agent_option_merges_existing_project_agents \
  tests/cli/test_cli_init.py::test_hidden_scan_trace_record_only_skips_side_substrates \
  tests/capture/test_parser_codex_cli_advanced.py tests/core/test_skill_detection.py \
  tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_codex_skill_name_invocations \
  tests/core/test_trace_index_plan056.py::test_rebuild_index_uses_codex_skill_body_read_invocations \
  tests/core/test_ingest.py::TestIngestOneSession::test_grown_file_after_upload_opens_new_generation \
  tests/otbox/test_codex_simulated_user_runner.py::test_codex_capture_refresh_dry_run_needs_no_codex_binary \
  tests/otbox/simulated_users/test_runner.py::test_install_hooks_dispatches_codex_cli \
  tests/otbox/simulated_users/test_runner.py::test_install_hooks_dispatches_claude_and_noops_echo -q

git diff --check
```

Closeout still needs:

```bash
.venv/bin/python -m pytest tests/test_skill_opt.py -q
make otbox-journeys
.venv/bin/python -m pytest tests/ -q
```

Document environment-bound skips only.

## Suggested Skills

- `goal-forge`: use if the completion contract changes.
- `handoff`: use before ending a long continuation session.
- `investigate`: use for repair idempotence or Trace Index count regressions.
- `tdd`: use for ingest/repair policy changes.
- `codex-review`: use before landing the branch.

## Boundaries

Allowed areas are the core skill-detection/index read side, ingest repair
plumbing, workflow templates, dataset/workflow CLI plumbing, SkillOpt consumer
and harness modules, focused tests including `tests/otbox`, and
`runs/skill-intelligence-pipeline/`.

Avoid destructive bucket cleanup, non-additive schema changes, full Trail/Context
Tree write-path rewrites, network/live-agent requirements in default CI,
automatic skill promotion, or `src/` importing from `tests/`.

## Logging

Append every attempt to `runs/skill-intelligence-pipeline/log.md` with phase,
diff/change, evidence observed, split/leakage notes, case-study artifact updates,
and next-step rationale.

On block, append `BLOCKED:` with attempted paths, evidence gathered, blocker, and
the input that would unlock progress.
