# Loop state — trace attribution test harness

> **For loop agents:** Read this file first every iteration. Update it last.
> Without this file, you cannot know what the previous iteration did.

Last update: 2026-04-14T00:20Z
Last iteration agent: claude-opus-4-6 (loop iter 1)

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
| clear_mid_session            | fail | 2026-04-13 | prompt timeout >240s after /clear. Likely auto-accept/quiesce issue post-/clear |
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

**Current: 20/22 passing (was 18/22).**

---

## Current focus

**Working on**: nothing active; next iteration picks one of the two remaining fails.

**Remaining fails** (next-iteration candidates):

1. **clear_mid_session** — prompt timeout >240s. Hypothesis: after `/clear`,
   claude's UI state doesn't emit the same quiesce signals the harness
   watches for, so the prompt-complete detector never fires. Needs pane
   inspection under `-v --keep`. Could be a harness bump (quiesce param)
   or genuine /clear regression.

2. **file_create_then_delete** — "README.md missing from attribution".
   The commit is `allow_empty = true` (net-zero create+delete), so
   `diff-tree` returns no files. The assertion expects README.md/pre-audit
   ≥1, but attribution only operates on commit-changed files. Either the
   assertion needs to shift to a file that IS in the commit (but there
   are none), or `attribute_commit` should include non-changed files from
   HEAD — which is a meaningful scope expansion. Flag for human decision.

**Next step**: investigate `clear_mid_session` first (lower-risk, likely a
harness timeout/quiesce tweak). Use `-v --keep clear_mid_session` then
inspect tmux pane.

---

## Open questions for the human

- `file_create_then_delete`: the scenario author wrote "no strong assertion
  to make — just ensure no crash" but then added `README.md pre-audit ≥1`,
  which can never match on an empty commit. Three options: (a) weaken to
  check `status = missing_from_audit` on a temp-ish file, (b) drop the
  assertion entirely (test becomes "did harness not crash"), (c) expand
  `attribute_commit` to blame unchanged HEAD files too. Which do you want?

---

## Activity log (newest first)

- 2026-04-14 — iter 1 (opus 4.6): Ran full suite: 18/22. Fixed the
  "No trace snapshots" RuntimeError by making `build` exit 0 when no
  snapshots exist and `attribute_commit` fall back to blaming the commit
  itself (all lines → pre-audit). Unblocks `bash_deletes` and
  `zero_file_history_session`. Verified 6/6 self-tests + 3 regression
  scenarios still pass. Now at 20/22.
