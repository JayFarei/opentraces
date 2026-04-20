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
an existing field will silently drop data during migration and is therefore
forbidden. Changes of that shape must bump MAJOR and ship with an explicit
migration function registered in `opentraces_schema.migrations` (module
reserved for that purpose; no implementation needed until a breaking change
actually lands).

The reciprocal CLI behavior: when a client encounters a remote whose
declared schema is newer than the client's local version, the push is
refused with an `ot setup upgrade` prompt. The client never overwrites a
newer declared schema with an older one.

During 0.x development the "MINOR may include breaking changes" clause
above is scoped down by this contract: breaking changes during 0.x are
still allowed, but they must ride a MAJOR bump (0.x → 0.y is additive;
0.x → 1.0 or future 1.x → 2.0 carries the breaking change).
