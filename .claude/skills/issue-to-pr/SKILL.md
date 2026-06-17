---
name: issue-to-pr
description: >
  Take one or more GitHub issues and drive each to a review-ready pull request,
  holding one belief throughout: an issue is a hypothesis, not a spec. The
  pipeline VERIFIES BEFORE IT TRUSTS and runs TWO ADVERSARIES before it commits:
  multi-agent investigation of the real cause (flagging any "landmine" — a cited
  function or file:line that does not exist) and the blast radius, a plan whose
  test must go RED BEFORE GREEN, two adversarial gates on that plan (a self/ethos
  checklist + a pure codex adversarial pass), sequential implementation in an
  isolated worktree, a green suite + docs sync, a PR that `Closes #<n>`, and a
  FINAL codex adversarial review of the diff. Honesty, not theater: it reports
  unmet acceptance criteria and invalid issues rather than faking a fix. The
  human merges. Everything happens in a per-issue git worktree; multiple issues
  each get their own worktree + branch + PR. Use when the user says "issue-to-pr",
  "solve issue", "fix issue #N", "take issue N to a PR", "investigate and fix
  #N", or hands over one or more issue numbers/URLs. For a plan-only pass use
  /new-plan; for docs sync use /docs-update; this is the full issue→PR pipeline.
---

# issue-to-pr

Distilled from the issue-#85 run. The load-bearing belief: **an issue is a hypothesis, not a spec.** Issues drift from the code, cite functions that no longer exist, and assert causes that aren't the real cause. So the pipeline verifies before it trusts, and runs two independent adversarial passes before committing to a plan — because on #85 both the codex pass and the self red-team independently caught the same real defects that a single reviewer missed.

## Leading words (leitwörter)

A handful of leading words carry this skill. **Use them by name** — in your plan, your commits, your PR body, and your own reasoning. Each compresses a whole behavior into one token you can repeat to keep yourself honest, and each is borrowed from a discipline whose instincts you want to borrow with it. They recur on purpose.

- **An issue is a hypothesis** *(science)* — never trust the issue's claims; reproduce the cause and check every assertion against the *current* tree before you believe it. When you catch yourself about to trust a line number, say the words and go verify.
- **Landmine** *(the refuted claim)* — a cited file:line / function / API that does not exist or is wrong. Name landmines out loud and route around them; **never build on a landmine** (#85 cited two functions that did not exist).
- **Blast radius** *(incident response)* — everything a fix could disturb: callers, tests, frozen `opentraces.*.v1` contracts, the schema, docs. Map the blast radius before you touch code.
- **Red before green** *(TDD)* — the new test must FAIL on the buggy code before it passes on the fix. A test that has never been red is **theater**, not evidence.
- **Two adversaries, then commit** *(red-teaming)* — the self/ethos pass AND codex each try to BREAK the plan; when two independent adversaries converge on the same defect, believe it. Adversarial *before* commit, not after.
- **Honesty, not theater** *(the house rule)* — report residue, partial failures as errors, unmet acceptance criteria, and invalid issues. A green checkmark that proves nothing is theater.
- **The simplest change that closes the issue** *(pragmatic minimalism)* — extend what exists; resist the speculative refactor riding along.
- **The human merges** *(the gate)* — assume forward and surface every decision in the PR, not in a question; but never merge — the merge is theirs.

## Anti-patterns (the failure modes these words name)

State the leitwort, then catch yourself in its opposite:

- **Building on a landmine** — taking the issue's file:line / function name as truth, so the fix targets something that doesn't exist or the wrong place. → *an issue is a hypothesis.*
- **Green theater** — a test that passes on the *unfixed* code, so it proves nothing. → *red before green.*
- **Silent no-op** — a reverser/handler that returns ok without doing (or undoing) anything; a skip with no reason; a partial failure dressed as success. → *honesty, not theater.*
- **One-reviewer confidence** — shipping a plan a single pass approved. → *two adversaries, then commit.*
- **The interview spiral** — stalling on questions the PR could answer. → *assume forward; the human merges.*
- **Scope bloom** — a refactor the issue never asked for. → *the simplest change that closes the issue.*

This skill is the **main-loop spine**: it owns judgment, the worktree/PR lifecycle, the codex MCP calls, the human gate, and multi-issue orchestration. It delegates only the **bounded, read-only parallel fan-out** to the Workflow tool (Stage 1). Implementation is **sequential in one worktree** — a deliberate v1 choice: parallel file-editing across worktrees is conflict-prone and rarely pays off for a typical issue, so parallelism is confined to the phases where it is free and safe (investigation + multi-lens review).

## Effort policy

Reasoning depth is a lever — spend it where judgment matters. **Max for planning and adversarial testing; xhigh for normal development.**

| Phase | Effort | Why |
|---|---|---|
| Plan (Stage 2) · both adversarial gates (3a self/ethos, 3b + 6 codex) · final synthesis | **max** | judgment and adversarial reasoning is where depth pays off — *two adversaries, then commit* |
| Implementation (Stage 4) and other normal dev work | **xhigh** | thorough, but mechanical |
| Investigation (Stage 1, read-only fan-out) | **high** | bounded evidence-gathering; reasoning needn't be maxed for grep-and-report |

Apply it through whatever lever the phase uses:
- **Main-loop stages** (you — planning, self/ethos, implementing): run at the tier above — set `/effort max` before the plan + adversarial stages and `/effort xhigh` for implementation, or simply reason at that depth.
- **Workflow `agent()` calls**: pass `effort: 'high'` (investigation) or `effort: 'max'` (any adversarial red-team fan-out — e.g. the codex-unavailable fallback).
- **codex MCP** (`mcp__codex__codex`): `config: { model_reasoning_effort: "high" }` — "high" is codex's top reasoning tier (the Claude-side `max`/`xhigh` names don't apply to codex; high *is* the adversarial max there).

## Model policy

**Opus across the board.** Run the main loop — planning, the adversarial gates, implementation, synthesis — on the latest **Opus** (the session default; don't downgrade it). The only exception is **cheap, high-volume, mechanical work**, where the latest **Sonnet** is appropriate: the Stage-1 investigation lenses (read-only grep-and-verify fan-out — N per issue, the highest fan-out in the pipeline, especially across multiple issues) run on `model: 'sonnet'`, while their judgment-bearing synthesis runs on Opus. codex (Stages 3b/6) is GPT — Claude model selection doesn't apply there.

## Inputs

`/issue-to-pr <n>` or `/issue-to-pr <n> <m> ...` — accepts issue numbers, `#n`, or full GitHub issue URLs. The repo is the current repo (`gh repo view`). No issue argument → ask which issue(s).

## Multi-issue orchestration

- Run **Stage 1 (investigation) for ALL issues in parallel** — it's read-only and cheap.
- Then take issues **one at a time** through Stages 2→7 (plan → gate → implement → PR). Each issue gets its **own worktree + branch + PR**. Sequential keeps the human gate legible and avoids cross-issue worktree thrash.
- If the user explicitly asks to background whole-issue pipelines (truly independent issues), you may, but default to sequential.
- Report a per-issue status line at the end (PR URL or terminal-outcome reason).

---

## Per-issue pipeline

### Stage 0 — Intake & worktree (preflight; fail fast and honestly)

1. **Resolve the issue:** `gh issue view <n> --json number,title,body,state,labels,comments`. If it's not OPEN, or not a real/actionable issue (discussion, duplicate, "wontfix"), STOP for this issue and report why — do not invent work.
2. **Preflight the environment** (each is a terminal outcome for this issue if it fails, with a clear message — never half-start):
   - `gh auth status` succeeds (else: "gh not authenticated — run `gh auth login`").
   - The base branch (`main`) is clean enough to branch from (uncommitted changes on the base are fine; you branch off the committed base).
   - No branch/worktree collision: if `fix/issue-<n>-*` or the target worktree path already exists, reuse it only if it's clearly a prior run of THIS issue; otherwise pick a suffixed name and say so.
3. **Create the worktree** off the up-to-date base:
   ```bash
   git worktree add -b fix/issue-<n>-<slug> ../<repo>-issue-<n> <base>
   ```
   (`feat/…` if the issue is labeled `feature`/`enhancement`.) Build/reuse a venv inside it (`python3 -m venv .venv` + `pip install -e packages/opentraces-schema` + `pip install -e ".[dev]"`) — run it in the background while you investigate. **The #85 lesson:** in-process tests can't isolate import-bound `$HOME` paths, so the worktree + a fresh venv + `$HOME`-isolated / subprocess tests are the reliable substrate.
4. All file work for this issue happens in that worktree from here on.

### Stage 1 — Investigate (multi-agent, read-only) → Workflow · effort: high

Run the bundled investigation workflow, scoped to this issue:
```
Workflow({
  scriptPath: ".claude/skills/issue-to-pr/issue-investigate.workflow.js",
  args: { issue: <n>, title: "<title>", body: "<issue body>", repoRoot: "<worktree path>" }
})
```
Pass `args` as a real JSON object (the workflow also defensively parses a JSON-string form). Keep `body` to the issue's substance — the workflow re-reads the repo itself, so a multi-KB body isn't needed and risks the stringified-args footgun.
It fans out read-only `Explore` agents to produce three things (see `references/stage-contracts.md` for the exact return schema). Throughout, hold the line: **an issue is a hypothesis** — the workflow's job is to confirm, correct, or refute it against the current tree.
- **Cause** — the real mechanism that produces the behavior, with file:line evidence. An issue is a hypothesis; find the actual cause, not the issue's asserted one.
- **Claim verification** — every file:line / function / API the issue cites, checked against the CURRENT tree: `confirmed` / `drifted` (corrected file:line) / `refuted` (a **landmine** — the issue is wrong; #85 cited two functions that did not exist) / `not-found`.
- **Blast radius** — callers, tests, docs, and frozen consumer contracts (`opentraces.*.v1` envelopes, schema, otbox journeys) the fix could disturb.

Read the consolidated report. If investigation shows the issue is **invalid / already-fixed / misdiagnosed**, that is a terminal outcome: report it (with evidence) and stop — a correct "this isn't a bug, here's why" beats a fabricated fix.

### Stage 2 — Plan · effort: max

Write `kb/plans/<nnn>-issue-<n>.md` (kb/ is gitignored; follow the existing numbering). It MUST contain (template in `references/stage-contracts.md`):
- **Root cause** — from Stage 1, with file:line.
- **Fix approach** — **the simplest change that closes the issue**, grounded in verified (never landmine) file:line and *existing patterns* (extend a registry/verb, additive + reversible schema evolution, data-safe by default, honesty not theater). Prefer modifying what exists over siloing something new; leave no scope bloom.
- **Test plan** — which tests at which layer (unit / `$HOME`-isolated subprocess e2e / otbox journey), and the exact assertions.
- **Test-completeness proof (executable, not aspirational)** — exactly how you will PROVE the test exercises the fix: a captured **red-before-green** output (the new test fails on the pre-fix code) OR a temporary reintroduce-the-bug patch that makes the new test fail for the *specific verified cause*. A test that passes on the buggy code is not evidence and does not count.
- **Acceptance map** — map to the issue's ACs; call out any AC that **cannot** be met (e.g. it depends on a refuted/fictional surface) rather than papering over it.

### Stage 3 — Dual adversarial gate on the plan · effort: max

**Two adversaries, then commit.** Two independent passes each try to BREAK the plan; fold both in before any code. Run this stage at **max** effort (and codex at its top tier) — adversarial reasoning is exactly where depth earns its cost.

**3a — Self / ethos (main-loop checklist).** Score the plan against `references/ethos-checklist.md` (the product-direction rubric derived from CLAUDE.md "Key Decisions"). Resolve every ❌. This is your own adversarial pass — be harsh.

**3b — Codex pure adversarial (Plan Reviewer).** Read the expert prompt and delegate read-only:
- Read `${CLAUDE_PLUGIN_ROOT}/prompts/plan-reviewer.md` (or `~/.claude/.../prompts/plan-reviewer.md`) and inject it as `developer-instructions`.
- Call `mcp__codex__codex` with `sandbox: "read-only"`, `cwd: "<worktree path>"`, `config: {model_reasoning_effort: "high"}`, and a 7-section prompt that points codex at the plan file + the real source and tells it to BREAK the plan (missed surfaces, wrong API shapes, fictional refs, data-loss, test gaps).
- **Codex-unavailable fallback:** if the MCP tool is absent (headless/cron), run a second Workflow red-team pass as the external adversary instead, and note in the plan that the codex gate was substituted.

Fold both passes into the plan. Convergence between 3a and 3b on the same defect is the strongest signal. Re-run a pass only if a blocker materially reshaped the plan.

### Stage 4 — Implement (sequential, in the issue worktree) · effort: xhigh

- Work the hardened plan top-to-bottom in the worktree. Match surrounding code (comment density, naming, idioms). Reuse existing reversers/registries/patterns the investigation surfaced.
- Write the tests from the Stage-2 plan and **run the test-completeness proof**: capture the red-before-green output (or apply+revert the reintroduce-the-bug patch) and confirm the new test actually fails for the verified cause. Record that evidence — it goes in the PR.
- Iterate to a green suite. Run the focused suites first, then the broad regression (`pytest tests/cli tests/core tests/capture` or the issue-relevant dirs) — a new public CLI command, schema field, or envelope will trip the surface-sweep / snapshot tests; updating those (e.g. `GLOBAL_JSON_ONLY` + regenerating `json_envelope_shapes.json`) is part of the work, not optional.

### Stage 5 — Tests green + docs + PR

1. Confirm the relevant suite is green in the worktree.
2. **`/docs-update`** — always a step (standing directive). It self-scopes: if the change touches no doc surface it's a clean no-op; if it adds/changes a CLI verb, flag, schema field, or install/uninstall flow, it syncs README / installation / commands / SKILL.md / llms.txt. Never hand-edit `llms.txt` — regenerate it.
3. Commit (conventional-commit subject; end the message with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer).
4. Push the branch; open the PR:
   ```bash
   gh pr create --base <base> --head fix/issue-<n>-<slug> --title "..." --body-file <body>
   ```
   The body MUST contain **`Closes #<n>`** (so the merge auto-closes the issue), a cause→fix→tests narrative, the **test-completeness evidence**, and any **unmet ACs** (honesty). If `gh pr create` fails (no remote / perms), report the pushed branch + the exact command for the user to run.

### Stage 6 — Final codex adversarial pass at the PR · effort: max (codex: high)

- Read `${CLAUDE_PLUGIN_ROOT}/prompts/code-reviewer.md`, inject it as `developer-instructions`, and call `mcp__codex__codex` (`sandbox: "read-only"`, `cwd: "<worktree>"`, high effort) on `git diff <base>...HEAD`. Tell it to hunt correctness / data-safety / regression holes and to try to BREAK the change, not nitpick.
- **Incorporate the feedback:** apply judgment (codex can be wrong), fix the real findings (each with a regression test), re-run tests, push. Run a focused **confirm round** on any PARTIAL findings — the #85 loop converged in three rounds.
- Post a PR comment documenting the adversarial loop (findings → resolution) so the human reviewer sees the gate closed.
- Codex-unavailable fallback: substitute a Workflow code-review red-team and say so in the PR comment.

### Stage 7 — Present (human gate — never merge)

- Confirm CI is green (`gh pr checks <pr>`); if red, fix until green or surface the failure.
- Present the PR for **review-before-merge**: PR URL, cause→fix→test→evidence summary, CI status, any unmet ACs, and (if the worktree should be cleaned up) the cleanup command. The merge is the human's decision — **do not merge**.

---

## Worktree lifecycle & cleanup

- One worktree + branch + venv per issue.
- On a successfully-presented PR, offer to remove the worktree (`git worktree remove <path>`); keep it on failure and report its path + branch so the user can inspect.
- Don't auto-`git worktree prune` other people's worktrees — this repo accumulates them; only clean up the ones this run created.

## What this skill does NOT do

- It does **not merge** — ever. The human merges.
- It does **not** fabricate a fix for an invalid issue, paper over an unmet AC, or trust an issue's file:line / API claims without verifying them.
- It does **not** parallelize file-editing across worktrees in v1 (parallelism is confined to read-only investigation + review).
- It does **not** count a test that passes on the buggy code as evidence.

## Failure / terminal outcomes (report, don't force)

`issue-not-actionable` (closed/discussion/duplicate) · `issue-invalid` (investigation refutes it) · `gh-unauthenticated` · `dirty/colliding-worktree` (resolve or rename) · `tests-cannot-run` (env broken) · `codex-unavailable` (substitute a Workflow red-team) · `pr-create-failed` (report branch + command). Each ends THIS issue's pipeline cleanly and moves to the next issue.
