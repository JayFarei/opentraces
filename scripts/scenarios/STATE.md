# Loop state — trace attribution test harness

> **For loop agents:** Read this file first every iteration. Update it last.
> Without this file, you cannot know what the previous iteration did.

Last update: (none yet — no iterations have run)
Last iteration agent: (none)

---

## Scenario status

Status legend: `pass` ✓ | `fail` ✗ | `unknown` ? | `flaky` 🌀 | `wip` 🔨 | `blocked` 🛑

| Scenario | Status | Last attempted | Notes |
|---|---|---|---|
| baseline                     | ? | — | — |
| bash_appends                 | ? | — | — |
| bash_creates_files           | ? | — | — |
| bash_deletes                 | ? | — | known gap: missing_from_audit expected |
| bash_overwrites              | ? | — | — |
| clear_mid_session            | ? | — | risky: /clear behavior unclear |
| close_without_exit           | ? | — | — |
| crash_during_prompt          | ? | — | — |
| exit_without_done            | ? | — | — |
| file_create_then_delete      | ? | — | uses allow_empty commit |
| formatter_after_edit         | ? | — | external edit attribution muddy |
| human_between_sessions       | ? | — | — |
| multi_commits_one_session    | ? | — | agent runs git commit via Bash |
| multiple_edits_one_turn      | ? | — | — |
| partial_commit               | ? | — | — |
| pre_session_content          | ? | — | — |
| revert_within_session        | ? | — | — |
| same_lines_overwrite         | ? | — | tests `lines = 0` for overwritten trace |
| sed_inplace_edit             | ? | — | macOS sed -i '' syntax |
| small_tweak                  | ? | — | the killer per-line case |
| watcher_offline              | ? | — | no start_watcher step |
| zero_file_history_session    | ? | — | likely fails: no audit ref → spike error |

Self-tests (`scripts/attribution_v2_selftest.py`): ?

---

## Current focus

**Working on**: (nothing — pick the first failing scenario from the table above)

**Hypothesis**: —

**Last attempted fix**: —

**Next step for next iteration**: Run the full suite once to populate the status table with real data. Use:
```
python3 scripts/attribution_v2_harness.py 2>&1 | tee /tmp/run-$(date -u +%Y%m%dT%H%M%SZ).log
```
Then update this file with each scenario's pass/fail.

---

## Open questions for the human

(none yet)

---

## Activity log (newest first)

(no entries yet — first iteration will append here)
