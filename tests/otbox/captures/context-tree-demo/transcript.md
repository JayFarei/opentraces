# Context Tree demo acceptance, run 2026-06-12T19:07:31.201048+00:00

Fixture: context-tree-demo-acceptance
Substrate: opentraces unknown, schema unknown
All 9 commands ran successfully. All 46 assertions passed.

---

## 1. opentraces ctx tree c70afe5d-50ef-483d-920d-922c05ec9aa8 --json

```
{
  "active_leaf_id": "sha256:202930b3a06d198143a51b230c1734ec63fb48548962fbe2c5eb66fad63861e4",
  "nodes": [
    {
      "branch_type": "root",
      "node_id": "sha256:32ba79d64f7fb8bb2865f824520f08a52f760f585203c0406049f2dd368f56b2",
      "parent_node_id": null,
      "step_index": 0,
      "transcript_uuid": "user-otbox-ctx-multi-turn-1-prompt"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:0474cb26d7856f2315658cfe366db206df9ffad96dd5e58544635932b4331347",
      "parent_node_id": "sha256:32ba79d64f7fb8bb2865f824520f08a52f760f585203c0406049f2dd368f56b2",
      "step_index": 1,
      "transcript_uuid": "asst-otbox-ctx-multi-turn-1-read"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:0d46158a0067648b11278d14af8797e019af5777a0460da25faabcf677a9e934",
      "parent_node_id": "sha256:0474cb26d7856f2315658cfe366db206df9ffad96dd5e58544635932b4331347",
      "step_index": 2,
      "transcript_uuid": "user-otbox-ctx-multi-turn-1-read-result"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:313704c4b2926db3fe27c066c0a95ca2a7455c7c25b55bb7fab84edb55973695",
      "parent_node_id": "sha256:0d46158a0067648b11278d14af8797e019af5777a0460da25faabcf677a9e934",
      "step_index": 3,
      "transcript_uuid": "asst-otbox-ctx-multi-turn-1-edit"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:65782e65c0c5672e0e990e94cf80acbaa4c3e1ffb446cb11861e9be6857351b3",
      "parent_node_id": "sha256:313704c4b2926db3fe27c066c0a95ca2a7455c7c25b55bb7fab84edb55973695",
      "step_index": 4,
      "transcript_uuid": "user-otbox-ctx-multi-turn-1-edit-result"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "parent_node_id": "sha256:65782e65c0c5672e0e990e94cf80acbaa4c3e1ffb446cb11861e9be6857351b3",
      "step_index": 5,
      "transcript_uuid": "asst-otbox-ctx-multi-turn-1-close"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:2b55a577599b0525f248efeddec2584f56bc6d326531518225e6df485471dbf4",
      "parent_node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "step_index": 6,
      "transcript_uuid": "user-otbox-ctx-multi-turn-2-prompt"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
      "parent_node_id": "sha256:2b55a577599b0525f248efeddec2584f56bc6d326531518225e6df485471dbf4",
      "step_index": 7,
      "transcript_uuid": "asst-otbox-ctx-multi-turn-2-read"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:302b926efe63d2feae5dbbab23c1dbb2f08eebe3c820ee958bfc8736065f4c42",
      "parent_node_id": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
      "step_index": 8,
      "transcript_uuid": "user-otbox-ctx-multi-turn-2-read-result"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:91e12204707027ff46bcf8f853211a2f0e475188e0f670c3f8cf0a0844529857",
      "parent_node_id": "sha256:302b926efe63d2feae5dbbab23c1dbb2f08eebe3c820ee958bfc8736065f4c42",
      "step_index": 9,
      "transcript_uuid": "asst-otbox-ctx-multi-turn-2-edit"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:220bd9a73354fd7b010192f84f8fc51d749471717d01dc827f26758a805d2f4a",
      "parent_node_id": "sha256:91e12204707027ff46bcf8f853211a2f0e475188e0f670c3f8cf0a0844529857",
      "step_index": 10,
      "transcript_uuid": "user-otbox-ctx-multi-turn-2-edit-result"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:148629432fcd0b0a80f7644d4db19b679bd6e60a38e245482ba664a581f07b5f",
      "parent_node_id": "sha256:220bd9a73354fd7b010192f84f8fc51d749471717d01dc827f26758a805d2f4a",
      "step_index": 11,
      "transcript_uuid": "asst-otbox-ctx-multi-turn-2-close"
    },
    {
      "branch_type": "linear",
      "node_id": "sha256:5d36258a856200ceb3dd8567487082b0601c4af2fcc2fe44b0e743050c919e0a",
      "parent_node_id": "sha256:148629432fcd0b0a80f7644d4db19b679bd6e60a38e2
... (truncated)
```

PASS: returncode, stdout_json, stdout_json, stdout_json_length_equals, stdout_json

## 2. opentraces ctx step c70afe5d-50ef-483d-920d-922c05ec9aa8 7 --json

```
{
  "layers": {
    "messages": {
      "byte_count": 4459,
      "capture_method": "transcript_reconstruction",
      "completeness": "full",
      "layer_id": "sha256:135f84e1fed6c0e26cdcec95f4937b5b9f0335dd4277c1cf619177ba7e17cc8a",
      "layer_type": "messages"
    },
    "runtime_state": {
      "byte_count": 835,
      "capture_method": "transcript_reconstruction",
      "completeness": "full",
      "layer_id": "sha256:4190fc0742c6dc8f7072bc1613901d90e8337709757c14fa3741e5a221c575b3",
      "layer_type": "runtime_state"
    },
    "system": {
      "byte_count": 549,
      "capture_method": "transcript_reconstruction",
      "completeness": "approximated",
      "layer_id": "sha256:f9d82b6d67666dc74a8b9dab635748962066a3e8a3d52b1e9403eba95d4998f5",
      "layer_type": "system"
    },
    "tool_registry": {
      "byte_count": 370,
      "capture_method": "transcript_reconstruction",
      "completeness": "approximated",
      "layer_id": "sha256:82972b13b2e53a91616e5de2dd45a1d995c39f9bb0027b7881a9fb957461f2ab",
      "layer_type": "tool_registry"
    }
  },
  "node": {
    "branch_type": "linear",
    "capture_completeness": "approximated",
    "node_id": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
    "parent_node_id": "sha256:2b55a577599b0525f248efeddec2584f56bc6d326531518225e6df485471dbf4",
    "step_index": 7,
    "subagent_session_id": null,
    "trace_id": "c70afe5d-50ef-483d-920d-922c05ec9aa8",
    "trail_anchor_hint": null,
    "transcript_offset": 7302,
    "transcript_uuid": "asst-otbox-ctx-multi-turn-2-read"
  },
  "node_id": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
  "schema_version": "opentraces.context_tree.v1"
}
```

PASS: returncode, stdout_json, stdout_json, stdout_json, stdout_json, stdout_json, stdout_json, stdout_json

## 3. opentraces ctx reads c70afe5d-50ef-483d-920d-922c05ec9aa8 --from-step 5 --to-step 9 --json

```
{
  "from_step": 5,
  "reads": [
    {
      "content_hash": "sha256:8bd7238fcf2c3f6cb42f7babd272e54191222637d9cdaff84d6c9a88a7329937",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-1-prompt"
    },
    {
      "content_hash": "sha256:926e62918df2b5bbebfd74e624cb3e08b4d7d43f3ca4d761ee4207e2504bec92",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-1-read-result"
    },
    {
      "content_hash": "sha256:8bfcdb5836eab8ee47d0b95723a209de266833e30c155994fb981778476e0cbb",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-1-edit-result"
    },
    {
      "content_hash": "sha256:230ffe626621bc1c6a1a164bfcafb22a151559e5ac171a32c1026ec95bab2d4e",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-2-prompt"
    },
    {
      "content_hash": "sha256:5340143017f86f69c8561b4105b771ed754ebd4fa0b422d7e2841bcb374a3a22",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-2-read-result"
    },
    {
      "content_hash": "sha256:3560ad605d3df93a18354ad52b0088b3e9238fce61faf6296fad1ee740aaa5b5",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-2-edit-result"
    },
    {
      "content_hash": "sha256:acb216e2bc256abbb5f1f120d96e9b7ceec58b9832700abf1adf42f3c2edd277",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-3-prompt"
    },
    {
      "content_hash": "sha256:d67bcdb98d44776a097cbd1f00c1e55f3729e152f7e85576fc4f0ce2fa1801a6",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-3-read-result"
    },
    {
      "content_hash": "sha256:4ab5c9d7b3d32752af12aac34895369175dc1ac7f4636b19ccede06bbe54fbe8",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-3-edit-result"
    },
    {
      "content_hash": "sha256:aaa93a0ffdbcca72cca41d6407a437cfe59600f94cf8ab8542d5bce032ee832c",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "user",
      "step_index": 5,
      "uuid": "user-otbox-ctx-multi-turn-bash-result"
    }
  ],
  "schema_version": "opentraces.context_reads.v1",
  "to_step": 9,
  "trace_id": "c70afe5d-50ef-483d-920d-922c05ec9aa8"
}
```

PASS: returncode, stdout_json, stdout_json, stdout_json, stdout_json, stdout_json

## 4. opentraces ctx writes c70afe5d-50ef-483d-920d-922c05ec9aa8 --from-step 5 --to-step 9 --with-trail-anchor --json

```
{
  "from_step": 5,
  "schema_version": "opentraces.context_writes.v1",
  "to_step": 9,
  "trace_id": "c70afe5d-50ef-483d-920d-922c05ec9aa8",
  "writes": [
    {
      "content_hash": "sha256:6d636a3b397ce6d2272dfa76fd6898b9b0464db699c2a56c5265a0d6419d0851",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-1-read"
    },
    {
      "content_hash": "sha256:8193b273faa5cf7be260d53c229ac5f65a829a2f1d37b536b300be85b80391da",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-1-edit"
    },
    {
      "content_hash": "sha256:98954bd6832b0d2358b05620f08b81eba93a827afb04e14a1e3e3be7d5210994",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-1-close"
    },
    {
      "content_hash": "sha256:956df0e6f6cbf69b80a6886537046a5f15a1ad0ba760cc391b645ea8dd4994df",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-2-read"
    },
    {
      "content_hash": "sha256:311b5502dad1dc4f8a63a7333afde52c81c242660431ed7ec29d7efc431535c6",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-2-edit"
    },
    {
      "content_hash": "sha256:b19945e400a0181cb2550e952bcff364bd1061aa9cb1edecfb4ef90037ff6628",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-2-close"
    },
    {
      "content_hash": "sha256:c98c92effd1ee5bd3a9f85f770526c650e2209ab75c1e9371ac9c61c3853fa99",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-3-read"
    },
    {
      "content_hash": "sha256:67ee1e35126802bf7432e54cdb79cb79b6b46aeb8486cc6a5c2daea04d6cc62c",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-3-edit"
    },
    {
      "content_hash": "sha256:68ac34d3980f291bf66bdd18cd31c27845e305c5c5767189e097ec2a78227763",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-3-close"
    },
    {
      "content_hash": "sha256:8d3be403d99806fb99a68b3a0cda0c6fd716691e20c9bb68263b7b0ec10eafbf",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint": null,
      "transcript_offset": 6494,
      "uuid": "asst-otbox-ctx-multi-turn-bash"
    },
    {
      "content_hash": "sha256:ba49adf32aec62c8a25f54ca6ce09b0f965412248b90c310771cc3ceff52481b",
      "node_id": "sha256:c7f9a3ad87199fb3d5dd9801d50125ee896534b9435fc79383a7d71004f09d90",
      "role": "assistant",
      "step_index": 5,
      "trail_anchor_hint
... (truncated)
```

PASS: returncode, stdout_json, stdout_json, stdout_json, stdout_json, stdout_contains

## 5. opentraces ctx prune sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 --source-jsonl <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl --to-session rewind-step7 --json

```
{
  "active_path_length": 8,
  "jsonl_path": <box>/home/.claude/projects/<box-encoded-project>/rewind-step7.jsonl",
  "new_session_id": "rewind-step7",
  "node_id": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
  "record_count": 8,
  "schema_version": "opentraces.context_resume.v1",
  "source_jsonl": <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl",
  "uuid_set": [
    "user-otbox-ctx-multi-turn-1-prompt",
    "asst-otbox-ctx-multi-turn-1-read",
    "user-otbox-ctx-multi-turn-1-read-result",
    "asst-otbox-ctx-multi-turn-1-edit",
    "user-otbox-ctx-multi-turn-1-edit-result",
    "asst-otbox-ctx-multi-turn-1-close",
    "user-otbox-ctx-multi-turn-2-prompt",
    "asst-otbox-ctx-multi-turn-2-read"
  ],
  "uuid_set_sorted": [
    "asst-otbox-ctx-multi-turn-1-close",
    "asst-otbox-ctx-multi-turn-1-edit",
    "asst-otbox-ctx-multi-turn-1-read",
    "asst-otbox-ctx-multi-turn-2-read",
    "user-otbox-ctx-multi-turn-1-edit-result",
    "user-otbox-ctx-multi-turn-1-prompt",
    "user-otbox-ctx-multi-turn-1-read-result",
    "user-otbox-ctx-multi-turn-2-prompt"
  ],
  "wrote": false
}
```

PASS: returncode, stdout_json, stdout_json, stdout_json

## 6. opentraces ctx prune sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 --source-jsonl <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl --to-session rewind-step7 --write --json

```
{
  "active_path_length": 8,
  "jsonl_path": <box>/home/.claude/projects/<box-encoded-project>/rewind-step7.jsonl",
  "new_session_id": "rewind-step7",
  "node_id": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
  "record_count": 8,
  "schema_version": "opentraces.context_resume.v1",
  "source_jsonl": <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl",
  "uuid_set": [
    "user-otbox-ctx-multi-turn-1-prompt",
    "asst-otbox-ctx-multi-turn-1-read",
    "user-otbox-ctx-multi-turn-1-read-result",
    "asst-otbox-ctx-multi-turn-1-edit",
    "user-otbox-ctx-multi-turn-1-edit-result",
    "asst-otbox-ctx-multi-turn-1-close",
    "user-otbox-ctx-multi-turn-2-prompt",
    "asst-otbox-ctx-multi-turn-2-read"
  ],
  "uuid_set_sorted": [
    "asst-otbox-ctx-multi-turn-1-close",
    "asst-otbox-ctx-multi-turn-1-edit",
    "asst-otbox-ctx-multi-turn-1-read",
    "asst-otbox-ctx-multi-turn-2-read",
    "user-otbox-ctx-multi-turn-1-edit-result",
    "user-otbox-ctx-multi-turn-1-prompt",
    "user-otbox-ctx-multi-turn-1-read-result",
    "user-otbox-ctx-multi-turn-2-prompt"
  ],
  "wrote": true
}
```

PASS: returncode, stdout_json, stdout_json, stdout_json, stdout_json_contains, stdout_json_equals_var

## 7. .testvenv/bin/python -c import json, os, sys; p = os.path.join(os.path.dirname(sys.argv[1]), 'rewind-step7.jsonl'); recs = [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]; print(json.dumps({'parse_ok': all(isinstance(r, dict) for r in recs), 'record_count': len(recs), 'uuids': [r.get('uuid') for r in recs]})) <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl

```
{"parse_ok": true, "record_count": 8, "uuids": ["user-otbox-ctx-multi-turn-1-prompt", "asst-otbox-ctx-multi-turn-1-read", "user-otbox-ctx-multi-turn-1-read-result", "asst-otbox-ctx-multi-turn-1-edit", "user-otbox-ctx-multi-turn-1-edit-result", "asst-otbox-ctx-multi-turn-1-close", "user-otbox-ctx-multi-turn-2-prompt", "asst-otbox-ctx-multi-turn-2-read"]}
```

PASS: returncode, stdout_json, stdout_json_equals_var, stdout_json_array_equals

## 8. opentraces trace query --lex helper --limit 1 --json

```
{
  "candidates": [
    {
      "candidate_kind": "trace",
      "commit_sha": "51acf1eb537fec6850c304c86632d43d84593523",
      "commit_subject": null,
      "committed": true,
      "facets": [
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "project_slug",
          "source": "exact_schema",
          "value": "project-27f118e1"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "agent.name",
          "source": "exact_schema",
          "value": "claude-code"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "provider.kind",
          "source": "exact_schema",
          "value": "anthropic"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "model",
          "source": "exact_schema",
          "value": "anthropic/claude-sonnet-4-5"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "outcome.committed",
          "source": "exact_schema",
          "value": true
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "tool.name",
          "source": "exact_schema",
          "value": "Bash"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "tool.name",
          "source": "exact_schema",
          "value": "Edit"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "tool.name",
          "source": "exact_schema",
          "value": "Read"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "git_link_tier",
          "source": "exact_schema",
          "value": "tool_emitted"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "survival.state",
          "source": "exact_schema",
          "value": "unknown"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "file.path",
          "source": "file_path",
          "value": "src/mod_a.py"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "file.kind",
          "source": "file_path",
          "value": "py"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "file.path",
          "source": "file_path",
          "value": "src/mod_b.py"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "file.path",
          "source": "file_path",
          "value": "src/mod_c.py"
        },
        {
          "confidence": "high",
          "evidence_ref": "tu:c70afe5d-50ef-483d-920d-922c05ec9aa8:tool:toolu_otbox_ctx_multi_turn_bash",
          "name": "bash.command_family",
          "source": "bash_command",
          "value": "grep"
        },
        {
          "confidence": "high",
          "evidence_ref": "tu:c70afe5d-50ef-483d-920d-922c05ec9aa8:tool:toolu_otbox_ctx_multi-turn-1_read",
          "name": "file.operation",
          "source": "tool_input",
          "value": "read"
        },
        {
          "confidence": "high",
          "evidence_ref": "tu:c70afe5d-50ef-483d-920d-922c05ec9aa8:tool:toolu_otbox_ctx_multi-turn-1_edit",
          "name": "file.operation",
          "source": "tool_input",
          "value": "edit"
        },
        {
          "confidence": "high",
          "evidence_ref": null,
          "name": "source_layer",
          "source": "exact_schema",
          "value": "canonical"
        }
      ],
      "files": [
        "src/mod_a.py",
        "src/mod_b.py",
        "src/mod_c.py"
      ],
      "headline": "Please add a `alpha()` helper to src/mod_a.py that computes the mean over a list. Commit once it's in.",
      "intent_preview": "Please add a `alpha()` helper to src/mod_a.py that computes the mean over a list. Commit once it's in.",

... (truncated)
```

PASS: returncode, stdout_json

## 9. opentraces ctx diff sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 --layer messages --json

```
{
  "diff": {
    "added": [],
    "changed": [],
    "removed": []
  },
  "node_a": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
  "node_b": "sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477",
  "schema_version": "opentraces.context_tree.v1"
}
```

PASS: returncode, stdout_json, stdout_json_length_equals, stdout_json_length_equals, stdout_json_length_equals

## Summary

| step | id | command | assertions | result |
|------|----|---------|------------|--------|
| 1 | demo-1-tree | opentraces ctx tree c70afe5d-50ef-483d-920d-922c05ec9aa8 --json | 5 | PASS |
| 2 | demo-2-step | opentraces ctx step c70afe5d-50ef-483d-920d-922c05ec9aa8 7 --json | 8 | PASS |
| 3 | demo-3-reads | opentraces ctx reads c70afe5d-50ef-483d-920d-922c05ec9aa8 --from-step 5 --to-step 9 --json | 6 | PASS |
| 4 | demo-4-writes | opentraces ctx writes c70afe5d-50ef-483d-920d-922c05ec9aa8 --from-step 5 --to-step 9 --with-trail-anchor --json | 6 | PASS |
| 5 | demo-5a-prune-dryrun | opentraces ctx prune sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 --source-jsonl <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl --to-session rewind-step7 --json | 4 | PASS |
| 6 | demo-5b-prune-write | opentraces ctx prune sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 --source-jsonl <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl --to-session rewind-step7 --write --json | 6 | PASS |
| 7 | demo-6-verify-pruned | .testvenv/bin/python -c import json, os, sys; p = os.path.join(os.path.dirname(sys.argv[1]), 'rewind-step7.jsonl'); recs = [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]; print(json.dumps({'parse_ok': all(isinstance(r, dict) for r in recs), 'record_count': len(recs), 'uuids': [r.get('uuid') for r in recs]})) <box>/home/.claude/projects/<box-encoded-project>/sess-otbox-context-tree-multi-turn.jsonl | 4 | PASS |
| 8 | demo-7-trace-query | opentraces trace query --lex helper --limit 1 --json | 2 | PASS |
| 9 | demo-8-diff | opentraces ctx diff sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 sha256:d4d47464f7974acae04ea0056bff274bea51a92318ea0b0d124560df711a8477 --layer messages --json | 5 | PASS |
