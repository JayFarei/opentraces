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
  - `parsers/` - Agent session parsers (claude_code.py, hermes.py)
  - `hooks/` - Claude Code hook scripts (on_stop.py, on_compact.py) for session enrichment
  - `security/` - Secret scanning, anonymization, classification (independently versioned via `SECURITY_VERSION`)
  - `enrichment/` - Git signals, attribution, dependencies, metrics
  - `quality/` - Trace quality assessment, persona rubrics, upload gates
  - `exporters/` - ATIF export
  - `upload/` - HF Hub sharded upload, dataset card generation
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
- Security pipeline has its own `SECURITY_VERSION` in `security/version.py`, bump it when changing detection logic (regex patterns, entropy thresholds, classifier heuristics, anonymization rules)

## Testing

```bash
source .venv/bin/activate
pytest tests/ -v
```
