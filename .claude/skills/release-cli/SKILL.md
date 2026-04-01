---
name: release-cli
description: >
  Release a new version of the opentraces CLI to PyPI, Homebrew, and GitHub.
  Use when the user says "release", "new release", "bump version", "publish new version",
  "release opentraces", "cut a release", or "release-cli". This handles the CLI package
  only, not the schema package separately. For a full coordinated release of schema + CLI +
  site together, use /release-pack instead.
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

| File                                                          | Field                      | Example                    |
| ------------------------------------------------------------- | -------------------------- | -------------------------- |
| `src/opentraces/__init__.py`                                  | `__version__ = "X.Y.Z"`    | `__version__ = "0.2.0"`    |
| `packages/opentraces-schema/src/opentraces_schema/version.py` | `SCHEMA_VERSION = "X.Y.Z"` | `SCHEMA_VERSION = "0.2.0"` |
| `web/site/src/lib/version.json`                               | `{"version":"X.Y.Z"}`      | `{"version":"0.2.0"}`      |

## Steps

### 1. Determine the true released version from remote sources

Local version files may have been bumped during development without a corresponding release. Always query the live remote sources first — they are the authoritative baseline.

```bash
# PyPI
curl -s https://pypi.org/pypi/opentraces/json | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"

# GitHub Releases
gh release list --limit 3 --json tagName,publishedAt

# Git tags
git tag -l 'v[0-9]*' --sort=-v:refname | head -3

# Homebrew
brew info JayFarei/opentraces/opentraces 2>/dev/null | head -3
```

The **published version** is the most recent version that appears on both PyPI and as a GitHub Release tag. Use this as the base for the bump, not the local file.

Then check what has changed since that tag:

```bash
LAST_TAG=$(git tag -l 'v[0-9]*' --sort=-v:refname | head -1)
git log --oneline ${LAST_TAG}..HEAD
```

Infer the bump type from the commit content unless the user specified one:

| Signal | Bump |
|---|---|
| Breaking flag rename, command removal | major |
| New subcommand, new option | minor |
| Bug fixes, refactors, parser hardening | patch |

Finally compare local file with the remote version:

```bash
grep '__version__' src/opentraces/__init__.py
```

If local is ahead of remote, note the unreleased bump and use the remote version as the base.

### 2. Show the user what will happen

Before making changes, print a summary:

```
Release plan:
  Remote (published):  v0.1.2  (PyPI + GitHub Release)
  Local file:          0.1.3   (unreleased bump — ignored as base)
  New target:          v0.1.3  (patch — bug fixes since v0.1.2)

  Unreleased commits since v0.1.2:
    abc1234  fix(parser): harden tool_input extraction
    def5678  feat(site): update messaging

  Files to update:
    - src/opentraces/__init__.py
    - packages/opentraces-schema/src/opentraces_schema/version.py
    - web/site/src/lib/version.json

  After commit: tag v0.1.3, push, create GitHub Release
  This triggers: PyPI publish + Homebrew formula update
```

**STOP HERE.** Do not edit any files, run any commands, or take any action until the user explicitly confirms. Accept "y", "yes", "go", "ship it", "looks good", or equivalent. If the user wants to adjust the version, bump type, or anything else, update the plan and re-present it. Only proceed once you have an unambiguous green light.

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

Wait ~120 seconds after the publish workflow succeeds before running these checks, so PyPI has time to propagate.

**pipx** (primary install method for end users):

```bash
pipx install opentraces==X.Y.Z --force
opentraces --version
pipx uninstall opentraces
```

**pip + fresh venv** (secondary check):

```bash
python3 -m venv /tmp/ot-release-verify
source /tmp/ot-release-verify/bin/activate
pip install opentraces==X.Y.Z
opentraces --version
deactivate
rm -rf /tmp/ot-release-verify
```

**brew** (tap install):

```bash
brew upgrade JayFarei/opentraces/opentraces
# or if not installed:
brew install JayFarei/opentraces/opentraces
opentraces --version
```

If brew is still on the old version, check the `update-homebrew.yml` workflow status — it may still be running:

```bash
gh run list --workflow=update-homebrew.yml --limit 1 --json status,conclusion
```

## Important notes

- The GitHub Release triggers two workflows automatically:
  - `publish.yml` — publishes both packages to PyPI via OIDC trusted publishing
  - `update-homebrew.yml` — updates the Homebrew tap formula with the new SHA (requires `HOMEBREW_TAP_TOKEN` secret)
- PyPI does not allow re-uploading the same version. If a release fails partway, you must bump again.
- The schema version and CLI version are are independent packages and their version can drift
