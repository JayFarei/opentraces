# Outcome & Attribution

## Outcome

The `outcome` object captures whether the session succeeded, providing training signals:

```json
{
  "outcome": {
    "success": true,
    "signal_source": "user_annotation",
    "description": "Test passes after fix",
    "patch": "unified diff string",
    "committed": true,
    "commit_sha": "def789abc..."
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `success` | boolean | no | Did the task succeed? |
| `signal_source` | string | no | `"user_annotation"`, `"ci_result"`, or `"heuristic"` |
| `description` | string | no | Human-readable outcome description |
| `patch` | string | no | Unified diff produced by the session |
| `committed` | boolean | no | Whether changes were committed to git |
| `commit_sha` | string | no | The specific commit, if committed |

### committed as a Quality Signal

A session that results in a commit is higher-signal than one abandoned or reverted. This is a cheap, deterministic quality signal derived from git history without user annotation. When `true`, `commit_sha` links to the specific commit for cross-referencing with `git diff` and CI results.

## Attribution

The `attribution` block embeds Agent Trace-compatible attribution recording which files and line ranges were produced by the agent session.

```json
{
  "attribution": {
    "version": "0.1.0",
    "files": [
      {
        "path": "src/parser.ts",
        "conversations": [
          {
            "contributor": {
              "type": "ai",
              "model_id": "anthropic/claude-sonnet-4-20250514"
            },
            "url": "opentraces://trace_id/step_2",
            "ranges": [
              {
                "start_line": 42,
                "end_line": 55,
                "content_hash": "murmur3:9f2e8a1b"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

### How Attribution is Constructed

Attribution is built deterministically from trace data, no user annotation needed:

1. **Edit tool calls** provide `file_path` and line ranges
2. **outcome.patch** provides the unified diff
3. **snippets** provide extracted code blocks with file positions

These are synthesized into Agent Trace `attribution` records.

### The Bridge

This field bridges trajectory (process) and attribution (output), making opentraces the ADP + Agent Trace unified format:

- `conversation.url` uses `opentraces://trace_id/step_N` to link each attributed range to the step that produced it
- `content_hash` (murmur3, matching Agent Trace convention) enables tracking attribution across refactors and file moves
- Sessions that produce no code changes have `attribution: null`

### Why Embed, Not Link

URLs may not persist in a crowdsourced dataset. Embedding creates a self-contained record. An Agent Trace record says "lines 42-55 were AI-generated." An opentraces record says "here is the full conversation that produced lines 42-55, including the reasoning, failed attempts, tool calls, and final edit."

## Reserved RL Fields

Not in v0.1, but the schema reserves space for:

- `token_usage.completion_token_ids` - Token ID sequences for RL training without retokenization drift
- `token_usage.logprobs` - Log probabilities per token for PPO/DPO-style training
- Step-level reward annotations for process reward models
