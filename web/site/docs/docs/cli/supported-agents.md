# Supported Agents

0.4 separates live capture from import support.

## Current Support

| Mode | Identifier | Status | Notes |
|------|------------|--------|-------|
| Live capture | `claude-code` | Supported | Installed via `opentraces init` or `opentraces setup claude-code`; supports snapshot-backed `--at-step` resume |
| Live capture | `codex-cli` | Supported | Installed via `opentraces setup codex-cli` plus `opentraces init --agent codex-cli` inside each repo |
| Context capture source | `capture-otlp` | Supported for Claude Code | Installed via `opentraces setup capture-otlp`; feeds Context Tree events from OTel/raw API-body capture |
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

`init --agent` accepts `claude`, `claude-code`, `codex`, and `codex-cli`.
`codex` is an alias for the canonical `agent.name = codex-cli`.

## Codex CLI Details

Codex CLI support is for the terminal Codex CLI, not Codex Desktop. Install and
authenticate Codex CLI first, then run:

```bash
opentraces setup codex-cli
opentraces init --agent codex-cli
```

The setup command registers native Codex hooks in `~/.codex/hooks.json` and
copies hook scripts to `~/.codex/hooks/opentraces/`. Future sessions write
sidecar events under `.opentraces/codex-cli/hooks/` in the active repo. Hooks
cover session start, user prompt submission, tool boundaries, permission
requests, compactions, and Stop; they observe permission prompts but do not
approve or deny them.

Codex traces participate in the shared substrates:

- **Trace**: Codex rollout JSONL is normalized into `TraceRecord` with
  `agent.name = codex-cli`.
- **Trail**: pre/post tool sidecars feed Trace Trails when hook evidence is
  present; git correlation remains shared.
- **Context Tree**: Codex uses `capture_method = transcript_reconstruction`
  and session-level approximated layers. It is not a Codex-specific raw
  Context Tree capture method and is not equivalent to Claude Code OTLP raw
  body capture.
- **Bucket**: captured Codex traces land in the same private bucket layout as
  other supported agents.
- **Resume**: native handoff uses `codex resume <session-id>` through
  `opentraces trace get <trace-id> --resume`; snapshot-backed `--at-step`
  materialization is unsupported for Codex.

Codex encrypted reasoning is not decrypted. When a rollout contains encrypted
reasoning without a plaintext summary, opentraces records an explicit redaction
marker instead of inventing hidden chain-of-thought content.

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
- steps and plaintext or summarized reasoning content when the source provides it
- tool calls and observations when the source provides them
- outcomes and metrics
- attribution and git links when available
