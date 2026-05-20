# Inbox

The public review surface currently lives in the CLI under `opentraces dataset review`. The old standalone `web` and `tui` trace-review clients are decommissioned, and `dataset review --web` / `--tui` are intentionally unavailable until the next dataset-scoped review UI lands.

```bash
opentraces dataset review <name>           # default summary
opentraces dataset review <name> --json    # row details
opentraces dataset review approve <name> <row-id>
opentraces dataset review reject <name> <row-id>
opentraces dataset review reset <name> <row-id>
opentraces dataset review approve <name> --all
```

For the project-level snapshot (stage counts, recent traces, active remote) keep using `opentraces status`. For searching retained traces, use `opentraces trace query`.

## CLI

```bash
opentraces dataset status my-dataset
opentraces dataset review my-dataset
opentraces dataset review approve my-dataset <row-id>
opentraces dataset review approve my-dataset --all
opentraces dataset review reject my-dataset <row-id>
opentraces dataset review reset my-dataset <row-id>
```

Use the CLI when you want scriptable review or a precise edit loop:

- `dataset status` reports row counts by state
- `dataset review` with a dataset name prints the dataset's review summary
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
opentraces dataset review approve my-dataset --all
opentraces dataset publish my-dataset
```

If you want a faster automatic path, set the project to auto-approve clean traces at capture time:

```bash
opentraces config set review_policy auto --project
```

That still does not publish automatically. Upload remains explicit.
