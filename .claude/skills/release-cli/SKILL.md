---
name: release-cli
description: >
  Release a new version of the opentraces CLI to PyPI, Homebrew, and GitHub.
  Use when the user says "release", "new release", "bump version", "publish new version",
  "release opentraces", "cut a release", or "release-cli". This handles the CLI package
  only, not the schema package separately.
---

# Release CLI

Automate a new opentraces CLI release across PyPI, Homebrew, and GitHub.

## What this does

1. Bumps the version number (patch by default, or minor/major if requested)
2. Updates all version references in the repo
3. Runs tests and verifies the build
4. Commits, tags, and pushes
5. Creates a GitHub Release (which triggers PyPI publish + Homebrew formula update)
6. Monitors the publish workflow

## Arguments

The user may specify:
- **bump type**: `patch` (default), `minor`, or `major`
- **explicit version**: e.g. `0.2.0` (overrides bump type)

Parse from the user's message. Examples:
- "release" → patch bump
- "minor release" → minor bump
- "release 0.2.0" → explicit version

## Version files to update

These three files must stay in sync:

| File | Field | Example |
|------|-------|---------|
| `src/opentraces/__init__.py` | `__version__ = "X.Y.Z"` | `__version__ = "0.2.0"` |
| `packages/opentraces-schema/src/opentraces_schema/version.py` | `SCHEMA_VERSION = "X.Y.Z"` | `SCHEMA_VERSION = "0.2.0"` |
| `web/site/src/lib/version.json` | `{"version":"X.Y.Z"}` | `{"version":"0.2.0"}` |

## Steps

### 1. Read current version and compute new version

```bash
grep '__version__' src/opentraces/__init__.py
```

Parse the semver components (major.minor.patch) and apply the bump.

### 2. Show the user what will happen

Before making changes, print a summary:

```
Release plan:
  Current version: 0.1.1
  New version:     0.1.2
  Bump type:       patch

  Files to update:
    - src/opentraces/__init__.py
    - packages/opentraces-schema/src/opentraces_schema/version.py
    - web/site/src/lib/version.json

  After commit: tag v0.1.2, push, create GitHub Release
  This triggers: PyPI publish + Homebrew formula update
```

Ask the user to confirm before proceeding.

### 3. Update version files

Edit all three files listed above with the new version string.

### 4. Run tests

```bash
source .venv/bin/activate
pytest tests/ -q
```

If tests fail, stop and fix before continuing.

### 5. Verify build

```bash
source .venv/bin/activate
cd packages/opentraces-schema && rm -rf dist && python -m build && cd ../..
rm -rf dist && python -m build --wheel
```

Both builds must succeed. If either fails, stop and investigate.

### 6. Commit

```bash
git add src/opentraces/__init__.py packages/opentraces-schema/src/opentraces_schema/version.py web/site/src/lib/version.json
git commit -m "release: opentraces vX.Y.Z"
```

### 7. Tag and push

```bash
git tag -a vX.Y.Z -m "opentraces vX.Y.Z"
git push origin main --tags
```

### 8. Create GitHub Release

```bash
gh release create vX.Y.Z --title "opentraces vX.Y.Z" --notes "$(cat <<'EOF'
## Install

\`\`\`bash
pipx install opentraces==X.Y.Z
# or
brew install JayFarei/opentraces/opentraces
\`\`\`

## Changes since vPREVIOUS

- [summarize key changes from git log since last tag]
EOF
)"
```

Generate the changelog from `git log --oneline vPREVIOUS..HEAD` (where vPREVIOUS is the previous tag). Write concise, user-facing bullet points, not raw commit messages.

### 9. Monitor publish workflow

```bash
gh run list --workflow=publish.yml --limit 1 --json status,conclusion,databaseId
```

Tell the user the workflow has been triggered and they can watch it at the Actions tab. If they ask, poll until completion:

```bash
gh run watch <run-id>
```

### 10. Verify install

After the workflow succeeds:

```bash
python3 -m venv /tmp/ot-release-verify
source /tmp/ot-release-verify/bin/activate
pip install opentraces==X.Y.Z
opentraces --version
rm -rf /tmp/ot-release-verify
```

## Important notes

- The GitHub Release triggers two workflows automatically:
  - `publish.yml` — publishes both packages to PyPI via OIDC trusted publishing
  - `update-homebrew.yml` — updates the Homebrew tap formula with the new SHA (requires `HOMEBREW_TAP_TOKEN` secret)
- PyPI does not allow re-uploading the same version. If a release fails partway, you must bump again.
- The schema version and CLI version are kept in sync for simplicity, but they are independent packages.
