# Supported Agents

0.4 separates live capture from import support.

## Current Support

| Mode | Identifier | Status | Notes |
|------|------------|--------|-------|
| Live capture | `claude-code` | Supported | Installed via `opentraces init` or `opentraces setup claude-code` |
| Live capture | `codex-cli` | Supported | Installed via `opentraces init --agent codex-cli` or `opentraces setup codex-cli` |
| Context capture source | `capture-otlp` | Supported for Claude Code | Installed via `opentraces setup capture-otlp`; feeds Context Tree events |
| Dataset import | `hermes` | Supported | Registered `FormatImporter`. Invoked from dataset workflows or via the schema package's serializers |

Planned adapters can follow the same contracts without changing the bucket,
workflow, review, publish, or schema layers.

## Live Capture vs Import

Live capture adapters discover and parse session files on disk.

Import adapters read external datasets or files and map them into `TraceRecord`.

That distinction matters in the public CLI:

```bash
opentraces init --agent claude-code
opentraces init --agent codex-cli
opentraces setup capture-otlp
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
```

## Adapter Contracts

The capture layer exposes small protocols:

- `SessionParser` for live agent session parsing
- `FormatImporter` for file or dataset imports
- `HookInstaller` for external integrations like Claude Code and git

This is why review, optional security tools, bucket storage, and dataset
publication stay consistent even as new sources are added.

## What Gets Normalized

All supported sources are normalized into the same schema with:

- trace-level metadata
- steps and reasoning content
- tool calls and observations when the source provides them
- outcomes and metrics
- attribution and git links when available
