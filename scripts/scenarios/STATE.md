# Loop state — trace attribution test harness

> **For loop agents:** Read this file first every iteration. Update it last.
> Without this file, you cannot know what the previous iteration did.

Last update: 2026-04-14T04:15Z
Last iteration agent: claude-opus-4-6 (loop iter 9)

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
| bash_rename                  | pass | 2026-04-14 | iter 3; rename semantics (seed-author keeps credit for moved content, watcher credits active trace for new path) |
| binary_file_added            | pass | 2026-04-14 | iter 6; exposed & fixed UnicodeDecodeError on blame output for non-UTF-8 bytes |
| clear_mid_session            | fail | 2026-04-14 | timeout fixed (harness slash-command handling); now fails on attribution — `/clear` drops file-history blobs for the cleared session, so early.md has no snapshots. Deeper spike question (see Open questions) |
| close_without_exit           | pass | 2026-04-13 | — |
| commit_amend                 | pass | 2026-04-14 | iter 9; amend doesn't break attribution (audit history immutable across SHA change) |
| crash_during_prompt          | pass | 2026-04-13 | — |
| exit_without_done            | pass | 2026-04-13 | — |
| file_create_then_delete      | fail | 2026-04-13 | "README.md missing from attribution" — empty commit has no diff-tree files; scenario asserts README.md pre-audit which isn't in the commit diff |
| formatter_after_edit         | pass | 2026-04-13 | — |
| human_between_sessions       | pass | 2026-04-14 | — |
| mixed_write_and_bash         | pass | 2026-04-14 | new this iter; Write + Bash append in one trace, all lines credit same trace (dual-signal integration) |
| multi_commits_one_session    | pass | 2026-04-13 | — |
| multiple_edits_one_turn      | pass | 2026-04-13 | — |
| no_trailing_newline          | pass | 2026-04-14 | iter 8; confirms last unterminated line still counts |
| partial_commit               | pass | 2026-04-13 | — |
| pre_session_content          | pass | 2026-04-13 | — |
| revert_within_session        | pass | 2026-04-13 | — |
| same_lines_overwrite         | pass | 2026-04-13 | — |
| sed_inplace_edit             | pass | 2026-04-13 | — |
| small_tweak                  | pass | 2026-04-14 | — |
| symlink_added                | pass | 2026-04-14 | iter 7; documents symlink-as-file behavior (watcher follows link via is_file, audit snapshots target content rather than link path). Latent design question: symlink should arguably blame to 1 line of target-path content. |
| two_traces_different_files   | pass | 2026-04-14 | new this iter; E2E variant of self-test, two sessions touching disjoint files, no cross-contamination |
| watcher_offline              | pass | 2026-04-13 | — |
| zero_file_history_session    | pass | 2026-04-14 | was failing; fixed by graceful-no-audit fallback |

Self-tests (`scripts/attribution_v2_selftest.py`): pass (6/6)

**Current: 27/29 passing.** Added scenarios across iters 3-9:
`bash_rename`, `mixed_write_and_bash`, `two_traces_different_files`,
`binary_file_added` (exposed a real spike bug), `symlink_added`,
`no_trailing_newline`, `commit_amend`. `clear_mid_session` and
`file_create_then_delete` remain blocked on human design decisions.

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

## Latent issues worth flagging

- **Symlink attribution is content-biased.** The watcher's
  `_working_tree_extras` uses `Path.is_file()` which follows symlinks,
  so audit snapshots a symlink as a *regular file* containing the
  target's bytes. Meanwhile HEAD stores the symlink as mode 120000
  with the link-target path as content. Attribution ends up blaming
  the target's line count rather than the 1-line link-target path
  that's actually committed. Not urgent (symlinks are rare in agent
  traces) but worth a conscious decision later. Locked-in by scenario
  `symlink_added` so future changes are intentional.

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

- 2026-04-14 — iter 9 (opus 4.6): Authored `commit_amend` — trace writes
  a file, user commits with a typo in the message, then `git commit
  --amend`s to fix it. Commit SHA changes but tree stays identical;
  attribution resolves through to the audit history unchanged. 3 lines
  still credited to trace a. Passed first try.

- 2026-04-14 — iter 8 (opus 4.6): Authored `no_trailing_newline` —
  agent creates a file via Bash `printf` without a terminating newline.
  Confirms the spike's line-count parsing treats the final
  unterminated line as a real attributed line (3 lines credited to
  trace a). Passed first try.

- 2026-04-14 — iter 7 (opus 4.6): Authored `symlink_added` — agent
  creates a target file then symlinks it via `ln -s`. Passed as
  written: attribution credits trace a for both files. Documents a
  latent design issue — audit snapshots the symlink as a regular file
  (Path.is_file follows links), so blame sees the target's content
  instead of the 1-line link-target path that HEAD actually stores.
  Logged under Latent issues; no fix this iteration (design question).

- 2026-04-14 — iter 6 (opus 4.6): Authored `binary_file_added` —
  agent uses Bash printf to create a file with non-UTF-8 bytes
  (PNG-like header + payload). First run crashed with
  `UnicodeDecodeError` deep in the spike's `git()` helper: blame
  output includes raw content bytes interleaved with ASCII porcelain
  metadata, and `subprocess.run(text=True)` defaults to strict UTF-8
  decoding. Fixed by switching to `encoding="utf-8",
  errors="replace"`; line-count integrity is preserved because the
  porcelain framing is ASCII-only. Scenario now passes; 3 lines
  attributed to trace a. No regressions across self-tests or
  baseline/small_tweak/bash_rename.

- 2026-04-14 — iter 5 (opus 4.6): Authored `two_traces_different_files` —
  E2E harness variant of the `two_sessions_different_files` self-test.
  Two sequential claude sessions write disjoint files; single commit.
  Each file must credit its own trace with no cross-contamination.
  Passed first try.

- 2026-04-14 — iter 4 (opus 4.6): Authored `mixed_write_and_bash` — one
  trace uses the Write tool then appends via Bash printf redirection in
  the same session. Locks in the dual-signal design: all 5 lines
  correctly attribute to the single trace (file-history catches the
  Write, watcher catches the Bash append, both resolve to the same
  trace_id). Passed first try; confirms integration of the two capture
  paths in the happy case.

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
