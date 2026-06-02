# Version Policy

opentraces-schema follows Semantic Versioning (semver) with schema-specific semantics.

## What the version numbers mean

- **MAJOR** (X.0.0): Breaking changes to existing fields. Renaming, removing, or
  changing the type of an existing field. Consumers must update parsers.
- **MINOR** (0.X.0): New optional fields, new models, new enum values added to
  existing Literal types. Existing parsers continue to work without changes.
- **PATCH** (0.0.X): Docstring fixes, validation constraint adjustments that do
  not change the serialized format, bug fixes in computed fields.

## Pre-1.0 stability

During 0.x development, MINOR bumps may include breaking changes. The schema is
not yet stable. Pin to exact versions (`opentraces-schema==0.2.0`) rather than
ranges until 1.0.

## Where the version lives

The single source of truth is `src/opentraces_schema/version.py`. The `SCHEMA_VERSION`
constant is used by:

- `pyproject.toml` via hatch dynamic versioning
- `TraceRecord.schema_version` default

## Bump checklist

1. Update `SCHEMA_VERSION` in `src/opentraces_schema/version.py`
2. Add entry to `CHANGELOG.md` under `[Unreleased]`, then move to new version header
3. Create `RATIONALE-{VERSION}.md` documenting design decisions for the new version
4. Link the rationale file from the CHANGELOG entry
5. Tag the commit: `git tag schema-v{VERSION}`

## CLI migration contract

The opentraces CLI automatically migrates older shards up to the client's
schema version when a user pushes to a remote whose declared schema is
older than their local version. For that auto-migration to be safe without
explicit per-release migration code, MINOR and PATCH bumps must be
strictly additive:

- New optional fields (with defaults)
- New nested optional BaseModels
- Widened types (narrow to wide)
- New enum values in existing Literal types

A MINOR or PATCH bump that renames, moves, removes, narrows, or restructures
an existing field will silently drop data during migration unless an explicit
forward-migration recovers it. Such a change must ship with a migration
function registered in `opentraces_schema.migrations` that is additive,
idempotent, non-mutating, and provably non-lossy (every dropped value is
either reconstructed into a new field or preserved verbatim under
`metadata.legacy.*`).

The reserved `opentraces_schema.migrations` module is now implemented. The
`0.5.0 -> 0.6.0` bump removed `Outcome.patch` (the session unified diff) in
favour of the `TraceRecord.patches[]` spine; this is the breaking change the
module was reserved for. `migrate_record` reconstructs `patches[]` from the
legacy diff and preserves the raw diff under `metadata.legacy.patch`, so the
removal is non-lossy through both the HuggingFace shard migration and the
bucket path. The `0.3.3 -> 0.4` upgrade acceptance suite (kb/plans/085,
`tests/test_migration_0_3_3_to_0_4.py`, and the otbox `c-legacy-v033`
journeys) is the standing proof that the only field a 0.3.0 record loses is
`Outcome.patch` and that `migrate_record` recovers it. See
[MIGRATION-0.3.3-to-0.4.md](MIGRATION-0.3.3-to-0.4.md).

Future breaking changes that cannot be made non-lossy by a registered
migration must instead bump MAJOR (`0.x -> 1.0`, future `1.x -> 2.0`).

The reciprocal CLI behavior: when a client encounters a remote whose
declared schema is newer than the client's local version, the push is
refused with an `ot setup upgrade` prompt. The client never overwrites a
newer declared schema with an older one.

During 0.x development the "MINOR may include breaking changes" clause
above is scoped down by this contract: breaking changes during 0.x are
still allowed, but they must ride a MAJOR bump (0.x → 0.y is additive;
0.x → 1.0 or future 1.x → 2.0 carries the breaking change).
