# Outcome & Attribution

## Outcome

The `outcome` object captures the session-level result and the confidence of the signal that set it:

Outcome fields are split by `execution_context`. Devtime agents (code-editing) use `committed`
as the primary reward proxy. Runtime agents (action-trajectory / RL) use `terminal_state` and `reward`.

**Devtime example:**

```json
{
  "outcome": {
    "success": true,
    "signal_source": "deterministic",
    "signal_confidence": "derived",
    "description": "Test passes after fix",
    "patch": "unified diff string",
    "committed": true,
    "commit_sha": "def789abc..."
  }
}
```

**Runtime example:**

```json
{
  "outcome": {
    "terminal_state": "goal_reached",
    "reward": 1.0,
    "reward_source": "rl_environment",
    "signal_confidence": "derived"
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `success` | boolean | no | Did the task succeed? |
| `signal_source` | string | no | Current implementation uses `deterministic` |
| `signal_confidence` | string | no | `derived`, `inferred`, or `annotated` |
| `description` | string | no | Human-readable outcome description |
| `patch` | string | no | Unified diff produced by the session |
| `committed` | boolean | no | Whether changes were committed to git (devtime) |
| `commit_sha` | string | no | The specific commit, if committed (devtime) |
| `terminal_state` | string | no | `goal_reached`, `interrupted`, `error`, or `abandoned` (runtime, added 0.2.0) |
| `reward` | float | no | Numeric reward signal from an RL environment or evaluator (runtime, added 0.2.0) |
| `reward_source` | string | no | Canonical: `rl_environment`, `judge`, `human_annotation`, `orchestrator` (added 0.2.0) |

### Committed as a Quality Signal

For devtime agents, a session that results in a commit is higher-signal than one abandoned or reverted. The commit hash gives a deterministic anchor for replaying the patch and comparing later revisions.

For runtime agents, `terminal_state` and `reward` serve the equivalent role — ground truth from the environment.

## Attribution

The `attribution` block records which files and line ranges were produced by the agent session.

```json
{
  "attribution": {
    "files": [
      {
        "path": "src/parser.ts",
        "conversations": [
          {
            "contributor": {
              "type": "ai",
              "model_id": "anthropic/claude-sonnet-4-20250514"
            },
            "url": "opentraces://trace/step_2",
            "ranges": [
              {
                "start_line": 42,
                "end_line": 55,
                "content_hash": "9f2e8a1b"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

### How Attribution Is Constructed

Attribution is built deterministically from trace data:

1. Edit and Write tool calls provide file paths and line ranges
2. `outcome.patch` provides the unified diff for cross-checking
3. Snippets provide extracted code blocks with file positions

These are synthesized into Agent Trace-compatible `attribution` records.

### The Bridge

This field bridges trajectory (process) and attribution (output):

- `conversation.url` links each attributed range back to the step that produced it
- `content_hash` is a short stable hash for tracking attribution across refactors
- Sessions that produce no code changes have `attribution: null`

### Why Embed, Not Link

Embedding keeps the record self-contained. An opentraces record can say "here is the full conversation that produced these lines, including the reasoning, tool calls, and final diff."

## Reserved RL Fields

The schema leaves room for:

- token ID sequences for RL training
- token log probabilities
- step-level reward annotations
