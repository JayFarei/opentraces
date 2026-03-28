# Schema Gap Analysis: 26/73 Fields

Investigated by 3 parallel agents across real session data from 19 projects.

## Verdict Summary

| Category | Count | Action |
|----------|-------|--------|
| Fixable bugs (parser key names, missing wiring) | 3 | FIX (already fixed this session) |
| Reclassify (wrong expected_when, data not in source) | 6 | UPDATE schema_audit.py |
| Fixable in enrichment (needs project dir wiring in eval) | 3 | FIX in engine.py |
| Truly session-dependent (correctly classified) | 10 | NO ACTION |
| Audit methodology gap (works in capture, not in eval) | 3 | DOCUMENT |
| Not yet implemented (data available at enrichment) | 1 | FIX (task.repository) |

## Already Fixed This Session

| Field | Was | Now | Fix |
|-------|-----|-----|-----|
| `duration_ms` | 0% | 6% | Key name: `"duration"` -> `"durationMs"` |
| `observations[].error` | 0% | 10% | Now captures `is_error` from tool_result blocks |
| `outcome.success` | 0% | 29% | Inferred from `committed=True` |

## Should Reclassify (6 fields)

These are not broken. The `expected_when` or source is wrong in schema_audit.py.

| Field | Rate | Current expected_when | Should Be | Reason |
|-------|------|----------------------|-----------|--------|
| `system_prompts` | 0% | always | optional | Claude Code does not persist API request (system prompt) to session JSONL. Only API response is stored. |
| `tool_definitions` | 0% | always | optional | Same. Tool schemas are in the API request, not logged. |
| `steps[].content` | 41% | always | session_dependent | Agent frequently responds with only tool_use blocks, no text. Normal LLM behavior. |
| `steps[].model` | 78% | always | agent steps only | User steps don't have a model (correct). 100% of agent steps have it. |
| `environment.shell` | 0% | always | optional | Not in session data at all. Could infer from $SHELL at enrichment time. |
| `task.source` | 0% | always | optional | Already set to "user_prompt" but shows 0% because... (needs investigation of why the parser sets it but audit doesn't see it) |

## Should Fix in Eval Engine (3 fields)

These work in the CLI capture path but the multi-project eval skips them.

| Field | Rate | Fix | Effort |
|-------|------|-----|--------|
| `dependencies` | 52% | Wire `extract_dependencies(project.path)` in engine.py (reads manifest files) | 5 lines |
| `language_ecosystem` | 60% | Add manifest-based ecosystem inference alongside step-based | 20 lines |
| `task.repository` | 0% | Add `git remote get-url origin` in enrichment, parse to owner/repo | 15 lines |

## Should Fix in Parser/Enrichment (1 field)

| Field | Rate | Fix | Effort |
|-------|------|-----|--------|
| `task.base_commit` | 0% | Wire existing `vcs.base_commit` to `task.base_commit` in CLI enrichment | 1 line |

## Audit Methodology Gaps (3 fields)

These work in real capture (`opentraces push`) but show 0% in the eval because the eval lacks the project directory.

| Field | Rate in Eval | Rate in Capture | Reason |
|-------|-------------|----------------|--------|
| `vcs.base_commit` | 0% | ~100% (git repos) | `detect_vcs()` needs project dir |
| `vcs.diff` | 0% | ~100% (git repos) | Same |
| `outcome.patch` | 0% | ~60% (committed sessions) | `check_committed()` needs project dir |

## Correctly Session-Dependent (10 fields, no action)

| Field | Rate | Why |
|-------|------|-----|
| `system_prompt_hash` | 0% | Cascades from empty system_prompts (source unavailable) |
| `subagent_trajectory_ref` | 0% | Only when sub-agent sessions exist |
| `parent_step` | 12% | Only for sub-agent steps |
| `reasoning_content` | 16% | Opus encrypted thinking, genuinely unavailable |
| `outcome.description` | 29% | Only sessions with git commits |
| `outcome.commit_sha` | 29% | Same |
| `tools_available` | 44% | Only steps with tool calls (semantic note: shows tools *used* not *available*) |
| `attribution` | 73% | Only sessions with Edit/Write calls |
| `attribution.files` | 73% | Same |
| `agent_role` | 78% | Set on all agent steps, None on user steps (correct) |
