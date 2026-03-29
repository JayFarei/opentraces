# Schema Changes

The opentraces schema is open source. Feedback, questions, and proposals are welcome via [GitHub Issues](https://github.com/jayfarei/opentraces/issues).

## How to Propose a Change

When suggesting a schema change, include:

1. **What** field or model you would add, change, or remove
2. **Why** it matters for your use case (training, analytics, attribution, etc.)
3. **How** it relates to existing standards (ATIF, Agent Trace, ADP, OTel) if applicable

## What Counts as Breaking

| Change | Version Bump |
|--------|-------------|
| New optional field | Minor |
| New optional model | Minor |
| Field rename | Major |
| Field removal | Major |
| Type change | Major |

See [Versioning](/docs/schema/versioning) for full policy.

## Adapter Contributions

To add support for a new agent (e.g., Cursor, Codex), implement the `BaseParser` interface:

```python
from opentraces.parsers.base import BaseParser

class CursorParser(BaseParser):
    agent_name = "cursor"

    def can_parse(self, path: Path) -> bool:
        # Return True if this path contains Cursor sessions
        ...

    def parse_session(self, path: Path) -> TraceRecord:
        # Parse a single session into a TraceRecord
        ...

    def discover_sessions(self) -> list[SessionInfo]:
        # Find all Cursor sessions on the system
        ...
```

Register the parser in `src/opentraces/parsers/__init__.py`. See `claude_code.py` for the reference implementation.

## Review Process

- Schema changes are reviewed by the maintainers
- Breaking changes require a new rationale document
- All changes are documented in the [CHANGELOG](https://github.com/jayfarei/opentraces/blob/main/packages/opentraces-schema/CHANGELOG.md)
