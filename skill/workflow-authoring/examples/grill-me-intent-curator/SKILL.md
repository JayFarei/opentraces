---
name: grill-me-intent-curator
description: Build rows for the grill-me-intents dataset.
mode: agent-skill
requires:
  - ot trace query
  - ot trace map
  - ot trace get
---

# Grill-me Intent Curator

Read the run packet and schema supplied by `ot dataset run`.

Use `ot trace query --skill grill-me --json` to find candidates. Prefer `ot trace map <trace_id> --candidate <unit_id> --json` before loading a full trace. Emit rows matching the dataset schema to `OT_DATASET_OUTPUT`.
