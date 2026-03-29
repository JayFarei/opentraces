# Review

The review surface is the inbox. Use the browser UI, the TUI, or the `session` subcommands to inspect and edit staged traces.

## Browser Inbox

```bash
opentraces web
opentraces web --port 8080
```

This serves the React viewer from `web/viewer/` through the local Flask inbox app.

## Terminal Inbox

```bash
opentraces tui
opentraces tui --fullscreen
```

## CLI Review

```bash
opentraces session list
opentraces session show <trace-id>
opentraces session commit <trace-id>
opentraces session reject <trace-id>
opentraces session reset <trace-id>
opentraces session redact <trace-id> --step 3
opentraces session discard <trace-id> --yes
```

`commit` moves a trace directly to `Committed`, `reject` keeps it local only, `reset` sends it back to `Inbox`, and `redact` rewrites the staged JSONL in place.

## Stage Vocabulary

| Stage | Meaning |
|-------|---------|
| `inbox` | Needs review |
| `committed` | Bundled for upload |
| `pushed` | Published upstream |
| `rejected` | Kept local only |

## What To Look For

- Secrets that escaped redaction
- Internal hostnames and collaboration URLs
- Customer names, paths, or identifiers
- Traces that are too short or too trivial
- Tool outputs that should be redacted before sharing

## Review Flow

```bash
opentraces session commit <trace-id>
opentraces commit --all
opentraces push
```

If you need the old compatibility entry point, `opentraces review` still exists as a hidden alias, but `web`, `tui`, and `session` are the current surfaces.
