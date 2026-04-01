---
name: docs-update
description: >
  Synchronize all documentation surfaces after code changes. Use when the user says
  "docs-update", "update docs", "sync docs", "update references", "docs are stale",
  or after any significant code change (CLI commands, schema fields, install methods,
  new features). This skill catches stale references across 50+ files: marketing site,
  mkdocs pages, llms.txt, the agent skill, CLI help text, viewer UI, and READMEs.
  Proactively suggest this after merging features or changing CLI surface.
---

# docs-update

Detect code changes and propagate them across every documentation surface in the project.

## Why this exists

opentraces has documentation spread across 50+ files in 10 zones: marketing site components, mkdocs reference pages, llms.txt (for AI visitors), the bundled agent skill, CLI inline help, the React viewer, schema package docs, and root READMEs. A single CLI flag rename can leave stale references in 15+ files. This skill prevents that drift.

## How to run

When triggered, follow these four phases in order. Do not skip the adversarial review.

---

## Phase 1: Detect what changed

Determine the scope of changes to propagate. Use whichever source the user indicates, or default to uncommitted changes:

```bash
# Uncommitted changes
git diff --stat
git diff --name-only

# Or changes since last release tag
git log --oneline $(git describe --tags --abbrev=0)..HEAD
git diff --name-only $(git describe --tags --abbrev=0)..HEAD
```

Classify each changed file into impact categories:

| If this changed... | These documentation zones need checking |
|---|---|
| `src/opentraces/cli.py` (commands, flags, help text) | Site, Docs, Core refs, Inline |
| `packages/opentraces-schema/src/opentraces_schema/models.py` | Site (schema-versions.ts, SchemaExplorer), Docs (schema/*.md), Core refs (schema README) |
| `src/opentraces/security/` | Site (PrivacyTrust.tsx), Docs (security/*.md) |
| `src/opentraces/parsers/` (new parser) | Site (InfraDiagram, Features), Docs (supported-agents.md), Core refs (CLAUDE.md) |
| `src/opentraces/__init__.py` (version bump) | Site (version.json), Docs (versioning.md), Core refs (CHANGELOG) |
| `pyproject.toml` (dependencies, extras) | Docs (installation.md, development.md), Core refs (README, CLAUDE.md) |
| `src/opentraces/clients/tui.py` | Site (Hero.tsx TUI mockup), Docs (review.md keybindings) |
| `src/opentraces/enrichment/` | Docs (parsing.md pipeline steps), Site (InfraDiagram) |

Read `references/surface-map.md` for the complete file-by-file catalog of what each documentation surface contains and what makes it go stale.

Present the change summary to the user before proceeding:

```
Changes detected:
  - CLI: added --dry-run flag to push command
  - CLI: renamed 'opentraces auth status' to 'opentraces auth check'

  Affected zones:
    Site:      Hero.tsx, PrivacyTrust.tsx, agent-prompt.ts
    Docs:      commands.md, pushing.md, authentication.md
    Core refs: skill/SKILL.md, llms.txt, README.md
    Inline:    (no changes needed)

  Proceed? [Y/n]
```

---

## Phase 2: Parallel update agents

Spawn up to 4 agents in parallel, one per documentation zone. Each agent receives the change summary and its file list.

### Agent 1: Site agent

Owns: `web/site/src/components/`, `web/site/src/lib/`

Files to check (read `references/surface-map.md` for specifics):
- `Hero.tsx` — install methods, terminal mockups (init, status, review, push), TUI keybindings, version badge
- `GetStarted.tsx` — terminal and agent step commands
- `PrivacyTrust.tsx` — security mode demos, regex pattern count, redaction format
- `ShareFrom.tsx` — install commands, step-by-step flow
- `InfraDiagram.tsx` — agent list, pipeline steps, quality check count
- `Features.tsx` — feature descriptions
- `SchemaExplorer.tsx` — inline schema example, field list
- `SecurityTiers.tsx` — policy mode names and descriptions
- `Stats.tsx` — model names, agent identifiers
- `agent-prompt.ts` — the canonical agent setup prompt
- `schema-versions.ts` — full schema field definitions (must match Pydantic models)
- `version.json` — version badge number

Instructions for the agent: Read the actual CLI source (`cli.py`), schema models, and security modules. For each file in your zone, find every reference to CLI commands, flags, schema fields, version numbers, install commands, and agent names. Compare against the actual source. Propose edits for anything that diverges.

### Agent 2: Docs agent

Owns: `web/site/docs/docs/**/*.md`, `web/site/docs/mkdocs.yml`, `web/site/src/lib/doc-nav.ts`

The 22 markdown files covering getting-started, CLI reference, schema, security, workflow, integration, and contributing. Read `references/surface-map.md` for the complete list.

Instructions: For each doc page, verify every CLI command, flag, code example, and field reference against the actual source. Pay special attention to:
- `docs/cli/commands.md` — the most detailed command reference, every flag must match `cli.py`
- `docs/schema/*.md` — field tables must match Pydantic models
- `docs/integration/ci-cd.md` — GitHub Actions YAML must use current install method
- `docs/contributing/development.md` — dev setup commands must work

### Agent 3: Core refs agent

Owns: `README.md`, `CLAUDE.md`, `skill/SKILL.md`, `web/site/public/llms.txt`, `packages/opentraces-schema/README.md`, `packages/opentraces-schema/CHANGELOG.md`

Instructions: These files are the highest-impact surfaces. `skill/SKILL.md` is what agents use to operate the CLI, so every command and flag reference is critical. `llms.txt` is what AI assistants see when visiting the site.

**`README.md` (project root) requires explicit attention.** It is the first thing developers see on GitHub and is often the most-outdated surface. Check:
- Install command block — must match current `pipx install opentraces` and brew tap
- Quick-start commands — verify each one exists and uses current flags
- Feature bullets — must reflect shipped features, not planned/removed ones
- Schema version badge or reference — must match `version.py`
- Any links to docs pages — verify slugs still exist

Cross-reference every command, flag, and example against `cli.py`. Verify:
- All commands in skill/SKILL.md quick reference exist in cli.py
- All exit codes match
- The JSON sentinel and response shapes match
- llms.txt install commands and setup prompt match the site
- GitHub repo URL is consistent (`JayFarei/opentraces` everywhere)

### Agent 4: Inline agent

Owns: `src/opentraces/cli.py` (help strings), `src/opentraces/clients/tui.py`, `src/opentraces/clients/web_server.py`, `web/viewer/src/`

Instructions: Check that CLI help text is consistent with what the docs say. Verify TUI keybinding labels match what Hero.tsx and docs/workflow/review.md show. Check viewer onboarding copy.

---

## Phase 3: Adversarial review

After all agents complete and edits are proposed, run a single review agent that checks the proposed changes against three rubrics. This is the quality gate — it catches mistakes the update agents made and cross-surface inconsistencies.

### Rubric A: Agent readability

For files consumed by AI agents (`llms.txt`, `skill/SKILL.md`, `agent-prompt.ts`):

1. Every CLI command referenced exists as a Click command in `cli.py`
2. Every flag (e.g. `--review-policy`) exists as a Click option on the correct command
3. Exit codes in the skill match the actual `sys.exit()` calls in cli.py
4. JSON output field names match actual `click.echo` JSON output
5. The agent setup prompt, if followed step by step, produces a working setup

Grep cli.py for the actual commands and flags:
```bash
grep -E '@cli\.|@\w+\.command|@\w+\.group' src/opentraces/cli.py
grep -E "option\('--" src/opentraces/cli.py
```

### Rubric B: User readability

For files shown to humans (site components, docs, README):

1. Install commands (`pipx install opentraces`, `brew install JayFarei/opentraces/opentraces`) are copy-pasteable and correct
2. Terminal mockups in Hero.tsx match actual CLI output format
3. Version numbers are consistent across all surfaces
4. No references to commands that don't exist (flag known issues: `opentraces install-skill`, `opentraces auth --install-hook` in ShareFrom.tsx)
5. All doc page slugs in `doc-nav.ts` correspond to actual .md files in `docs/docs/`

### Rubric C: Cross-surface consistency

1. GitHub repo URL: must be `JayFarei/opentraces` everywhere (not `opentraces/opentraces`)
2. Homebrew tap: must be `JayFarei/opentraces/opentraces` everywhere
3. Install method: `pipx install opentraces` for end users, `pip install` only for CI and dev
4. Schema version string: same in version.py, version.json, schema-versions.ts, SchemaExplorer.tsx, docs/schema/versioning.md
5. Stage vocabulary: `inbox`, `committed`, `pushed`, `rejected` everywhere
6. Review policy names: `auto` and `review` everywhere

Run these checks:
```bash
# GitHub URL consistency
grep -rn "opentraces/opentraces" --include="*.md" --include="*.tsx" --include="*.ts" --include="*.txt" | grep -v node_modules | grep -v .venv
grep -rn "JayFarei/opentraces" --include="*.md" --include="*.tsx" --include="*.ts" --include="*.txt" | grep -v node_modules | grep -v .venv

# Version consistency
grep -rn '"0\.' --include="*.json" --include="*.py" --include="*.ts" --include="*.md" | grep -v node_modules | grep -v .venv | grep -v package
```

Report any rubric failures before applying changes.

---

## Phase 4: Apply and report

1. Apply all approved edits
2. Regenerate `llms.txt` if any docs content changed — read the current llms.txt structure and rebuild it from the updated docs pages
3. Present a summary grouped by zone:

```
docs-update complete:

  Site (4 files changed):
    - Hero.tsx: updated push command mockup to include --dry-run
    - agent-prompt.ts: renamed auth status → auth check
    - ...

  Docs (3 files changed):
    - commands.md: added --dry-run to push flags table
    - ...

  Core refs (2 files changed):
    - skill/SKILL.md: updated quick reference
    - llms.txt: regenerated

  Issues requiring manual attention:
    - ShareFrom.tsx references `opentraces install-skill claude` which does not exist in CLI
    - Stats.tsx model name `claude-sonnet-4` may need updating to current model naming
```

---

## When NOT to use this skill

- For version bumps only → use `/release-cli` instead (it handles version files)
- For deploying the site → use `/deploy-site` instead
- For schema-only changes that don't affect docs → just edit the Pydantic models directly
