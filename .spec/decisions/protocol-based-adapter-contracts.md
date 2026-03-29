---
schema_version: "1.0"
title: Protocol-Based Adapter Contracts
scope: src/opentraces/parsers, src/opentraces/exporters
date_detected: 2026-03-28
confidence: high
---

# Protocol-Based Adapter Contracts

## What

All extension points (parsers, importers, exporters) use Python's `typing.Protocol` with `@runtime_checkable` for structural typing rather than abstract base classes or inheritance. The docstring in `parsers/base.py` explicitly states: "Uses typing.Protocol (structural typing, not inheritance) so new adapters only need to implement the interface without importing this module."

## Why

The system is designed for community contributions of new agent parsers (Cursor, Codex CLI, Gemini CLI). Structural typing means a contributor can write a parser in a separate package without importing any opentraces code. If their class has `agent_name: str`, `discover_sessions()`, and `parse_session()` with the right signatures, it satisfies the contract. This reduces coupling between the core pipeline and adapter implementations.

## Tradeoff

**Gained**: Zero coupling between adapters and the core framework. Contributors can develop and test parsers independently. Runtime checking (`isinstance(parser, SessionParser)`) works for validation without requiring import-time dependency.

**Lost**: No shared base implementation for common patterns (e.g., file walking, JSONL reading, error handling). Each parser must reimplement these. No IDE-enforced "implements" relationship, only runtime checking.

## Alternatives Rejected

1. **Abstract base classes**: Would force `import opentraces.parsers.base` in every adapter, creating a hard dependency on the framework package.
2. **Plugin registry with entry points**: More complex discovery mechanism. The current approach is simpler: the CLI hardcodes `ClaudeCodeParser` and future parsers would be added via similar explicit imports or a lightweight registry.
3. **No formal contract**: Would make it unclear what methods a parser must implement.

## Source

- `src/opentraces/parsers/base.py` (Protocol definitions with docstring rationale)
- `src/opentraces/exporters/base.py` (FormatExporter Protocol)

## Transferability

High. Any Python project with a plugin/adapter pattern benefits from Protocol-based contracts when: (a) adapters may live in separate packages, (b) you want runtime type checking without import-time coupling, and (c) the interface is small enough that structural typing is unambiguous.
