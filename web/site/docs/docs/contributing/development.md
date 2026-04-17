# Development

## Setup

```bash
git clone https://github.com/JayFarei/opentraces
cd opentraces
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
```

## Optional Dependencies

```bash
pip install -e ".[web,tui]"       # Web and TUI inbox clients
pip install -e ".[release]"       # Build and publish tools (build, twine)
```

## Running Tests

```bash
pytest tests/ -v
```

Some tests require real Claude Code trace data and are skipped by default. To run them, set the env var pointing to your project's Claude Code sessions directory:

```bash
export OPENTRACES_TEST_PROJECT_DIR=~/.claude/projects/<your-project-slug>
pytest tests/ -v
```

The repository also has frontend test suites under `web/viewer/` and buildable docs under `web/site/`.

## Layout

Core directories:

- `packages/opentraces-schema/` - standalone schema package
- `packages/opentraces-ui/` - shared UI package and design system
- `src/opentraces/cli/` - Click command surface
- `src/opentraces/core/` - config, paths, workflow, state, review, inbox, publish flow
- `src/opentraces/capture/` - live parsers, importers, and hook installers
- `src/opentraces/publish/` - serializers and Hugging Face publishing
- `src/opentraces/enrichment/` - git signals, attribution, dependencies, metrics
- `src/opentraces/quality/` - scoring and upload gates
- `src/opentraces/security/` - redaction and scanning pipeline
- `src/opentraces/clients/` - TUI and web backend clients
- `web/viewer/` - React trace review UI
- `web/site/` - Next.js docs and marketing site
- `tests/` - Python test suite

The CLI version lives in `src/opentraces/__init__.py`. The schema version lives in `packages/opentraces-schema/src/opentraces_schema/version.py`.

## Adding A Parser

1. Create a capture adapter under `src/opentraces/capture/`
2. Implement `SessionParser` or `FormatImporter` from `src/opentraces/capture/_base.py`
3. Register it in `src/opentraces/capture/__init__.py`
4. Add tests under `tests/`

## Notes

- The current live-capture adapter is Claude Code
- Hermes currently ships as an import path via `opentraces pull --parser hermes`
- The public inbox workflow is `web/tui/list/show -> add/reject/redact -> push`
