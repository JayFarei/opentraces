# Stage contracts — /issue-to-pr

The typed shapes that flow between stages, and the templates each stage fills. Keeping these explicit is what lets the skill stay a thin spine over deterministic phases.

## Stage 1 — `issue-investigate.workflow.js` return

```
{
  issue: <number>,
  report: "<markdown VERIFICATION REPORT>",   // the synthesized human-readable report
  raw: [ { lens, findings: [ { statement, verdict, evidence, corrected? } ], notes? }, ... ]
}
```

`verdict ∈ {confirmed, drifted, refuted, not-found, risk}`.
- **drifted** findings carry `corrected` = the TRUE current file:line/signature → the implementation uses these, never the issue's stale refs.
- **refuted** findings are landmines → the plan must not rely on them.
- The report's "Readiness verdict" section drives whether to proceed to Stage 2 or terminate the issue as `issue-invalid`.

## Stage 2 — plan doc template (`kb/plans/<nnn>-issue-<n>.md`)

```markdown
# Plan <nnn> — issue #<n>: <title>

## Root cause
<verified mechanism, file:line — from Stage 1, NOT the issue's assertion>

## Verification corrections (from Stage 1)
- Drifted refs (use these): <corrected file:line list>
- Refuted / fictional (do NOT rely on): <list — or "none">

## Fix approach
<grounded in verified file:line + existing patterns. Reuse existing
reversers/registries; additive + reversible; data-safe; honest.>

## Test plan (two layers — name both)
### Mechanism (unit / $HOME-isolated subprocess e2e)
| Test | Layer | Asserts |
|------|-------|---------|
| ...  | ...   | ...     |

### Acceptance — the otbox journey (REQUIRED for user/agent-observable change)
| Journey TOML | tier (0=CI / 1=opt-in) | seed/checkpoint | steps → assertion kinds |
|---|---|---|---|
| `tests/otbox/catalogue/journeys/<name>.toml` | 0 | smoke/world/`c-…` | … |

> If the change is NOT user/agent-observable (pure internal refactor, perf, docs-only), write: "No otbox journey — <reason>." Do not leave this section blank. Acceptance is an otbox journey.

## Test-completeness proof (executable — not aspirational)
How we PROVE the test exercises the fix:
- [ ] Red-before-green: the new test FAILS on the pre-fix code (capture the output), then PASSES after the fix; OR
- [ ] Reintroduce-the-bug: a temporary patch that restores the verified cause makes the new test fail.
The proof targets the SPECIFIC cause from Stage 1. A test that passes on the
buggy code is not evidence.

## Acceptance map
| Issue AC | Met? | Note |
|----------|------|------|
| ...      | yes/no/partial | (if no: why — e.g. depends on a refuted surface) |

## Adversarial gate results (Stage 3)
- 3a self/ethos: <✓ all / list of ❌ resolved>
- 3b codex Plan Reviewer: <APPROVE/REJECT + how each finding was folded in>
```

## Stage 3b — codex Plan Reviewer call (shape)

```
mcp__codex__codex({
  prompt: "<7-section: TASK / EXPECTED OUTCOME / CONTEXT (point at the plan file + real source) / CONSTRAINTS (read-only) / MUST DO (break the plan — missed surfaces, wrong APIs, fictional refs, data-loss, test gaps) / MUST NOT DO / OUTPUT FORMAT (APPROVE|REJECT + file:line findings)>",
  "developer-instructions": "<contents of prompts/plan-reviewer.md>",
  sandbox: "read-only",
  cwd: "<worktree path>",
  config: { model_reasoning_effort: "high" }
})
```

## Stage 6 — codex Code Reviewer call (shape)

Same shape, but `developer-instructions` = `prompts/code-reviewer.md`, and the prompt points codex at `git diff <base>...HEAD` with instructions to hunt correctness / data-safety / regression holes and try to BREAK the change. Run a focused **confirm round** on any PARTIAL findings (the #85 PR loop converged in three rounds). Codex-unavailable → substitute a Workflow code-review red-team and say so.

## Stage 5 — PR body must contain

- `Closes #<n>` (auto-closes the issue on merge).
- Cause → fix → tests narrative.
- The **test-completeness evidence** (the red-before-green output or reintroduce-the-bug result).
- The **otbox acceptance journey(s)** added/extended + their tier and CI status (acceptance is an otbox journey) — or the justified "no journey" reason.
- Any **unmet ACs** stated honestly.

## Per-issue terminal status (reported at the end)

`pr: <url>` · or one of `issue-not-actionable` / `issue-invalid` / `gh-unauthenticated` / `colliding-worktree` / `tests-cannot-run` / `codex-unavailable (substituted)` / `pr-create-failed (branch pushed: <branch>)` — each with a one-line reason.
