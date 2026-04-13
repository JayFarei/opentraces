# Mission brief — trace attribution test harness

**Read this first if you're a loop agent picking up this work without prior context.**

---

## What you're working on

`opentraces` is building **trace → commit attribution** for AI coding agents.
Given a Claude Code session (a "trace") and a git commit, we want to answer:
*which lines of this commit were authored by which trace?*

The hard cases are the realistic ones:
- Trace A writes 20 lines, trace B tweaks 2 of them → 18 lines belong to A, 2 to B.
- Agent uses Bash to create 30 files → all 30 attribute to that agent.
- A user manually edits a file between agent sessions → those lines belong to "external", not to either trace.
- A commit bundles work from multiple traces → blame must decompose per-line, per-source.

**Why this matters**: agent-driven development needs accountability. Sessions and
commits live on different timelines. Without attribution, you can't answer
"did this session's work land?" or "which sessions contributed to this commit?"
That's the product thesis.

---

## What's been built

Three Python files in `scripts/`:

1. **`trace_attribution_spike_v2.py`** — the actual attribution system being tested.
   - Reads Claude's `~/.claude/file-history/<sid>/` blobs and `~/.claude/projects/*.jsonl` transcripts
   - Constructs a **synthetic git audit history** under `refs/opentraces/audit/<project_id>` where each prompt-boundary snapshot becomes a synthetic commit, authored by the trace
   - Runs `git blame --line-porcelain` against the audit history to compute per-line attribution
   - Has a **watcher** mode that polls the working tree to capture Bash-mediated changes that file-history misses
   - Subcommands: `build`, `watch`, `attribute`, `show-ref`

2. **`attribution_v2_harness.py`** — automated test runner that drives real
   `claude` REPLs in tmux and validates attribution outcomes against expectations.
   Loads scenarios from declarative TOML files in `scenarios/`.

3. **`mine_claude_projects.py`** — analytics tool that surveys real-world
   session shapes from `~/.claude/projects/`. Used to prioritize scenarios
   based on how often each pattern actually occurs.

---

## What "good" looks like (your goal)

For each scenario in `scripts/scenarios/*.toml`, the harness runs it
end-to-end and reports pass/fail. The goal:

1. **All scenarios pass.** When the spike is correct, every assertion in
   every TOML evaluates true.
2. **When a scenario fails**, identify whether the bug is in:
   - The **spike's attribution logic** (the most important class of bug)
   - The **harness** (test machinery itself)
   - The **scenario** (incorrectly-specified expectation)
   Then fix the right one.
3. **When a real-world edge case isn't covered**, author a new scenario
   that exercises it, then make it pass. Each fix earns the harness more
   coverage. Aim for 1-2 new scenarios per ~10 existing ones.

---

## What you should DO

When the harness runs and reports failures:

1. **Read the failing scenario's TOML.** Understand what it's trying to validate.
2. **Re-run with `-v --keep <scenario_name>`** to see verbose tmux output and keep the project dir + tmux sessions for inspection.
3. **Inspect the audit history**:
   ```
   python3 scripts/trace_attribution_spike_v2.py show-ref /tmp/ot-h-<name>
   git -C /tmp/ot-h-<name> log refs/opentraces/audit/<id>
   git -C /tmp/ot-h-<name> blame --line-porcelain refs/opentraces/audit/<id> -- <file>
   ```
4. **Inspect the JSONL** at `~/.claude/projects/<encoded_path>/<session_id>.jsonl` to see what the agent actually did.
5. **Inspect the file-history blobs** at `~/.claude/file-history/<session_id>/` to see what content the snapshots captured.
6. **Form a hypothesis** about whether the bug is spike, harness, or scenario.
7. **Make a focused fix** (small diff, addresses the specific failure).
8. **Re-run the scenario** to confirm.
9. **Run the full suite** to confirm no regression.
10. **Document the fix** in a commit message that explains what was wrong and why.

When the harness runs and everything passes:

1. **Pick the next class of edge case** not yet covered (use the taxonomy in CLAUDE.md or imagine new ones).
2. **Write a new TOML scenario** under `scripts/scenarios/`.
3. **Run it**: it should fail (because it's a new case).
4. **Investigate, fix, iterate** as above.

---

## What you should NOT do

- **Don't change the architecture.** The blame-over-synthetic-history model
  (per the senior engineer's recommendation) is decided. Don't pivot back
  to patch-id matching or invent a new attribution scheme without a clear
  written justification.
- **Don't broaden scope.** This is the spike, not the production CLI.
  Don't refactor into modules under `src/opentraces/` without the human
  giving you a green light. Promotion is a separate decision.
- **Don't disable failing scenarios** to make the suite green. If a scenario
  reveals a real bug, the fix is to the spike, not to the test.
- **Don't add Python dependencies.** stdlib only (TOML via `tomllib`).
- **Don't push commits or modify git remotes** without explicit instruction.
- **Don't run more than ~20 scenario invocations per hour** unless asked.
  Each costs API budget. Be deliberate.
- **Don't change the scenario format** (TOML schema) without updating the
  harness AND every existing scenario AND this MISSION.md. Schema stability
  is more important than schema improvement.

---

## How to run the harness

```bash
# List all scenarios
python3 scripts/attribution_v2_harness.py --list

# Run all
python3 scripts/attribution_v2_harness.py

# Run one by name
python3 scripts/attribution_v2_harness.py small_tweak

# Run several
python3 scripts/attribution_v2_harness.py baseline small_tweak

# Verbose (shows tmux commands, prompts sent, JSONL events)
python3 scripts/attribution_v2_harness.py -v small_tweak

# Keep dirs / tmux sessions on success (for inspection)
python3 scripts/attribution_v2_harness.py --keep small_tweak
```

Project dirs: `/tmp/ot-h-<scenario_name>/`. Cleaned up after passing scenarios
unless `--keep`. Tmux sessions named `otah-<truncated_name>-<label>`.

The spike's audit refs live in each test project's `.git/refs/opentraces/audit/`
and the watcher's working-tree event sidecar is at `.git/opentraces-watcher-events.jsonl`.

Self-tests (no API cost, synthetic only):
```bash
python3 scripts/attribution_v2_selftest.py
```

These should ALWAYS pass. If they don't, the spike has a regression in the
blame-against-synthetic-history mechanism. Fix that before tackling harness scenarios.

---

## Scenario TOML schema

A scenario file has three sections:

```toml
name = "my_scenario"
description = """multi-line, plain English"""

[[steps]]              # ordered list, mix step types freely
type = "<step_type>"
# ... step-specific params

[[assertions]]         # ordered list, run after all steps
file = "<path>"
# ... assertion-specific keys
```

### Step types

| Step | Required params | Optional params |
|---|---|---|
| `reset` | (none) | `initial_readme = "..."` |
| `claude` | `prompts = [...]` | `label = "a"`, `quiesce = 5`, `timeout = 240`, `cleanup = "exit"` or `"kill"` |
| `commit` | `message = "..."` | `files = [...]`, `allow_empty = true` |
| `write_file` | `path = "..."`, `content = "..."` | |
| `delete_file` | `path = "..."` | |
| `shell` | `command = "..."` | |
| `git` | `args = [...]` | |
| `wait` | `seconds = 2` | |
| `start_watcher` | (none) | |
| `stop_watcher` | (none) | |

### Assertion keys

Every assertion needs `file = "..."`, plus ONE of:

- `trace = "<label>", lines = N` — exact line count for that trace's contribution to that file
- `trace = "<label>", lines = N, tolerance = T` — within ±T
- `trace = "<label>", lines_min = N` — at least N lines
- `trace = "<label>", lines_max = N` — at most N lines
- `total_lines = N` — total attributed lines in the file
- `status = "missing_from_audit"` — file doesn't appear in attribution

Special trace label: `trace = "pre-audit"` matches any line attributed to a
non-trace source (init commit, etc.).

### Trace labels

The `label` you give a `claude` step becomes a string handle for the actual
trace_id assigned by Claude at runtime. Use the same label in assertions
to reference that trace.

---

## Common failure modes and how to fix

### 1. "claude welcome never appeared in 30s"
- The shell alias `c` may not be loaded in tmux's spawned shell. Verify with `zsh -ic 'alias c'`.
- Bumped: try `STARTUP_TIMEOUT = 60` in `attribution_v2_harness.py` if your machine is slow.
- Check `tmux capture-pane -t otah-<name>-<label> -p` to see what's actually on screen.

### 2. "prompt didn't complete in 240s"
- Claude may be waiting for a tool-permission prompt that auto-accept didn't catch. Check the pane.
- Or claude crashed silently. Check the pane for error output.
- Or quiesce never triggered: try `quiesce = 10` on that prompt.

### 3. "jsonl never appeared after first prompt"
- The encoded project dir name may differ from what we compute. Check `~/.claude/projects/` for any new directory.
- Path encoding rule: every non-alphanumeric, non-hyphen char → `-`.

### 4. "audit ref not found"
- The session produced zero file-history snapshots AND no working-tree events.
- Either: the spike needs to gracefully attribute everything to pre-audit when no audit exists (a fix in `trace_attribution_spike_v2.py:attribute_commit`).
- Or: the scenario needs a `start_watcher` step so SOME audit content gets captured.

### 5. Assertion fails with wrong line count
- Check the actual `by_trace` breakdown in the error message.
- If a trace got fewer lines than expected: the agent may have written different content than instructed. Re-run with `-v --keep` and inspect the JSONL to see what the agent actually did. Tighten the prompt for determinism.
- If lines went to "pre-audit" instead of the trace: the file-history may not have captured the edit (the agent used Bash redirection without the watcher running, etc.). Confirm the scenario starts the watcher when needed.

### 6. "nothing to commit, working tree clean"
- The scenario's claude step didn't actually produce any working-tree change (agent may have refused / done nothing).
- Or the change was intentionally net-zero (create + delete). Add `allow_empty = true` to the commit step if that's the design.

### 7. Scenario passes locally but fails on rerun
- Stale state in `~/.claude/projects/` or `~/.claude/file-history/`. The harness's `reset` only cleans the project dir's JSONLs, not the file-history blobs. If a previous failed run left blobs, the build may include stale data.
- Workaround: manually `rm -rf ~/.claude/file-history/<old_trace_id>` between runs.

### 8. Path-encoding mismatch (rare)
- Claude Code's encoding rule: `/`, `_`, `.` → `-`. If you see "JSONL never appeared" but the JSONL DOES exist under a slightly different name, the encoding rule has changed. Update `_enc()` in both files.

---

## What's in scope vs out of scope

**In scope** for the loop agent:
- Fixing bugs in `trace_attribution_spike_v2.py` (attribution correctness)
- Fixing bugs in `attribution_v2_harness.py` (test machinery)
- Writing new TOML scenarios to expand coverage
- Updating MISSION.md when patterns or pitfalls change
- Improving error messages and diagnostic output

**Out of scope** without explicit human approval:
- Promoting the spike code into `src/opentraces/` modules
- Adding Python dependencies
- Changing the synthetic-audit-history architecture
- Modifying the schema in `packages/opentraces-schema/`
- Changing git ref namespaces (`refs/opentraces/audit/*`)
- Pushing commits / modifying remotes
- Running >20 scenario invocations/hour without budget approval

---

## Reporting

After a run, the harness prints a summary like:

```
→ small_tweak (...)
  ✓ config/server.py/a: 18; config/server.py/b: 2  [48.7s]

5/5 passed
```

If you've worked through a session of fixes, leave a one-line commit per
fix with the format:
```
spike: <one line of what you changed and why>
  Fixes: scenario `<name>`
```

Or for harness fixes:
```
harness: <change>
  Affects: scenarios <names>
```

Or for new scenarios:
```
scenario: <name>
  Tests: <one-line description of what edge case>
```

Keep commits small and focused. One bug per commit if possible. The audit
trail matters for understanding the evolution.

---

## When to ask the human

- You've made 3+ failed attempts to fix the same scenario. Stop and ask.
- You think the spike's architecture itself is wrong (vs. a bug in implementation). Stop and ask.
- A scenario reveals an entirely new failure mode you haven't seen documented. Document your findings, stop, and ask before authoring a fix.
- You want to add a Python dependency. Ask first.
- You want to change the TOML schema. Ask first.
- You want to promote anything into `src/opentraces/`. Ask first.

When in doubt, write down what you'd do and why, and ask before doing it.
