# Rationale: opentraces-schema 0.6.0

`0.6.0` makes the trace patch spine explicit. Earlier records exposed a
single `Outcome.patch` unified diff, which was convenient for simple readers
but wrong for the current workflow: a trace can produce many patches, those
patches can mature into different commits, and their survival through Git
history is tracked by the Trace Trails substrate and private bucket
companions.

## What landed

- `Patch`: a structured reference to one trace-produced change. It carries the
  stable `patch_id`, file path, optional `step_index` / `tool_call_id` joins,
  capture methods, before/after snapshot ids, optional `GitAnchor`, supersede
  history, and capture limitations.
- `GitAnchor`: the patch-to-Git match with commit id, anchor range, evidence
  tier, and firmness.
- `TraceRecord.patches[]`: the authoritative output set for dev-time traces.
  A patch is one tool-produced change/hunk, not a file and not a commit.
- `Outcome.patch` removed. Full diff content belongs to the Trail companion
  (`trail.jsonl.gz`) and the bucket blob store; the `TraceRecord` row carries
  stable join keys and compact compatibility projections.

## Why not keep `Outcome.patch`

`Outcome.patch` flattened a trace into one final diff. That lost the shape the
new workflow needs:

- intent slicing wants the edit burst that caused one change, not the final
  repo diff;
- Trace Trails wants per-patch survival through commit, rebase, revert, and
  formatter divergence;
- Context Tree joins want the patch's producing step, so consumers can ask
  what the agent saw when it made the change;
- bucket sync wants compact records plus deterministic companions, not giant
  duplicated diffs inside every dataset row.

The new contract keeps `TraceRecord` as the stable spine and pushes the large
or time-varying evidence into bucket companions:

- `trace.json` / JSONL row: compact trace spine and join keys;
- `trail.jsonl.gz`: patch history, Git anchors, survival observations;
- `context.jsonl.gz`: Context Tree layers and nodes;
- `sources.jsonl.gz`: capture-source events;
- blob store: content-addressed context and raw payloads.

## Compatibility

This is a minor bump inside the pre-1.0 series, but it is a real reader-facing
shape change: readers that relied on `Outcome.patch` must switch to
`TraceRecord.patches[]` plus the Trail companion.

Reader-compatible fields remain:

- `Outcome.committed`
- `Outcome.commit_sha`
- `TraceRecord.git_links`

Those fields are now derived projections of `patches[].anchor` and
`patches[].superseded_by`, kept so trace index, inverse blame, viewers, and
older dataset consumers can keep using commit-level summaries while newer
consumers use the patch spine.

## What was deliberately not included

- Full Trace Trails event models remain in the CLI package. The schema package
  exposes the stable trace-row joins, not the entire append-only substrate.
- Context Tree layer/node models remain outside the schema package. Schema
  `0.5.0` already added the cross-reference fields needed to join into that
  substrate.
- No automatic redaction or publication policy is encoded into the schema.
  Security processing is workflow/config driven and recorded in metadata by
  the CLI pipeline.
