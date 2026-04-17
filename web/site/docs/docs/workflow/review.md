# Inbox

The inbox is where traces are reviewed before upload. In 0.3 the public review surface is `web`, `tui`, and the flat CLI commands like `list`, `show`, `add`, `reject`, `reset`, `redact`, and `discard`.

## Web Inbox

```bash
opentraces web
opentraces web --port 6060 --no-open
```

`web` starts the local Flask server for the current project's inbox and opens the React viewer at `http://127.0.0.1:6000` (override with `--port`, pass `--no-open` to skip the browser launch). It is the richest review surface, with side-by-side trace inspection and a built-in push flow.

### Review tab

![Web inbox - review view](/docs/assets/web-review.png)

The **review** tab shows the Inbox / Staged / Pushed columns on the left and the selected trace on the right. Switch between the `conversation` and `blame` tabs at the top of the preview to flip between the flattened chat stream and the commit-blame view for that trace.

- `j` / `k` — move the inbox selection up / down
- `space` — add the selected inbox trace to Staged, or remove it from Staged
- `r` — refresh the inbox from the session files on disk
- `?` — toggle the review help overlay (also shows the row-legend)
- `q` — quit the local server (browser tab closes automatically)

Per-row actions are visible on hover:

- `+` — stage an inbox trace
- `✕` — reject an inbox trace (kept local only)
- `−` — unstage a staged trace back to the inbox
- `i` — open the security-pipeline modal for that trace

The **Push** button at the top of the Staged column opens the push modal. You can push directly, or run an optional Tier-2 LLM review first (requires `opentraces setup llm-review`). The header also exposes a global `i` (project-wide security info) and `?` (help).

### Graph tab

![Web inbox - graph view](/docs/assets/web-graph.png)

The **graph** tab is the blame surface. It lists recent commits on the left; selecting one shows every trace that contributed lines to that commit, plus a per-file breakdown with attributed line counts. This is how you answer "which trace produced this code?" at commit-granularity.

- `j` / `k` — move the commit selection
- `enter` — jump to the blamed trace in the review tab
- `q` — quit

## Terminal Inbox

```bash
opentraces tui
opentraces tui --fullscreen
opentraces tui --limit 0
```

The TUI is the shell-native inbox. It loads the same trace set and the same stage model (Inbox / Staged / Pushed) as the web viewer, and exposes trace detail, security status, staging, rejection, discard, and push without leaving the terminal.

![Terminal inbox](/docs/assets/tui.png)

The layout is two columns — Info / Inbox / Staged / Pushed on the left, the selected trace's preview on the right. Numeric keys focus a pane directly.

### Navigation

- `1` / `2` / `3` / `4` — focus Info / Inbox / Staged / Pushed
- `5` — focus the trace preview
- `tab` — cycle focus across the panes
- `j` / `k` (or `↑` / `↓`) — move selection
- `enter` — inspect the selected trace (focus the preview)
- `g` / `G` — jump to top / bottom of the preview
- `[` / `]` — page the preview up / down from any pane
- `a` — toggle conversation view vs. full view

### Actions

- `space` — add inbox→staged, or remove staged→inbox
- `p` — open the push modal (LLM review or push now)
- `r` — refresh (re-capture and reload)
- `d` — discard the selected trace (deferred; actually deleted on quit)
- `u` — undo the last reject / discard / stage move
- `i` — open the security-pipeline modal for the selected trace
- `?` — toggle the full help overlay
- `q` — quit (flushes pending discards)

### Trace row legend

- `·` — normal trace
- `◐` (dim cyan) — recently touched in roughly the last 2 hours
- `●` (yellow) — security findings still need review
- `●` (red) — blocked trace
- `↑N` (dim cyan) — session generation; `↑1` is the first captured trace for that session, `↑2+` means the same session kept going and this newer trace replaces an older one. Refresh pulls the latest; review and push the latest generation.

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
