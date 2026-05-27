# Run log — 0.3.3 → 0.4 Migration Acceptance Suite

Append one entry per iteration. Newest at the bottom.

---

## 2026-05-27 — seeding

- Mapped the migration boundary (see `kb/plans/085`): source CLI 0.3.3 / schema
  0.3.0, target CLI 0.4.0 / schema 0.6.0, 190 commits apart.
- Confirmed a 0.3.3 user has no bucket / trails / context tree / local datasets.
- Confirmed the one breaking change: `TraceRecord.patch` removed in 0.6.0;
  `migrate_outdated_shards` silently drops it. `opentraces_schema.migrations`
  never created.
- Confirmed otbox has no legacy-version world seeder/checkpoint — that is the
  first enabling task (R1–R3).
- Wrote plan 085 (spec + S1–S12 scenarios), this handoff, and GOAL.md.
- **Next:** Phase 1 audit (S2) — pin the exact added/dropped field set by running
  a real 0.3.0 record through the 0.6.0 model; then decide S3 reconstruction.

## 2026-05-27 — Phase 1 (S2 audit) COMPLETE

- Change: added `tests/migration/audit_schema_fieldloss.py` +
  `tests/migration/fixtures/record_schema_0_3_0.json` (committed, reproducible
  S2 audit). Full recursive class×field diff of schema models v0.3.3 vs HEAD.
- Evidence: the ONLY removed field across 0.3.0 → 0.6.0 is **`Outcome.patch`**
  (no classes removed; new classes `GitAnchor`, `Patch`). Runtime round-trip of
  a 0.3.0 record through the 0.6.0 model: `outcome.patch` is **silently dropped**
  (Pydantic `extra="ignore"`, no `model_config`), `patches[]` is empty,
  `schema_version` stays `0.3.0` on bare `model_validate` (HF path re-stamps
  separately). Audit verdict: PRE-FIX.
- Decision: S3 confirmed = reconstruct `outcome.patch` (standard unified diff,
  structurally parseable) into `patches[]`, fallback preserve under
  `metadata.legacy.patch`. No second breaking field change — scope holds.
- Next: de-risk the named blocker (build a real v0.3.3 venv for the fixture),
  then implement the S3 reconstruction (flips the audit to FIXED).

## 2026-05-27 — blocker cleared + Phase 4 keystone (S3) + S6 done

- Blocker CLEARED: real v0.3.3 CLI / schema 0.3.0 builds in an isolated venv via
  `git worktree add /tmp/ot-v033-worktree v0.3.3` then `python -m venv` +
  `pip install -e packages/opentraces-schema` + `pip install -e .`. One snag:
  the CLI wheel force-includes `web/viewer/dist` (gitignored build output) —
  fixed by stubbing `web/viewer/dist/index.html`. Verified `opentraces 0.3.3 |
  schema 0.3.0`. Phase 2 fixture is now buildable.
- Change (keystone fix): added `packages/opentraces-schema/.../migrations.py` —
  the reserved migrations module, now real. `migrate_record(raw, target)` is the
  single source of truth: reconstructs `patches[]` from the legacy unified diff
  (one Patch per file, content-addressed `patch_id`, provenance markers),
  preserves the raw diff under `metadata.legacy.patch`, drops the dead
  `outcome.patch`, additive + idempotent + non-mutating. Wired into
  `HFUploader.migrate_outdated_shards` (one call site) so HF + bucket share it.
- Evidence: audit now FIXED — bare load loses only `outcome.patch`;
  `migrate_record` recovers both files (foo.py, bar.py) + preserves raw diff.
  pytest: `tests/test_migration_0_3_3_to_0_4.py` 10/10; new S6 integration test
  `test_migrate_shard_reconstructs_legacy_outcome_patch` green; full
  `TestSchemaMigration` + 57 upload/migrate tests pass (no regression).
- Next: Phase 2 — drive the v0.3.3 venv to build a real legacy world (project +
  0.3.0 traces with outcome.patch + state.json + config + 0.3.0 HF dataset),
  freeze it, add `c-legacy-v033` + upgrade checkpoint (plan-072 pattern).

## 2026-05-27 — Phase 2 R1/R2 done + S1 verified + S5 decision needed

- Change: `tests/migration/build_legacy_world.py` drives the REAL v0.3.3 CLI
  (`init` + `_scan --reparse`) on a synthetic Claude session and freezes a
  genuine schema-0.3.0 world to `tests/migration/fixtures/legacy_world_v033/`
  (scrubbed paths + PROVENANCE.json). `tests/migration/restore_legacy_world.py`
  rehydrates it into a live HOME/project (the restore the `c-legacy-v033`
  checkpoint will perform). Real v0.3.3 ingest emits a 3-step 0.3.0 trace;
  builder guarantees `outcome.patch`.
- Evidence (pytest): migration set now 16/16 (incl. real-fixture-bound
  `test_frozen_legacy_trace_migrates_without_loss` — the frozen 0.3.0 record's
  `app.py` reconstructs into `patches[]`, raw diff preserved).
- Evidence (0.4 CLI over restored world):
  * S1 read-compat PASSES — `--version`/`status`/`doctor` rc=0, and
    `trace query farewell` rc=0 FINDS the legacy trace
    (6961102e… "add a farewell() helper"). No crash reading 0.3.3 state.
  * S5: `ctx list` -> "No traces in local bucket." The legacy trace is fully
    readable in-place but is NOT in the bucket. `setup bucket --migrate` only
    upgrades a pre-plan-080 bucket *layout*; NO path adopts legacy
    `traces/*.jsonl` into the v2 bucket. read-in-place is non-lossy and works.
- DECISION NEEDED (S5 Open Decision, blocked-stop trigger): pick the 0.3.3→0.4
  bucket contract — (A) read-in-place is the contract (document; bucket holds
  only 0.4+ captures), or (B) build an explicit `bucket adopt-legacy`
  (reads traces/*.jsonl -> migrate_record -> write_trace_record). Determines
  Phase 4 bucket scope + what the S5/S12 journeys assert. Surfaced to user.
- Committed the green increment (migrations + audit + builder + fixture +
  restore + tests). Remaining: c-legacy-v033 checkpoint + 12 journey TOMLs +
  precondition vocab/gate (Phase 3), S5 per decision, S8 config-forward-compat
  check, docs (Phase 5).
