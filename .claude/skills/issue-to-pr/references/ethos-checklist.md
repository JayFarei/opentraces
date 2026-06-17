# Ethos checklist — Stage 3a (self/ethos adversarial pass)

Score the **plan** against each rule below: ✓ (aligned), ❌ (violates — must resolve before coding), or N/A. This is your own adversarial pass; be harsh. It is grounded in the opentraces "Key Decisions" (CLAUDE.md) so it scores against real, repo-specific direction — not vibes. The codex pass (3b) is the independent external check; convergence between the two on the same defect is the strongest signal.

## Product direction & architecture

1. **Extend, don't silo.** Does the fix slot into an existing verb/group/registry/pattern (e.g. `IMPORTERS`, `get_hook_installers()`, the security tool registry, a `setup` subcommand, an existing `core/` module) rather than inventing a parallel surface? New top-level surfaces need a real reason. *(Key decision: "Extend existing CLI patterns, don't silo new features.")*
2. **Right altitude / scope.** Is the change scoped to the issue, not a speculative refactor or a feature the issue didn't ask for? Resist hypothetical future needs (pragmatic minimalism).
3. **Reuse existing reversers/helpers.** If the fix is the inverse or sibling of something, does it reuse the existing implementation rather than reimplement it? (#85 inverted `integration_repair`; it didn't rewrite the reversers.)

## Data safety & correctness

4. **Data-safe by default.** Does the default path preserve captured data (bucket, datasets, projects, staging, git refs)? Is anything destructive gated behind an explicit flag + confirmation, and is "unrecoverable" stated honestly (incl. remote copies that survive)?
5. **Reversible / additive.** Is the change additive where possible? For schema: MINOR/PATCH must be additive; a breaking change needs MAJOR + a registered migration (VERSION-POLICY). For files/markers/hooks: surgical edits over blind overwrites; preserve user-owned content.
6. **Frozen consumer contracts.** If the change touches a frozen `opentraces.*.v1` envelope, the schema features map, the dataset/bucket layout, or otbox assertion shapes, is the version bumped and are all consumers updated? Does it avoid an enum/field rename that breaks downstream positional/keyed consumers?

## Honesty, not theater (the load-bearing cultural rule)

7. **Honesty, not theater.** Does the change report what it did/didn't do truthfully — skips with reasons, residue left behind, partial failures as errors (not silent success)? The two-class skip taxonomy (`not-installed` is OK; `error`/`unsupported` fails the aggregate) and "report residue, don't hide it" are house style. A reverser that returns ok=True without doing anything is a **silent no-op** — a bug.
8. **No silent caps / no fabricated success.** If the plan bounds coverage (top-N, sampling, no-retry) does it log what was dropped? Does it refuse to claim an unmet AC is met, or to fix an invalid issue?
9. **Red before green.** Does the test-completeness proof demonstrate the test FAILS on the buggy code (red-before-green / reintroduce-the-bug)? A test that passes on the pre-fix code is **green theater** — it proves nothing.
10. **Acceptance is an otbox journey.** For any user/agent-observable change, does the plan add or extend an otbox journey (`tests/otbox/catalogue/journeys/*.toml`) that drives the real CLI/agent and asserts on real state — not just a unit test? Tier-0 journeys run in default CI; tier-1 are documented as opt-in. If no journey applies (pure-internal change), is that stated and justified rather than skipped silently?

## Process & safety rails

10. **Opt-in for heavy/external tools.** New security/AI/network dependencies are opt-in (`setup <tool>`), default-off, and degrade gracefully when absent — never a hard new default dependency.
11. **Deterministic where it claims to be.** Enrichment/attribution/derivation that the product calls deterministic stays deterministic (no hidden LLM in a "deterministic" path) unless the issue is explicitly about an LLM-rendered consumer.
12. **Human-gated & worktree-isolated.** The change ships via a worktree → PR → human merge; nothing auto-merges, and the work never mutates the user's main checkout out from under them.
13. **Workflow-first for "trace data at a place/time."** If the issue is "get trace data to a destination" (PR body, Slack, dashboard, CI), is it a workflow + consumer/renderer rather than a bespoke one-off command? *(Key decision: workflow→consumer pattern.)*

## Verdict

- All ✓ (or justified N/A) → the plan is ethos-aligned; proceed to 3b (codex).
- Any ❌ → revise the plan and re-score the affected rules before proceeding.
