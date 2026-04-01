---
name: release-pack
description: >
  Full coordinated release of all opentraces packages — schema, CLI, and marketing site — in
  a single orchestrated flow. Use when the user says "release everything", "full release",
  "release pack", "release all", "cut a full release", "ship it all", "release-pack", or
  when doing a versioned release that should touch all three packages at once. This is the
  right skill any time you want to go from current code to a published, verified release
  across PyPI (schema + CLI), GitHub Releases, Homebrew, and Vercel in one pass.
  It handles version bumps, docs checks, ordered publishing, propagation waits, and
  post-release verification of both pipx and brew installs automatically.
---

# Release Pack

Orchestrate a complete release: schema → CLI → site, with verification.

## When to use this vs. individual skills

- `/release-pack` — releasing everything together (the normal case)
- `/release-schema` — schema-only change with no CLI changes
- `/release-cli` — CLI-only change, schema unchanged
- `/deploy-site` — site copy/design change with no package release

---

## Arguments

The user may specify bump type and whether to skip the schema release:

| Input | Meaning |
|---|---|
| "release pack" | auto-detect bump type from commits |
| "minor release pack" | force minor bump on both |
| "release pack, skip schema" | CLI + site only, no schema release |
| "release pack CLI minor, schema patch" | explicit per-package bump types |
| "release pack 0.2.0" | explicit target version for CLI |

Parse these from the user's message before starting. The user-supplied bump type overrides the auto-detected one but the remote version is always the base.

---

## Step 0: Resolve branch and worktree context

Development often happens on feature branches or in git worktrees. Before touching any files or querying remote versions, establish where you are.

```bash
# Current branch and worktree
git branch --show-current
git worktree list

# Is this a worktree?
git rev-parse --git-dir
```

If you are on a feature branch (not `main`):
1. **Do not cut a release from the branch.** Releases must be tagged on `main`.
2. Check whether the branch has already been merged: `git log main..HEAD --oneline`
3. If unmerged, stop and ask the user: "You're on branch `BRANCH`. Should I land this to main first, or were you planning to release from `main` after merging?"
4. If the user confirms they want to release and the branch is already merged, switch to `main`: `git checkout main && git pull origin main`
5. If in a **worktree**, all git operations (tags, pushes) still operate against the shared repo, but `git push` will use the worktree's checked-out branch. Confirm the right branch is checked out before pushing tags.

Only proceed to Step 1 once you are on `main` and it reflects the intended release state.

---

## Step 1: Determine the true released versions from remote sources

Local version files cannot be trusted as the baseline — they may have been bumped during development without a corresponding release. Always query the live remote sources first.

### Fetch remote versions

```bash
# PyPI — most authoritative for published state
curl -s https://pypi.org/pypi/opentraces/json | python3 -c "import sys,json; d=json.load(sys.stdin); print('CLI PyPI:', d['info']['version'])"
curl -s https://pypi.org/pypi/opentraces-schema/json | python3 -c "import sys,json; d=json.load(sys.stdin); print('Schema PyPI:', d['info']['version'])"

# GitHub — last CLI release tag and last schema release tag
gh release list --limit 5 --json tagName,publishedAt | python3 -c "import sys,json; [print(r['tagName'], r['publishedAt']) for r in json.load(sys.stdin)]"
git tag -l 'v[0-9]*' --sort=-v:refname | head -3
git tag -l 'schema-v*' --sort=-v:refname | head -3

# Homebrew — what the tap currently pins
brew info JayFarei/opentraces/opentraces 2>/dev/null | head -3

# Live site — version displayed (relies on PyPI at deploy time, so PyPI is authoritative)
curl -s https://opentraces.ai | grep -o '"version":"[^"]*"' | head -1
```

Collect all of these. The **published CLI version** is the most recent version that appears on BOTH PyPI and as a GitHub Release tag. The **published schema version** is the most recent version on PyPI for `opentraces-schema`. If the sources disagree (e.g. PyPI has 0.1.2 but GitHub Release only has 0.1.1), flag the discrepancy — do not silently pick one.

### Compare with local files

```bash
grep '__version__' src/opentraces/__init__.py
grep 'SCHEMA_VERSION' packages/opentraces-schema/src/opentraces_schema/version.py
```

If local files are ahead of the remote (e.g. local is `0.1.3-dev` but PyPI is `0.1.2`), note that the local bump was unreleased and use the remote version as the true baseline. The new release target will be computed from the remote baseline, not from the local file.

---

## Step 2: Determine what changed and derive the appropriate bump

Use the remote version's corresponding git tag as the boundary. Everything committed since that tag is unreleased.

### CLI changes since last CLI tag

```bash
LAST_CLI_TAG=$(git tag -l 'v[0-9]*' --sort=-v:refname | head -1)
git log --oneline ${LAST_CLI_TAG}..HEAD
git diff --stat ${LAST_CLI_TAG}..HEAD
```

### Schema changes since last schema tag

```bash
LAST_SCHEMA_TAG=$(git tag -l 'schema-v*' --sort=-v:refname | head -1)
git log --oneline ${LAST_SCHEMA_TAG}..HEAD -- packages/opentraces-schema/src/
git diff --name-only ${LAST_SCHEMA_TAG}..HEAD -- packages/opentraces-schema/src/opentraces_schema/models.py
```

If there are no schema source changes since `LAST_SCHEMA_TAG`, default to skipping the schema release. The user can override with "force schema release".

### Infer bump type from commit content

Read the commit messages and changed files since the last tag and classify the bump, unless the user specified one explicitly:

| Signal in commits/diff | Bump |
|---|---|
| Breaking CLI flag rename, schema field removal, model rename | major |
| New subcommand, new optional schema field, new enum value | minor |
| Bug fixes, docstring updates, parser hardening, validation tweaks | patch |

When mixed signals exist, the highest-priority signal wins (major > minor > patch). Show your reasoning.

---

## Step 3: Show unified release plan and confirm

Before any changes, print a plan that shows the remote baseline, what changed, and the proposed target:

```
Release pack plan:

  Remote (live) versions:
    CLI:    v0.1.2  (PyPI + GitHub Release)
    Schema: v0.1.1  (PyPI)
    Site:   opentraces.ai shows v0.1.2
    Brew:   JayFarei/opentraces/opentraces @ 0.1.2

  Unreleased commits since v0.1.2:
    abc1234  fix(parser): harden tool_input extraction
    def5678  feat(site): update messaging
    [N more]

  Proposed release:
    CLI:    v0.1.2 → v0.1.3  (patch — bug fixes and hardening)
    Schema: v0.1.1 → skipping (no schema source changes)
    Site:   deploy after CLI tag

  Bump reasoning: commits are all fixes and non-breaking improvements

  Files to update:
    - src/opentraces/__init__.py  (0.1.2 → 0.1.3)
    - web/site/src/lib/version.json  (0.1.2 → 0.1.3)

  Sequence:
    1. Docs update (skill/SKILL.md is bundled in the wheel — must be correct before build)
    2. Regenerate llms.txt
    3. Release CLI → GitHub Release → PyPI + Homebrew  (v0.1.3)
    4. Deploy site → Vercel
    5. Wait ~120s for PyPI propagation
    6. Verify: pipx  pip  brew

Proceed? [Y/n]
```

**STOP HERE.** Do not run any commands, edit any files, or take any action until the user explicitly confirms. Accept "y", "yes", "go", "ship it", "looks good", or equivalent. If the user asks to adjust anything (different bump type, skip schema, change target version), update the plan and re-present it for another confirmation. Only move forward once you have an unambiguous green light.

---

## Step 4: Docs update

**`skill/SKILL.md` is bundled inside the wheel** (`pyproject.toml` force-includes it). Any stale command references or missing flags ship to end users and agents. Run `/docs-update` now — before building — so the wheel contains correct docs.

After docs-update completes:

```bash
# Regenerate llms.txt from updated docs source (never hand-edit it)
bash web/site/scripts/generate-llms-txt.sh
```

Commit any docs changes before proceeding to the build step.

### Quick spot-check (if skipping full docs-update)

Only skip the full docs-update if the release is a patch with no new commands, flags, or schema fields. In that case, do a minimal check:

```bash
# Version references
grep -r "0\." src/opentraces/__init__.py packages/opentraces-schema/src/opentraces_schema/version.py web/site/src/lib/version.json

# CLI command surface (spot check)
grep -E '@cli\.|@\w+\.command' src/opentraces/cli.py | head -20

# skill/SKILL.md quick-ref vs cli.py
grep 'opentraces ' skill/SKILL.md | grep -v '#' | head -20
```

Flag any obvious staleness but don't block the release unless it's a broken reference in `skill/SKILL.md` or `web/site/public/llms.txt`. Report issues and let the user decide whether to fix now or open a follow-up.

---

## Step 5: Release schema (if releasing)

### 4a. Update version file

Edit `packages/opentraces-schema/src/opentraces_schema/version.py` with the new schema version.

### 4b. Update CHANGELOG.md

```bash
git log --oneline $(git tag -l 'schema-v*' --sort=-v:refname | head -1)..HEAD -- packages/opentraces-schema/
```

Add a new version entry. Move [Unreleased] items under it.

### 4c. Build and test

```bash
source .venv/bin/activate
cd packages/opentraces-schema && rm -rf dist && python -m build && python -m twine check dist/* && cd ../..
pytest tests/ -q
```

Stop if either fails.

### 4d. Commit, tag, push

```bash
git add packages/opentraces-schema/
git commit -m "release: opentraces-schema vSCHEMA_VERSION"
git tag -a schema-vSCHEMA_VERSION -m "opentraces-schema vSCHEMA_VERSION"
git push origin main --tags
```

### 4e. Publish schema via workflow dispatch

```bash
gh workflow run publish.yml -f repository=pypi -f package=opentraces-schema
```

Wait for the schema publish to complete before continuing to the CLI release:

```bash
gh run list --workflow=publish.yml --limit 1 --json status,conclusion,databaseId
# Wait until conclusion is "success"
```

---

## Step 6: Release CLI

### 5a. Update version files

Edit all three files atomically:

| File | Field |
|---|---|
| `src/opentraces/__init__.py` | `__version__ = "X.Y.Z"` |
| `packages/opentraces-schema/src/opentraces_schema/version.py` | `SCHEMA_VERSION = "X.Y.Z"` (only if NOT doing a separate schema release) |
| `web/site/src/lib/version.json` | `{"version":"X.Y.Z"}` |

### 5b. Build and test

```bash
source .venv/bin/activate
pytest tests/ -q
rm -rf dist && python -m build --wheel
```

### 5c. Commit, tag, push

```bash
git add src/opentraces/__init__.py web/site/src/lib/version.json
git commit -m "release: opentraces vCLI_VERSION"
git tag -a vCLI_VERSION -m "opentraces vCLI_VERSION"
git push origin main --tags
```

### 5d. Create GitHub Release

Generate changelog from git log since previous CLI tag:

```bash
git log --oneline $(git tag -l 'v[0-9]*' --sort=-v:refname | head -2 | tail -1)..HEAD
```

Write user-facing bullet points (not raw commit messages). Then:

```bash
gh release create vCLI_VERSION \
  --title "opentraces vCLI_VERSION" \
  --notes "$(cat <<'EOF'
## Install

\`\`\`bash
pipx install opentraces==CLI_VERSION
# or
brew install JayFarei/opentraces/opentraces
\`\`\`

## Changes

- [generated changelog bullets]
EOF
)"
```

This triggers `publish.yml` (PyPI) and `update-homebrew.yml` (tap formula) automatically.

### 5e. Monitor publish workflow

```bash
gh run list --workflow=publish.yml --limit 2 --json status,conclusion,databaseId
```

Tell the user both workflows are running. They can watch at the Actions tab. Continue to site deploy while PyPI publishes.

---

## Step 7: Deploy site

Build and deploy from the repo root (not `web/site/`):

```bash
cd web/site && npm run build && cd ../..
npx vercel --prod
```

If the build fails, fix before deploying. The production URL is `https://opentraces.ai`.

---

## Step 8: Wait for PyPI propagation

After site is deployed, pause before running install verification. PyPI typically propagates within 60-120 seconds, but can take longer during peak load.

```bash
echo "Waiting 120s for PyPI to propagate..."
sleep 120
```

Tell the user you're waiting and why, so they're not confused by the pause.

---

## Step 9: Verify installs

Run all three verification methods. Use isolated environments.

### pipx

```bash
pipx install opentraces==CLI_VERSION --force
opentraces --version
pipx uninstall opentraces
```

### pip (fresh venv)

```bash
python3 -m venv /tmp/ot-release-verify
source /tmp/ot-release-verify/bin/activate
pip install opentraces==CLI_VERSION
opentraces --version
deactivate
rm -rf /tmp/ot-release-verify
```

### brew

```bash
brew upgrade JayFarei/opentraces/opentraces
# or if not installed:
brew install JayFarei/opentraces/opentraces
opentraces --version
```

If brew is still on the old version, the `update-homebrew.yml` workflow may not have completed yet. Check it:

```bash
gh run list --workflow=update-homebrew.yml --limit 1 --json status,conclusion
```

Wait and retry once if it's still in progress.

### schema package

```bash
python3 -m venv /tmp/schema-verify
source /tmp/schema-verify/bin/activate
pip install opentraces-schema==SCHEMA_VERSION
python -c "from opentraces_schema import SCHEMA_VERSION; print(SCHEMA_VERSION)"
deactivate
rm -rf /tmp/schema-verify
```

---

## Step 10: Post-release docs update

After verifications pass, run a docs-update pass to propagate the release changes across all documentation surfaces.

```bash
# Regenerate llms.txt from updated docs (always do this — do not hand-edit llms.txt)
bash web/site/scripts/generate-llms-txt.sh
```

Then invoke the `/docs-update` skill to audit and fix any stale references introduced by the release (new commands, flags, version numbers, schema fields). This step catches drift that the pre-release docs check (Step 4) doesn't cover because it runs *before* the code is tagged.

Key things that commonly go stale after a release:
- `llms.txt` — regenerate from source, never hand-edit
- `web/viewer/src/types/trace.ts` — TypeScript types must track schema model changes
- `web/viewer/src/lib/sample-data.ts` — `schema_version` hardcoded string
- `packages/opentraces-schema/CHANGELOG.md` — verify no version entry gaps
- `CLAUDE.md` — parsers list, new directories in structure section

Commit the docs-update changes before the final status report.

---

## Step 11: Final status report

After all verifications pass, print a concise summary:

```
Release pack complete:

  opentraces-schema vSCHEMA_VERSION
    PyPI:   published
    verify: opentraces_schema.SCHEMA_VERSION == SCHEMA_VERSION

  opentraces vCLI_VERSION
    PyPI:     published
    Homebrew: JayFarei/opentraces/opentraces @ vCLI_VERSION
    pipx:     opentraces CLI vCLI_VERSION
    pip:      opentraces CLI vCLI_VERSION

  Site:   https://opentraces.ai (deployed)

  Tags:   schema-vSCHEMA_VERSION  vCLI_VERSION
  GH:     https://github.com/JayFarei/opentraces/releases/tag/vCLI_VERSION
```

If any verification failed, show it clearly and suggest what to check.

---

## Failure modes

| Failure | Response |
|---|---|
| Tests fail (step 5c/6b) | Stop, fix, restart from that step |
| Schema publish fails | Fix and re-dispatch; do not continue to CLI release |
| CLI GitHub Release fails | The tag is already pushed, just recreate the release (`gh release create`) |
| PyPI already has this version | You must bump again; PyPI does not allow re-uploads |
| brew not updated after 5 min | Check `update-homebrew.yml` workflow; may need HOMEBREW_TAP_TOKEN secret |
| Site build fails | Fix Next.js errors before deploying |
