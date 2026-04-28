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
| `src/opentraces/capture/` (new adapter, protocol change, or registry edit) | Docs (integration/capture-integration.md, cli/supported-agents.md, contributing/development.md), Core refs (CLAUDE.md, capture/README.md), Site (InfraDiagram agent list, Features) |
| `src/opentraces/capture/_base.py` (protocol signatures) | Docs (integration/capture-integration.md, cli/supported-agents.md), Core refs (capture/README.md). The integration spec quotes the protocol bodies — must stay byte-for-byte aligned. |
| `src/opentraces/capture/claude_code/hooks/` or `install.py` | Docs (integration/capture-integration.md "Tier 3" / "Tier 4" sections, cli/commands.md `setup claude-code` flags), Core refs (skill/SKILL.md hook reference) |
| `src/opentraces/watcher/` (daemon or installer) | Docs (integration/capture-integration.md "Watcher integration" section, cli/commands.md `watcher` group), Core refs (CLAUDE.md if behavior changes) |
| `src/opentraces/core/trails/` (Trace Trails substrate) | Docs (integration/capture-integration.md "Tier 4" section if capture API changed, cli/commands.md `trail` group), Core refs (CLAUDE.md trace-trails decision block) |
| `src/opentraces/parsers/` (legacy path — collapsed into `capture/` since 0.4) | If anything still references this path, it is itself stale: update to point at `capture/` instead. |
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
- `docs/integration/capture-integration.md` — contributor spec for adding a new agent. Must match four code surfaces exactly:
  1. Protocol signatures (`SessionParser`, `FormatImporter`, `HookInstaller`, `ParseOutcome`) match `src/opentraces/capture/_base.py`
  2. Registry entries (`PARSERS`, `IMPORTERS`, `HOOK_INSTALLERS`, `HARNESS_DIRS`) match `src/opentraces/capture/__init__.py` `_register_defaults()` and `src/opentraces/capture/skill/install.py`
  3. The "Known coupling" file:line refs to hardcoded `ClaudeCodeParser` imports still exist (`core/ingest.py`, `quality/engine.py`, `cli/__init__.py`, `clients/web/server.py`, `cli/trace.py`) — update line numbers, or delete rows if the coupling is generalized
  4. Trace Trails Tier 4 contract: `metadata["hook_pre_tool_use"]` / `["hook_post_tool_use"]` / `["hook_stop"]` keys are still what `emit_step_window_events_from_record()` reads in `src/opentraces/core/trails/snapshots.py`, and `write_worktree_tree(cwd)` is still the synchronous boundary call
  Also verify `docs/cli/supported-agents.md` agrees on the protocol names (this used to be inconsistent with `contributing/schema-changes.md` per the known-issues list)
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

### Rubric D: Capture integration spec drift

Only run this rubric when `src/opentraces/capture/`, `src/opentraces/watcher/`, or `src/opentraces/core/trails/` changed in the diff. The integration spec at `web/site/docs/docs/integration/capture-integration.md` is held to a tighter standard than other docs because contributors copy code from it.

1. **Protocol signatures match `_base.py`**. The spec quotes the bodies of `SessionParser`, `FormatImporter`, `HookInstaller`, and `ParseOutcome`. Diff the doc's quoted blocks against the source:
   ```bash
   grep -A 10 "class SessionParser" src/opentraces/capture/_base.py
   grep -A 10 "class FormatImporter" src/opentraces/capture/_base.py
   grep -A 10 "class HookInstaller" src/opentraces/capture/_base.py
   ```
   Any field rename, parameter add, or return-type change in `_base.py` must be reflected verbatim in the spec.

2. **Registry block matches `_register_defaults`**. The "Registration" section shows the actual `PARSERS` / `IMPORTERS` / `HOOK_INSTALLERS` registrations:
   ```bash
   grep -A 15 "_register_defaults" src/opentraces/capture/__init__.py
   grep -A 5 "HARNESS_DIRS" src/opentraces/capture/skill/install.py
   ```

3. **"Known coupling" file:line refs are still accurate**. The spec lists hardcoded `ClaudeCodeParser` imports in `core/ingest.py`, `quality/engine.py`, `cli/__init__.py`, `clients/web/server.py`, and `cli/trace.py`. Verify each cited line still contains the cited code:
   ```bash
   grep -n "ClaudeCodeParser" src/opentraces/core/ingest.py src/opentraces/quality/engine.py src/opentraces/cli/__init__.py src/opentraces/clients/web/server.py src/opentraces/cli/trace.py
   grep -n '"claude-code" not in agents' src/opentraces/cli/__init__.py
   grep -n '"agents":' src/opentraces/cli/__init__.py
   ```
   Update line numbers, or remove rows from the spec if a coupling has been generalized through the registry.

4. **Trace Trails Tier 4 contract**. The spec asserts that `metadata["hook_pre_tool_use"]` / `["hook_post_tool_use"]` / `["hook_stop"]` are the exact keys `emit_step_window_events_from_record` reads. Verify:
   ```bash
   grep -n "hook_pre_tool_use\|hook_post_tool_use\|hook_stop" src/opentraces/core/trails/snapshots.py
   grep -n "def write_worktree_tree" src/opentraces/core/trails/snapshots.py
   ```

5. **Hook event names match the installer**. The spec's "Recommended hook events" table uses generic names (Session start, Tool call begin, etc.) but the worked Codex example assumes Stop / PreToolUse / PostToolUse parity. The Claude Code reference must still register these events:
   ```bash
   grep -A 6 "EVENT_SCRIPTS" src/opentraces/capture/claude_code/install.py
   ```

6. **Watcher integration claim**. The spec says the watcher daemon is agent-agnostic except for `_claude_jsonl_dir`. Verify:
   ```bash
   grep -n "_claude_jsonl_dir\|_jsonl_activity_since" src/opentraces/watcher/daemon.py
   ```
   If `daemon.py` has grown a dispatch over multiple agents, update the spec's "Watcher integration" section.

7. **`src/opentraces/capture/README.md` and `docs/cli/supported-agents.md` agree** with the integration spec on protocol names and registration steps. The "Adapter Contracts" section in supported-agents.md is the user-facing summary, capture-integration.md is the long-form spec, capture/README.md is the source-tree authority. They must not contradict each other.

8. **Test coverage requirements section is current**. The "Coverage matrix", "What you inherit, what you must add", and "Hardcoded coupling: refactor risk" tables in the spec are load-bearing for any new agent contributor. Drift here means contributors ship without required tests. Run when `tests/`, `src/opentraces/capture/`, or `src/opentraces/core/trails/` changed:
   ```bash
   # Substrate test files referenced as "free" — they must still exist and pass
   ls tests/core/test_trail_event_log.py tests/core/test_trail_anchor_tiers.py tests/core/test_trail_rebuild.py tests/core/test_trail_reconciler.py tests/core/test_doctor_trail_event_log.py tests/cli/test_trail_search_phase7.py
   # Phase-7 UAT helper used as the spec's recommended pattern
   grep -n "_append_anchored_patch\|append_exact_patch_trail" tests/cli/test_trail_search_phase7.py
   # Registry-presence test pattern referenced (5-liner template)
   grep -n "test_importers_registry\|test_parsers_registry" tests/capture/test_parser_hermes.py tests/publish/test_exporters.py
   # Hook subprocess test pattern referenced for non-Python hooks
   grep -n "subprocess.run\|HOOK_PATH" tests/capture/test_hook_ingest_spawn.py
   # Schema stability fixture (spec asks contributors to extend it)
   ls tests/fixtures/trace_record_stability/v02_sample.jsonl
   # Conftest isolation fixture line refs (spec quotes line 25-33 + 36)
   grep -n "_isolate_opentraces_global_state\|monkeypatch.setenv.*HOME" tests/conftest.py
   ```
   If the "Hardcoded coupling: refactor risk" table cites tests by path that no longer exist, or new coupling sites have been added without spec mention, update the table. If `tests/capture/test_registry.py` now exists (i.e. someone backfilled the registry-uniqueness tests), update the "What you inherit" table to move registry consistency from "MUST ADD" to "FREE".

Report any rubric failures before applying changes.

---

## Phase 4: Apply and report

1. Apply all approved edits
2. Regenerate `llms.txt` by running the generation script — **never hand-edit `llms.txt` directly**, it is derived from the docs source:
   ```bash
   bash web/site/scripts/generate-llms-txt.sh
   ```
   Run this whenever any docs markdown file changed. If new workflow/doc pages were added, first add them to the ordered list in `generate-llms-txt.sh`.
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
