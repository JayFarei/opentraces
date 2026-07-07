# ADR-0009: The event-sourcing contract (event-first writes, registered projections)

- Status: Accepted (as the normative contract for all new and refactored evidence/read-model code; retrofit tracked by #241, #242, #246)
- Date: 2026-07-07
- Context thread: architectural review of epics #241 (T3, lineage and capture truth) and #242 (T4, scale and read-path health), 2026-07-07; sync-functionality review grounding #246 (cloud-first bucket); review comments on #241 and #242 carry the file:line evidence

## Context

opentraces is architected as an event-sourced system. The canonical append-only log (`refs/opentraces/local/events/v1`) is declared the source of truth, and TraceRecord fields, manifest counts, indexes, caches, and lineage projections are declared rebuildable derivations of it. Until this ADR, that doctrine existed only as prose scattered across plans and CLAUDE.md. No mechanism enforced it, and the drift was measurable:

- Two anchor writers touched the same TraceRecord: one emitted `git_anchor_created` events and derived the field, the other stamped `Patch.anchor` directly with zero events, so `anchored_count` and `anchors_by_trace` could disagree while both were faithfully reporting their own store (#139).
- A committed, edit-bearing trace could carry zero Trail events with no witness marker, because the step-window emitter silently skipped foreign-worktree tool calls and its caller discarded the skip report (#237).
- Cache-shaped rows were written INTO the immutable canonical log (`patch_survival_cached`, ~505K legacy events), because `append_event_batch` accepts any event type with zero validation (#116A; cured procedurally in `survival_cache_store.py`, still structurally unprotected).
- Eight derived read models (search snapshot, bucket manifest accelerator, search projection, legacy trace index, event-log snapshot, event index, survival cache, bucket digest) each re-solved dirty/drain/reclaim independently. The watcher tick drained none of them; doctor saw only some; one builder ran on every ingest with zero readers; four status surfaces contradicted each other after a security pass until a manual repair (#235, #236, #114, #204).
- The two NEWEST stores (`core/trails/event_index.py`, `core/trails/survival_cache_store.py`) each independently invented the correct lifecycle pattern without anyone unifying it, proof that the missing piece was a contract, not capability.

Every defect in both epics is an instance of one of two missing laws. This ADR states them once, so later work applies them instead of re-negotiating them.

## Decision

### Law 1 (write side): evidence is event-first

Every evidence claim lands in the canonical append-only log as an event BEFORE it appears anywhere else. Record fields, manifest counts, and lineage surfaces are projections computed FROM events, never independently authored.

Corollaries:

1. **One writer per fact.** A fact (e.g. "this patch is anchored to this commit") has exactly one write path, and that path emits the event. A second code path that reaches the same conclusion by a different algorithm and stamps a field directly is a defect, even when its conclusion is correct.
2. **Witness-or-nothing.** When evidence cannot be recorded (foreign worktree, missing hook metadata, unresolvable repo), the writer emits an explicit skip/incomplete witness event and surfaces the skip count. Silence is never an acceptable failure mode; a committed trace must never be indistinguishable from an untouched one.
3. **Admission control.** `append_event_batch` accepts only witness-shaped event types (an explicit allowlist). Caches, accelerators, and perf hints live in local evictable stores, never on the canonical ref.
4. **Declared channels only.** A producer's output flows through its declared return value or the event log, never through instance state a caller reads back out-of-band (the `step_anchors` rule, #117).
5. **Derivability is checkable.** For every trace, record-store claims must be derivable from event-store facts (or an explicit skip witness). This invariant is enforced by a PR-gated cross-store probe (#140), not by convention.

### Law 2 (read side): every projection is registered

Every derived read model (index, cache, accelerator, snapshot, local materialization of remote state) implements one lifecycle contract, registered in `core/projection_registry.py` with five slots:

1. `name`
2. `source_token_fn`: the cheap version signal of its source (event-ref head, manifest dirty-marker token, or, for remote-backed materializations, the HF repo revision)
3. `freshness_fn`: compare source token to what the projection was built from
4. `rebuild_fn`: recompute from source, safe to call anytime
5. `reclaim_fn`: evict/delete, safe because rebuildable

Corollaries:

1. **One drain owner.** The watcher tick drains dirty registered projections; no projection depends on a user remembering a rebuild command.
2. **One visibility surface.** `doctor` reports every registered projection's freshness; a store that can go stale invisibly is a defect.
3. **One convergence command.** `bucket repair` = rebuild-all over the registry; every surface that detects staleness names it.
4. **Digest-excluded by construction.** Projections never feed `bucket_digest` or any cross-machine identity; they are derived, local, and disposable.
5. **Prefer inline self-heal.** New projections should follow the `event_index.py` pattern (head comparison + rebuild-on-miss at the read site, incremental extension at the single write chokepoint); the mark-and-drain pattern (`DirtyMarker`) is reserved for out-of-band writers that cannot cheaply recompute their own signal.

### Why the two laws are one contract

Law 1 makes the event log the sole source of facts; Law 2 makes everything derived from it honest about freshness and rebuildable. Together they are what "the log is canonical, everything else is an advisory projection" means mechanically. They are also the precondition for the cloud-first bucket (#246): with N machines writing, append-only events are the only data that merges cleanly (Law 1 as the convergence model), and a partial local materialization of a remote bucket is only correct if it is a freshness-keyed registered projection (Law 2 as cache coherence).

## Consequences

- The retrofit is tracked by #241 (Law 1 instances: single anchor-writer seam, witness-not-silence, parser return channels, cross-store probe), #242 (Law 2 instances: projection registry, watcher drain, doctor visibility, repair convergence, builder collapse), and #246 (both laws under cloud-first: lanes, registry-backed materialization, HF-native Merkle digest, atomic allow-list push). The review comments on #241/#242 (2026-07-07) carry the per-defect evidence and the corrections to stale claims in those epic bodies.
- New code is held to the contract at review time: an evidence write without an event, a silent skip, a new event type outside the allowlist, or a new cache without a registry entry is a correctness defect, not a style preference.
- The contract deliberately does NOT require rearchitecting existing stores that already satisfy it (`event_index.py`, `survival_cache_store.py`); registration is for visibility, not correction.
- Trust downstream depends on this: the claim-class ladder's rung 1 (witness truth) and every grading/reward/router-training consumer read the capture chain these laws make self-consistent.

## References

- Review comment on #241: https://github.com/JayFarei/opentraces/issues/241#issuecomment-4905055429
- Review comment on #242: https://github.com/JayFarei/opentraces/issues/242#issuecomment-4905060378
- Cloud-first bucket epic: https://github.com/JayFarei/opentraces/issues/246
- ADR-0008 (seal-family contract): the egress/explainability contract this ADR composes with; ADR-0008 governs what may leave the bucket, ADR-0009 governs how truth is written and read inside it.
