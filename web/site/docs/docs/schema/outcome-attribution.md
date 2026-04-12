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
    "experimental": false,
    "revision": {
      "vcs_type": "git",
      "revision": "def789abc..."
    },
    "unaccounted_files": ["build/generated.ts"],
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
            "ids": {
              "anthropic": "msg_01xyz"
            },
            "related": [
              {"type": "plan", "url": "opentraces://t/plan_3"}
            ],
            "ranges": [
              {
                "start_line": 42,
                "end_line": 55,
                "content_hash": "murmur3:9f2e8a1b...",
                "change_type": "modification",
                "original": {
                  "start_line": 42,
                  "end_line": 54,
                  "content_hash": "murmur3:abc123..."
                },
                "contributor": {
                  "type": "human",
                  "id": "alice"
                }
              }
            ]
          }
        ]
      }
    ]
  }
}
```

### Attribution fields

| Field | Type | Description |
|-------|------|-------------|
| `experimental` | boolean | `true` when any range is low-confidence or a fallback resolution was used; `false` when every range was produced by the PostToolUse hook or unified diff. |
| `revision` | object | Pins this attribution block to a specific commit/revision. `{vcs_type: "git"|"jj", revision: <sha|change_id>}`. Added 0.3.0 (RFC #5/#25). |
| `unaccounted_files` | array\<string\> | Files changed at commit time whose hunks were not produced by any tracked Edit/Write tool call. Typically Bash-applied edits (`sed`, codemods). Surfaced at low confidence. Added 0.3.0 (RFC #26). |
| `files[]` | array\<AttributionFile\> | Per-file attribution, each with a list of `conversations`. |

### AttributionConversation fields

| Field | Type | Description |
|-------|------|-------------|
| `contributor` | object | Default contributor for all ranges under this conversation, e.g. `{type: "ai", model_id: "anthropic/claude-sonnet-4-20250514"}`. |
| `url` | string | `opentraces://trace_id/step_N` link back to the producing step. |
| `ids` | object | Provider-native conversation identifiers. E.g. `{anthropic: "msg_01xyz", openai: ["resp_1", "resp_2"]}`. Added 0.3.0 (RFC #9). |
| `related` | array\<object\> | Links to broader resources using the RFC #16 baseline vocabulary. Each entry: `{type, url}`. E.g. `{type: "plan", url: "opentraces://t/plan_3"}`. Added 0.3.0. |
| `ranges[]` | array\<AttributionRange\> | Attributed line ranges. |

### AttributionRange fields

| Field | Type | Description |
|-------|------|-------------|
| `start_line`, `end_line` | int | Inclusive range in the final file. |
| `content_hash` | string | `murmur3:<32-hex>` hash for cross-refactor tracking. |
| `confidence` | `"high"` \| `"medium"` \| `"low"` | Resolver confidence. |
| `change_type` | `"addition"` \| `"modification"` \| `"deletion"` | Nature of the change. Added 0.3.0 (RFC #11). |
| `original` | object | Pre-processing state for divergent ranges — set when a formatter or human rewrote the agent's output after the fact. Keys: `start_line`, `end_line`, `content_hash`. Added 0.3.0 (RFC #5). |
| `contributor` | object | Per-range override of the enclosing conversation's contributor. Added 0.3.0. |

### How Attribution Is Constructed

Attribution is built deterministically by a three-layer pipeline (plan 041):

1. **PostToolUse hook** — fires after each Edit/Write, reads the file from disk, and records the exact post-edit lines plus a `murmur3:` hash. Highest confidence.
2. **Unified diff** — when no hook event is present, the session's diff is parsed to recover ranges. Medium confidence.
3. **`str.find` fallback** — last-resort textual match of tool output back to the file. Low confidence, always marked `experimental: true`.

These feed a common resolver that emits Agent Trace-compatible `attribution` records and, where possible, pins them to a specific commit via `attribution.revision` and the trace's `git_links`.

## GitLink and the Evidence Tiers

A `GitLink` (entries in `TraceRecord.git_links`) records one commit this trace contributed to, annotated with how strong the evidence is.

```json
{
  "git_links": [
    {
      "vcs_type": "git",
      "revision": "def789abc...",
      "repo_url": "https://github.com/org/repo",
      "branch": "main",
      "tier": "tool_emitted",
      "commit_reachable": true,
      "content_alive": true
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `vcs_type` | `"git"` \| `"jj"` | Version control system. Defaults to `"git"`. |
| `revision` | string | Commit SHA or jj change id. |
| `repo_url` | string | Canonical remote URL. |
| `branch` | string | Branch name if known. |
| `tier` | enum | Evidence tier — see below. |
| `commit_reachable` | bool | Computed lazily on read; `false` if the commit was force-pushed away. |
| `content_alive` | bool | Computed lazily on read; `false` if the agent's hashed bytes no longer appear at `HEAD`. |

### Evidence tiers

Consumers filter by tier to build training subsets of the desired signal quality. The four tiers, strongest to weakest:

| Tier | Meaning |
|------|---------|
| `tool_emitted` | Hashes emitted by Edit/Write tool calls appear verbatim in the commit's staged hunks. Gold-standard signal — use for SFT and RL. |
| `tool_emitted_with_divergence` | The files line up, but the committed bytes don't hash-match (a formatter, pre-commit hook, or human rewrote the output). Still high value when paired with `AttributionRange.original`. |
| `overlapping` | Only file-set and time-window overlap — no hash match. Safer to treat as weakly linked. |
| `orphan` | No viable commit link. Keep the trace, don't claim authorship. |

## Lifecycle: Provisional vs Final

`TraceRecord.lifecycle` gates when a trace is safe to treat as revision-anchored:

- `"provisional"` — captured at session end. `git_links` may be empty or speculative.
- `"final"` — the `opentraces setup git` post-commit hook has correlated this trace to at least one commit and pinned `attribution.revision`. Promoted exactly once; never downgraded.

Dataset consumers that want only revision-anchored traces should filter on `lifecycle == "final"` and then on `git_links[].tier`.

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
