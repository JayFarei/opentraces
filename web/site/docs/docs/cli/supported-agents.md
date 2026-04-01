# Supported Agents

opentraces currently ships with two live parsers: Claude Code and Hermes.

## Current Support

| Agent | Identifier | Category | Status |
|-------|-----------|----------|--------|
| Claude Code | `claude-code` | dev-time | Supported |
| Hermes | `hermes` | run-time | Supported |
| Cursor | `cursor` | dev-time | Planned |
| Codex | `codex` | dev-time | Planned |
| OpenCode | `opencode` | dev-time | Planned |
| OpenClaw | `openclaw` | run-time | Planned |
| NemoClaw | `nemoclaw` | run-time | Planned |

## How Detection Works

The parser registry is discovered at runtime from `src/opentraces/parsers/`.

```python
from opentraces.parsers import get_parsers

supported = list(get_parsers().keys())
```

`opentraces init --agent ...` uses the same registry to validate agent selection.

## What Parsers Extract

All parsers normalize agent sessions into the opentraces schema with:

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
