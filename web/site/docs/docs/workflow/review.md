# Inbox

In 0.4 the public review surface lives under `opentraces dataset review`. The standalone `web` and `tui` commands from 0.3 have been folded into the dataset review flow; row staging, approval, rejection, and reset are dataset-scoped, not project-scoped.

```bash
opentraces dataset review <name>           # default summary
opentraces dataset review <name> --tui     # terminal review
opentraces dataset review <name> --web     # browser review
opentraces dataset review <name> approve <row-id>
opentraces dataset review <name> reject <row-id>
opentraces dataset review <name> reset <row-id>
opentraces dataset review <name> approve --all
```

For the project-level snapshot (stage counts, recent traces, active remote) keep using `opentraces status`. For searching retained traces, use `opentraces trace query`.

## Web Review

```bash
opentraces dataset review my-dataset --web
```

`--web` starts the local Flask server for the dataset's review inbox and opens the React viewer (default `http://127.0.0.1:6000`). It is the richest review surface, with side-by-side row inspection and a built-in publish flow.

### Review tab

![Web inbox - review view](/docs/assets/web-review.png)

The **review** tab shows the Inbox / Approved / Published columns on the left and the selected row on the right. Switch between the `conversation` and `blame` tabs at the top of the preview to flip between the flattened chat stream and the commit-blame view for that row.

- `j` / `k`, move the inbox selection up / down
- `space`, approve the selected inbox row, or move it back from Approved
- `r`, refresh the inbox
- `?`, toggle the review help overlay (also shows the row-legend)
- `q`, quit the local server (browser tab closes automatically)

Per-row actions are visible on hover:

- `+`, approve an inbox row
- `✕`, reject an inbox row (kept local only)
- `−`, move an approved row back to the inbox
- `i`, open the security-pipeline modal for that row

The **Publish** button at the top of the Approved column opens the publish modal. You can publish directly, or run an optional Tier 2 LLM review first (requires `opentraces setup llm-review`). The header also exposes a global `i` (project-wide security info) and `?` (help).

### Graph tab

![Web inbox - graph view](/docs/assets/web-graph.png)

The **graph** tab is the blame surface. It lists recent commits on the left; selecting one shows every trace that contributed lines to that commit, plus a per-file breakdown with attributed line counts. This is how you answer "which trace produced this code?" at commit-granularity.

- `j` / `k`, move the commit selection
- `enter`, jump to the blamed trace in the review tab
- `q`, quit

## Terminal Review

```bash
opentraces dataset review my-dataset --tui
```

The TUI is the shell-native inbox for a dataset. It loads the same row set and the same stage model (Inbox / Approved / Published) as the web viewer, and exposes row detail, security status, approval, rejection, redaction, and publish without leaving the terminal.

![Terminal inbox](/docs/assets/tui.png)

The layout is two columns, Info / Inbox / Approved / Published on the left, the selected row's preview on the right. Numeric keys focus a pane directly.

### Navigation

- `1` / `2` / `3` / `4`, focus Info / Inbox / Approved / Published
- `5`, focus the row preview
- `tab`, cycle focus across the panes
- `j` / `k` (or `↑` / `↓`), move selection
- `enter`, inspect the selected row (focus the preview)
- `g` / `G`, jump to top / bottom of the preview
- `[` / `]`, page the preview up / down from any pane
- `a`, toggle conversation view vs. full view

### Actions

- `space`, add inbox→approved, or remove approved→inbox
- `p`, open the publish modal (LLM review or publish now)
- `r`, refresh
- `d`, discard the selected row (deferred; actually deleted on quit)
- `u`, undo the last reject / discard / move
- `i`, open the security-pipeline modal for the selected row
- `?`, toggle the full help overlay
- `q`, quit (flushes pending discards)

### Row legend

- `·`, normal row
- `◐` (dim cyan), recently touched in roughly the last 2 hours
- `●` (yellow), security findings still need review
- `●` (red), blocked row
- `↑N` (dim cyan), session generation. `↑1` is the first captured trace for that session, `↑2+` means the same session kept going and this newer trace replaces an older one.

## CLI

```bash
opentraces dataset status my-dataset
opentraces dataset review my-dataset
opentraces dataset review my-dataset approve <row-id>
opentraces dataset review my-dataset approve --all
opentraces dataset review my-dataset reject <row-id>
opentraces dataset review my-dataset reset <row-id>
```

Use the CLI when you want scriptable review or a precise edit loop:

- `dataset status` reports row counts by state
- `dataset review` with no positional args prints the dataset's review summary
- `approve` / `reject` / `reset` operate on row ids, optionally with `--all`
- Pass `--json` for machine-readable output

For trace-level search (across retained traces, not dataset rows) use `opentraces trace query`.

## Stage Vocabulary

| Stage | Meaning |
|-------|---------|
| `inbox` | Needs review |
| `approved` | Ready for the next publish |
| `published` | Uploaded upstream |
| `rejected` | Kept local only |
| `blocked` | Needs action before it can be approved |

Internally the state machine tracks additional states. The public CLI and UIs collapse those down to the visible stages above.

## What To Look For

- Secrets that escaped redaction
- Internal hostnames and collaboration URLs
- Customer names, paths, or identifiers
- Rows that are too short or too trivial
- Tool outputs that should be redacted before sharing

## Inbox Flow

```bash
opentraces dataset review my-dataset approve --all
opentraces dataset publish my-dataset
```

If you want a faster automatic path, set the project to auto-approve clean traces at capture time:

```bash
opentraces config set review_policy auto --project
```

That still does not publish automatically. Upload remains explicit.
