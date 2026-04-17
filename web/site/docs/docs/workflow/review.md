# Inbox

The inbox is where traces are reviewed before upload. In 0.3 the public review surface is `web`, `tui`, and the flat CLI commands like `list`, `show`, `add`, `reject`, `reset`, `redact`, and `discard`.

## Web Inbox

```bash
opentraces web
opentraces web --port 6060 --no-open
```

This starts the local Flask server for the current project's inbox. Use it when you want the richest review surface, detailed trace inspection, or the built-in push flow.

![Web inbox - timeline view](/docs/assets/web-timeline.png)

![Web inbox - review view](/docs/assets/web-review.png)

## Terminal Inbox

```bash
opentraces tui
opentraces tui --fullscreen
opentraces tui --limit 0
```

The TUI is the shell-native inbox. It loads the same trace set and exposes trace detail, security status, staging, rejection, discard, and push.

![Terminal inbox](/docs/assets/tui.png)

Current key bindings include:

- `j` / `k` to move
- `space` to select or stage
- `p` to push
- `d` to discard
- `r` to refresh
- `a` to toggle view
- `u` to undo
- `i` for security info
- `?` for help
- `q` to quit

### When you see `↑N`

In the TUI, `↑2`, `↑3`, and so on mean the current row is a newer trace
from the same session replacing an older one.

- Press `r` to refresh the inbox from the session files on disk.
- If the session is still just growing in the inbox or staged flow, refresh updates the same trace in place.
- If that session had already produced an older finalized trace and then kept going, refresh creates a newer trace generation for the same session instead.
- Review and push the latest generation, not the older one.

This does **not** mean "there is a trace above this row in the list". It
means "this session kept going after an older trace from it had already been
captured".

## CLI

```bash
opentraces list
opentraces list --stage inbox
opentraces list --by-commit
opentraces show <trace-id>
opentraces show <trace-id> --verbose
opentraces show <trace-id> --markdown
opentraces add <trace-id>
opentraces add --all
opentraces reject <trace-id>
opentraces reset <trace-id>
opentraces redact <trace-id> --step 3
opentraces discard <trace-id> --yes
```

Use the CLI when you want scriptable review or a precise edit loop:

- `list` filters the local inbox by stage, model, agent, remote, or commit grouping
- `show` prints the full trace detail, with `--verbose` to remove the default 500 character truncation
- `show --markdown` wraps the trace for safe handoff to another LLM
- `add` stages upload-eligible traces
- `reject` keeps a trace local only
- `reset` moves a trace back to Inbox
- `redact` rewrites the stored trace JSON in place
- `discard` permanently deletes the local trace

## Stage Vocabulary

| Stage | Meaning |
|-------|---------|
| `inbox` | Needs review |
| `staged` | Ready for the next push |
| `pushed` | Published upstream |
| `rejected` | Kept local only |
| `blocked` | Needs action before it can be staged |

Internally the state machine tracks additional states. The public CLI and UIs collapse those down to the visible stages above.

## What To Look For

- Secrets that escaped redaction
- Internal hostnames and collaboration URLs
- Customer names, paths, or identifiers
- Traces that are too short or too trivial
- Tool outputs that should be redacted before sharing

## Inbox Flow

```bash
opentraces add <trace-id>
opentraces add --all
opentraces push
```

If you refreshed and a session produced a newer generation, stage and push the
latest generation for that session.

If you want a faster automatic path, set the project to auto-approve clean traces:

```bash
opentraces setup review-policy --auto
```

That still does not push automatically. Upload remains explicit.
