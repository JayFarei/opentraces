# Rationale: schema 0.8.0

## Summary

0.8.0 adds the additive schema home for exact dependency pins plus interpreter and CPU-architecture identity on the `Environment` model (issue #200). It is a purely additive MINOR bump: two new nested optional BaseModels (`PinRecord`, `Interpreter`) plus five new optional fields on `Environment`, all absent by default. Every existing record validates unchanged, no migration is registered, and the published `TraceRecord` wire shape gains only optional keys.

This is the seal-family M3 train's single sanctioned schema change (`0.7.0 -> 0.8.0`).

## Why these fields belong in the schema

A capsule is supposed to be *replayable*, but today the schema cannot even express a reproducible environment. Dependencies are captured name-only (`requests`, not `requests==2.31.0`), and there is no record of which interpreter or which CPU the original session ran on. Until the schema has a place to *hold* exact pins, no resolver can fill them and every environment tier is stuck at the floor.

Issue #155 split this work into two independently-shippable parts:

- **Part A (this bump):** add the FIELDS — the structured home for pins and runtime identity. No behaviour, no resolver, no `env_tier` change.
- **Part B (the follow-up):** the resolver that computes the exact pins and threads the captured interpreter into replay, lifting `env_tier` from `L0` to `L1`.

Part A is deliberately additive and independent so the version-policy review proceeds ahead of, and in parallel with, the still-open resolver-home decision.

## Design decisions

### `PinRecord`, not `list[str]`

A resolved pin is captured as a structured `PinRecord { name, version?, hash?, marker?, source? }`, not a bare `name==version` string. The extra optional fields let a pin carry a wheel `hash` (for the future L3 hermetic path), a PEP 508 environment `marker`, and its resolver `source` — so the L3 work needs no second schema bump. `name` is the only required field; a resolver that knows a dependency but not its version can still record it.

### `Interpreter` nested model, not a bare `python_version`

Interpreter identity is a nested `Interpreter { name?, version? }` rather than a single `python_version` string, so a non-CPython runtime (pypy, etc.) is expressible without a later rename. `arch`, `platform`, and `abi_tag` are flat scalars because they are single opaque tags (`arm64`, `macosx_14_0_arm64`, `cp311`) that map the L3 wheel-platform-specificity boundary.

### All fields default absent (`None`)

`resolved_dependencies` and `interpreter` default to `None` — the honest "not resolved / not captured" sentinel, distinct from an empty list that would falsely claim "resolved to an empty closure". Because every new field is absent by default, a pre-0.8.0 record round-trips with the new fields reading back as `None`, and the model-driven HuggingFace features map declares them additively.

## Honesty boundary — a home, not a trust lift

The load-bearing constraint: **the fields' mere presence must not raise any trust tier.** `verdict_trust` is the minimum over four factors, one of which is `env_tier`. Adding a home for pins is not the same as pinning them: `env_tier` is DERIVED downstream (`core/capsule`) and stays at its `L0` floor across the entire M3 stack, because the resolver that would lift it is the out-of-train follow-up. On today's corpus every honest capsule therefore still reports `verdict_trust=floor` and refuses "reproducible" — the intended honest outcome, not a gap.

This is enforced structurally: no model in the schema package carries an `env_tier` / `verdict_trust` / `oracle_trust` / `diff_trust` / `sandbox_tier` field. The trust vocabulary lives entirely outside the schema; the schema only holds the resolver's future *inputs*.

## Additivity against VERSION-POLICY.md

Per `VERSION-POLICY.md`, a MINOR bump is "new optional fields, new nested optional BaseModels" and must be strictly additive. This bump is exactly that:

- New nested optional BaseModels: `PinRecord`, `Interpreter`.
- New optional fields on `Environment`: `resolved_dependencies`, `interpreter`, `arch`, `platform`, `abi_tag`.
- No existing field is renamed, moved, removed, narrowed, or restructured.

Because the change is strictly additive, the CLI auto-migration contract is satisfied without a registered migration: auto-migration only rewrites strictly-older rows, and there is nothing to reconstruct — the new fields simply read back absent on any record that predates them. `migrate_record` is a transparent no-op across the `0.7.0 -> 0.8.0` boundary, exactly as it was across `0.6.0 -> 0.7.0`.

## bucket_digest byte-identity

The bump is byte-safe for `bucket_digest` on the rebuild-without-rewrite path. The cross-machine `bucket_digest` reads STORED per-record hashes and a per-trace count-summary that contains no Environment content, and the manifest's own `schema_version` in the digest material is the bucket-manifest schema string (not this package's `SCHEMA_VERSION`). So rebuilding a manifest from an existing bucket after the bump reproduces the same digest. A full re-serialization (`bucket repair` / `sync_trace_records_from_local_stores`) would legitimately emit the new null-default fields and recompute write-path hashes — that is correct new-data behaviour and is deliberately excluded from the byte-identity guard.

## Compatibility

- Existing records (no new fields): validate unchanged; new fields read as `None`.
- New records: may carry pins and interpreter identity; round-trip losslessly.
- HuggingFace: features map derives the new `environment` keys additively; older shards migrate, newer rows are preserved byte-identically.
