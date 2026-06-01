<!--
GOAL (active /goal — plan 089): Prove in the open that upgrading a CONSUMED dependency
flips a trace capsule's verdict reproduces→fixed with zero change to the client's own code,
on both axes: (A) library bump humanduration@v0.1.0→v0.2.0, (B) server-side convert-api
redeploy deploy-v1→v2. Done when a real-trace capsule pulled anonymously from HF reproduces
at the pin, the same capsule via `capsule test --with/--matrix` flips to fixed with resolved_in,
two real GitHub issues (owned JayFarei/* repos) carry the EXECUTED verdict and are CLOSED, and
`capsule watch` reports UNBLOCKED. Preserve frozen capsule.v1 required keys (additive only),
existing capsule tests green, no un-redacted publish. Worktree community-traces-capsule, branch
feat/capsule-dependency-unblock. Spec: kb/plans/089-capsule-dependency-unblock.md.

Per-attempt template:
## Attempt N — <timestamp>
Change: <one-line diff summary>
Evidence: <verification-surface output>
Decision: <next step | COMPLETE | BLOCKED>
-->

# Capsule Dependency-Unblock — Run Log

## Attempt 1 — 2026-06-01 (U0 start)
Change: Created branch `feat/capsule-dependency-unblock` off `feat/trace-capsule-prototype`;
scaffolded this run log; beginning U0 (humanduration fixture world).
Evidence: `git branch --show-current` → feat/capsule-dependency-unblock.
Decision: build the humanduration lib (v0.1.0 buggy / v0.2.0 fixed git tags) + client + a
real captured failing client session, then smoke-test the two versions differ (3600 vs 5400).

## Attempt 2 — 2026-06-01 (U0 done)
Change: Added `tests/fixtures/capsule_dep_unblock/world.py` — deterministic builder for the
humanduration lib (v0.1.0 buggy / v0.2.0 fixed git tags) + the client (app.py + test_delay.py
encoding `delay_seconds("1h30m") == 5400`). Same source feeds the live U5 GitHub repo.
Evidence: smoke test across both tags via real `pip install "humanduration @ git+file://…@<tag>"`
in isolated venvs — v0.1.0: parse('1h30m')=3600 → pytest rc=1 (🔴 reproduces); v0.2.0:
parse('1h30m')=5400 → pytest rc=0 (🟢 fixed). Client source byte-identical across both runs.
Decision: U0 complete; the dependency-version axis is real. Next U1+U2 — add `environment.consumes`
to the capsule envelope and teach `run.py` to install the pinned/overridden consumed dep in an
isolated venv before the test (the `--with`/`--matrix` machinery).

## Attempt 3 — 2026-06-01 (U1+U2 done)
Change: Added `core/capsule/consumes.py` (pure override/version logic: parse_with, parse_matrix,
apply_override, resolve_consumes, consumes_used). Extended `run.py`: `_consumes_setup` ctx mgr
stands up an isolated venv per `package` consume (installs pinned/overridden spec, prepends bin to
PATH) and injects each `service` consume's endpoint as the client env var; `run_capsule_test` gains
`with_overrides`; install/setup failure → inconclusive (never a false reproduce). Result carries
`consumes_used`. Added `tests/test_capsule_dependency_unblock.py` (5 tests).
Evidence: `pytest tests/test_capsule_dependency_unblock.py -q` → 5 passed. Same client bundle bytes
across runs; humanduration v0.1.0→reproduces / v0.2.0→fixed via real venv git-tag install; matrix
loop yields resolved_in=v0.2.0; service consume injects CONVERT_API_URL and an override flips it.
Full capsule suite: 61 passed, 2 skipped (no regression).
Decision: U1+U2 complete. Next U3 (CLI `capsule test --with`/`--matrix`) + U4 (redaction publish guard).

## Attempt 4 — 2026-06-01 (U3+U4 done)
Change: U3 — `capsule test` gained `--with NAME=VER|SPEC|URL` (repeatable) and `--matrix
NAME=v1,v2` (sweep + resolved_in). U4 — added `redaction.ensure_redacted()` (idempotent floor +
gate) and called it inside `publish_capsule` so no build path can emit un-redacted (closes the
freeze_capsule gap). Tests: `tests/test_capsule_publish_redaction.py` (2).
Evidence: CLI on the fixture capsule — default(v0.1.0) 🔴 reproduces; `--with humanduration=v0.2.0`
🟢 fixed; `--matrix humanduration=v0.1.0,v0.2.0` → 🔴v0.1.0/🟢v0.2.0, `resolved_in: humanduration=
v0.2.0` (R3 met). R7: planted 48-hex secret in an un-redacted freeze_capsule envelope is scrubbed
before any upload byte (HfApi monkeypatched); ensure_redacted idempotent. Full capsule suite: 63
passed, 2 skipped.
Decision: U1–U4 complete; the local mechanism is proven end to end through the CLI. Next U5 (live
library proof: push JayFarei/humanduration, export real-trace capsule, publish to HF, issue+verdict+
close+watch) then U6 (live convert-api) then U7 (evidence/journey/docs). U5/U6 are outward —
confirm exact repos/deploys before firing.
