# Handoff — 0.3.3 → 0.4 Migration Acceptance Suite

**For:** the agent picking up the `/goal` in `GOAL.md`.
**Spec (authoritative):** `kb/plans/085-migration-0.3.3-to-0.4-test.md`.
**Run log:** append to `runs/migration-0.3.3-to-0.4/log.md` every iteration.

## What this is

Land a migration acceptance test, and fix what it catches, proving a real
opentraces **0.3.3** user can upgrade to **0.4** with zero data loss. Scope is
**test + fix** (confirmed by the user).

## The boundary (already verified — do not re-derive, just trust + spot-check)

- Source: CLI `0.3.3` / schema `0.3.0` (tag `v0.3.3`, 2026-04-20).
- Target: CLI `0.4.0` / schema `0.6.0` (working tree, untagged).
- A 0.3.3 user has **only**: project dirs + JSONL traces (schema 0.3.0,
  carrying a `patch` field) + `state.json` + 0.3.3 config + maybe an HF dataset
  declared at schema 0.3.0. **No** bucket / trails event log / context tree /
  local datasets — those are all post-0.3.3. Migration is mostly *adopting new
  subsystems from old primitives*, not transforming old subsystems.
- **The one breaking change:** `TraceRecord.patch` was removed in schema 0.6.0.
  `HFUploader.migrate_outdated_shards` re-validates old records through the
  0.6.0 model and (Pydantic `extra="ignore"`) **silently drops `patch`** with an
  empty `patches[]`. This is the central data-loss bug. Decided handling:
  **reconstruct `patch` → `patches[]`**, fallback **preserve raw under
  `metadata.legacy.patch`**; re-confirm after the S2 audit.
- `opentraces_schema.migrations` was *reserved* but never created. No registered
  migration exists. `core/migrate_trace_ids.py` only does pre-0.3 id rewrites.

## Why otbox can't do this yet (the enabling work)

otbox only seeds *current-world* (0.4) boxes. There is **no legacy-version world
seeder or checkpoint**. The first real task is building one:
`c-legacy-v033` restores a frozen 0.3.3 world; a 0.4-upgrade step/checkpoint runs
the new CLI over it. Follow the **plan-072 artifact-preferred / synthetic-
fallback** pattern so CI stays network-free and deterministic. Checkpoint
metadata already records `opentraces_schema_version` / `opentraces_cli_version`
— reuse that provenance field.

## Where things live (orientation)

- otbox: `tests/otbox/` — `seed.py`, `snapshot.py`, `env.py`, `journey.py`,
  `inventory.py`, `matrix.py`, `checkpoints/` (register pattern in
  `checkpoints/__init__.py`), `catalogue/journeys/*.toml` (126 today), `README.md`.
- Schema: `packages/opentraces-schema/src/opentraces_schema/` — `version.py`
  (`SCHEMA_VERSION="0.6.0"`), `models.py`, `VERSION-POLICY.md`,
  `RATIONALE-0.{4,5,6}.0.md`, `CHANGELOG.md`.
- HF migration: `src/opentraces/publish/huggingface/upload.py`
  (`migrate_outdated_shards`, `detect_outdated_shards`, `RemoteSchemaAheadError`).
- Bucket: `src/opentraces/core/bucket_store.py`, `bucket_remote.py`.
- Migration helper: `src/opentraces/core/migrate_trace_ids.py`.

## Scenario list (full) — in the spec, summarized here

S1 read-compat/no-crash · S2 schema field-loss audit · S3 `patch`→`patches[]`
reconstruction · S4 trace-id idempotency · S5 legacy traces read-in-place
(RESOLVED: 0.3 had no bucket, nothing to adopt) · S6 HF shard forward-migration
· S7 HF downgrade refusal (reciprocal) · S8 config forward-compat · S9 git-ref
additivity · S10 migration idempotency · S11 non-destructive/recoverable · S12
end-to-end upgrade journey (gold ship gate).

## STATUS (2026-05-28, PAUSED for handoff)

DONE on `main` (`3efdeeae9c`), 16 tests green, no regression — see
`runs/migration-0.3.3-to-0.4/log.md` for the blow-by-blow:
- Phase 1 / S2 audit · Phase 4 keystone (the `migrate_record` reconstruction +
  HF wiring — the whole point) · S6 · R1 frozen v0.3.3 world
  (`tests/migration/fixtures/legacy_world_v033/`) + R2 restore helper · S1
  read-compat verified against the live 0.4 CLI · v0.3.3 venv builds at
  `/tmp/ot-v033-worktree/.venv-v033` (recreate via `git worktree add` v0.3.3 +
  stub `web/viewer/dist/index.html` before `pip install -e .`).

RESUME from Phase 3: `c-legacy-v033` checkpoint wrapping
`tests/migration/restore_legacy_world.py`, then journey TOMLs for
S1/S7/S8/S9/S10/S11/S12 (S2/S3/S4/S6 are pytest-covered), precondition vocab +
tiered-gate/inventory wiring, S8 config-compat check, Phase 5 docs. S5 is
read-in-place (assert, don't build adoption).

## How to verify

- `source .venv/bin/activate` for all CLI/pytest; system Python lacks the
  editable packages.
- otbox slices: `make otbox-slice` / `otbox-journeys` / `otbox-matrix` /
  `otbox-inventory`. New migration journeys must enter the tiered gate +
  inventory coverage map (plan 069 / 062).
- Build the 0.3.3 fixture from the **actual `v0.3.3` tag** in an isolated venv
  (`pip install` the tagged CLI), never by hand-faking 0.3.0 records — the point
  is real legacy bytes. Commit the frozen artifact + a synthetic fallback.

## Guardrails

- Plans in this repo often route through Ultraplan before implementation. This
  handoff + goal exist to *seed that*. Confirm with the user before treating the
  local plan as final and starting Phase 2+ implementation, unless told to run.
- Commit on `main` and push `origin main` directly in this repo (user
  preference) — but keep migration-suite work reviewable; land in coherent
  commits.
- No `—` em-dashes in prose the user reads.

## Blocked stop condition

Stop and surface to the user if: the S2 audit reveals a *second* breaking field
change (scope grows), the real `v0.3.3` venv cannot be built in this environment
(fixture provenance at risk), or S5's bucket-adoption path requires a product
decision the spec's Open Decisions can't resolve.
