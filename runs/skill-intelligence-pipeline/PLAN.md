# Plan: Skill Intelligence Pipeline Closeout

Status: continuation plan after retained-corpus repair.

Worktree: `/Users/jayfarei/src/tries/community-traces-skillopt`

Branch: `feat/skillopt-native-slice`

Run log: `runs/skill-intelligence-pipeline/log.md`

Goal: `runs/skill-intelligence-pipeline/GOAL.md`

## Current Facts

The missing-skill-count problem is no longer a blocker. The retained corpus was
rebuilt from raw Claude/Codex sessions using the updated skill extraction path.

Confirmed state on 2026-05-27:

- raw sessions scanned by repair: 366;
- parser skips: 26, all `below parse quality gate`;
- ingest errors: 0;
- retained trace files after additive repairs: 1027;
- Trace Index raw index: 1027 traces, 333916 units, 647 raw skill rows across
  additive generations;
- Trace Index default latest-generation query: 212 `skill_invocation` units
  across 116 traces;
- latest skill units by agent: `codex-cli=158`, `claude-code=54`;
- no wildcard `skill.name="*"` rows remain;
- top latest skills: `otbox=29`, `goal-forge=22`, `tdd=16`,
  `architecture-patterns=16`, `docs-update=14`, `agent-browser=11`,
  `scout-research=10`;
- `runs/skill-intelligence-pipeline/corpus-audit.json` now reports
  `total_skill_invocation_units=212`;
- the deterministic dry-run selected `goal-forge` and wrote
  `runs/skill-intelligence-pipeline/case-study/ci-echo/skill-update-report-v1.json`;
- current dry-run split counts are `Dtrain=14`, `Dsel=4`, `Dtest=12`;
- reported held-out `dtest_score=1.0`;
- promotion remains `default_off`.

## Product Direction

Land Skill Intelligence as an OpenTraces-native consumer, not a parallel skill
ledger. The authoritative read side is:

```text
retained TraceRecords
  -> opentraces.core.skill_detection
  -> Trace Index skill_invocation units
  -> skill-episodes-v1
  -> skill-eval-tasks-v1 with leakage-safe Dtrain/Dsel/Dtest
  -> skill-rollouts-v1 from otbox-backed harness runs
  -> skill-update-report-v1 for human review
```

Automatic promotion is out of scope. Otbox remains an internal/default-off
testing harness, not an end-user product surface.

## Already Done

- Codex parser detects real skill body reads from
  `.codex/.claude/.agents/skills/<skill>/SKILL.md`.
- Core `opentraces.core.skill_detection` detects both metadata skill calls and
  retained tool-call body-read evidence.
- Wildcard/glob skill paths such as `skills/*/SKILL.md` are rejected.
- Trace Index skill units consume the core detector.
- Existing-project `opentraces init --import-existing` can now re-import instead
  of returning early.
- Existing-project `opentraces init --agent codex-cli` can merge Codex into the
  marker agent list and refresh hooks.
- `_scan --trace-record-only` gives a fast repair path that refreshes
  TraceRecords/bucket trace objects without expensive per-session Trail/Context
  Tree side projections.
- New generations are stamped with `metadata.supersedes`,
  `metadata.supersedes_reason`, and `metadata.superseded_trace_ids`.
- Otbox simulated user setup installs the OpenTraces skill namespace for both
  Claude and Codex harnesses.
- The repaired corpus and dry-run case-study artifacts exist under
  `runs/skill-intelligence-pipeline/`.

## Remaining Work

### Phase 1: Make Repair Idempotent

Problem: repeated forced repair on an `auto` project currently opens additive
new generations. Query-time supersession now collapses latest results, but
rerunning repair still grows local state.

Work:

- add a repair mode or ingest policy that refreshes the latest generation when
  source bytes are unchanged and the caller is explicitly doing repair/reparse;
- preserve additive behavior for real resumed sessions after terminal states;
- keep writer-declared supersession metadata for any genuinely new generation;
- add focused ingest/CLI regressions.

Verification:

```bash
.venv/bin/python -m pytest tests/core/test_ingest.py tests/cli/test_cli_init.py -q
```

### Phase 2: Audit The Case Study Report

Problem: the dry-run mechanically completes, but the case study must be a useful
human review artifact.

Work:

- inspect `case-study/ci-echo/skill-update-report-v1.json`;
- verify baseline/candidate digests, candidate diff, Dsel gate, Dtest score,
  rollout refs, limitations, and `default_off` promotion state;
- add or update a compact case-study summary if the report lacks a human-readable
  entry point;
- ensure selected skill remains data-driven from `corpus-audit.json`.

Verification:

```bash
.venv/bin/opentraces workflow skill-intelligence \
  --project 2026-03-27-community-traces-hf-24eb286b \
  --out runs/skill-intelligence-pipeline \
  --dry-run --json
```

### Phase 3: Tighten Data Contract Tests

Work:

- schema/workflow tests for `skill-episodes-v1`;
- schema/workflow tests for `skill-eval-tasks-v1`;
- schema/workflow tests for `skill-rollouts-v1`;
- schema/workflow tests for `skill-update-report-v1`;
- leakage tests proving variants of one seed cannot cross Dtrain/Dsel/Dtest;
- no `src/` imports from `tests/`.

Verification:

```bash
.venv/bin/python -m pytest tests/test_skill_intelligence.py tests/test_skill_opt.py -q
```

### Phase 4: Otbox Default-CI Journey

Work:

- keep the deterministic synthetic path as the default CI path;
- keep real Claude/Codex evidence opt-in and separately blockable;
- confirm the new Skill Intelligence path is represented in `make otbox-journeys`
  without requiring live agents or network.

Verification:

```bash
make otbox-journeys
```

### Phase 5: Full Closeout

Work:

- run focused tests after each change;
- run the full suite only once the focused surface is stable;
- update `runs/skill-intelligence-pipeline/log.md` with every attempt;
- leave case-study artifacts committed and self-describing.

Verification:

```bash
.venv/bin/python -m ruff check src/opentraces/capture/codex_cli/parse.py \
  src/opentraces/core/skill_detection.py src/opentraces/core/ingest.py \
  src/opentraces/core/trace_index.py src/opentraces/cli/__init__.py \
  tests/capture/test_parser_codex_cli_advanced.py tests/core/test_skill_detection.py \
  tests/core/test_ingest.py tests/cli/test_cli_init.py
.venv/bin/python -m pytest tests/test_skill_opt.py -q
make otbox-journeys
.venv/bin/python -m pytest tests/ -q
git diff --check
```

## Boundaries

Allowed:

- `src/opentraces/capture/codex_cli/parse.py`;
- `src/opentraces/core/skill_detection.py`;
- relevant Trace Index read-side code;
- ingest/CLI repair plumbing;
- workflow templates for the four Skill Intelligence row types;
- `src/opentraces/consumers/skill_intelligence/`;
- `src/opentraces/consumers/skill_opt/` where needed;
- focused tests, including `tests/otbox`;
- `runs/skill-intelligence-pipeline/`.

Avoid:

- destructive bucket cleanup or history deletion;
- non-additive schema changes;
- changing Trail/Context Tree write contracts except for separately justified
  additive fixes;
- network/live-agent requirements in default CI;
- automatic skill promotion;
- presenting otbox as an end-user product surface.

## Logging Contract

Append every attempt to `runs/skill-intelligence-pipeline/log.md` with:

- phase;
- diff/change;
- evidence observed;
- split/leakage notes;
- case-study artifact updates;
- next-step rationale.

On block, append `BLOCKED:` with attempted paths, evidence gathered, the blocker,
and the input that would unlock progress.
