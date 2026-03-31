# Schema Rationale: 0.2.0

**Date:** 2026-03-31
**Status:** Shipped

## What Changed

Three new fields on `Outcome`, one new field on `TraceRecord`. All nullable, backward compatible.

```
TraceRecord.execution_context: "devtime" | "runtime" | None
Outcome.terminal_state: "goal_reached" | "interrupted" | "error" | "abandoned" | None
Outcome.reward: float | None
Outcome.reward_source: str | None
```

## Why

The 0.1.x schema was designed around devtime agents: code-editing agents that produce git artifacts. The core outcome signal was `committed=True`, grounded in git history.

Community traces from datasets like `kai-os/carnice-glm5-hermes-traces` represent a different agent type: runtime agents that execute action trajectories against live environments (file systems, web browsers, code interpreters). These agents do not produce git commits. Their outcome signal is action trajectory completion, not VCS artifacts.

Trying to evaluate runtime traces against devtime quality checks produced two failure modes:

**False penalties.** A runtime trace with no `committed=True` scored low on RL1 (grounded outcome signal) even when it had a clear terminal state. The check was looking for the wrong thing.

**False passes.** A check that found "no VCS data" returned `score=1.0` as N/A, inflating scores for traces that were missing something the devtime checks genuinely required.

The 0.2.0 schema bump gives the quality engine a session-level discriminator (`execution_context`) to branch on, and adds the runtime analog of `committed` (`terminal_state`) plus RL-native signals (`reward`, `reward_source`).

## Design Decisions

### `execution_context` on TraceRecord, not inferred

We considered inferring context from step content (e.g., checking whether `committed=True` or `terminal_state` is set). This would have worked for the current parsers but created a fragile ordering dependency: the field you're trying to infer from might not be set yet when the check runs.

Explicit declaration is clearer. Parsers set `execution_context` at parse time. Quality checks branch on it directly.

### `terminal_state` as a closed enum

The four values cover the meaningful outcomes of an action trajectory:
- `goal_reached` — task completed as intended
- `interrupted` — external stop (user, timeout, resource limit)
- `error` — agent hit an unrecoverable failure
- `abandoned` — agent gave up (policy decision, not external stop)

We chose a closed enum over a free string to make quality checks tractable. A free string would require pattern matching across source-specific vocabulary.

### `reward` and `reward_source`

Some runtime agent datasets include a numeric score from an RL environment or judge. This is the strongest possible outcome signal for RL/RLHF consumers, stronger than `terminal_state` alone. Including it as a first-class field lets RL1 and RL2 quality checks give it appropriate credit, and lets downstream training pipelines filter by it without parsing free text.

`reward_source` is a free string with canonical values documented in the schema: `rl_environment`, `judge`, `human_annotation`, `orchestrator`. Free string because new reward sources will emerge.

### Backward compatibility

All new fields default to `None`. A 0.1.x `TraceRecord` deserialized under 0.2.0 will have `execution_context=None`, `terminal_state=None`, etc. Quality checks treat `execution_context=None` as devtime (the default before this field existed), so existing devtime traces score identically.

## Quality Engine Changes (not schema, but context)

The schema bump was paired with quality engine changes:

**`skipped` flag on `CheckResult`.** A check can now return `skipped=True` to signal "this check is structurally N/A for this trace type" vs `score=0.0` which means "this trace failed a real requirement". The engine excludes skipped checks from the weighted average.

This matters because the engine previously had no way to distinguish:
- "Runtime trace, no per-step timestamps available from source format" (should skip)
- "Devtime trace, timestamps were not captured" (should penalize)

Both returned `score=0.0`, but only the second is a quality gap. The skip mechanism fixes this.

**Affected checks:**
- A1 (cache hit rate): skip for runtime
- A3 (total duration): skip for runtime
- A4 (step timestamps): skip for conversation-turn fidelity
- A5 (token breakdown per step): skip for conversation-turn fidelity
- A8 (warmup distinction): skip for conversation-turn fidelity
- C11 (call_type populated): skip for conversation-turn fidelity
- C14 (snippets): skip for runtime with no code-writing tools
- C19 (cache hit rate): skip for runtime
- C21 (duration): skip for runtime
- D1 (language ecosystem): skip for runtime with no code-writing tools
- D4 (VCS info): skip for runtime
- D5 (snippets with language tags): skip when no snippets
- T10 (warmup steps labeled): skip for conversation-turn fidelity

**RL1 loophole closed.** The old RL1 check had a branch: `if signal_source != "deterministic"` gave 0.8 credit. This meant any runtime trace that hadn't explicitly failed got near-full credit for the most important RL check. Removed. RL1 now requires an actual grounded signal: `committed=True` (devtime), `terminal_state` set (runtime), or `reward` set (runtime).

**D7 version relaxed for runtime.** Agent version is typically available from Claude Code session metadata but not from third-party dataset imports. Runtime traces with a valid agent name pass D7 at 0.7 rather than failing for missing version.

## Migration

No migration required for existing devtime traces. The new fields are all `None` by default. Devtime parsers can optionally set `execution_context="devtime"` for clarity.

Runtime parsers must set `execution_context="runtime"` and should populate `terminal_state` from source completion metadata.
