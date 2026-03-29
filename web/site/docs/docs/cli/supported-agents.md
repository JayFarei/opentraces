# Supported Agents

opentraces currently ships with one live parser: Claude Code.

## Current Support

| Agent | Identifier | Status |
|-------|-----------|--------|
| Claude Code | `claude-code` | Supported |
| Cursor | `cursor` | Planned |
| Codex | `codex` | Planned |
| Gemini CLI | `gemini-cli` | Planned |
| Cline | `cline` | Planned |
| OpenCode | `opencode` | Planned |

## How Detection Works

The parser registry is discovered at runtime from `src/opentraces/parsers/`.

```python
from opentraces.parsers import get_parsers

supported = list(get_parsers().keys())
```

Today that registry contains `claude-code`. `opentraces init --agent ...` uses the same registry to validate agent selection.

## What The Parser Extracts

Claude Code traces are normalized into the opentraces schema with:

- user / agent / system steps
- tool calls and observations
- system prompt deduplication
- snippets from edit/write activity
- per-step token usage
- sub-agent hierarchy when present

## Adapter Contract

New parsers implement the `SessionParser` protocol:

```python
class SessionParser(Protocol):
    agent_name: str
    def discover_sessions(self, projects_path: Path) -> Iterator[Path]: ...
    def parse_session(self, session_path: Path, byte_offset: int = 0) -> TraceRecord | None: ...
```

That keeps the parser surface small and lets new agents plug in without changing the review or upload workflow.
