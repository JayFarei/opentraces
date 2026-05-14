---
name: pr-intent-summary-v1
description: Per-commit trace lineage rows for a branch context, deterministic.
mode: deterministic-script
requires:
  - opentraces.core.entity_join
  - opentraces.core.bursts
  - opentraces.core.trace_summary
  - opentraces.core.bucket_store
---

# pr-intent-summary-v1

Emits one structured row per commit on a branch, joining each commit to the
agent traces that authored it. The row carries:

- the commit identity (sha, subject, body, merge flag)
- a `lineage` array of contributing traces, each with:
  - the user's *intent* (`Burst.intent.most_substantive_spec`)
  - a deterministic English summary of *what changed*
    (`trace_summary.summarize_contribution`)
  - a stable `ot://` resource pointer + bucket path
- a `no_lineage_reason` for commits without trace coverage

This row shape is the *endpoint*. Any consumer can read it: the PR body
renderer in `core.branch_context`, a future Slack notifier, a dashboard,
a CI check.

## Scope contract

Reads `scope` from `run_packet.json`. Expects `scope.kind == "branch_context"`
with:

```jsonc
{
  "kind": "branch_context",
  "project_dir": "/abs/path/to/repo",
  "project_id": "...",
  "base": "main",
  "head": "HEAD",
  "merge_base": "abc1234...",
  "commits": [
    {"sha": "...", "short_sha": "abc1234", "subject": "...", "body": "...", "is_merge": false},
    ...
  ],
  "scope_digest": "..."
}
```

## Row schema

See `schemas/row.schema.json`. One JSONL row per commit in `scope.commits`
order.

## Builder

```bash
python ~/.opentraces/workflows/pr-intent-summary-v1/scripts/build_rows.py
```

The script reads `OT_RUN_PACKET` for the run packet and writes rows to
`OT_DATASET_OUTPUT`. It is fully deterministic — same inputs produce
byte-identical output (modulo dict ordering).

## Consumers

- `opentraces trail pr render | create | update` — renders the row stream
  as a GitHub PR body and (for create/update) pushes via `gh`.

Future consumers can read the same JSONL and project to other destinations
without changing this workflow.
