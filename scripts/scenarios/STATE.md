# Loop state — trace attribution test harness

> **For loop agents:** Read this file first every iteration. Update it last.
> Without this file, you cannot know what the previous iteration did.

Last update: 2026-04-14T01:15Z
Last iteration agent: claude-opus-4-6 (loop iter 3)

---

## Scenario status

Status legend: `pass` ✓ | `fail` ✗ | `unknown` ? | `flaky` 🌀 | `wip` 🔨 | `blocked` 🛑

| Scenario | Status | Last attempted | Notes |
|---|---|---|---|
| baseline                     | pass | 2026-04-14 | — |
| bash_appends                 | pass | 2026-04-13 | — |
| bash_creates_files           | pass | 2026-04-13 | — |
| bash_deletes                 | pass | 2026-04-14 | was failing; fixed by graceful-no-audit fallback |
| bash_overwrites              | pass | 2026-04-13 | — |
| bash_rename                  | pass | 2026-04-14 | new this iter; documents rename semantics (seed-author keeps credit for moved content, watcher credits active trace for new path) |
| clear_mid_session            | fail | 2026-04-14 | timeout fixed (harness slash-command handling); now fails on attribution — `/clear` drops file-history blobs for the cleared session, so early.md has no snapshots. Deeper spike question (see Open questions) |
| close_without_exit           | pass | 2026-04-13 | — |
| crash_during_prompt          | pass | 2026-04-13 | — |
| exit_without_done            | pass | 2026-04-13 | — |
| file_create_then_delete      | fail | 2026-04-13 | "README.md missing from attribution" — empty commit has no diff-tree files; scenario asserts README.md pre-audit which isn't in the commit diff |
| formatter_after_edit         | pass | 2026-04-13 | — |
| human_between_sessions       | pass | 2026-04-14 | — |
| multi_commits_one_session    | pass | 2026-04-13 | — |
| multiple_edits_one_turn      | pass | 2026-04-13 | — |
| partial_commit               | pass | 2026-04-13 | — |
| pre_session_content          | pass | 2026-04-13 | — |
| revert_within_session        | pass | 2026-04-13 | — |
| same_lines_overwrite         | pass | 2026-04-13 | — |
| sed_inplace_edit             | pass | 2026-04-13 | — |
| small_tweak                  | pass | 2026-04-14 | — |
| watcher_offline              | pass | 2026-04-13 | — |
| zero_file_history_session    | pass | 2026-04-14 | was failing; fixed by graceful-no-audit fallback |

Self-tests (`scripts/attribution_v2_selftest.py`): pass (6/6)

**Current: 21/23 passing.** (Added bash_rename. clear_mid_session and
file_create_then_delete remain blocked on human design decisions.)

---

## Current focus

**Working on**: nothing active; next iteration picks one of the two remaining fails.

**Remaining fails** (next-iteration candidates):

1. **clear_mid_session** — timeout fixed this iteration. Now fails because
   `/clear` wipes the cleared session's file-history directory, so no
   snapshots exist for the pre-clear trace. Session 1's JSONL still
   records 5 `Write` tool_use calls, but `~/.claude/file-history/<sid1>/`
   does not exist. See Open questions for design choice.

2. **file_create_then_delete** — "README.md missing from attribution".
   The commit is `allow_empty = true` (net-zero create+delete), so
   `diff-tree` returns no files. Assertion expects README.md/pre-audit
   ≥1, but attribution only operates on commit-changed files. See Open
   questions.

**Next step**: neither remaining fail can be fixed without a human
decision. Next iteration should either (a) pick up one of the human
answers if provided, or (b) author a new scenario covering an uncovered
edge case (suggestions: Edit-tool vs Write-tool distinction; rename
detection; binary-file touch; very-large-file blame; symlink file).

---

## Open questions for the human

- **`clear_mid_session` — attribution across /clear.** Observed: `/clear`
  retires the current session (new JSONL opens), and the cleared
  session's `~/.claude/file-history/<sid>/` directory is deleted by
  Claude Code. Session 1's JSONL still exists and records 5 `Write`
  tool_use calls, but with no backup blobs, the spike has nothing to
  snapshot. Options:
    (a) Fall back to mining `Write`/`Edit` tool_use `content` fields
        directly from the JSONL when file-history blobs are absent.
        Biggest correctness win; moderate implementation (need to
        reconstruct per-turn file state from Edit old→new string
        replacements).
    (b) Accept the gap; mark `clear_mid_session` as
        expected-attribution-loss and assert early.md lines go to
        pre-audit instead of trace `a`.
    (c) Change the scenario to avoid `/clear` entirely (but that
        sidesteps the case entirely; we still don't know how to
        attribute across it in real traces).
  Which direction? (a) is the honest answer but is a real feature.

- **`file_create_then_delete` — assertion mismatch.** The scenario
  author wrote "no strong assertion to make — just ensure no crash"
  but then added `README.md pre-audit ≥1`, which can never match on an
  empty commit (diff-tree returns no files). Options:
    (a) weaken to check `status = missing_from_audit` on the
        ephemeral file,
    (b) drop the assertion (test becomes "did harness not crash"),
    (c) expand `attribute_commit` to blame unchanged HEAD files too
        (bigger scope).

---

## Activity log (newest first)

- 2026-04-14 — iter 3 (opus 4.6): Authored new scenario `bash_rename`
  (agent renames file via Bash `mv`). First run failed my initial
  expectation (I assumed original.md would be missing_from_audit); the
  actual spike behavior is better — audit tree retains the pre-rename
  content, so the commit's delete side blames through to the seed
  author (pre-audit), while the new path attributes to the watcher-
  captured active trace. Updated scenario assertions to document and
  lock in these semantics: renaming doesn't reassign authorship. Green.

- 2026-04-14 — iter 2 (opus 4.6): Tackled `clear_mid_session`. Root cause of
  the 240s timeout: prompts starting with `/` are local UI actions that
  produce no JSONL growth, so the quiesce-on-growth detector never fires.
  Fixed in harness: slash-command prompts get a 3s fixed pause and skip the
  growth wait; `/clear` additionally resets `jsonl_path` so the next real
  prompt re-discovers the new session's JSONL. Scenario still fails at the
  attribution stage — `/clear` destroys the cleared session's file-history
  blobs, so session 1's Writes leave no snapshot trail. Flagged as an open
  design question; did not attempt a spike-side fix (would require mining
  Write/Edit tool_use content directly from JSONL). No regressions across
  baseline/small_tweak/bash_deletes/zero_file_history_session.

- 2026-04-14 — iter 1 (opus 4.6): Ran full suite: 18/22. Fixed the
  "No trace snapshots" RuntimeError by making `build` exit 0 when no
  snapshots exist and `attribute_commit` fall back to blaming the commit
  itself (all lines → pre-audit). Unblocks `bash_deletes` and
  `zero_file_history_session`. Verified 6/6 self-tests + 3 regression
  scenarios still pass. Now at 20/22.
