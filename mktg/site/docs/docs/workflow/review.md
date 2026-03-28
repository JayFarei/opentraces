# Review

The review interface lets you inspect, approve, redact, or reject traces before they're uploaded. Required for guarded (flagged traces) and strict (all traces) tiers.

## CLI Review

```bash
opentraces review
```

Walks through staged traces one at a time. For each trace, you can:

- **Approve** - Mark as ready for upload
- **Redact** - Remove specific turns, tool outputs, or file contents
- **Reject** - Discard the trace entirely
- **Skip** - Leave for later

## Web Review

```bash
opentraces review --web
opentraces review --web --port 8080
```

Launches a local Flask web UI for reviewing traces in the browser. Provides a richer view with syntax highlighting and side-by-side comparisons.

## TUI Review

```bash
opentraces review --tui
```

Terminal UI with keyboard navigation. Requires `pip install opentraces[tui]`.

## What to Look For

During review, pay attention to:

- **Secrets** that slipped past scanning (unusual API key formats)
- **Internal tool names** or proprietary system references
- **Customer data** mentioned in prompts or tool outputs
- **File paths** that reveal org structure
- **Low-quality traces** (trivial interactions, failed sessions)

## Review Status

After review, traces are in one of three states:

| Status | Meaning |
|--------|---------|
| `approved` | Ready for upload |
| `rejected` | Will not be uploaded |
| `pending` | Not yet reviewed |

## Push After Review

```bash
# Push everything approved
opentraces push --approved-only

# Push all staged (for open tier)
opentraces push
```

See [Pushing](/docs/workflow/pushing) for upload options.
