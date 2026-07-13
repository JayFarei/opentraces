# CONTEXT — opentraces CLI interfaces refactor (human + agent)

> Single-context domain doc for the holistic CLI re-grammar. Seeded by a `/grill-with-docs` session on 2026-06-24. Pairs with the ADRs in `docs/adr/` and the agent-side design at `experiments/axi-integration/DESIGN.md`. Source threads: GitHub issue [#129](https://github.com/JayFarei/opentraces/issues/129) (human porcelain v6) and axi.md (agent ergonomics).

## The mission

The opentraces CLI has grown to a **hostile surface for both audiences**. This effort is one holistic refactor that makes the CLI *fit for purpose for both*:

- **Human:** simple, progressive disclosure, learn-as-you-use — the command tree teaches the product ("traces are the new source code").
- **Agent:** thrives in both *discovery* and *usage* — low token cost, few turns, self-describing, recoverable.

These are not two projects. They are two **projections of one command surface** — the same "capture once, project many" philosophy that defines the product itself, turned inward on its own front door.

## The two efforts being unified

- **#129 — porcelain v6 (human half).** 16 groups / ~96 verbs → ~10 groups / ~28 verbs by *hiding* (not removing) machinery. `hidden ≠ removed`; demoted verbs stay callable + `--json`-scriptable. No schema / `bucket_digest` change. Its embedded audit (7-agent, read-only) found: the porcelain verbs **don't exist yet** (`trace show`, `trace diff`, `bucket sync`, `workflow new`, `capsule verify`, `config get/edit`), and otbox coverage **proves registration, not behavior** (most journeys assert `--help` only).
- **AXI integration (agent half).** Adopt the agent-ergonomic principles from axi.md: compact/TOON output, minimal default schemas, content-first home views, a unifying response envelope with `next_steps`, structured errors, a machine-readable command manifest, ambient context. Full design + the four locked decisions: `experiments/axi-integration/DESIGN.md`.

## The collisions that force unification (why neither half ships alone)

1. **Discoverability is opposite-valanced.** Humans want fewer visible verbs; agents want a complete map *including* hidden plumbing + next-step templates. "Hidden from `--help`" must not mean "undiscoverable by agent." → two discovery channels over one surface.
2. **Dependency direction.** The agent half's `next_steps`/manifest emit command templates; #129 renames the verbs underneath them. The agent surface must teach the *porcelain* names, so renames land first (or together).
3. **Shared test debt.** Both halves need real-execution otbox journeys; today's are `--help`-only stubs. Paid once, for both.

## Glossary (shared vocabulary)

- **Surface** — the single set of commands/verbs the CLI registers. There is exactly one.
- **Projection** — a rendering of the surface for one **interaction mode** (not one audience — see ADR-0002).
- **Interaction mode** — *non-interactive* (`--json`/non-TTY/scripted/piped; AXI-governed; never prompts) vs *interactive* (TTY; progressive disclosure; may guide/prompt). The projection axis.
- **Job / JTBD** — a unit of user/agent intent ("which sessions produced this commit?"). The unit of design; verbs serve jobs. The minimal verb set is anchored to the job taxonomy. (A `jtbd-command-map` SSoT exists but is stale per #129's audit.)
- **NL→agent→CLI path** — a human completing a task by talking to an agent in natural language; the agent drives verbs via the skill. The skill must procedurally capture units-of-tasks for this to work.
- **Acceptance testing (user-AT / agent-AT)** — otbox captures both: a human's interactive task completion, and an agent's non-interactive journey. The progress ledger for the refactor.
- **A/B arm** — baseline (current CLI) vs treatment (consolidated + AXI), measured for success + efficiency.
- **Porcelain / Plumbing** — porcelain = the visible, concept-teaching front door; plumbing = machinery, hidden from `--help` but callable and (for agents) manifest-listed. (`hidden ≠ removed`.)
- **Progressive disclosure** — the human-projection principle: surface only what a newcomer must learn to grasp the mission; reveal depth on use.
- **Discovery turn** — an agent tool-call spent figuring out *which command to run next* rather than doing work. The agent projection aims to drive these toward zero.
- **Unifying envelope** — the one additive JSON shape every `--json` command converges on (`status`/`count`/`total`/`<data_key>`/`truncated`/`reason`/`help`/`next_steps`/`error`). See DESIGN.md §5.
- **next_steps contract** — runnable, fully-substituted next-command templates emitted in the agent projection (carry active flags forward; suppress when source value is null).
- **Manifest** — `completions manifest --json`: a runtime-derived map of the *entire* surface (incl. plumbing) — the agent's discovery channel, the analogue of the human's curated `--help`.
- **Frozen envelope** — a versioned downstream-consumer contract (`opentraces.*.vN`). Shape changes require a `schema_version` bump; output *encoding* changes (compact/TOON) must not mutate them.
- **The three records** — Trace (what a session *did*), Trail (what *changed/survived*), Ctx (what the model *saw*). The product's core mental model; each is a sibling group.
- **Substrate** — an append-only source of truth (Trace / Trail / Ctx) over the canonical event log. Distinct from a projection.

## The three workstreams (per ADR-0002)

1. **Verb consolidation** — the minimal verb set, each verb keyed to the job(s) it serves (the JTBD spine).
2. **Interactive UX** — the TTY experience of each verb against its jobs (progressive disclosure, learn-as-you-use).
3. **AXI conformance** — each agent journey through the verbs satisfies the 10 non-interactive principles.

Cross-cutting: the **skill** encodes units-of-tasks for the NL→agent→CLI path; **otbox** captures user-AT + agent-AT; **A/B** (baseline vs consolidated+AXI) measures success + efficiency.

## Settled (see ADRs)

- ADR-0001 — Measure the hostility before prescribing the cure.
- ADR-0002 — One surface, two modes (interactive / non-interactive); AXI governs non-interactive; minimal verbs anchored to JTBD; A/B-via-otbox measurement.
- ADR-0003 — Agent-mediated weighting (highest usage + the human's first-impression/learning channel); **one shared verb taxonomy + roots across both user types**; the only divergence is **affordance depth** (agent gets more options / nested options / hidden commands); teaching lives in the skill.
- ADR-0004 — Program structure: **#129 promoted to the umbrella epic**, 5 sequenced phases (0 foundations+baseline · 1 lock taxonomy · 2 facade · 3a human ∥ 3b agent-AXI · 4 skill); **scoping-map-first** (decide taxonomy from evidence); GitHub restructure after the map.
- ADR-0005 — Agent-ergonomic **consistency** (Cloudflare `cf` role model): verb-noun grammar, one fixed lint-enforced verb vocabulary, the **irreducible-user-journey** test, *don't build what the agent already has* (dropped `schedule`), generated manifest, universal `--json`.
- ADR-0006 — **Phase-1 LOCKED taxonomy.** Canonical verbs (`query/list/get(+--as)/diff/create/delete/run/publish/sync/status/doctor`, `--force`, `--remote`, `--json`) + domain verbs (`blame/track/graph/resume/review`); 10 roots; picks: `get`, Cloudflare lifecycle set, `run` folded. Full surface in the ADR.

Evidence: `docs/TAXONOMY.md` (7-area verified scoping map + 2 reduction rounds + A/B plan). #129 restructure draft: `experiments/axi-integration/EPIC-129-RESTRUCTURE.md`.

## Key inputs (grounding)

- Live CLI: `src/opentraces/cli/` + `.venv/bin/opentraces`. ~155 commands / 7 areas.
- JTBD SSoT (stale — reconcile): `tests/otbox/jtbd-command-map.md` (+ `jtbd.py`, `test_jtbd_ssot.py` CI gate).
- v6 internal design: `kb/plans/100-cli-v6-mission-aligned-redesign.md`.
- #129 issue body (proposed porcelain) + its 7-agent alignment audit (coverage = registration-not-behavior).
- 228 otbox journeys: `tests/otbox/catalogue/journeys/*.toml`.

## Open / parked

- **The concrete minimal shared taxonomy** — Phase 1, decided from the scoping map (in progress).
- **Q2 (parked):** A/B arms + success/efficiency metrics + thresholds — what measured result kills a P0, for *each* user type?
- **What "interactive" includes:** rich output only, or prompts/wizards too? (resolve during scoping)
- **"we see a line"** — baseline / timeline / throughline? (awaiting clarification)

## Language — learning-loop strategy (seeded by the 2026-07-03 grill)

> Product/strategy domain vocabulary, distinct from the CLI-refactor vocabulary above. Split into its own context doc if it outgrows this section.

**Learning Loop**:
The conversion of an institution's own agent experience into accumulated, verifiable judgment. Runs iff the action-time record captures **intent, observation, action, and environment**, every verdict over it is **verifiable** (enters as a claim, priced by calibration), and the trajectory is **replayable** (perturbable). The inner loop verifies at action time; the outer loop is the world's verdict on its own clock.
_Avoid_: flywheel (marketing), training loop (only the last rung).

**Learning Surface**:
A locus where agent-mediated experience accumulates and a learning loop can compound. Instances: an application with agentic users, a codebase built by agent-mediated developers (the current wedge), an enterprise workflow run by agent-mediated employees.

**Learnable Artifact**:
The asset a learning loop improves: any artifact that requires grading and passes the admission test (it has an address, an oracle, a world signal, and a replay story). Code-surface instances: skills/procedures, context/memory inclusions, verifiers themselves, autonomous-loop policy, runtime governing tasks, RL reward targets; model weights deliberately last.
_Avoid_: "the model" as the default learner.

**Harvested Environment**:
An environment lifted from captured production work — trajectory + oracle + perturbation-response — at near-zero marginal cost, trust-priced per replay tier. Contrast: an **authored environment**, built synthetically per task (HUD's unit, $200–2,000/task external estimate).
_Avoid_: synthetic benchmark.

**Phases of Utility**:
The lifecycle of one harvested environment: evidence → eval/grading → guidance/search → training substrate. One asset, monetizable per phase.

**Oracle**:
A verifier whose verdict grades attempts inside an environment. An oracle's output always enters the record as a claim (issuer, evidence, maturation, trust state), never as asserted ground truth.

**Evidence-engineered sample efficiency**:
Lowering the observations needed per learning advancement through evidence quality (calibration removes wrong-signed labels, replay multiplies episodes from one session, wire-fidelity observation eases credit assignment), not through optimizer research (adopted, never owned). Replay multiplies inner-loop samples only; outer-loop labels are minted by the world alone.

**Executable Theory**:
The human-aligned, runnable statement of what good means for an artifact: intent, checks, defeaters, stakes, escalation. Alignment made executable; the source from which claims are issued. (From loopverify.)
_Avoid_: bare "alignment" (RLHF connotation), spec-as-document.

**Late Verifier**:
A world signal treated as a verifier whose verdict arrives on the world's clock (survival, CI failures, incidents, tickets). It never grades in-line; it reprices the early verifiers.
_Avoid_: treating world signals as action-time ground truth.

**Environment**:
A built, executable, gradeable place to act, harvested from captured work or authored, always trust-priced. We build environments.
_Avoid_: "world" for anything built.

**World**:
The real outcome source outside every environment, never modeled, only listened to; it holds the last word.
_Avoid_: "app world" for reconstructed environments (rename candidate in loopverify).

**Loop Credit**:
The single billing currency for loop usage. Operations (environment hours, reconciliations, artifact gradings) consume credits at rate-card prices; no operation's credit cost ever depends on its verdict (ADR-0008).
_Avoid_: seats, per-token pricing, success fees.

**Environment Template**:
A per-product-type loop recipe in the factory catalog: the interaction surface (native capture), the executable-theory scaffold, the world-adapter set, and the reachable replay tier. Ships only after its loop is proven on a real product (ADR-0009). Lineage: code surface (opentraces, proven), runtime search (datafetch, near-proven), traditional software and API product (envrun, unproven).
_Avoid_: boilerplate, starter kit (a template is a loop recipe, not scaffolding).
