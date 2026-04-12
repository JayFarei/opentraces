# opentraces.ai

## Project Overview

Open schema + CLI for crowdsourcing agent traces to HuggingFace Hub. Parses coding agent sessions, applies security scanning and redaction, enriches with attribution/git signals, and publishes as structured JSONL datasets.

## Stack

- **Language**: Python 3.10+
- **Schema**: `opentraces-schema` (standalone Pydantic v2 package in `packages/`)
- **CLI**: Click-based (`src/opentraces/cli.py`)
- **Web review**: Flask (`src/opentraces/clients/web/`) + React SPA (`web/viewer/`)
- **Marketing site**: Next.js (`web/site/`)
- **Coming soon page**: Static HTML (`web/coming-soon/`)

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
pip install flask gradio  # optional
pytest tests/ -v
```

## Structure

- `skill/` - Claude Code skill definition (skills.sh convention)
- `packages/opentraces-schema/` - Standalone schema package (Pydantic models)
- `packages/opentraces-ui/` - Design system (tokens, base, components, React wrappers, logo assets, DESIGN.md)
- `src/opentraces/` - Main CLI package
  - `agents/<name>/` - Agent-specific parsers. `agents/claude_code/parser.py`, `agents/hermes/parser.py`.
  - `installers/` - One-off installers for integrations we wire into external tools. `installers/claude_code_hooks/` ships the `on_stop` / `on_compact` / `on_tool_use` scripts that `opentraces hooks install` copies to `~/.claude/hooks/`, plus `intent_adapter.py`. `installers/git_hook.py` installs the post-commit correlator.
  - `parsers/` - Cross-agent parser infrastructure: base protocol, quality gate, lazy registry.
  - `security/` - Secret scanning, anonymization, classification (independently versioned via `SECURITY_VERSION`)
  - `enrichment/` - Git signals, attribution, dependencies, metrics, and session intent summarization (`intent.py` + `intent_backends.py`, plan 038)
  - `processors.py` - Generic post-processor subprocess runner (stdin/stdout JSON contract, plan 038 phase 4)
  - `quality/` - Trace quality assessment, persona rubrics, upload gates
  - `exporters/` - ATIF export
  - `upload/` - HF Hub sharded upload, dataset card generation (includes Intent coverage stats)
  - `inbox.py` - Shared data access for all review clients
  - `clients/` - Presentation layers (CLI, TUI, web backend)
- `web/` - Web frontends
  - `viewer/` - React SPA trace review UI
  - `site/` - Next.js marketing site
  - `coming-soon/` - Static coming-soon page (Vercel)
- `tests/` - Test suite
- `kb/` - Research and discussion logs (gitignored in OSS)

## Key Decisions

- Claude Code and Hermes (runtime agents) for v0.2, adapter contract ready for additional parsers
- Own schema (superset of ATIF), export to ATIF via `opentraces export --format atif`
- Sharded JSONL upload (one file per push, never append to existing)
- Attribution derived from Edit tool calls, not unified diff
- Context-aware security scanning (different rules per field type)
- Per-project review policy (auto/review) controlling whether traces need manual approval
- Zero required annotation, all enrichment is deterministic
- Security pipeline has its own `SECURITY_VERSION` in `security/version.py` (currently `0.4.0`), bump it when changing detection logic (regex patterns, entropy thresholds, classifier heuristics, anonymization rules). Tiers: 1a regex, 1b entropy (always on); 1.5 TruffleHog, 1.8 LLM PII, 2 LLM semantic review (opt-in); 3 human inbox. Tier 1.5 findings move traces to the CLI-local `TraceStatus.BLOCKED` state and never reach upload. Opt-in commands: `opentraces setup trufflehog`, `opentraces review-llm`, `opentraces push --llm-review`, and `opentraces doctor` for pipeline health.
- Session `Intent` block (schema 0.3.0, plan 038) is written by LLM hook, post-processor, or user; `source` is a closed enum (`llm_hook` / `post_processor` / `user`). Security pipeline always runs before Intent or any post-processor — enforced by the ordering-invariant test.
- Post-processors are declared per-project as an ordered list (`post_processors: [{name, command, args, env, when}]`). Contract: stdin = trace JSON, stdout = trace JSON, exit 0. Non-zero exit / missing binary / invalid output are non-fatal by default, promoted to hard errors under `--strict`. Byte-identical output = no-op. `opentraces doctor` probes configured processors.

## Testing

```bash
source .venv/bin/activate
pytest tests/ -v
```
