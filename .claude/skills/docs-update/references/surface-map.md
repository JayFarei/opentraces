# Documentation Surface Map

Complete catalog of every file that contains documentation references which can go stale.
Organized by zone (matching the agent assignments in SKILL.md).

---

## Zone 1: Site (`web/site/src/`)

### Components

| File | What it documents | Key stale risks |
|------|-------------------|-----------------|
| `components/Hero.tsx` | Install tabs, terminal mockups (init/status/review/push), TUI keybindings, version badge | Install commands, CLI output format, TUI keybindings (`j/k/c/r/q`), `--publish` flag |
| `components/GetStarted.tsx` | "60 seconds" flow — terminal steps + agent steps | `pipx install`, `opentraces init/tui/push` commands |
| `components/PrivacyTrust.tsx` | Security mode demos, redaction examples | "19 regex patterns" count, `--review-policy auto`, redaction format `[REDACTED_*]` |
| `components/ShareFrom.tsx` | Step-by-step publish flow | **Known issue:** references `opentraces install-skill claude` and `opentraces auth --install-hook` which don't exist |
| `components/InfraDiagram.tsx` | Architecture: agent sources, pipeline, push modes | Agent list, pipeline step names, "10 quality checks" count |
| `components/Features.tsx` | 9-feature grid | "Claude Code hook" description, "structured JSON" claim |
| `components/SchemaExplorer.tsx` | Inline JSON schema example | `schema_version: "0.1.0"`, model ID format, field list |
| `components/SecurityTiers.tsx` | Auto vs review mode selector | Policy names and descriptions |
| `components/Stats.tsx` | Dashboard mockup with fake data | Model names (`claude-sonnet-4`), agent IDs (`codex`, `gemini-cli`) |
| `components/Dashboard.tsx` | Live explorer, empty-state install command | `pipx install opentraces && opentraces init` |

### Libraries

| File | What it documents | Key stale risks |
|------|-------------------|-----------------|
| `lib/agent-prompt.ts` | Canonical agent setup prompt (copied from Hero) | All CLI commands: `auth status`, `login`, `init --agent --review-policy auto --import-existing`, `tui`, `web`, `push` |
| `lib/schema-versions.ts` | Full schema field definitions for explorer | Every field name, type, required/optional status — must match Pydantic models in `packages/opentraces-schema/src/opentraces_schema/models.py` |
| `lib/version.json` | Version for hero badge | Must match `src/opentraces/__init__.py` |
| `lib/doc-nav.ts` | Sidebar navigation for docs viewer | Slugs must match actual .md filenames in `docs/docs/` |

---

## Zone 2: Docs (`web/site/docs/`)

All paths relative to `web/site/docs/docs/`.

| File | Key stale risks |
|------|-----------------|
| `index.md` | Full agent setup prompt, install commands, doc section links |
| `getting-started/installation.md` | `pipx install`, `brew install JayFarei/...`, source clone URL, Python 3.10 |
| `getting-started/authentication.md` | `opentraces login --token`, OAuth flow, `HF_TOKEN`, credentials path |
| `getting-started/quickstart.md` | All quick-start commands, screenshot paths |
| `cli/commands.md` | **Most detailed reference** — every command, flag, hidden command, exit code |
| `cli/supported-agents.md` | Agent support table, `SessionParser` protocol signature |
| `cli/troubleshooting.md` | Error message strings (must match CLI output), session file paths |
| `security/tiers.md` | Policy names, init flags |
| `security/scanning.md` | Field-by-field scan table, redaction format |
| `security/configuration.md` | Config file paths, JSON shape, `config set` flags |
| `schema/overview.md` | JSONL structure, `pip install opentraces-schema`, Python usage |
| `schema/trace-record.md` | All field tables, JSON examples |
| `schema/steps.md` | Step field table, `call_type`/`agent_role` enums |
| `schema/outcome-attribution.md` | Outcome fields, `signal_confidence` enums |
| `schema/standards.md` | ATIF version, Agent Trace sponsors |
| `schema/versioning.md` | "Current Version: 0.1.0", version check behavior |
| `workflow/parsing.md` | Session file paths, enrichment pipeline steps (7 numbered) |
| `workflow/review.md` | Stage vocabulary, TUI shortcuts, hidden `opentraces review` alias |
| `workflow/pushing.md` | Push flags, shard naming `traces-NNNN.jsonl` |
| `workflow/export.md` | States export is not public yet — update when it ships |
| `integration/agent-setup.md` | `opentraces _capture` hidden command signature |
| `integration/ci-cd.md` | GitHub Actions YAML (uses `pip install`, should use `pipx`?) |
| `contributing/development.md` | Dev install, make targets, key file paths, parser protocol |
| `contributing/schema-changes.md` | `BaseParser` interface (inconsistent with supported-agents.md `SessionParser`) |

Also: `mkdocs.yml` (nav structure must match file existence).

---

## Zone 3: Core refs (repo root + key files)

| File | Key stale risks |
|------|-----------------|
| `README.md` | Dev install, "Tell Your Agent" prompt (all commands), quick start, project structure tree |
| `CLAUDE.md` | Stack summary, dev setup, directory structure, key decisions |
| `skill/SKILL.md` | **Critical for agents** — every command, flag, exit code, JSON shape, troubleshooting errors |
| `web/site/public/llms.txt` | Full project reference for AI visitors — install methods, setup prompt, inline docs |
| `packages/opentraces-schema/README.md` | Schema install, Python usage |
| `packages/opentraces-schema/CHANGELOG.md` | Release history entries |
| `packages/opentraces-schema/VERSION-POLICY.md` | Bump checklist, source of truth path |
| `packages/opentraces-schema/FIELD-MAPPINGS.md` | ATIF/ADP/OTel mapping tables |

---

## Zone 4: Inline (source code + viewer)

| File | Key stale risks |
|------|-----------------|
| `src/opentraces/cli.py` | Help strings for all commands — these are the source of truth |
| `src/opentraces/clients/tui.py` | Keybinding labels, status bar text, stage display names |
| `src/opentraces/clients/web_server.py` | HTML template strings, route descriptions |
| `web/viewer/src/` (various) | Onboarding copy when inbox empty, remote form descriptions, field labels in UI |

---

## Known existing issues (pre-existing drift)

These should be flagged every time the skill runs until they're fixed:

1. **ShareFrom.tsx** references `opentraces install-skill claude` and `opentraces auth --install-hook` — neither command exists
2. ~~**GitHub URL inconsistency**~~ **Fixed** — README.md and schema README now use `JayFarei/opentraces`
3. **Parser interface inconsistency** — `supported-agents.md` shows `SessionParser`, `schema-changes.md` shows `BaseParser`
4. ~~**Schema version 0.1.0 hardcoded**~~ **Fixed** — SchemaExplorer.tsx uses dynamic `latestVersion` import, docs updated to 0.1.1, `Attribution.version` field removed from schema
5. **ci-cd.md** uses `pip install opentraces` instead of `pipx install opentraces`
