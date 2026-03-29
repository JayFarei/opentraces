# Inbox

The inbox is where you inspect and edit staged traces before committing. Use the web inbox, the terminal inbox, or the `session` CLI subcommands.

## Web Inbox

```bash
opentraces web
opentraces web --port 8080
```

This serves the React viewer from `web/viewer/` through the local Flask app. The timeline view shows each step with tool calls and token counts. The review view groups context items by source (user input, filesystem, external, LLM output).

![Web inbox - timeline view](/docs/assets/web-timeline.png)

![Web inbox - review view](/docs/assets/web-review.png)

## Terminal Inbox

```bash
opentraces tui
opentraces tui --fullscreen
```

Three-panel layout: sessions list, summary, and detail. Keyboard shortcuts for navigation, commit, reject, and discard.

![Terminal inbox](/docs/assets/tui.png)

## CLI

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

## Inbox Flow

```bash
opentraces session commit <trace-id>
opentraces commit --all
opentraces push
```

If you need the old compatibility entry point, `opentraces review` still exists as a hidden alias, but `web`, `tui`, and `session` are the current surfaces.
