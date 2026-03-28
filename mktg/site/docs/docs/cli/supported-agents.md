# Supported Agents

opentraces v0.1 ships with a Claude Code parser. The adapter contract is ready for additional agents.

## Compatibility

| Agent | Identifier | Storage Format | Status |
|-------|-----------|----------------|--------|
| Claude Code | `claude-code` | JSONL | Supported (v0.1) |
| Cursor | `cursor` | SQLite | Planned |
| Codex | `codex` | JSON | Planned |
| Gemini CLI | `gemini-cli` | JSON | Planned |
| Cline | `cline` | JSON | Planned |
| OpenCode | `opencode` | JSON + SQLite | Planned |

## How Detection Works

When `opentraces discover` or `opentraces parse` runs, the CLI scans for agent session files:

- **Claude Code** - Looks for `~/.claude/projects/` containing `.jsonl` session files

For planned agents, the adapter contract defines a standard interface that each parser implements. See [Contributing > Schema Changes](/docs/contributing/schema-changes) for the adapter interface.

## What the Parser Extracts

The Claude Code parser normalizes raw JSONL sessions into the opentraces schema:

- **Messages** - User prompts, agent responses, thinking content
- **Tool calls** - Bash, Read, Edit, Write, Glob, Grep, Agent, and MCP tools
- **Observations** - Tool outputs with optional summaries
- **Token usage** - Input, output, cache read/write per step
- **Sub-agents** - Explore, Plan agents linked via `parent_step`
- **Code snippets** - Extracted from Edit/Write tool calls
- **System prompts** - Deduplicated into a lookup table by hash

## Adapter Contract

Each agent parser implements a common interface:

```python
class BaseParser:
    def can_parse(self, path: Path) -> bool: ...
    def parse_session(self, path: Path) -> TraceRecord: ...
    def discover_sessions(self) -> list[SessionInfo]: ...
```

This follows the ADP (Agent Data Protocol) pattern: O(D+A) adapters instead of O(D*A) format conversions.
