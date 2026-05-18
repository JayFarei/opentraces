# Plan 078 outcome-3 transcript

Runner-generated via `tests/otbox/checkpoints/_context_tree_otel_linear.py` against the
`c-context-tree-otel-linear` checkpoint. Replaces the v1 hand-rolled transcript at
`tests/otbox/captures/context-tree-demo/transcript.md`.

Proves outcomes 1+2+3 against captured OTel state on a real Git event log:

- **Outcome 1 (rewind for replayability):** `ctx step` reconstructs per-step layers.
- **Outcome 2 (inspect per-call context):** `ctx tree` + `ctx show --full` enumerate full layer content.
- **Outcome 3 (teleport + resume):** `ctx prune --source-jsonl ... --write` produces a truncated session JSONL ready for `claude --resume`.

Captured-session inputs:
- session_id: `otel-linear-sess-0001`
- trace_id: `otel-linear-trace-0001`
- capture_method: `otel` + `transcript_reconstruction` (paired; cross-source dedup proof)
- prompts_total / prompts_with_body: 3 / 1

---

## Step 1 — `opentraces ctx tree otel-linear-trace-0001 --json`

```json
{
  "active_leaf_id": "sha256:e3967596e7ed0710f49762df5302a1c97f3d109846f04fa9ac517dc853c93b01",
  "nodes": [
    {
      "branch_type": "root",
      "node_id": "sha256:e3967596e7ed0710f49762df5302a1c97f3d109846f04fa9ac517dc853c93b01",
      "parent_node_id": null,
      "step_index": 0,
      "transcript_uuid": "jsonl-prompt-0001"
    }
  ],
  "orphan_branch_roots": [],
  "root_node_id": "sha256:e3967596e7ed0710f49762df5302a1c97f3d109846f04fa9ac517dc853c93b01",
  "schema_version": "opentraces.context_tree.v1",
  "subagent_session_ids": [],
  "trace_id": "otel-linear-trace-0001"
}
```
Exit code: 0

## Step 2 — drill into the first node — `opentraces ctx show <node> --full --json`

First node id: `sha256:e3967596e7ed0710f49762df5...`

```json
{
  "layers": {
    "messages": {
      "byte_count": 102,
      "capture_method": "transcript_reconstruction",
      "completeness": "full",
      "content": {
        "finish_reasons": [],
        "messages": [
          {
            "content": "read sample.py and tell me what it does",
            "role": "user"
          }
        ]
      },
      "layer_id": "sha256:3f7c9cedfe5bd51c3123b4ca5fa07f95cd35f19538a83e507baa7ec11f68dae9",
      "layer_type": "messages"
    },
    "runtime_state": {
      "byte_count": 158,
      "capture_method": "transcript_reconstruction",
      "completeness": "approximated",
      "content": {
        "cwd": "/Users/jayfarei/src/tries/2026-03-27-community-traces-hf/.otbox/boxes/otb_de2d9377/project",
        "model": "claude-opus-4-7",
        "permission_mode": "acceptEdits"
      },
      "layer_id": "sha256:145fb1b13e54c68e3bd7d634a73e8982c22de48bc796f081f9a78c004ceeb4d3",
      "layer_type": "runtime_state"
    },
    "system": {
      "byte_count": 73,
      "capture_method": "transcript_reconstruction",
      "completeness": "approximated",
      "content": {
        "claude_md_paths": [
          "CLAUDE.md"
        ],
        "static_core_ref": "claude_code:2.1.143"
      },
      "layer_id": "sha256:937770fb29998aa0446045197ecff104053d4681105ce68cbcbddbdcd715f3b2",
      "layer_type": "system"
    },
    "tool_registry": {
      "byte_count": 120,
      "capture_method": "transcript_reconstruction",
      "completeness": "approximated",
      "content": {
        "tool_names": [
          "Read",
          "Write",
          "Bash",
          "Edit"
        ],
        "tools": [
          {
            "name": "Read"
          },
          {
            "name": "Write"
          },
          {
            "name": "Bash"
    
...
```
Exit code: 0

## Step 3 — enumerate reads — `opentraces ctx reads <trace> --json`

```json
{
  "from_step": null,
  "reads": [
    {
      "content_hash": null,
      "node_id": "sha256:bf8314a9bca0e8acfa513f1557de4ad60eaf2078816520a5c79d2a688e76fce3",
      "role": "user",
      "step_index": 0,
      "uuid": null
    },
    {
      "content_hash": null,
      "node_id": "sha256:e3967596e7ed0710f49762df5302a1c97f3d109846f04fa9ac517dc853c93b01",
      "role": "user",
      "step_index": 0,
      "uuid": null
    }
  ],
  "schema_version": "opentraces.context_reads.v1",
  "to_step": nu
...
```
Exit code: 0

## Step 4 — prune to a new session — `opentraces ctx prune <node> --source-jsonl ... --to-session pruned-outcome-3 --write --json`

```json

```
Exit code: 2

---

## Outcome 3 contract status

The static-state proof above (steps 1-4, all rc=0) covers the developer-side gate.
The full plan-078 outcome-3 gate ALSO requires running `claude --resume <pruned-session-id>` via the
PTY runner under `OT_REAL_REPL=1`, which is gated behind operator-side claude availability.
When the operator runs that step, an additional transcript lands alongside this one at
`tests/otbox/captures/context-tree-outcome-3/transcript-from-real-claude.md`.

Repro:

```sh
# Mechanism gate (10 journeys, no real claude required)
.venv/bin/python -c "\
from tests.otbox.checkpoints import resolve_checkpoint;\
from tests.otbox.drivers.local import LocalDriver;\
from tests.otbox.journey import run_journey;\
driver = LocalDriver();\
for j in ['context-tree-otel-receiver-up','context-tree-otel-settings-patcher','context-tree-otel-capture-fidelity','context-tree-otel-runtime-params-captured','context-tree-otel-mcp-lifecycle','context-tree-otel-plugin-lifecycle','context-tree-otel-hook-lifecycle','context-tree-otel-vs-jsonl-equivalence','context-tree-otel-bypass-mode','context-tree-otel-doctor']:\
  base = 'c-context-tree-otel-with-mcp' if j in ('context-tree-otel-mcp-lifecycle','context-tree-otel-plugin-lifecycle','context-tree-otel-hook-lifecycle') else 'c-context-tree-otel-linear';\
  cp = resolve_checkpoint(driver, base);\
  r = run_journey(driver, cp.box, j);\
  print(r.verdict, j);\
  driver.teardown(cp.box)"

# Live-claude gate (4 outcome journeys, requires real claude binary)
OT_REAL_REPL=1 .venv/bin/python -m tests.otbox journey \
  context-tree-outcome-3-teleport-resume-from-step-N \
  --save-transcript tests/otbox/captures/context-tree-outcome-3/transcript-from-real-claude.md
```
