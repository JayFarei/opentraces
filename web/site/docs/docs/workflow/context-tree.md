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
- **Reads and writes are queryable.** `ctx reads` and `ctx writes` expose what
  the agent observed and changed from the context substrate.
- **Resume is a first-class output.** `ctx resume` creates a structured packet
  for an agent to restart from a context node.

## Commands

```bash
opentraces ctx list --json
opentraces ctx info <trace-id> --json
opentraces ctx tree <trace-id> --json
opentraces ctx show <context-node-id> --json
opentraces ctx step <trace-id> <step-index> --json
opentraces ctx reads <trace-id> --json
opentraces ctx writes <trace-id> --json
opentraces ctx diff <node-a> <node-b> --json
opentraces ctx compactions <trace-id> --json
opentraces ctx resume <context-node-id> --json
opentraces ctx prune <context-node-id> --source-jsonl <session.jsonl>
opentraces ctx resolve ot://context-node/<id> --json
opentraces ctx anchor-for-step <trace-id> <step-index>
```

`ctx list` and `ctx info` are manifest-only reads for fast inventory. The
other commands load Context Tree events and layer blobs as needed.

## Layers

Context nodes reference content-addressed layers:

| Layer | Meaning |
|-------|---------|
| `system` | System prompt and instruction context |
| `messages` | Conversation messages in scope |
| `tool_registry` | Tools and schemas visible to the model |
| `runtime_state` | Captured runtime settings and state hints |

## Capture Sources

The substrate accepts additive capture sources:

| Source | Status | Notes |
|--------|--------|-------|
| JSONL reconstruction | available | Works from agent session logs; some views are session-level approximations |
| Pi extension sidecars | available for Pi | Provider/context sidecars use `capture_method=live_capture` with fuller messages/tool registry/runtime state when available; transcript fallback records limitations |
| OTLP receiver | available for Claude Code | Captures fuller API body, tool schema, and sampling parameter evidence |
| HTTP proxy | reserved/deferred | Historical prototype, not the current path |

## OTLP Operations

```bash
opentraces setup capture-otlp
opentraces capture-otlp start
opentraces capture-otlp status --json
opentraces capture-otlp flush --session <session-id> --project <repo> --trace-id <trace-id>
opentraces capture-otlp stop
```

If the receiver is down, agent traffic is not blocked. Claude Code's OTel
emission is fire-and-forget.

## Use Cases

- resume from a specific context node;
- compare two steps after compaction;
- build training rows that include the visible context;
- audit whether a tool write was made with enough surrounding information;
- warm a new session by pulling only the relevant prior trace context.
