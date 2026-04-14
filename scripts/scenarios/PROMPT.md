/loop 30min

Loop iteration for opentraces trace-attribution test harness.

═══ ORIENT (do this every iteration) ═══

1. Read scripts/scenarios/MISSION.md — mission, architecture, scope
2. Read scripts/scenarios/STATE.md — current progress and what to focus on
3. Run the synthetic self-tests (free, no API):
   python3 scripts/attribution_v2_selftest.py
   These MUST pass. If they don't, the spike has regressed — that's
   priority 1 for this iteration.
4. Skim recent git log to see what previous iterations attempted:
   git log --oneline -20 scripts/

═══ DECIDE (one focused thing per iteration) ═══

Based on STATE.md, pick exactly ONE:
a) If status table has any "?" entries: run the full suite to populate
them. Update STATE.md with results.
b) If a scenario is "fail": investigate it, form a hypothesis, apply
the smallest possible fix to the right layer (spike / harness /
scenario), re-run that scenario, then full suite to check regression.
c) If everything passes: pick an unwritten edge case (see MISSION.md
taxonomy or invent one); author a new TOML scenario that fails first
time, then fix.
d) If you've made 3+ failed attempts on the same thing: STOP. Document
what you tried under "Open questions for human" in STATE.md. Move
on or end this iteration.

Don't try to do more than one substantive thing per iteration. Depth > breadth.

═══ HOW TO RUN ═══

# One scenario, verbose, keep state for inspection

python3 scripts/attribution_v2_harness.py -v --keep <name>

# Full suite (~15-20 min, ~$2-5 API)

python3 scripts/attribution_v2_harness.py

# List available

python3 scripts/attribution_v2_harness.py --list

═══ HOW TO RECOGNIZE FAILURE ═══

The harness reports per-scenario: ✓ (pass) or ✗ (fail) with exit code.
Failure modes:

- "AssertionError" — wrong line count or wrong status. Check the
  breakdown in the message: actual {trace_id: count} vs expected.
- "TimeoutError" — claude didn't start, or prompt didn't finish in
  240s. Inspect the tmux pane: tmux capture-pane -t otah-<name>-<label> -p
- "RuntimeError" — git command or shell command failed. Read stderr
  in the message.
- "audit ref not found" — no audit history exists; either the spike
  needs graceful fallback (recommended fix) or the scenario needs
  start_watcher.

After a failure, inspect:
python3 scripts/trace_attribution_spike_v2.py show-ref /tmp/ot-h-<name>
git -C /tmp/ot-h-<name> log refs/opentraces/audit/<id>
git -C /tmp/ot-h-<name> blame --line-porcelain refs/opentraces/audit/<id> -- <file>
ls ~/.claude/file-history/<session_id>/
cat ~/.claude/projects/-private-tmp-ot-h-<name>/<session_id>.jsonl | head -50

═══ WHAT YOU CAN CHANGE ═══

Allowed (small, focused commits, one fix per commit):
✅ scripts/trace_attribution_spike_v2.py (attribution bugs)
✅ scripts/attribution_v2_harness.py (test machinery bugs)
✅ scripts/scenarios/\*.toml (existing or new scenarios)
✅ Per-prompt quiesce / timeout values (if Claude is genuinely slow)
✅ STATE.md (always update at end of iteration)

NOT allowed without explicit human approval:
❌ Architecture changes (blame-over-synthetic-history is decided)
❌ Schema changes (packages/opentraces-schema/)
❌ Python dependencies (stdlib only)
❌ Anything in src/opentraces/ (this is spike work, not promotion)
❌ TOML schema changes (would invalidate existing scenarios)
❌ Weakening assertions to make tests pass (find the real bug instead)
❌ Skipping/disabling failing scenarios
❌ git push or remote operations

═══ STATE TRACKING (CRITICAL — you have no memory between iterations) ═══

At the END of every iteration, you MUST:

1. Update scripts/scenarios/STATE.md:

   - Bump "Last update" timestamp and your agent identifier
   - Update the scenario status table with any new pass/fail data
   - Update "Current focus" to point the next iteration at what to do
   - Append to "Activity log" with a one-line summary of what you did
   - Add to "Open questions for human" if you got stuck or want a decision

2. Commit your work with focused messages:
   spike: <one line of what you changed and why>
   Fixes: scenario `<name>`
   or
   harness: <change>
   Affects: <which scenarios>
   or
   scenario: <name>
   Tests: <one-line description of edge case>
   or
   state: update STATE.md (no code changes this iteration)

3. The next iteration's agent will read STATE.md to know where to pick up.
   If STATE.md isn't updated, the loop is broken — every iteration would
   redo the same work.

═══ WHEN TO STOP / ESCALATE ═══

End the iteration without changes (just update STATE.md) when:

- 3+ failed fix attempts on the same scenario
- Architecture or scope question you can't decide alone
- Suspected platform/environment issue (not a code bug)
- You'd be guessing rather than diagnosing

Document what you'd do and why under "Open questions for human" in STATE.md.

═══ MISSION REMINDER ═══

opentraces is building trace → commit attribution for AI coding agents.
Given a Claude session and a git commit, decompose every line of the
commit by which trace authored it. The hard cases — small tweaks across
sessions, Bash-mediated file creation, abrupt exits — drive the design.

Spike: trace_attribution_spike_v2.py materializes prompt-boundary
snapshots as synthetic git commits on refs/opentraces/audit/<project_id>,
attributed via author email. git blame --line-porcelain against that
audit history yields per-line ownership.

Watcher: closes the gap for shell-mediated changes (file-history misses
Bash-created files; git status --porcelain polling captures them).

Your job: run scenarios, find failures, fix the right layer, commit small
focused changes, expand coverage. Don't change the architecture, don't
broaden scope, don't disable failing scenarios. Update STATE.md.

Full context in scripts/scenarios/MISSION.md.
