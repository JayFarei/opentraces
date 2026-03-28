# Development

## Setup

```bash
git clone https://github.com/opentraces/opentraces
cd opentraces
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

## Optional Dependencies

```bash
# Web review UI
pip install flask

# TUI review
pip install opentraces[tui]
```

## Running Tests

```bash
pytest tests/ -v
```

## Project Structure

```
packages/opentraces-schema/   Schema package (Pydantic v2 models)
src/opentraces/               CLI package
  cli.py                      Click-based CLI entry point
  parsers/                    Agent session parsers
    claude_code.py            Claude Code parser
    base.py                   Base parser interface
    dataclaw_import.py        DataClaw import adapter
  security/                   Secret scanning, anonymization
  enrichment/                 Git signals, attribution, metrics
  upload/                     HF Hub sharded upload
  review/                     CLI and web review interfaces
  config.py                   Configuration management
tests/                        Test suite
```

## Key Files

- `src/opentraces/cli.py` - All CLI commands
- `src/opentraces/parsers/claude_code.py` - Reference parser implementation
- `src/opentraces/parsers/base.py` - Parser interface
- `packages/opentraces-schema/src/opentraces_schema/models.py` - Pydantic models

## Adding a Parser

1. Create `src/opentraces/parsers/your_agent.py`
2. Implement `BaseParser` (see [Schema Changes](/docs/contributing/schema-changes))
3. Register in `src/opentraces/parsers/__init__.py`
4. Add tests in `tests/`

## Code Style

- Python 3.10+ type hints
- Click for CLI commands
- Pydantic v2 for schema models
- Every CLI command emits structured JSON with `next_steps` and `next_command`
