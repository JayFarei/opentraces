---
name: release-schema
description: >
  Release a new version of the opentraces-schema package to PyPI.
  Use when the user says "release schema", "bump schema", "schema release",
  "publish schema", "release-schema", or when schema models have changed
  and need publishing independently of the CLI. This handles the schema
  package only, not the CLI.
---

# Release Schema

Automate a new opentraces-schema release to PyPI and GitHub.

## How this differs from /release-cli

The schema package (`opentraces-schema`) versions independently. It uses:
- A different version file (`SCHEMA_VERSION` not `__version__`)
- A different tag format (`schema-vX.Y.Z` not `vX.Y.Z`)
- Its own changelog and rationale documents
- A separate PyPI publish (can be triggered alone via workflow dispatch)

## Arguments

The user may specify:
- **bump type**: `patch` (default), `minor`, or `major`
- **explicit version**: e.g. `0.2.0` (overrides bump type)

Parse from the user's message. Examples:
- "release schema" → patch bump
- "minor schema release" → minor bump
- "release schema 0.2.0" → explicit version

## Semver rules for this schema

- **MAJOR**: Breaking changes to existing fields (rename, remove, type change)
- **MINOR**: New optional fields, new models, new enum values
- **PATCH**: Docstring fixes, validation constraint tweaks, computed field bugs

During pre-1.0, MINOR bumps may include breaking changes.

## Files to update

| File | What to change |
|------|---------------|
| `packages/opentraces-schema/src/opentraces_schema/version.py` | `SCHEMA_VERSION = "X.Y.Z"` |
| `packages/opentraces-schema/CHANGELOG.md` | Add new version entry, move [Unreleased] items |
| `packages/opentraces-schema/RATIONALE-X.Y.Z.md` | Create new rationale doc for this version |
| `packages/opentraces-schema/FIELD-MAPPINGS.md` | Update if fields were added/renamed/removed |
| `web/site/src/lib/schema-versions.ts` | Add new version object with field definitions |
| `web/site/src/components/SchemaExplorer.tsx` | Update inline schema example if fields changed |

Also check whether the CLI's dependency constraint in `pyproject.toml` needs widening:
```toml
dependencies = [
    "opentraces-schema>=0.1.0",  # may need updating
]
```

## Steps

### 1. Read current version and compute new version

```bash
grep 'SCHEMA_VERSION' packages/opentraces-schema/src/opentraces_schema/version.py
```

Parse the semver and apply the bump.

### 2. Show the user what will happen

```
Schema release plan:
  Current version: 0.1.1
  New version:     0.2.0
  Bump type:       minor

  Files to update:
    - packages/opentraces-schema/src/opentraces_schema/version.py
    - packages/opentraces-schema/CHANGELOG.md
    - packages/opentraces-schema/RATIONALE-0.2.0.md (new)
    - packages/opentraces-schema/FIELD-MAPPINGS.md (if fields changed)
    - web/site/src/lib/schema-versions.ts
    - pyproject.toml (if constraint needs widening)

  After commit: tag schema-v0.2.0, push, publish via workflow dispatch
```

Ask the user to confirm before proceeding.

### 3. Update version file

Edit `packages/opentraces-schema/src/opentraces_schema/version.py`.

### 4. Update CHANGELOG.md

Read the current changelog format. Add a new version header with the date, move any [Unreleased] items under it. Generate change entries from `git log` since the last schema tag:

```bash
git log --oneline $(git tag -l 'schema-v*' --sort=-v:refname | head -1)..HEAD -- packages/opentraces-schema/
```

### 5. Create RATIONALE document

Create `packages/opentraces-schema/RATIONALE-X.Y.Z.md` documenting the design decisions for this version. Read the previous rationale file for format reference. Include:
- What changed and why
- Trade-offs considered
- Migration notes (for breaking changes)

### 6. Update FIELD-MAPPINGS.md (if fields changed)

Check if any fields were added, renamed, or removed by diffing the Pydantic models:

```bash
git diff $(git tag -l 'schema-v*' --sort=-v:refname | head -1)..HEAD -- packages/opentraces-schema/src/opentraces_schema/models.py
```

If fields changed, update the ATIF/ADP/OTel mapping tables in FIELD-MAPPINGS.md.

### 7. Update schema-versions.ts

Add a new version entry in `web/site/src/lib/schema-versions.ts` with all field definitions matching the updated Pydantic models.

### 8. Check CLI dependency constraint

Read `pyproject.toml` and check if `opentraces-schema>=X.Y.Z` still covers the new version. If the new version is a major bump, the constraint likely needs updating.

### 9. Build and test

```bash
source .venv/bin/activate
cd packages/opentraces-schema
rm -rf dist
python -m build
python -m twine check dist/*
cd ../..
pytest tests/ -q
```

### 10. Commit and tag

```bash
git add packages/opentraces-schema/ web/site/src/lib/schema-versions.ts pyproject.toml
git commit -m "release: opentraces-schema vX.Y.Z"
git tag -a schema-vX.Y.Z -m "opentraces-schema vX.Y.Z"
git push origin main --tags
```

### 11. Publish via workflow dispatch

The schema package does not auto-publish on a GitHub Release (that's for the CLI). Instead, trigger the publish workflow manually:

```bash
gh workflow run publish.yml -f repository=pypi -f package=opentraces-schema
```

Monitor:
```bash
gh run list --workflow=publish.yml --limit 1 --json status,conclusion
```

### 12. Verify install

```bash
python3 -m venv /tmp/schema-verify
source /tmp/schema-verify/bin/activate
pip install opentraces-schema==X.Y.Z
python -c "from opentraces_schema import SCHEMA_VERSION; print(SCHEMA_VERSION)"
rm -rf /tmp/schema-verify
```

## Important notes

- The schema tag format is `schema-vX.Y.Z` (not `vX.Y.Z` which is for the CLI)
- Schema releases do not trigger automatically from GitHub Releases, use workflow dispatch
- After a schema release, you may want to follow up with a CLI release (`/release-cli`) if the CLI needs the new schema version
- PyPI does not allow re-uploading the same version. If a release fails, you must bump again.
