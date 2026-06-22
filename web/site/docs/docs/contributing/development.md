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
pip install -e ".[release]"       # Build and publish tools (build, twine)
```

## Running Tests

```bash
make test-premerge          # fast xdist lane; excludes integration/e2e, otbox, perf
make test-integration-shard # optional local shard; set SHARD_INDEX/SHARD_TOTAL
pytest tests/ -v            # full local sweep
```

Some tests require real Claude Code trace data and are skipped by default. To run them, set the env var pointing to your project's Claude Code sessions directory:

```bash
export OPENTRACES_TEST_PROJECT_DIR=~/.claude/projects/<your-project-slug>
pytest tests/ -v
```

The Textual TUI and the Flask web review backend have been removed; `src/opentraces/clients/` now ships only the text renderers used by the CLI. The legacy React viewer source remains under `web/viewer/` but is decommissioned and out of the default build/test gate until the next dataset-scoped review UI lands. The buildable docs live under `web/site/`.

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
- `src/opentraces/clients/` - text renderers for CLI trace review (the Textual TUI and Flask web backend have been removed)
- `web/viewer/` - legacy React trace review UI source (decommissioned)
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
- Hermes currently ships as a registered `FormatImporter` consumed by dataset workflows or the schema package directly
- The public 0.4 workflow is `init -> trace query -> dataset new -> dataset run -> dataset review -> dataset publish`
