# Rationale: opentraces-schema 0.5.0

`0.5.0` adds two cross-reference fields tying `TraceRecord` to the new
Context Tree substrate (plan 077). The bump is strictly additive over
`0.4.0`; no existing field is renamed, removed, narrowed, or
restructured. Pre-`0.5.0` JSONL parses unchanged because both fields
default to an empty value (`None` and `{}` respectively).

## What landed

- `Step.context_node_id: str | None` (default `None`). When Context Tree
  capture observed a node for the step, this carries the
  `sha256:<hex>` id of that `ContextNode`. Consumers can resolve it via
  `ot://context-node/sha256/<hex>` or look it up in a
  `ContextTreeProjection` to get the assembled model view at that step.
- `TraceRecord.context_tree_summary: dict[str, Any]` (default `{}`).
  Stamped at trace-finalization time from the projection's `summary()`
  method. Typical keys: `node_count`, `layer_count`, `active_path_leaf_id`,
  `capture_limitations`. Empty when Context Tree capture did not run.

## Why two narrow fields instead of an embedded substrate

Context Tree is a separate substrate that rides on Trail's append-only
event log; the canonical data lives in
`refs/opentraces/local/events/v1`, not in the JSONL. We want
`TraceRecord` to remain a single-line snapshot, so the two new fields are
deliberately small:

- `context_node_id` on each step is a pointer, not the node itself. The
  node's layer contents (system, messages, tool_registry, runtime_state)
  stay in the substrate's content-addressed store and are resolved
  on demand.
- `context_tree_summary` on the record is a roll-up, not the projection.
  A consumer that needs the full tree builds the
  `ContextTreeProjection` from the event log; the summary is enough for
  the common "did this trace have Context Tree captured, and how big was
  it" question without forcing every reader to also read the trail event
  log.

This keeps the JSONL parse cheap and the additive contract clean.

## Backward compatibility

- All existing 0.4.0 JSONL records parse without modification: both new
  fields use defaults (`None`, `{}`).
- All existing 0.4.0 schema tests pass against the new model definitions.
- An old reader (pre-0.5.0) will receive 0.5.0 records with the new
  fields stripped from `model_fields` view; serialized to JSON, the old
  reader sees extra keys but Pydantic v2 ignores unknown fields by
  default for `TraceRecord` (no `extra="forbid"`), so round-trip on the
  pinned fields is preserved.
- Auto-migration via the CLI's `migrate_outdated_shards` path is safe
  under the additive contract in `VERSION-POLICY.md`: missing fields
  on a 0.4.0 row receive the documented defaults when re-validated as
  0.5.0.

## What was deliberately NOT included

- No new top-level model. `ContextNode` and `ContextLayer` live in the
  CLI package's `core/context_tree/models.py`, not in
  `opentraces-schema`. The schema package only exposes the
  cross-reference pointers because the substrate's content store has
  different lifecycle, query, and storage semantics from the trace
  record. Promoting those models into the schema package is deferred
  until we have a second consumer that needs the in-memory shape
  outside the CLI.
- No restructuring of `Step` or `TraceRecord` beyond the two new fields.
  Plan 077 explicitly scopes the schema delta to these two cross-refs;
  anything larger would force a major bump.
