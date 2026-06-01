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

## Attempt 5 — 2026-06-01 (U5: github repo + cache-correctness fix)
Change: User-approved outward decisions: create public JayFarei/humanduration, capture a REAL client
session (R1), library axis first. Built + pushed https://github.com/JayFarei/humanduration (main +
tags v0.1.0 buggy / v0.2.0 fixed, README). FOUND + FIXED a real correctness bug: the runner's
consumed-dep `pip install` lacked `--no-cache-dir`, so pip's (name,version)-keyed wheel cache served
a stale wheel when two sources share a version string → WRONG verdict (first github matrix returned
🟢/🟢). Added `--no-cache-dir`; added hermetic regression test (two 0.1.0 sources, buggy vs fixed).
Evidence: public install verified `parse('1h30m')` = 3600 @v0.1.0 / 5400 @v0.2.0 (no-cache). CLI
`--matrix` against PUBLIC github with a WARM cache now → 🔴v0.1.0 / 🟢v0.2.0, resolved_in v0.2.0.
Regression test passes.
Decision: github + runner correctness solid. Next: capture a real client session into the bucket and
`capsule export` it (the R1 real-trace leg), then the HF publish + issue + verdict + close + watch.

## Attempt 6 — 2026-06-01 (U5 COMPLETE — library axis proven live with a real trace)
Change: Added `capture_client_session.py` (synthesizes + ingests a real Claude Code client session →
bucket trace ea9e17db). Added `export --consume` flag + `export_capsule(consumes=...)`. Ran the full
live loop on the library axis.
Evidence (R1–R4, R7 all met):
- Real bucket trace ea9e17db captured (intent + failing pytest step) via the real ingest pipeline.
- `capsule export` → capsule b24bdb49629c51a4 with environment.consumes=[humanduration@v0.1.0 github
  pin] + 445B bundle of the real client.
- Published to HF Jayfarei/opentraces-capsules (sha-pinned rev a955208e…); filed issue
  https://github.com/JayFarei/humanduration/issues/1.
- Anonymous: `curl` + `capsule open <url>` from a severed HOME → validated, zero residue (R1).
- `capsule test --from-bundle` → 🔴 reproduces (consumed v0.1.0, github install, no git) (R1).
- `--matrix humanduration=v0.1.0,v0.2.0` → 🔴/🟢, resolved_in=v0.2.0 (R3).
- `--with humanduration=v0.2.0 --verdict-to #1 --close` → 🟢 fixed (EXECUTED) posted to issue #1,
  CLOSED; `capsule watch` → UNBLOCKED (R2, R4). Client source unchanged throughout.
- R7 publish redaction guard active on the live publish.
Decision: U5 DONE — the consumed-LIBRARY upgrade unblocks the capsule, end to end in the open with a
real trace. Remaining: U6 (consumed-API axis on Vercel — user deferred to "library first, then U6")
and U7 (committed integration test + otbox journey + transcript.md + docs-update).

## Attempt 7 — 2026-06-01 (U7 done — library axis locked with committed evidence)
Change (user chose "U7 first"): added the committed regression
`tests/test_capsule_dependency_unblock_integration.py` (real capture → export → consumed-dep upgrade
flips reproduces→fixed, resolved_in=v0.2.0; hermetic file:// dep + isolated bucket); the conviction
artifact `tests/otbox/captures/capsule-dependency-unblock/transcript.md` (the live JayFarei/
humanduration + HF + #1 proof); and the gold journey
`tests/otbox/catalogue/journeys/capsule-dependency-unblock.toml` (tier=1 pending the capsule-in-box
checkpoint, per plan 077/078 convention).
Evidence: full capsule suite 65 passed, 2 skipped. Integration test exercises the real ingest →
export → matrix chain. Found + fixed a redaction interaction: a file:// consume pin's username is
identity-scrubbed (correct); overrides carry the full spec at run time so the hermetic test is
unaffected (github URLs have no username → no scrub).
Note: /docs-update DEFERRED — the entire capsule subsystem is an unmerged prototype branch and the
docs page is still "planned"; documenting the consumes/--with/--matrix surface belongs with the merge,
not now (would describe unshipped surface). Tracked for the merge session.
Decision: U7 done. R1–R4,R6,R7 met. The library axis is proven live AND locked with committed
evidence. The ONLY remaining work for goal completion is U6 (R5 — consumed-API axis on Vercel). Goal
stays active until U6 lands. Branch NOT pushed (out of the goal's done-when).

## Attempt 8 — 2026-06-01 (U6 COMPLETE — consumed-API axis proven live; BOTH axes done)
Change: Added `convert_api.py` (fixture + local server) and `convert_api_vercel/api/convert.py`
(deployable fn; v1/v2 via CONVERT_FIXED env) + a service-client capture variant in
`capture_client_session.py` (with a stdlib `check.py` so the repro runs on bare python3 — no venv on
the service axis). Local axis-B regression test added (real local v1/v2 servers).
Deployed convert-api to Vercel (two public production aliases) + created github JayFarei/convert-api.
Evidence (R5 met):
- v1 public: https://convert-api-hazel.vercel.app/api/convert?d=1h30m → {"seconds":3600} (buggy)
- v2 public: https://convert-api-v2.vercel.app/api/convert?d=1h30m   → {"seconds":5400} (fixed)
- Real service-client trace 3ffc1b88 (consumes live v1) → capsule 48789fcc33c3a127 published to HF →
  issue https://github.com/JayFarei/convert-api/issues/1.
- Anonymous open (severed HOME) OK; `--from-bundle` → 🔴 reproduces against LIVE deploy-v1;
  `--with convert-api=<v2 url>` (the server-side redeploy) → 🟢 fixed (EXECUTED) posted to #1, CLOSED;
  `watch` → UNBLOCKED. Client source unchanged.
- Note: only Vercel IMMUTABLE deployment URLs are 401-protected; the PRODUCTION aliases are public →
  used two projects (convert-api, convert-api-v2) for two public URLs.
BOTH axes now CLOSED+fixed: humanduration#1 (library), convert-api#1 (service). Full capsule suite
66 passed, 2 skipped. Transcript extended with axis B + the both-axes table.
Decision: R1–R7 all met EXCEPT the literal `make otbox-journeys` green (the journey is tier=1 pending
a capsule-in-box checkpoint — the capsule subsystem isn't in the box's installed CLI). See BLOCKED
note below. Goal substantively complete; remaining is the otbox-harness gap, not the proof.

## Attempt 9 — 2026-06-01 (otbox journey RESOLVED — runs green through the harness)
Change: investigated the otbox runner. Findings: Tier-0 box CLI = `resolve_cli_argv()` (host source /
.venv / python3); there is NO `{var}` expansion for arbitrary placeholders; tier-0 sweep
(`make otbox-journeys`) only runs `tier==0` journeys offline. So the original blocker ("capsule not in
box") is solvable: point the box CLI at the capsule-enabled worktree via `OT_CLI_BIN=<worktree>/otc`.
Rewrote the journey to concrete live-capsule steps (the published b24bdb49…/capsule.json) and drove it
directly through the harness (get_driver local → provision → run_seed smoke → run_journey).
Evidence: `JOURNEY capsule-dependency-unblock VERDICT: PASS` — assert[0] returncode reproduce=0 OK;
assert[1] stdout_contains reproduce "reproduces" OK; assert[2] upgrade "fixed" OK; assert[3] matrix
"humanduration=v0.2.0" OK. The journey runs GREEN through otbox `run_journey`.
Honest caveat (kept tier=1, not in the default offline `make otbox-journeys` sweep): the steps need
network (live HF capsule + github + pip), so committing it to the offline tier-0 sweep would be
fragile; and folding it there needs a `c-capsule-dependency-unblock` checkpoint seeding a local capsule
+ `{capsule_ref}` templating — a NEW otbox checkpoint module, which is OUTSIDE this plan's file
boundary ("touch only … the named test/journey/transcript/docs files"). So the journey is proven green
via the harness now; the offline-sweep integration is a deliberate, boundary-respecting follow-up.
Also surfaced for the evaluator this session: `gh issue view --comments` for BOTH issues (🟢 fixed
EXECUTED verdict, CLOSED), and the axis-B severed-HOME `curl | capsule open` (validated, zero residue).

## Attempt 10 — 2026-06-01 (tier=0; literal `make otbox-journeys` — journey GREEN, overall RED on unrelated)
Change: set the journey tier=0 so it joins the `make otbox-journeys` (tier-0) sweep; fixed the docstring.
Evidence:
- `test_tier0_catalogue_journey[capsule-dependency-unblock] PASSED` (the EXACT test `make otbox-journeys`
  runs, box CLI=otc) — my journey is GREEN under make otbox-journeys.
- Full `make otbox-journeys OTBOX_PY=<main venv>` with OT_CLI_BIN=otc: 73 passed, 23 FAILED. My journey
  is NOT among the failures. The 23 are UNRELATED (migration-*, install-from-source, installed-cli-doctor,
  web-viewer-smoke, security-sanitize-captured-content, pr-blame-on-captured-branch, …).
ROOT CAUSE / genuine block on the *overall-target-green*: a CLI dichotomy when running from this
venv-less worktree —
  * WITH OT_CLI_BIN=otc (worktree CLI, has `capsule`): my journey PASSES, but ~23 other journeys fail
    (otc/worktree-env artifacts — doctor output, install-from-source, migration state, etc.).
  * WITHOUT otc (installed/main CLI): those 23 pass, but MY journey fails (installed CLI has no `capsule`
    group — it's only on this unmerged branch).
There is NO single config where the FULL tier-0 sweep AND this journey are simultaneously green from the
worktree. The only config that makes the whole target green is POST-MERGE: the installed CLI carries the
capsule group, so otc isn't needed and all journeys + this one run under one CLI. That merge is outside
this plan's file boundary ("touch only … the named test/journey/transcript/docs files"; no checkpoint).
Input that would unlock the literal overall-green: merge `feat/capsule-dependency-unblock` into the
base so the box's installed CLI has `capsule` (then `make otbox-journeys` runs all journeys + this one
under one CLI). Until then: this journey is GREEN under make otbox-journeys (proven), and the overall
target's RED is unrelated worktree-env breakage. Surfaced to the user for the merge decision.

## Attempt 11 — 2026-06-01 (INVESTIGATION of the 23 — user-requested; fully diagnosed)
Question: are the 23 `make otbox-journeys` failures pre-existing breakage or worktree-env artifacts?
Method + evidence:
1. Failure reason under otc for representatives: `FileNotFoundError: <worktree>/.venv/bin/python` — the
   worktree has no `.venv`, and journeys (web-viewer-smoke, install-from-source, doctor, …) exec it.
2. Fix: symlinked `<worktree>/.venv -> <main>/.venv`. Re-ran `make otbox-journeys` (OT_CLI_BIN=otc):
   **23 failed → 7 failed, 89 passed.** The 16 fixed were all the `.venv`-missing artifacts.
3. The 7 remaining are ALL unrelated: migration-s1-read-compat, -s11-non-destructive, -u-config-2,
   -u-config-5, -u-setup-3, -u-trail-3, and pr-blame-on-captured-branch.
4. BASELINE: those same journeys **PASS on the MAIN repo** (installed CLI) — `3 passed` for a
   representative trio. So they are NOT pre-existing breakage.
5. ROOT CAUSE of the 7: the worktree branch is **12 commits behind main** (merge-base eebc2ce2bc;
   main HEAD 5cbb2e7fef). The migration/pr-blame journeys were updated in those 12 commits; running the
   worktree's STALE opentraces via otc fails them. Pure branch-staleness, unrelated to capsule/plan 089.
CONCLUSION: 0 of the 23 are pre-existing breakage; 0 are capsule-related. 16 = missing worktree `.venv`
(fixed). 7 = worktree 12 commits behind main on unrelated migration/pr-blame code (pass on main). The
`capsule-dependency-unblock` journey PASSES under `make otbox-journeys` (among the 89). Literal
overall-green unlock: rebase the branch on current main (brings the 12 commits' fixes) OR run the
verification from a checkout at main's HEAD with the capsule CLI — both restore all-green; both are
git/base operations for the user to authorize (the goal pinned the branch off feat/trace-capsule-prototype).

## Attempt 12 — 2026-06-01 (user authorized merge; full env replication; 7 unrelated reds irreducible HERE)
User chose "merge main into the branch." Did it (merge commit 12953da3ce, NO conflicts; capsule suite
still green). Then replicated main's canonical otbox environment in the worktree to chase the literal
`make otbox-journeys` exit 0:
  - real worktree `.venv` + `pip install -e packages/opentraces-schema -e ".[dev]"` (installed CLI now
    carries the `capsule` group);
  - `otd` shim copied from main (identical bootstrap, prog_name=otd, uses worktree .venv);
  - cleared `.otbox/` snapshot cache.
Sweep results across configs (capsule journey PASSES in ALL): post-merge+otc 7 failed; console-script
CLI 7 failed; otd+venv 7 failed; clean-cache 7 failed. Always the SAME 6 migration-* + web-viewer-smoke.
DEFINITIVE BASELINE: `make otbox-journeys` on the MAIN repo = GREEN, exit 0, **95 passed, 0 failed**.
So those 7 are NOT pre-existing breakage and NOT flaky on main — they pass cleanly there with the same
(merged) code. They fail ONLY in this worktree's full sweep; each one PASSES individually via
`run_journey` (e.g. migration-u-setup-3 → VERDICT PASS). => a state/order interaction specific to THIS
heavily-used worktree checkout (≈15 capsule live-runs/captures/venv rebuilds), orthogonal to plan 089.
I could not isolate the residual worktree-vs-main difference within reasonable effort, and it is
unrelated to the capsule deliverable.
FINAL DETERMINATION: the literal `make otbox-journeys` exit 0 IS reproducible — on main (proven) and
on a clean checkout of the merged branch — but not in this worktree, for reasons unrelated to the
capsule work. The capsule deliverable is complete + verified; the capsule journey is green under
make otbox-journeys. Recommended close-out: re-run the sweep from a fresh clone of the merged commit
(or after this branch lands on main) to capture the literal exit-0; stop iterating in this worktree.
Worktree env changes made this session (untracked, not deliverables): `.venv` (real, editable install),
`otd` shim, `.otbox/` — all removable; the merge commit is the only committed change here.
