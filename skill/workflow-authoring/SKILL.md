---
name: opentraces-workflow-authoring
description: Design and test OpenTraces local dataset workflows that emit schema-valid JSONL rows.
---

# OpenTraces Workflow Authoring

Use this skill when building a workflow for `ot dataset run`.

## Contract

- The dataset schema in `schemas/row.schema.json` is the only public row shape.
- A workflow emits plain JSONL rows to `OT_DATASET_OUTPUT`.
- Source traces and raw sessions are immutable inputs. Do not mutate them.
- Use `ot trace query` first, `ot trace map` for bounded verification, and `ot trace get` only when a bounded packet/slice is insufficient.
- Use `ot dataset run <name> --dry-run --limit N --verbose` while developing. Dry-runs are never promotable.
- A real run must execute freshly and append only valid non-duplicate rows.

## Recommended Loop

1. Read `.opentraces/manifest.yaml` and `schemas/row.schema.json`.
2. Run a narrow `ot trace query` with exact facets or signals.
3. Expand one candidate with `ot trace map <trace_id> --candidate <unit_id> --json`.
4. Write or update helper scripts under the workflow package.
5. Emit rows to `OT_DATASET_OUTPUT`, one JSON object per line.
6. Run the dataset in dry-run mode and inspect validation/dedupe counts.
7. Run the dataset for real only after the dry-run is clean enough.

## Example Row

```json
{"source_trace_id":"trace-1","source_unit_id":"tu:trace-1:trace","summary":"The user wanted a stricter design review."}
```
