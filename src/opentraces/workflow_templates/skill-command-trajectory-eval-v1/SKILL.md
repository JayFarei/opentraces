---
name: skill-command-trajectory-eval-v1
description: Derive compact eval rows from command-attributed trace trajectories.
mode: agent-skill
requires:
  - ot dataset
  - ot trace get
  - ot trace map
---

# skill-command-trajectory-eval-v1

Build a compact, publish-safe evaluation row projection from the raw
`skill-command-trajectories` evidence source.

The source evidence is intentionally verbose: it keeps full steps, Trace Map
nodes, and raw trace/bucket references so attribution can be audited. This
workflow is the projection layer: it emits the smaller row shape an eval
harness actually needs:

- clean command intent
- expected skill or command label
- expected trajectory start/end steps
- excluded injected-skill-body steps
- write/file/tool labels
- evidence refs back to the raw trace and raw dataset row

## Row Shape

```jsonc
{
  "source_trace_id": "raw:raw-row-000001",
  "source_unit_id": "raw-unit:raw-row-000001",
  "summary": "scout-research: research Voyage AI...",

  "eval_task_type": "command_trajectory_attribution",
  "source_raw_row_id": "raw-row-000001",
  "project_slug": "2026-05-01-hackathon-...",
  "session_id": "...",

  "command_name": "/scout-research",
  "command_source": "claude_slash_command",
  "command_intent": "research Voyage AI...",
  "command_intent_source": "command_args",

  "expected_skill_name": "scout-research",
  "expected_trajectory_start_step": 1,
  "expected_trajectory_end_step": 11,
  "expected_excluded_steps_json": "[]",
  "expected_has_write_operations": true,
  "expected_write_operation_count": 1,
  "expected_files_modified_json": "[\"kb/br/01-voyage-ai-code-mode-data-interface.md\"]",
  "expected_tools_json": "[\"Bash\", \"Read\", \"Write\"]",

  "label_confidence": "high",
  "source_has_injected_body_args": false,
  "source_skill_body_mismatch": false,
  "limitations_json": "[]",
  "evidence_refs_json": "{\"trace\":\"...\",\"unit\":\"...\"}"
}
```

## Deterministic Builder

```bash
python ~/.opentraces/workflows/skill-command-trajectory-eval-v1/scripts/build_rows.py \
  --output "$OT_DATASET_OUTPUT"
```

The script defaults to:

```text
~/.opentraces/datasets/skill-command-trajectories/data/train.jsonl
```

Pass `--source <path>` to project a different raw command trajectory dataset.

## Eval Use

This row shape supports deterministic checks:

- exact skill label match
- trajectory span overlap / IoU
- injected body exclusion
- write/no-write classification
- modified-file overlap
- tool-family classification

Raw evidence stays in the private evidence source/bucket. Eval rows carry only
compact labels and the source raw row ordinal for audit. The private evidence
source retains the exact trace ids, unit ids, and `ot://` refs.
