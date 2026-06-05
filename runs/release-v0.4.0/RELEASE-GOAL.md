# OpenTraces v0.4.0 — Release stabilization GOAL (do not stop until met)

**Mode:** ultracode. Started 2026-06-04. Ship target: **today**.
**Base:** `main` @ `f5c03eeeb1` (contains PR #12 Pi-capture support + PR #20 Pi-capture-opt-out).
**Landing unit:** `simplify/review-fixes` @ `3d59954791` (octopus of all 8 simplification PRs + code-review fixes; 28 commits; net ~ −4,857 LOC + module splits + seam/dedup fixes).

## Objective (the contract)

Reconcile the verified simplification stack onto the diverged main, prove the composed
result contradicts neither itself nor the parallel Pi-capture work, add terminal-control
journey footage to otbox, run the full release QA + docs pass, then land v0.4.0 to main
and tag it. Preserve every documented behaviour of the release feature set.

## User-confirmed scope (2026-06-04)

- **Ship:** single verified integration merge of the composite onto main (preserves all
  per-PR merge history), tag `v0.4.0`, push `main` + tag to origin. **No** external
  package/dataset publish this cut.
- **Footage:** build the termctrl recorder integration + `make otbox-footage*` + gallery,
  install termctrl, run the **full journey sweep** (all scenarios × applicable harnesses).
  Graceful-degrade when termctrl absent (default CI safe).

## Phase ledger (update as we go)

- [ ] **A — Reconcile & land-on-branch.** Branch `simplify/land-to-main` off main; merge
      review-fixes; resolve 3 conflicts (README.md, web/site/docs/docs/index.md trivial;
      `cli/installers.py` real — must preserve BOTH PR#20 Pi-capture-opt-out AND PR#17
      doctor-extraction split). Tree imports clean.
- [ ] **B — Contradiction / semantic-composition gate.** Authoritative: full `pytest` on the
      composed tree + otbox clean-rebuild + CLI-surface smoke. Adversarial: fan-out verifiers
      (import graph / dangling module refs; monkeypatch seams; Pi-capture-opt-out preserved
      through the split; doctor split intact; CLI parity; docs accuracy). The git-merge ≠
      semantic-composition lesson: tests on the COMPOSED stack are the gate, not merge-cleanliness.
- [ ] **C — terminal-control footage in otbox.** termctrl recorder backend behind the runner's
      session helpers (`_capture_pane`/`_send_keys`/`_kill_session` ↔ termctrl `show`/`send`/`stop`),
      `--record` → `.termctrl` → `termctrl video` MP4, `make otbox-footage` / `otbox-footage-all`,
      `footage/gallery.html`. Verify on echo-meta + one real harness, commit, kick full sweep.
- [ ] **D — Release QA + docs.** doc accuracy sweep (CLAUDE.md, README, web/site docs, skill),
      CHANGELOG / release notes, wheel build (opentraces-0.4.0), `doctor` clean, CLI surface
      complete. Triage perf/otbox/s7 env-failures separately (see reference memory).
- [ ] **E — Land.** Merge `simplify/land-to-main` → `main` (single integration merge),
      tag `v0.4.0`, push `main` + tag to origin. Close the stack PRs as merged-via-integration.

## Completion condition

Complete only when: stack reconciled onto main with conflicts resolved; composed tree passes
full QA + otbox clean-rebuild + contradiction gate; terminal-control footage integration built,
committed, and full sweep kicked; docs accurate for the v0.4.0 feature set; main landed and
tagged v0.4.0 and pushed to origin.

## Operational constraints

- Reconcile/conflict-resolution done inline (load-bearing, sequential).
- Verification + QA + docs + feature build fan out to clean-context agents/workflows.
- Test isolation: `.venv` editable points at ../pi-support → PYTHONPATH-pin the main repo's
  src for any in-repo pytest run; otbox needs `rm -rf .otbox` to rebuild from current source
  (see [[reference_worktree_test_isolation]]).
- Footage MP4 artifacts are generated/reviewable, gitignored (large media); recorder code +
  make target + gallery are committed.
- Preserve documented behaviour. No external publish. Commit/push to main directly per repo norm.
