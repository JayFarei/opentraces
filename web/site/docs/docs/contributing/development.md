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
pip install -e ".[web,tui]"
```

## Running Tests

```bash
./.venv/bin/pytest -q
(cd web/viewer && npm test)
(cd web/site && npm run build)
```

The repository also has frontend test suites under `web/viewer/` and buildable docs under `web/site/`.

## Project Structure

```
packages/opentraces-schema/   Schema package (Pydantic v2 models)
src/opentraces/               CLI package
  cli.py                      Click-based CLI entry point
  clients/                    TUI and Flask inbox clients
  parsers/                    Agent session parsers
  security/                   Secret scanning and anonymization
  enrichment/                 Git signals, attribution, metrics
  upload/                     Hugging Face upload helpers
tests/                        Python test suite
web/viewer/                   React inbox viewer
web/site/                     Next.js docs and marketing site
```

## Key Files

- `src/opentraces/cli.py` - CLI commands and hidden automation hooks
- `src/opentraces/clients/web_server.py` - Flask inbox server that serves the React viewer
- `src/opentraces/clients/tui.py` - Textual inbox client
- `packages/opentraces-schema/src/opentraces_schema/models.py` - Pydantic schema models

## Adding A Parser

1. Create `src/opentraces/parsers/your_agent.py`
2. Implement the `SessionParser` protocol in `src/opentraces/parsers/base.py`
3. Register it in `src/opentraces/parsers/__init__.py`
4. Add tests under `tests/`

## Notes

- The current shipped parser is Claude Code
- The inbox workflow is `web/tui/session -> approve/reject/redact -> commit -> push`
- Hidden commands still exist for compatibility and automation, but the public docs should use `web`, `tui`, `session`, `commit`, and `push`
