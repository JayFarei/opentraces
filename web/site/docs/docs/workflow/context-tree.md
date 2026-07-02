# Context Tree

The Context Tree answers: what did the model see at this point in the trace?

It is the sibling substrate to Trace Trails. Trace Trails tracks what changed;
Context Tree tracks the visible context used to make that change: messages,
system instructions, tool registry, runtime state, compaction boundaries, and
resume points.

Schema joins:

- `Step.context_node_id`
- `TraceRecord.context_tree_summary`

## Principles

- **Context is geometry.** A trace is not just a transcript; it is a tree of
  model-visible context nodes across turns, compactions, forks, and resumes.
- **Layers are content-addressed.** Context nodes point to layer blobs, so the
  bucket can move the environment without embedding every byte into each row.
- **The bare noun is the read.** `ctx <trace>` renders the overview; there is
  no separate lookup step for "what did the model see" — reads and writes are
  a `--json | jq` decomposition of that same view, not their own subcommands.
- **Resume is a first-class output.** `ctx resume` creates a structured packet
  for an agent to restart from a context node.

## Commands

```bash
opentraces ctx <trace-id>                        # overview: shape + capture method
opentraces ctx <trace-id>:<step-index>            # model input at that step
opentraces ctx <trace-id>:last                    # the final / active step
opentraces ctx <trace-id>:<step-index> --layer system|messages|tools|runtime
opentraces ctx <trace-id>:<step-index> --full     # inline the full hydrated model input
opentraces ctx <trace-id> --json
```

`ctx <trace>[:<step>|:last]` is the bare-noun context read (`opentraces.ctx.view.v1`):
no subcommand needed, the ref IS the command. `<trace>:<step>` is the same
universal address that `trace get` and `trail` resolve, so the identical ref
can be piped between them (`opentraces trace query --json | opentraces ctx --json`).
`--layer` renders one layer readably instead of the bounded four-layer card;
`--full` inlines the complete hydrated model input (the fork/eval-row packet).

`ctx list` and `ctx info <trace-id>` (manifest-only inventory reads, no blob
loads) and the old subcommand forms (`ctx tree`, `ctx show`, `ctx step`,
`ctx reads`, `ctx writes`, `ctx diff`, `ctx compactions`, `ctx resume`,
`ctx prune`, `ctx resolve`, `ctx anchor-for-step`) remain callable and
`--json`-scriptable; the bare-noun ref is the documented entry point going
forward.

## Layers

Context nodes reference content-addressed layers. `ctx <trace>:<step> --layer <name>` takes the
human-facing alias on the left; it maps to the substrate's layer type on the right:

| `--layer` alias | Layer type | Meaning |
|-----------------|------------|---------|
| `system` | `system` | System prompt and instruction context |
| `messages` | `messages` | Conversation messages in scope |
| `tools` | `tool_registry` | Tools and schemas visible to the model |
| `runtime` | `runtime_state` | Captured runtime settings and state hints |

## Capture Sources

The substrate accepts additive capture sources, and `ctx <trace>` is honest about which one
produced a given node — a two-rung fidelity ladder, `jsonl` (structure-only) or `otel` (full wire):

| Source | `capture_method` | Fidelity | Notes |
|--------|-------------------|----------|-------|
| JSONL reconstruction | `transcript_reconstruction` | `jsonl` | Works from agent session logs; structure-only — message content sizes are not stored, so `ctx <trace>` draws no size chart, and some views are session-level approximations |
| Pi extension sidecars | `live_capture` | `jsonl` | Provider/context sidecars use fuller messages/tool registry/runtime state when available; transcript fallback records limitations |
| OTLP receiver | `otel` | `otel` | Byte-perfect wire capture for Claude Code — full API body, tool schema, and sampling parameter evidence; `ctx <trace>` reports `fidelity: otel` and draws a token-size sparkline from it |
| HTTP proxy | `proxy` | — | Reserved/deferred; historical prototype, not the current path |

## OTLP Operations

```bash
opentraces setup capture-otlp
opentraces capture-otlp start
opentraces capture-otlp status --json
opentraces capture-otlp flush --session <session-id> --project <repo> --trace-id <trace-id>
opentraces capture-otlp flush --from-raw-bodies --session <session-id> --project <repo> --trace-id <trace-id>
opentraces capture-otlp stop
```

`flush` reconstructs the session into Context Tree nodes (one node per
llm-request), lands them in the bucket companion, and joins them to the trace
spine via `Step.context_node_id` and `TraceRecord.context_tree_summary`, so
`ctx show` / `ctx step` can hydrate the captured context. `--from-raw-bodies`
reconstructs an already-captured session per-step straight from the raw
request/response bodies (paired by `session_id` and the message chain) with no
live receiver — the full-fidelity path. `capture-otlp status` lists the
captured session ids available to flush.

You usually do not need to run `flush` by hand: the watcher tick auto-flushes a
project's active OTel sessions into the bucket once each session goes idle
(zero-touch, watermark-gated, at most once per session), so live captures land
on the trace automatically. Run `flush` explicitly only to land a session
immediately or to reconstruct an already-captured session retroactively with
`--from-raw-bodies`.

If the receiver is down, agent traffic is not blocked. Claude Code's OTel
emission is fire-and-forget.

## Use Cases

- resume from a specific context node;
- compare two steps after compaction;
- build training rows that include the visible context;
- audit whether a tool write was made with enough surrounding information;
- warm a new session by pulling only the relevant prior trace context.
