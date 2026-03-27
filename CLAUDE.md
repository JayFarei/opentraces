# opentraces.ai

## Project Overview

Open protocol + CLI for crowdsourcing agent traces to HuggingFace Hub. Parses Claude Code sessions, applies configurable security tiers, enriches with attribution/git signals, and publishes as structured JSONL datasets.

## Stack

- **Language**: Python 3.10+
- **Schema**: `opentraces-schema` (standalone Pydantic v2 package in `packages/`)
- **CLI**: Click-based (`src/opentraces/cli.py`)
- **Web review**: Flask (`src/opentraces/review/web/`)
- **HF Space explorer**: Gradio (`explorer/`)
- **Marketing site**: Static HTML (`site/`)

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

- `packages/opentraces-schema/` - Standalone schema package (Pydantic models)
- `src/opentraces/` - Main CLI package
  - `parsers/` - Agent session parsers (claude_code.py, dataclaw_import.py)
  - `security/` - Secret scanning, anonymization, classification
  - `enrichment/` - Git signals, attribution, dependencies, metrics
  - `upload/` - HF Hub sharded upload, dataset card generation
  - `review/` - CLI and web review interfaces
- `explorer/` - HF Space Gradio app
- `site/` - Marketing website
- `tests/` - Test suite
- `resources/` - Design docs (intent.md, outcome.md)
- `kb/` - Research and discussion logs

## Key Decisions

- Claude Code only for v0.1, adapter contract ready for multi-agent
- Own schema (superset of ATIF), export to ATIF via `opentraces export --format atif`
- Sharded JSONL upload (one file per push, never append to existing)
- Attribution derived from Edit tool calls, not unified diff
- Context-aware security scanning (different rules per field type)
- Per-project security tier configuration with per-session override
- Zero required annotation, all enrichment is deterministic

## Testing

```bash
source .venv/bin/activate
pytest tests/ -v
```
