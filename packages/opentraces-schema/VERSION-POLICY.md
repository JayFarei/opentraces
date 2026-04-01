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
