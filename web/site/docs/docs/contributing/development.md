# Development

## Setup

```bash
git clone https://github.com/jayfarei/opentraces
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
make test                         # or: ./.venv/bin/pytest -q
make lint                         # ruff check
```

Some tests require real Claude Code session data and are skipped by default. To run them, set the env var pointing to your project's sessions directory:

```bash
export OPENTRACES_TEST_PROJECT_DIR=~/.claude/projects/<your-project-slug>
make test
```

The repository also has frontend test suites under `web/viewer/` and buildable docs under `web/site/`.

## Building and Releasing

The `Makefile` orchestrates the full build and release pipeline:

```bash
make build            # Build viewer, schema, and CLI packages
make version-check    # Show current versions
make release          # Full pipeline: test, lint, build, publish to PyPI, tag
```

The CLI version lives in `src/opentraces/__init__.py` (single source of truth). The schema version lives in `packages/opentraces-schema/src/opentraces_schema/version.py`.

## Project Structure

```
packages/opentraces-schema/   Schema package (Pydantic v2 models)
src/opentraces/               CLI package
  cli.py                      Click-based CLI entry point
  clients/                    TUI and Flask inbox clients
  parsers/                    Agent session parsers
  security/                   Secret scanning and anonymization
  enrichment/                 Git signals, attribution, metrics
  quality/                    Trace quality assessment and rubrics
  upload/                     Hugging Face upload helpers
tests/                        Python test suite
web/viewer/                   React inbox viewer (bundled in pip package)
web/site/                     Next.js docs and marketing site
Makefile                      Build, test, and release orchestration
Formula/                      Homebrew formula template
```

## Key Files

- `src/opentraces/cli.py` - CLI commands and hidden automation hooks
- `src/opentraces/clients/web_server.py` - Flask inbox server that serves the React viewer
- `src/opentraces/clients/tui.py` - Textual inbox client
- `src/opentraces/pipeline.py` - Enrichment and security pipeline
- `packages/opentraces-schema/src/opentraces_schema/models.py` - Pydantic schema models

## Adding A Parser

1. Create `src/opentraces/parsers/your_agent.py`
2. Implement the `SessionParser` protocol in `src/opentraces/parsers/base.py`
3. Register it in `src/opentraces/parsers/__init__.py`
4. Add tests under `tests/`

## Notes

- The current shipped parser is Claude Code
- The inbox workflow is `web/tui/session -> commit/reject/redact -> push`
- Hidden commands still exist for compatibility and automation, but the public docs should use `web`, `tui`, `session`, `commit`, and `push`
- The React viewer (`web/viewer/dist`) is bundled into the pip package via `force-include` in `pyproject.toml`
