# Migration: opentraces 0.3.3 → 0.4

This note records the upgrade contract for a real opentraces **0.3.3** user
(CLI `0.3.3` / schema `0.3.0`) moving to **0.4** (CLI `0.4.0` / schema `0.6.0`).
It is the documentation half of the acceptance suite in `kb/plans/085`,
`tests/test_migration_0_3_3_to_0_4.py`, and the otbox `c-legacy-v033` /
`c-legacy-v033-upgraded` journeys.

## What a 0.3.3 user has on disk

Project dirs with JSONL trace records at schema `0.3.0`, `state.json`, a
0.3.3-shaped `config.json`, the `.opentraces.json` marker, and possibly one or
more HuggingFace datasets whose `dataset_infos.json` declares schema `0.3.0`.

They do **not** have a private bucket, a Trace Trails append-only event log,
a Context Tree, or local datasets/workflows — every one of those subsystems
landed *after* 0.3.3. Migration is therefore mostly *adoption of new
subsystems from old primitives*, not *transformation of old subsystems*.

## The one breaking change: `Outcome.patch`

Schema `0.6.0` removed `Outcome.patch` (the session unified diff) in favour of
the `TraceRecord.patches[]` spine. It is the only field a 0.3.0 record loses on
a bare load — confirmed by the reproducible field-loss audit in
`tests/migration/audit_schema_fieldloss.py` (S2): no other field is dropped and
no model is removed.

**The decision:** rather than accept the loss, the removal rides a registered
forward-migration. `opentraces_schema.migrations.migrate_record`:

- reconstructs `patches[]` from the legacy unified diff (one `Patch` per file,
  content-addressed `patch_id`, provenance markers
  `capture_method=legacy_outcome_patch_migration`);
- preserves the raw diff verbatim under `metadata.legacy.patch` (so a diff that
  cannot be structurally reconstructed is still recoverable);
- is additive, idempotent, and never mutates its input.

Both the HuggingFace shard migration (`HFUploader.migrate_outdated_shards`) and
the bucket path call `migrate_record`, so legacy devtime data survives the
upgrade through exactly one code path. A bare `TraceRecord.model_validate`
still drops `Outcome.patch` — always route a pre-0.6.0 record through
`migrate_record` first.

## Read-in-place: the bucket holds only 0.4+ captures

0.3.3 had no bucket subsystem at all, so there is nothing to *migrate into* a
bucket — the bucket is a 0.4-only concept. The contract is **read-in-place**:

- a 0.3.3 user's `traces/*.jsonl` stay fully readable on 0.4 (`status`,
  `doctor`, `trace query`, and — after the 0.4 first-touch `trace index
  rebuild` — `trace get` / `trace map` all resolve the legacy trace);
- the private bucket and Context Tree honestly report empty on a freshly
  upgraded world; nothing auto-adopts the legacy traces;
- a new session captured on 0.4 inside the legacy repo creates
  `refs/opentraces/local/events/v1` + `refs/notes/opentraces` (and trace
  snapshot refs) **additively** over the repo's pre-existing history — the
  original commits are never rewritten.

There is intentionally no forced/automatic bucket adoption. An opt-in
`bucket adopt-legacy` is possible future polish, explicitly out of scope for a
non-lossy upgrade.

## Reciprocal safety: a 0.3.3 client never clobbers a 0.6.0 remote

The schema-ahead guard predates this work and is byte-identical in 0.3.3 and
0.4 (`HFUploader._sync_dataset_infos`). A 0.3.3 client (local schema `0.3.0`)
pushing to a remote already declaring `0.6.0` is refused with
`RemoteSchemaAheadError`; the 0.3.3 push CLI (`cli/publish.py`) maps that to
exit code 3 with an `ot setup upgrade` hint and never overwrites the newer
`dataset_infos.json`. Covered by the S7 tests (including a layer that drives the
real v0.3.3 client).

## Non-destructive / recoverable

Reading the legacy world on 0.4 never rewrites the source records: the on-disk
`schema_version` stays `0.3.0` and the original `Outcome.patch` bytes remain on
disk. Reconstruction happens only at publish/adoption time, into new artifacts.
The pre-upgrade trace data is always recoverable.
