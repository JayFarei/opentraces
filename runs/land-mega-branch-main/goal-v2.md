# Goal V2 - Land Mega Branch To Main

Created: 2026-05-21T20:01:54Z
Skill inputs: goal-forge, thermo-nuclear-code-quality-review
Lazyusage evidence: `bun run dev usage-check codex --json-only` from `/Users/jayfarei/src/tries/2026-02-05-test-usage-via-cli` reported Codex `5h.used_pct=7`, `source=api`, `stale=false`, `error=null`.

```text
Goal: Using goal-forge discipline, land the current `feat/buckets-trails-milestone` mega-branch head `0aca134fd11a5b2447926650ec5e059e63b150aa` onto `main` with `git merge-base --is-ancestor 0aca134fd11a5b2447926650ec5e059e63b150aa main` true and no unresolved landing, maintainability, otbox, or performance blockers. Completion requires a thorough branch-vs-main `thermo-nuclear-code-quality-review` pass with zero unresolved blocker/high findings; no otbox scenario inventory regression against `/tmp/opentraces-otbox-scenarios-handoff-2026-05-21.md` including the 122 catalogue journey TOMLs and 15 simulated-user scenario TOMLs unless an intentional count change is documented; relevant changed-subsystem tests passing; and performance testing showing no meaningful regression or documenting/fixing any hot-path slowdown found while making the branch faster and simpler where practical.

Verify by reporting the final Git ancestry/status, completed review register at `runs/land-mega-branch-main/thermo-nuclear-review.md`, successful no-regression commands from `runs/land-mega-branch-main/no-regression-evaluation.md`, focused pytest results including changed subsystems and `tests/perf` or a documented scoped perf equivalent, and the latest reliable Codex 5-hour usage reading before every expensive phase. Preserve the public CLI/schema/docs behavior in `AGENTS.md`, security posture, schema/dataset contracts, otbox user journeys, performance budgets, and any user-authored worktree changes.

Before starting expensive Codex work, long test runs, or agent fan-out, run `lazyusage usage-check codex --json-only`; if unavailable, run `bun run dev usage-check codex --json-only` from `/Users/jayfarei/src/tries/2026-02-05-test-usage-via-cli`; parse `.services[] | select(.name == "codex") | .metrics[] | select(.name == "5h")` and use `used_pct` as the hard 5-hour signal. If the Codex service has `source == "fallback"`, `stale == true`, or `error != null`, treat the reading as unreliable and do not start large Codex work or spawn extra agents; if `5h.used_pct >= 95`, stop, warn, and sleep until reset plus a small buffer; if `5h.used_pct >= 90`, warn and avoid new large work unless it is tiny and explicitly urgent; if `<90`, proceed normally and re-check before each long-running step.

Use only this repo, connected Git/GitHub tooling when needed, `/tmp/opentraces-otbox-scenarios-handoff-2026-05-21.md`, lazyusage, and focused edits/tests needed to eliminate blockers, improve maintainability, preserve behavior, and keep or improve performance. Log each attempt to `runs/land-mega-branch-main/log.md` with diff, evidence observed, lazyusage status when relevant, and next-step rationale; on block, append a `BLOCKED:` entry there with attempted paths, evidence gathered, the blocker, and the exact user input or external state needed to unlock progress.
```

Audit: Outcome present; verification present; constraints present; boundaries present; iteration present; blocked present.
