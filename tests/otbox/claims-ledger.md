# Claims ledger — release claims and the verifiers that bind them

This file is the EXECUTABLE source of truth for the release gate
(`tests/otbox/test_claims_ledger.py` parses and enforces it;
`tests/otbox/release_gate.py` rolls it up into a go/no-go verdict). It is
vendored in-repo for the same reason as `tests/otbox/jtbd-command-map.md`:
a gate whose SSoT lives in the gitignored `kb/` cannot run in any clean
checkout. The narrative draft remains at
`kb/projects/opentraces/otbox-claims-ledger-draft.md`; THIS file is what the
gate reads. The spec-journey map (`tests/otbox/claims_map.py`, J1-J18) is a
sibling at journey granularity — this ledger is claim-granular and may cite
the same catalogue journeys.

Derivation rules (enforced by the gate):

- **Status vocabulary**: `verified` / `partial` / `open` / `waived` /
  `tracked` — nothing else parses.
- **verified** — every named verifier exists, is NOT quarantined
  (`tests/otbox/catalogue/QUARANTINE.toml`), and PASSes in its lane's
  latest run (pr/nightly for tier-0 journeys, the ci-release live-HF lane
  for live verifiers, the default pytest sweep for node-ids). A verified
  row with no verifiers, a missing verifier, or only quarantined verifiers
  fails the gate.
- **partial** — an executing verifier exists but the evidence is
  incomplete: weak assertions, synthetic-only world, opt-in lane, or the
  verifier covers only part of the claim. Same resolution rules as
  verified (verifiers must exist and be unquarantined).
- **open** — no executing verifier yet. Honest debt; never blocks the
  gate. BKT-1 is the acknowledged deprioritized tail (maintainer decision
  2026-06-10): it stays open and never blocks the gate.
- **tracked** — ownership transferred to a named GitHub issue; the Issue
  cell is REQUIRED (`#NNN`, or a `TBD-<tag>` placeholder until the issue
  is filed).
- **waived** — deliberately out of release scope, rationale in the claim
  text.
- **Verifiers cell** — comma-separated catalogue journey names (must exist
  as `tests/otbox/catalogue/journeys/<name>.toml`) and/or pytest node-id
  prefixes (`tests/...py` or `tests/...py::test_name`; the file part must
  exist). Empty cells render as `—`.
- Status counts (2026-06-11, post-release-gate-095): 26 verified, 15 partial,
  8 open, 7 tracked, 0 waived — 56 rows.

## A. Capture (per harness)

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| CAP-1 | Claude Code session capture lands a queryable trace (hooks -> ingest -> index) on a real captured world | A | verified | agent-session-trail-explain-happy, tests/otbox/test_agent_session_slice.py | — |
| CAP-2 | Codex CLI capture parity (13 scenarios incl. compaction, subagent, permission); 5 parity journeys remain quarantined baseline-red | A | partial | codex-parity-linear, codex-parity-compaction, codex-parity-subagent, codex-full-parity-latest | #25 |
| CAP-3 | Pi capture parity incl. provider/context sidecars, fail-open extension; full-parity + incremental-recovery quarantined baseline-red | A | partial | pi-extension-capture-linear, pi-compaction-branch-fidelity, pi-context-tree-provider-fidelity | #25 |
| CAP-4 | Global tracking is opt-out; repos auto-enroll on first capture; `excluded` marker respected (now enforced at the ingest choke point for ALL agents) | A | verified | capture-safety-tracking-mode, capture-safety-excluded-marker, tests/test_tracking_mode.py, tests/core/test_ingest.py::TestExcludedProjectGate | — |
| CAP-5 | `init --import-existing` backfills historical Claude sessions | A | verified | capture-safety-import-existing, tests/cli/test_cli_init_autoscan.py | — |
| CAP-6 | Hook failures never block the agent session (always exit 0) — 4 Claude scripts x 4 faults + missing-package sweep, Codex modules, git shim | A | verified | tests/otbox/test_faultpoints.py | — |
| CAP-7 | OTel capture yields `completeness=full` layers; receiver-down never blocks agent traffic | A | partial | tests/test_otlp_capture.py, context-tree-otel-receiver-up, context-tree-otel-bypass-mode | — |
| CAP-8 | Installers (`setup claude-code/codex-cli/pi/git`) are idempotent and preserve unrelated hooks (one refspec-duplication finding still open) | A | partial | tests/otbox/test_idempotency_sweep.py, pi-setup-dry-run, onboard-integrations | — |
| CAP-9 | Regenerated capture batches (B0 capture-refresh) stay green via acceptance journeys on the refreshed worlds | A | tracked | — | #61 |

## B. Bucket and privacy boundary

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| BKT-1 | Raw traces never leave the bucket without explicit opt-in (negative-space verifier: NOTHING egressed during a full capture+publish arc) | B | open | — | #62 |
| BKT-2 | `bucket replay --repo` reconstructs the canonical Git event ref byte-identically | B | verified | bucket-events-mirror-replay-equals-git, bucket-self-sufficient-everything | #25 |
| BKT-3 | Cross-machine byte-identity (gzip mtime=0 everywhere) | B | verified | bucket-cross-machine-content-identity, bucket-symmetric-local-remote, tests/otbox/test_determinism.py | #25 |
| BKT-4 | `bucket repair` is idempotent; `prune` never touches events or trace.json | B | verified | bucket-prune-orphan-only, bucket-write-order-discipline-local, bucket-rebuild-context-tree-substrate | #25 |
| BKT-5 | `bucket verify` detects corrupted blobs and dangling refs (corrupted-blob fault world is the remaining follow-up) | B | partial | bucket-verify-detects-dangling, bucket-compression-integrity-roundtrip | #25 |
| BKT-6 | Remote sync push order blobs -> events -> envelopes -> manifest; diff/status honest; proven against REAL HF in the ci-release live lane | B | verified | bucket-remote-push, bucket-remote-pull, bucket-remote-digests, live-hf-bucket-roundtrip, tests/otbox/test_live_hf_slice.py | — |
| BKT-7 | Append-only hash-chained event log survives GC and rewrites | B | open | — | — |

## C. Discovery (query/map/slice/get + intelligence)

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| DSC-1 | `trace query` returns bounded candidate packets from the BM25+semantic index without full transcript loads (bounded-reads budgets now cover 25 read surfaces) | C | verified | build-dataset-lineage-search, trace-map-and-slice, tests/otbox/test_envelope_budgets.py | — |
| DSC-2 | `trace map --bursts` emits deterministic burst intent (trigger/spec/commit) | C | verified | trace-map-and-slice | — |
| DSC-3 | `trace slice` templates produce bounded packets | C | verified | trace-map-and-slice, tests/otbox/test_envelope_budgets.py | — |
| DSC-4 | `--waste` / `--run-intel` / `trace compare` deterministic, derive-on-demand, byte-identical across map/get (run-twice + cross-verb identity asserted) | C | verified | tests/otbox/test_determinism.py | — |
| DSC-5 | Query latency acceptable on a mature repo (the Spotlight perf regression class); steady-state search latency budget executes, scale-world budgets tracked #40/#41 | C | partial | tests/otbox/test_perf_budgets.py::test_steady_state_search_latency_budget | #40, #41 |

## D. Trails / lineage

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| TRL-1 | `trail blame commit <sha>` resolves a commit to contributing sessions with coverage shares | D | verified | trail-blame-and-graph, agent-session-trail-explain-happy | — |
| TRL-2 | `trail blame pr render` walks branch commits to sessions with intent | D | verified | pr-blame-on-captured-branch | — |
| TRL-3 | `trail blame pr create/update` posts/updates the GitHub PR idempotently (needs gh stub + golden body) | D | open | — | — |
| TRL-4 | The 8 survival states are computed from real git history; a reverted edit shows `reverted` (gold journey asserts strict result_count >= 1 on dual artifact/synthetic worlds; only `reverted` is exercised credibly) | D | verified | survival-walk-reverted, tests/otbox/test_agent_session_slice.py | — |
| TRL-5 | Reverse blame: any file:line resolves to session/prompt/diff | D | open | — | — |
| TRL-6 | Post-commit hook stays within latency/memory budget on mature repos | D | tracked | — | #44 |

## E. Context Tree

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| CTX-1 | `ctx tree/step/reads/writes` reconstruct what the model saw per step (phantom checkpoints owned by the otbox-debt lane) | E | tracked | — | #42 |
| CTX-2 | `ctx resume` produces a usable continuation packet | E | tracked | — | #42 |
| CTX-3 | Compaction and rewind branches structurally correct | E | tracked | — | #42 |
| CTX-4 | OTel vs JSONL structural equivalence for the same session | E | tracked | — | #42 |

## F. Security pipeline

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| SEC-1 | All tools default off; policies flip exactly the documented flag sets | F | verified | bucket-security-policy-basic, bucket-security-fresh-off, enable-security-tools | — |
| SEC-2 | Canonical tool order enforced; `--tools` re-sorted (unit-level only) | F | partial | tests/security/test_pipeline_api.py::test_tools_canonical_order | — |
| SEC-3 | A planted secret never appears in query output / published rows after sanitize (real negative assertion, synthetic secret world, no mutation kill) | F | verified | security-sanitize-captured-content, pi-security-sanitize-captured-content | — |
| SEC-4 | `dataset publish --check-only` blocks rows missing required tools, keyed on per-row evidence (bypass paths not probed) | F | partial | dataset-security-required-rejection | — |
| SEC-5 | Capsule redaction floor (regex+entropy+business_logic) unconditional; prompts excluded by default | F | open | — | — |
| SEC-6 | Post-processors always see post-redaction traces (ordering invariant; unit-level only) | F | partial | tests/core/test_processors.py::test_secret_absent_from_processor_stdin | — |

## G. Datasets / publish

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| DST-1 | Capture -> workflow -> review -> publish arc: approved rows only, with trace lineage | G | verified | agent-session-to-published-dataset | — |
| DST-2 | Publish writes new shards, never appends (shard semantics not byte-checked) | G | partial | cli-publish-happy-path, build-publishable-dataset-shape | — |
| DST-3 | Schema-ahead safety on the publish path: a remote advertising a newer schema blocks publish | G | verified | tests/otbox/test_live_hf_slice.py::test_live_hf_schema_ahead_blocks_publish | — |
| DST-4 | Real HuggingFace publish works (auth, card, dataset_infos, loadable) — standing in the ci-release live-HF lane | G | verified | live-hf-dataset-publish, tests/otbox/test_live_hf_slice.py::test_live_hf_journey | — |
| DST-5 | Schedules pause/resume/remove and never bypass review gates | G | verified | dataset-sync-skill-history | — |
| DST-6 | Workflow security contracts (required/disallowed tools) rejected/enforced at dataset new | G | verified | dataset-security-workflow-seeding, dataset-security-required-rejection, dataset-security-optional-toggle | — |

## H. Agent-driven CLI

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| AGT-1 | Every public command supports `--json` with a structured envelope (sweep executes in the PR lane; 4 of 5 KNOWN_FINDINGS fixed, 1 documented in-sweep waiver for watcher-tick array shape) | H | verified | tests/cli/test_json_surface_sweep.py | #25 |
| AGT-2 | `--json` envelopes carry actionable `next_steps` forming a coherent graph to the documented goals (nightly-gating confirmed) | H | partial | tests/otbox/test_next_steps_walker.py | — |
| AGT-3 | Frozen envelope contracts (`opentraces.context_*.v1` etc.) never drift without a version bump (`opentraces.security_tools.v1` newly frozen; non-versioned envelopes remain) | H | partial | tests/cli/test_json_surface_sweep.py::test_envelope_shapes_match_snapshot | — |
| AGT-4 | The paste-in setup prompt drives an agent through install+auth+capture successfully | H | partial | prompt-install-auth-flow | — |
| AGT-5 | Documented exit codes (e.g. 6 unresolved ref, 3 schema-ahead, 2 contract) stable across releases (exit 6/3/2 asserted at the CLI boundary, PR lane) | H | verified | tests/cli/test_exit_code_contract.py | — |
| AGT-6 | Pi slash commands / model tools (`ot_search`, `ot_trace`, ...) respond correctly in-session (captures exist; no executing verifier) | H | open | — | — |

## I. Non-functional standing guarantees

| ID | Claim | Axis | Status | Verifiers | Issue |
|---|---|---|---|---|---|
| NF-1 | Determinism: same world, same command, byte-identical output (two-fork checkpoint harness; full per-command sweep open) | I | partial | tests/otbox/test_determinism.py | — |
| NF-2 | Idempotency sweep across setup/repair/rebuild verbs (fixed-point digest, Click-walk drift guard) | I | verified | tests/otbox/test_idempotency_sweep.py | — |
| NF-3 | Hook p95 latency + RSS ceilings at 50K-event scale | I | open | — | — |
| NF-4 | Watcher soak: event-log growth slope bounded (plan-090 contract) | I | open | — | — |
| NF-5 | N-1 -> N upgrade: every read verb returns legacy data correctly (0.3.3->0.4 specific; not generalized) | I | partial | migration-s12-end-to-end-upgrade, migration-s1-read-compat | — |
| NF-6 | No host residue after box teardown | I | verified | tests/otbox/test_otbox_slice.py::test_zero_host_residue | — |
| NF-7 | Watcher daemon memory stays bounded during long sessions | I | tracked | — | #45 |
