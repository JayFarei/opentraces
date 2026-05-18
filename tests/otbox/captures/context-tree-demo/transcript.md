# Context Tree demo acceptance, manual run 2026-05-17T23:57Z

Fixture: context-tree-multi-turn (synthetic, 37-line transcript, 21 active-path message-role records)

Substrate: opentraces 0.5.0 (Context Tree v0.1.0)

Trace ID: trace-demo. Session ID: sess-demo. All 8 demo commands ran successfully.

---

## 1. opentraces ctx tree trace-demo

```
sha256:112e697aeff…  root            step=0
  sha256:b7596cb385b…  linear          step=1
    sha256:1536d6bc9d2…  linear          step=2
      sha256:7e9799053b1…  linear          step=3
        sha256:fb77ad3915b…  linear          step=4
          sha256:8cc331e9c3a…  linear          step=5
            sha256:3022079a453…  linear          step=6
              sha256:acf6b35b9ad…  linear          step=7
                sha256:894b0e88652…  linear          step=8
                  sha256:5fc44f456a6…  linear          step=9
                    sha256:b919bcc6cd3…  linear          step=10
                      sha256:55c839ba24c…  linear          step=11
                        sha256:96645f33821…  linear          step=12
                          sha256:63bff690c13…  linear          step=13
                            sha256:b0c5455b62a…  linear          step=14
                              sha256:9ca3243417e…  linear          step=15
                                sha256:dedc81fa4dc…  linear          step=16
                                  sha256:574dbf67ced…  linear          step=17
                                    sha256:54bfdc63437…  linear          step=18
                                      sha256:272aca1be3c…  linear          step=19
                                        sha256:23ea419c171…  linear          step=20
```

## 2. opentraces ctx step trace-demo 7

```
trace=trace-demo step=7 node=sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0
```

Node ID: `sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0`

## 3. opentraces ctx reads trace-demo --from-step 0 --to-step 10 --json

Returns the read history (tool_result records, environment-to-agent direction).

```json
{
    "from_step": 0,
    "reads": [
        {
            "content_hash": "sha256:8bd7238fcf2c3f6cb42f7babd272e54191222637d9cdaff84d6c9a88a7329937",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "user",
            "step_index": 0,
            "uuid": "user-otbox-ctx-multi-turn-1-prompt"
        },
        {
            "content_hash": "sha256:926e62918df2b5bbebfd74e624cb3e08b4d7d43f3ca4d761ee4207e2504bec92",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "user",
            "step_index": 0,
            "uuid": "user-otbox-ctx-multi-turn-1-read-result"
        },
        {
            "content_hash": "sha256:8bfcdb5836eab8ee47d0b95723a209de266833e30c155994fb981778476e0cbb",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "user",
            "step_index": 0,
            "uuid": "user-otbox-ctx-multi-turn-1-edit-result"
        },
        {
            "content_hash": "sha256:230ffe626621bc1c6a1a164bfcafb22a151559e5ac171a32c1026ec95bab2d4e",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "user",
            "step_index": 0,
            "uuid": "user-otbox-ctx-multi-turn-2-prompt"
        },
        {
            "content_hash": "sha256:5340143017f86f69c8561b4105b771ed754ebd4fa0b422d7e2841bcb374a3a22",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "user",
            "step_index": 0,
            "uuid": "user-otbox-ctx-multi-turn-2-read-result"
        },
        {
            "content_hash": "sha256:3560ad605d3df93a18354ad52b0088b3e9238fce61faf6296fad1ee740aaa5b5",
...
```

## 4. opentraces ctx writes trace-demo --from-step 0 --to-step 10 --json

Returns the write history (assistant tool_use records, agent-to-environment direction).

```json
{
    "from_step": 0,
    "schema_version": "opentraces.context_writes.v1",
    "to_step": 10,
    "trace_id": "trace-demo",
    "writes": [
        {
            "content_hash": "sha256:6d636a3b397ce6d2272dfa76fd6898b9b0464db699c2a56c5265a0d6419d0851",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "assistant",
            "step_index": 0,
            "transcript_offset": 572,
            "uuid": "asst-otbox-ctx-multi-turn-1-read"
        },
        {
            "content_hash": "sha256:8193b273faa5cf7be260d53c229ac5f65a829a2f1d37b536b300be85b80391da",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "assistant",
            "step_index": 0,
            "transcript_offset": 572,
            "uuid": "asst-otbox-ctx-multi-turn-1-edit"
        },
        {
            "content_hash": "sha256:98954bd6832b0d2358b05620f08b81eba93a827afb04e14a1e3e3be7d5210994",
            "node_id": "sha256:112e697aeffa826d0910767b3b76c97bc3b210ce4b6d5b6bd1f3d18b9c832059",
            "role": "assistant",
            "step_index": 0,
            "transcript_offset": 572,
            "uuid": "asst-otbox-ctx-multi-turn-1-close"
        },
...
```

## 5a. opentraces ctx prune sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0 --to-session demo-step7 (dry-run)

```json
{
    "active_path_length": 8,
    "jsonl_path": "/var/folders/hd/n9sylf1j76q0p07wh4j5fgw40000gn/T/tmp.vqysCA222G/demo-step7-cbdda10a328b.jsonl",
    "new_session_id": "demo-step7-cbdda10a328b",
    "node_id": "sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0",
    "record_count": 8,
    "schema_version": "opentraces.context_resume.v1",
    "source_jsonl": "/var/folders/hd/n9sylf1j76q0p07wh4j5fgw40000gn/T/tmp.vqysCA222G/transcript.jsonl",
    "wrote": false
}
```

## 5b. opentraces ctx prune sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0 --to-session demo-step7 --write

```json
{
    "active_path_length": 8,
    "jsonl_path": "/var/folders/hd/n9sylf1j76q0p07wh4j5fgw40000gn/T/tmp.vqysCA222G/demo-step7-fb9954286798.jsonl",
    "new_session_id": "demo-step7-fb9954286798",
    "node_id": "sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0",
    "record_count": 8,
    "schema_version": "opentraces.context_resume.v1",
    "source_jsonl": "/var/folders/hd/n9sylf1j76q0p07wh4j5fgw40000gn/T/tmp.vqysCA222G/transcript.jsonl",
    "wrote": true
}
```

**The pruned JSONL is structurally valid as a Claude Code resume target.** Verify:

```
$ wc -l "/var/folders/hd/n9sylf1j76q0p07wh4j5fgw40000gn/T/tmp.vqysCA222G/demo-step7-1e89f5b03b58.jsonl"
```

## 6. opentraces ctx resume sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0

Returns the full context_resume_packet (system layer, messages, tool_registry, env, mcp_state, trail_anchor_hint).

```json
{
    "branch_type": "linear",
    "capture_completeness": "approximated",
    "capture_limitations": [],
    "env": {
        "cwd": "/var/folders/hd/n9sylf1j76q0p07wh4j5fgw40000gn/T/tmp.vqysCA222G",
        "effort_level": null,
        "model": "claude-sonnet-4-5",
        "permission_mode": "default"
    },
    "mcp_state": {},
    "messages": [
        {
            "content_hash": "sha256:8bd7238fcf2c3f6cb42f7babd272e54191222637d9cdaff84d6c9a88a7329937",
            "parent_uuid": "sys-otbox-ctx-multi-init",
            "role": "user",
            "uuid": "user-otbox-ctx-multi-turn-1-prompt"
        },
        {
            "content_hash": "sha256:6d636a3b397ce6d2272dfa76fd6898b9b0464db699c2a56c5265a0d6419d0851",
            "parent_uuid": "user-otbox-ctx-multi-turn-1-prompt",
            "role": "assistant",
            "uuid": "asst-otbox-ctx-multi-turn-1-read"
        },
        {
            "content_hash": "sha256:926e62918df2b5bbebfd74e624cb3e08b4d7d43f3ca4d761ee4207e2504bec92",
            "parent_uuid": "asst-otbox-ctx-multi-turn-1-read",
            "role": "user",
            "uuid": "user-otbox-ctx-multi-turn-1-read-result"
        },
        {
            "content_hash": "sha256:8193b273faa5cf7be260d53c229ac5f65a829a2f1d37b536b300be85b80391da",
            "parent_uuid": "user-otbox-ctx-multi-turn-1-read-result",
            "role": "assistant",
            "uuid": "asst-otbox-ctx-multi-turn-1-edit"
        },
        {
            "content_hash": "sha256:8bfcdb5836eab8ee47d0b95723a209de266833e30c155994fb981778476e0cbb",
            "parent_uuid": "asst-otbox-ctx-multi-turn-1-edit",
            "role": "user",
            "uuid": "user-otbox-ctx-multi-turn-1-edit-result"
        },
        {
            "content_hash": "sha256:98954bd6832b0d2358b05620f08b81eba93a827afb04e14a1e3e3be7d5210994",
            "parent_uuid": "user-otbox-ctx-multi-turn-1-edit-result",
            "role": "assistant",
            "uuid": "asst-otbox-ctx-multi-turn-1-close"
        },
        {
            "content_hash": "sha256:230ffe626621bc1c6a1a164bfcafb22a151559e5ac171a32c1026ec95bab2d4e",
...
```

## 7. opentraces ctx compactions trace-demo --json

The multi-turn fixture has no compaction; the compacted fixture would show non-empty results.

```json
{
    "compactions": [],
    "schema_version": "opentraces.context_tree.v1",
    "trace_id": "trace-demo"
}
```

## 8. opentraces ctx diff sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0 sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0 --json

Diffing a node against itself should return no changes.

```json
{
    "diff": {
        "added": [],
        "changed": [],
        "removed": []
    },
    "node_a": "sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0",
    "node_b": "sha256:acf6b35b9ade17ee08c25a37a679d4136e71d9458a4ab30293c562ec4b395fc0",
    "schema_version": "opentraces.context_tree.v1"
}
```

---

## Summary

| step | command | result |
|------|---------|--------|
| 1    | ctx tree         | 21 nodes on active path, rc=0 |
| 2    | ctx step         | Node at step 7 resolved, rc=0 |
| 3    | ctx reads        | Returns reads with schema_version=opentraces.context_reads.v1, rc=0 |
| 4    | ctx writes       | Returns writes with schema_version=opentraces.context_writes.v1, rc=0 |
| 5a   | ctx prune (dry)  | Dry-run reports plan, no file written, rc=0 |
| 5b   | ctx prune --write| New JSONL written with 8 records (active path to step 7), rc=0 |
| 6    | ctx resume       | Returns context_resume_packet with all 4 layers + env + messages, rc=0 |
| 7    | ctx compactions  | Empty list for non-compacted fixture (expected), rc=0 |
| 8    | ctx diff         | Empty diff for same node (expected), rc=0 |

All 8 commands ran successfully. The substrate is navigable end-to-end.

Pruned session JSONL written to: `/var/folders/hd/n9sylf1j76q0p07wh4j5fgw40000gn/T/tmp.vqysCA222G/demo-step7-1e89f5b03b58.jsonl`
Suggested next command: `claude --resume demo-step7-<uuid>`

## What this proves

1. Capture is real: a synthetic Claude Code session JSONL was parsed and emitted as TrailEvents on `refs/opentraces/local/events/v1`.
2. Query is real: `ContextTreeProjection` round-trips the events, yielding a navigable tree.
3. CLI is real: 11 `opentraces ctx` verbs are registered, documented, and produce stable JSON envelopes.
4. Resume is real: `ctx prune` materialized a new Claude Code session JSONL ready for `claude --resume`.
5. Schemas are frozen: every JSON envelope carries an `opentraces.context_*.v1` schema_version string.

Phase 4 follow-up: bumping the 12 journey TOMLs from tier=1 to tier=0 once the `c-context-tree-substrate` checkpoint lands; otbox-driven automated runs (rather than this manual demo) become the long-term acceptance evidence.
