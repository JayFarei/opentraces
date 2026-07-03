# ADR-0008 — The seal-family contract + corrected trust lattice

- Status: Proposed (lattice pending human ratification at the M1 checkpoint of #178)
- Date: 2026-07-02
- Context thread: PRD #178 (seal-family train), milestone #179, design session 2026-07-02; lattice draft from #154 / #146 (VERIFIER §6)

## Context

The bucket captures rich agent evidence, but everything built ON that evidence is weaker than the evidence itself. Datasets record which workflow and bucket state produced their rows, yet nothing can replay that function to prove the rows reproduce. The workflow engine is duplicated (a dataset-bound runner and a dataset-free primitive with two run-packet shapes), one of its three executors is permanently dead, and agent execution breaks reconstructability by construction because the LLM's output is not a recorded input. Shared projection logic inherits the operator's full environment, tokens included. Capsules predate the v7 grammar, their companions leak by tail-probe, and a replay can claim "fixed" with no machine-readable signal of how little that verdict deserves trust. Egress has three doors with different locks.

Every one of those defects is an instance of the same missing contract. This ADR states the contract once, so every later decision in the train (#179-#201) is an application of it, not a new negotiation.

## Decision

### 1. The contract

A projection is **explainable** iff it is a pure function of content-addressed inputs:

```
projection = f(scope_ref, transform@digest, bucket_state@digest, answers)
```

- `scope_ref` — a v7 address (`trace`, `trace:step`, `trace:A-B`, or a bucket-wide scope), never a convention-scraped field set.
- `transform@digest` — the workflow package pinned by content digest, with the integrity invariant **digested bytes == installed bytes == executed bytes**.
- `bucket_state@digest` — the bucket manifest digest (plus per-row watermarks where a delta is projected).
- `answers` — recorded judgments. Judgment is not an exception to determinism; it is an INPUT. A workflow that needs judgment emits structured JudgmentRequests and exits `rc=10` (the slicing precedent); the answers are persisted in the run packet; re-running with the same answers is byte-identical.

### 2. Two seals, exactly

- A **dataset** is a growing, reviewed seal: workflow-projected rows with per-row provenance carrying the full contract triple, appended under review gates.
- A **capsule** is an immutable, URL-addressed seal: a redacted mini-bucket of one scope, byte-stable under re-seal, with a deterministic `capsule_id`.

Nothing else seals. A PR body, a standup, a dashboard are *renderings* of projections, not seals; they own no storage contract.

### 3. One clearance predicate on every egress

Exactly one predicate decides whether a trace's bytes may leave the private bucket, extracted from the `bucket sync push` gate (#174) and adopted by dataset publish and capsule publish. Egress is never on by default; a refusal moves zero bytes. The predicate is evaluated against a push-time snapshot (no check-then-copy races).

### 4. The honesty-label rule

A projection or replay MAY exist un-proven, but may NEVER claim otherwise. Every run result carries `reconstructable: true|false`; every replay packet carries `verdict_trust` computed by the clamp below; every capsule declares what it cannot prove (`env_tier=L0`, floor verdicts, limitations) in front-matter. Honesty labels only ever go green by lowering a claim or by raising the underlying real state — never by softening the label.

### 5. The corrected trust lattice (ratification table)

`verdict_trust = OUTPUT[min(pos(oracle_trust), pos(env_tier), pos(diff_trust), pos(sandbox_tier))]` where `OUTPUT = {0: "floor", 1: "low", 2: "medium", 3: "high"}`. The four factors live in incommensurable vocabularies; each maps onto one shared lattice position:

| position | oracle_trust | env_tier | diff_trust | sandbox_tier | → verdict_trust |
|---|---|---|---|---|---|
| **0** | `none` | `L0` | `unanchored`, `file_list_only` | `none` (S0) | **`floor`** |
| **1** | `intent_reposed` | `L1` | `partial` | `jail` (S1) | **`low`** |
| **2** | `captured_pass`, `captured_error` | `L3` | — | `container` (S2) | **`medium`** |
| **3** | `declared` | `L4` | `exact` | `microvm` (S3) | **`high`** |

Each factor defaults to its floor value when absent, so an un-upgraded producer's capsule reads as floor, never as a silent over-claim. This table freezes on ratification; any later position change, factor addition, or vocabulary change is a `schema_version` bump on the replay envelope.

The four known defects in the #154 draft, resolved:

1. **The L2 rung is deleted, and the deletion is documented here.** The env ladder's tier names are historical labels with published meanings — `L0` name-only, `L1` resolved pins, `L3` vendored wheels (hermetic on a matching platform), `L4` OCI image (cross-platform hermetic). The historical `L2` (committed-lockfile carry) collapsed into `L1`: a carried lock without resolution is just another source of resolved pins, indistinguishable in trust from a resolver-emitted pin set. We keep the surviving names unrenumbered (renumbering would silently re-meaning every existing reference in #146/#155/#202); the gap in the numbering is deliberate and this paragraph is its record. Any future intermediate tier takes a NEW name and a `schema_version` bump — `L2` is never reused.
2. **`diff_trust: exact` sits at position 3, resolving the table-vs-acceptance contradiction.** The #154 draft put `exact` at position 2, which made `high` arithmetically unreachable (min over four factors, one capped at 2) while its own acceptance example asserted `clamp(declared, L3, exact, microvm) == "high"`. The principle: a factor's strongest vocabulary value means "this factor does not degrade the verdict" and therefore sits at the top position. `exact` — the carried diff bounds exactly the sealed slice — is the strongest claim a diff can make. Positions may be sparse (`diff_trust` has no rung at 2); `min()` is over positions, not over dense columns.
3. **`high` is reachable, and its bar is deliberate: `declared` + `L4` + `exact` + `microvm`.** The #154 acceptance example claiming `high` at `env_tier=L3` was WRONG and is corrected: `clamp(declared, L3, exact, microvm) == "medium"` (L3 is position 2 — vendored wheels are hermetic only on a matching platform, a real fidelity gap on cross-machine replay). `high` requires the cross-platform hermetic env. The corrected acceptance set is: `clamp(declared, L4, exact, microvm) == "high"`; every single-floored factor floors the verdict; today's corpus floors everywhere.
4. **`declared` stays above `captured_pass`/`captured_error`, with the rationale recorded.** `oracle_trust` measures the fidelity of the grading contract to the episode's intent, not the oracle's observational history. A declared test is an explicit commitment — "this command grades this episode" — made by the party sealing it. A captured test is an inference from observation: it demonstrably ran, but its selection as *the* oracle is guessed, and for the success-session majority the captured test may be incidental to the intent. Both captured forms share position 2 (`captured_error` is the classic repro oracle, `captured_pass` the observed green bar; neither carries a commitment). The known cost — a declared test may never have executed at seal time — is mitigated operationally, not by reordering: seal-time execution of a declared test additionally stamps `captured_pass`, and the exit-126/127 env-differs guard voids verdicts from environments that cannot run the oracle at all.

**Properties surface (the PRIMARY read).** The replay packet leads with four named PROPERTIES — `reproducible`, `gradable`, `scoped`, `sandboxed` — each a `{ok, level, note}` triple DERIVED from its lattice-ranked ordinal above (`reproducible` ← `env_tier`, ok at `L3`/`L4`; `gradable` ← `oracle_trust`, ok at `captured_pass`/`captured_error`/`declared`; `scoped` ← `diff_trust`, ok at `exact`; `sandboxed` ← `sandbox_tier`, ok at any non-`none` tier). The properties are the plain-language surface a human or agent reads; `verdict_trust` — the `min` over the four positions — remains on the packet as the DERIVED, secondary weakest-link summary for automation thresholds. The lattice table and the `min` computation are UNCHANGED: they define the ordinal positions each property exposes. This keeps the future-proofing intact — each property auto-upgrades the moment its underlying factor rises (env via `#202`, oracle/diff via the seal, sandbox via a real tier) with no envelope change, because a property is a derivation over the ordinals, not a stored field. The reshape is additive within `opentraces.capsule_replay.v1` (the `properties` block is new optional keys; `verdict_trust` stays), so no `schema_version` bump.

Bundle safety is a publish GATE (block on secret findings), never a trust factor: a secret-bearing bundle does not degrade to a lower tier, it does not leave.

## Consequences

- M1 builds the substrate to the contract: one engine (`execute_workflow` as the single execution seam), one versioned run packet, the rc=10 judgment handshake, the shared clearance predicate, the isolation primitive, and companion sanitization (#143). M2 makes both seals real (dataset lineage/sync/verify; capsule create/get/import + preview + clearance-gated publish). M3 lands the clamp on this ratified table, slice-scoped `diff_trust`, oracle widening, bundle gate + sandbox v1, and the additive Environment schema fields (the train's one MINOR bump).
- On today's corpus every honest capsule reports `verdict_trust: floor` and refuses "reproducible" — that is the system working, not a gap. Trust rises only when a sibling raises real state (`#202` resolver → L1, wheels → L3, microVM → L4/S3).
- The acceptance examples in #154 are superseded where they contradict this table (defect 3); implementations test against THIS table.
- `capsule get` stays read-only and stateless; `capsule import` is the explicit opt-in write. Egress remains off by default everywhere.
- A future factor (e.g. an attestation tier) or vocabulary change requires a `schema_version` bump and a fresh ratification.

## Alternatives considered

- **Keep `exact` at position 2 and declare `high` unreachable-by-design.** Rejected: it permanently caps a maximally-proven replay (declared oracle, OCI-hermetic env, exact diff, microVM) at `medium`, which misreports real state — exactly the dishonesty the lattice exists to prevent, in the conservative direction.
- **Renumber the env ladder L0-L3 to close the L2 gap.** Rejected: silently re-means every existing reference to `L3`/`L4` across #146/#155/#202 and any captured artifact; a name that changes meaning is worse than a gap.
- **Rank `captured_error` above `declared`.** Rejected for the grading-contract rationale in defect 4; revisit only with evidence that declared oracles over-claim in practice (the ratification record is the place to overturn it).
- **Make bundle safety a fifth trust factor.** Rejected: safety is binary and gating (leak = never leaves), not gradational; folding it into trust would let a "clean bundle" raise a verdict it has nothing to do with.
- **Per-consumer clearance predicates (sync vs dataset vs capsule).** Rejected: three locks on three doors is the present defect; divergence is guaranteed drift.
