# Skill Verifier Factory — Run Log

Goal: Deliver an OpenTraces Skill Verifier Factory that mines retained TraceRecords +
Trace Index latest-generation `skill_invocation` units into trace-grounded verifier
packages for skills entering SkillOpt / eval workflows. Three tangible bucket-derived
examples (goal-forge goal-contract/downstream-outcome, tdd red-green-refactor, review
grounded-findings), a `skill-verifier-candidates-v1` summary, a dry-run CLI, schema +
scorer + leakage-safe split tests, and ≥1 default-CI otbox verifier journey.

Constraints: additive-only schema; preserve TraceRecord/bucket/Trail/Context Tree write
contracts; SkillOpt strict Dsel gate + held-out Dtest; manual/default-off promotion; no
network/live-agent in default CI; no otbox end-user product surface; no automatic
verifier generation without explicit human approval.

---

## Phase 0 — Orientation (complete)

Read briefs kb/br/66 (SkillOpt), 53 (skillgrade), 51 (VCS lineage), 56 (Alpha Eval).
Mapped the existing substrate via 4 read-only Explore passes + direct source reads.

Evidence observed (seams the factory will build on, not rebuild):

- **Reads.** `core.trace_index.list_skill_invocation_units_from_records(project_slug)`
  returns latest-generation `skill_invocation` TraceUnits (already deduped via
  `_latest_units`). `core.bucket_store.iter_trace_record_objects(project_slug)` yields
  full `TraceRecord`s. Bucket layout v2 at `~/.opentraces/bucket/objects/traces/v1/...`.
- **Mining reuse.** `consumers.skill_intelligence.pipeline.audit_skill_invocations(...)`
  + `build_episode_rows(...)` already project units → `opentraces.skill_episodes.v1`
  rows with `outcome_reward = skill_opt.outcome_reward(success, committed, survival)`.
- **SkillOpt gate seam.** `consumers.skill_opt.engine.run_optimization(initial, rows,
  propose=..., gate_fn=..., test_fn=...)` with strict gate `candidate > current` and
  held-out `Dtest`. `split_rows_three_way` is the leakage-safe hash-banded split.
  `rerollout.make_rerollout_gate(FakeReRolloutRunner(), tasks)` builds a deterministic
  `gate_fn` from required-marker coverage; `default_proposer` appends `rule[<tag>]`
  edits for `RolloutRow.failure_tags`. This is the proven CI-safe mechanism — the
  factory makes the markers archetype-semantic + trace-grounded.
- **Workflow template shape.** `workflow_templates/<name>/{SKILL.md, schemas/row.schema.json,
  scripts/build_rows.py, examples/expected-row.json}`; `build_rows.py` reads
  `OT_RUN_PACKET` scope + writes JSONL to `OT_DATASET_OUTPUT`. Auto-discovered.
- **CLI.** New verbs attach to `workflow_group` in `cli/workflow.py` (auto-registered).
- **otbox default-CI journey.** `skillopt-online-loop-echo.toml` (tier=0, gold,
  `type="shell"` pytest step, returncode+stdout_contains assertions; echo binary; SKIPs
  without tmux). The template for the new verifier journey.
- **Live bucket evidence** (matches goal context): goal-forge=67, otbox=73,
  architecture-patterns=48, tdd=48, docs-update=43, review=16 usable episodes.

Design: new internal consumer `consumers/verifier_factory/` (archetypes, scorers,
mining, packaging, factory) + a `skill-verifier-candidates-v1` workflow template + a
`workflow verifier-factory` dry-run CLI verb. Verifier packages mirror the skillgrade
eval shape (graders[] = one CI-safe deterministic leg + declared-deferred opt-in live
legs, weighted), carry source trace/unit refs + firmness, and reuse the SkillOpt gate
for Dsel/Dtest. Label-free path = contract elements inferred from evidence; labeled path
= verifier-creator decisions overriding inferred defaults. No core schema changes.

Split/leakage: three-way split keyed on source trace id (one trace never spans
Dtrain/Dsel/Dtest) via `split_rows_three_way`; each verifier row carries `leakage_key`.

Next: Phase 1 — schema module + archetype registry + deterministic scorers (pure logic),
tested in isolation.

---

## Phase 1 — Consumer package built + real artifacts generated (complete)

Created `src/opentraces/consumers/verifier_factory/`:
- `schema.py` — 4 new additive artifact schemas + validators + `dump_yaml`
  (`skill_verifier_candidates|package|rows|report.v1`). No core schema touched.
- `archetypes.py` — 3 archetypes (goal-forge/tdd/review), each with trace-grounded
  contract elements (label-free detectors), rule markers, verifier-creator questions
  (inferred defaults = label-free; override = labeled), and declared-deferred opt-in
  live legs (agent re-rollout / LLM rubric, default_enabled=False).
- `scorers.py` — deterministic `score_episode` (contract detection), `score_skill`
  (marker coverage), mining aggregates, and the self-contained per-package `scorer.py`
  renderer.
- `mining.py` — `mine_verifier_candidates` → `skill-verifier-candidates-v1` (reuses
  `skill_intelligence.audit_skill_invocations` + `build_episode_rows`; bucket read-only).
- `packaging.py` — `emit_verifier_package` → spec.yaml + fixtures + scorer.py +
  Dtrain/Dsel/Dtest rows + best_skill.md/edit_apply_report.json + report.{json,md}.
  Scores via real `run_optimization` (strict Dsel gate + held-out Dtest).
- `factory.py` — `run_factory` orchestrates mine + 3 example packages + index/README.

### Bugs found & fixed (evidence)
1. `state.export()` needs a directory arg — switched to `result.state.export(package_dir)`.
2. `split_success_failure` returns `(success, failure)`; I unpacked it backwards, so
   `addressable` was computed from success rows while the proposer reflects over
   failures → tdd plateaued at 0.5. Fixed unpack → all three reach 1.0 on synthetic data.
3. **Key design correction (real-bucket evidence):** captured skill_invocation traces
   mostly LANDED (high `outcome_reward`), so a reward=outcome split left the failure
   minibatch empty and the optimizer proposed nothing (Dsel 0→0). But the contracts are
   deeply incomplete (goal-forge: constraint_preservation deficient 55/67, honest_stop
   55/67, verification 49/67). This is exactly the brief-66 verifier gap: outcome ≠
   contract quality. Switched the verifier reward to trace-grounded CONTRACT
   COMPLETENESS (which already weights the downstream/landed element), keeping
   outcome_reward as row provenance.

### Real bucket-derived results (committed under runs/skill-verifier-factory/)
- goal-forge `goal_contract_downstream_outcome_v1`: split 28/23/16, Dsel 0.000→1.000,
  Dtest 1.000, addressable = constraint_preservation, honest_stop, verification_surface.
- tdd `tdd_red_green_refactor_v1`: split 25/12/11, Dsel 0.000→1.000, Dtest 1.000,
  addressable = red_first, refactor.
- review `review_grounded_findings_v1`: split 10/5/1, Dsel 0.000→1.000, Dtest 1.000,
  addressable = cite_file_line, grounded_findings.
- candidates summary recommends all three (leakage_safe_split=True; distinct source
  traces 34/36/10). Labeled-override path verified (decision source flips to "labeled").

Split/leakage: three-way split banded on source trace id (a trace never spans splits);
addressable markers derived from TRAIN failure minibatch only (no held-out leakage);
each verifier row carries `leakage_key` + source trace/unit refs.

Next: Phase 2 — CLI dry-run verb `workflow verifier-factory`, `skill-verifier-candidates-v1`
workflow template, then the test suite + default-CI otbox journey + full regression.

---

## Phase 2 — CLI verb, workflow template, tests, otbox journey (complete)

Surfaces added:
- CLI `opentraces workflow verifier-factory` (cli/workflow.py): always dry-run; writes
  candidates + 3 packages, prints each spec path + Dsel/Dtest; `--example skill:archetype`
  (repeatable), `--project`, `--out`, `--json`. `--promote` is reserved and NEVER
  auto-promotes (human approval required). Verified: `--help`, `--json` dry run.
- Workflow template `workflow_templates/skill-verifier-candidates-v1/` (SKILL.md,
  schemas/row.schema.json `opentraces.skill_verifier_candidates_row.v1`,
  scripts/build_rows.py, examples/expected-row.json, tests/README.md). Auto-discovered;
  runs end-to-end via `run_workflow_rows` (one row per skill×archetype).
- scorer.py now renders over the package's *addressable* (enforced) markers so the
  shipped self-contained scorer matches the deterministic grader + the Dsel/Dtest gate
  (standalone `python scorer.py best_skill.md` → 1.0).

Tests:
- `tests/test_verifier_factory.py` (19): registry, scorers, mining + candidates schema,
  package emission (Dsel 0→1, held-out Dtest 1, parametrized over all 3 examples),
  grader shape (1 deterministic default-on + opt-in-only live legs), addressable =
  TRAIN-only (no held-out leakage), leakage-safe split (a trace never spans splits),
  labeled-override path flips decision source, determinism, self-contained scorer runs
  via subprocess, factory orchestration, schema guards (reject auto-promotion / missing
  deterministic grader / default-on live leg), dump_yaml.
- `tests/test_verifier_factory_workflow.py` (2): template through the real runner +
  row.schema.json required-keys validation; single-skill scoping.
- `tests/otbox/test_verifier_factory_journey.py` (2): default-CI deterministic proof
  that the emitted verifier feeds the SkillOpt strict gate (0→1 Dsel, Dtest 1) and that
  factory tasks are otbox ReRolloutTask-compatible.
- otbox journey `tests/otbox/catalogue/journeys/skill-verifier-factory-echo.toml`
  (tier=0, silver, requires=["cli"], shell pytest step + returncode/stdout_contains).
  Discovered by `available_journeys()`; default-CI safe (no tmux/agent/network). The
  real-agent fidelity leg stays in the opt-in skillopt-online-loop-echo gold journey.

Focused regressions GREEN (98 passed): verifier_factory ×3 files + test_skill_opt +
test_skill_intelligence + test_skill_detection + test_trace_index_plan056.

### Integrator follow-up (cross-checkout; cannot do here)
`kb/` is NOT checked out in this worktree, so the JTBD/063 SSoT drift gate
(`tests/otbox/test_jtbd_ssot.py`) SKIPS (environment-bound). When this branch lands in
a checkout WITH kb, the new Click command `workflow verifier-factory` must get: (a) a
row in `kb/plans/063-jtbd-command-map.md` (§ Workflow bucket), and (b) journey
ownership (the `skill-verifier-factory-echo` journey covers it) so `unowned_commands`
stays empty. Same pattern as the existing `workflow optimize` / `workflow
skill-intelligence` entries.

Next: full `pytest tests/ -q` regression sweep + document any environment-bound skips.

---

## Phase 3 — Full regression sweep + triage (complete)

`pytest tests/ -q` → **3073 passed, 168 skipped, 2 xfailed, 3 failed** (1292s).

All verifier-factory code green. The 3 failures are pre-existing / environmental, none
introduced by this slice (my changes are confined to new `consumers/verifier_factory/`,
new `workflow_templates/skill-verifier-candidates-v1/`, a new `cli/workflow.py` verb,
3 new test files, 1 new journey TOML, and `runs/skill-verifier-factory/`):

1. `tests/perf/test_core_perf.py::test_core_perf[inverse-blame-smoke]` — p95 362.5ms vs
   350ms budget. ENVIRONMENTAL: **passes in isolation** (re-run: 2 passed in 5.29s); only
   tripped under the 21-min full-run + concurrent load. Not on any path I touched.
2. `tests/perf/test_bucket_performance_gates.py::test_bench_capture_hot_path` — same
   class; passes in isolation alongside (1). CLAUDE.md: triage perf budgets separately.
3. `tests/integration/test_trace_trails_corpus.py::test_trace_trails_corpus_fixture_is_current`
   — PRE-EXISTING: the `tests/fixtures/trace_trails_corpus/v1/*` fixtures were already
   modified vs HEAD at session start (in the opening `git status`), before any of my
   work. The Trace Trails corpus is unrelated to the verifier factory; regenerating it
   is an intentional accept-after-scenario-change action owned by the Trail work, not
   this slice.

168 skips are environment-bound (tmux / OT_REAL_REPL / OT_OTBOX_TIER1 not set; kb not
checked out in this worktree → JTBD/063 SSoT + portrayal UAT skip; optional security
extras absent). No new skips were introduced by this slice that are not env-bound.

### Done-criteria check (all met)
- skill-verifier-candidates-v1 summary from local bucket — ✅ (CLI + workflow template +
  `runs/skill-verifier-factory/skill-verifier-candidates-v1.json`).
- verifier-creator decisions guided/encoded — ✅ (creator_questions w/ inferred defaults
  = label-free; `decisions=` override = labeled; source recorded per decision).
- packages w/ spec.yaml + fixtures + deterministic scorer + Dtrain/Dsel/Dtest rows +
  source trace/unit refs + report — ✅ (3 packages emitted + validated).
- 3 tangible bucket-derived examples — ✅ goal-forge / tdd / review, all Dsel 0→1.000,
  Dtest 1.000, accepted, addressable markers = real top deficiencies.
- schema/workflow/unit tests for candidates/packages/scorers/leakage-safe splits — ✅.
- focused regressions (skill detection, Trace Index latest-gen reads, SI/SkillOpt,
  otbox deterministic replay) — ✅ green.
- dry-run CLI surfacing package paths + Dsel/Dtest — ✅ `workflow verifier-factory`.
- ≥1 default-CI otbox verifier journey — ✅ `skill-verifier-factory-echo` (tier 0).
- pytest tests/ -q, only documented env-bound skips — ✅ (3 unrelated failures triaged
  above: 2 environmental/pass-in-isolation, 1 pre-existing branch fixture drift).

### Constraints honored
Additive-only schema (4 new artifact schema_versions; zero edits to opentraces_schema);
bucket/TraceRecord/Trail/Context Tree read-only; SkillOpt strict Dsel gate + held-out
Dtest reused verbatim; manual/default-off promotion everywhere (approval_state +
automatic_promotion=False enforced by validators); no network/live-agent in default CI
(FakeReRollout/deterministic only; live legs declared default_enabled=False); otbox stays
test-only (no end-user surface); `--promote` reserved and never auto-promotes.

Slice complete.

---

## Phase 4 — Codex architect review + genericity / trace-data / agent-experience (complete)

User asked to (a) test the factory on another skill and (b) use Codex as a reviewer to
ensure it is generic, makes full use of trace data, and has the right verifier-creator
agent experience. Delegated to the Codex Architect (advisory, read-only,
`mcp__codex__codex`). Codex validated both gaps and found two real issues; implemented
its prioritized recommendations.

### Findings acted on
1. **Codex exec_command opacity (Quick).** Codex traces wrap every action in
   `exec_command`, so detectors keyed on tool NAMES never fired. Added a command-family
   classifier reading the real command from `record.metadata.normalized_tool_calls[].shell.command`
   (+ step tool-call `cmd/command/script` fallback). Detectors now use families
   (verification / git_commit / search_read / edit_write / build) on BOTH agents.
   Evidence: architecture-patterns generic now detects verification_run 42/48 and
   context_read 48/48 (previously invisible).
2. **Lossy projection → rich evidence adapter (Short).** `evidence_from_episode(episode,
   record=None)` enriches from the TraceRecord (real commands + `outcome.committed` +
   step count) when available; mining + packaging load records once and thread them
   through. Backward compatible (record=None = old projection; injected-episode tests
   unchanged).
3. **Not generic → generic archetype + propose→approve draft (Medium).** Added
   `generic_skill_outcome_v1`, a skill-namespaced trace-derived archetype (markers
   `rl.<skill>.generic.<element>`); `archetypes_for_skill` falls back to it for any
   unregistered skill; `resolve_archetype(skill, id)` binds it. Mining emits a
   `propose_archetype_draft` (per-element support + example/counterexample refs +
   questions + `approval_required=True`) for generic candidates. Generic packages are
   never auto-`recommended` (human approval required).
4. **Fail-closed honesty (Quick).** Packages carry `gate_basis`
   (train_failure_minibatch | train_union_fallback | corpus_union_fallback |
   full_contract_fallback) and `recommended` (only when basis=train_failure_minibatch,
   accepted, dtest>0, non-generic). The deterministic grader declares `semantics`
   stating it is a CI-safe COVERAGE PROXY, not proof of semantic behavior change (that
   needs the opt-in live legs) — br/56 reward-integrity caveat made explicit.
5. **Verifier-reward fragility fix (the key correction).** Codex's rich-evidence change
   raised contract completeness so most traces scored >0.5, EMPTYING the failure
   minibatch (all curated packages regressed to Dsel 0→0, not recommended). Root cause:
   tying "learnable" to overall-completeness<0.5 conflates "bad trace" with "missing one
   element". Fix: the SkillOpt split reward is now binary — a trace is a verifier
   *success* only if it satisfies the FULL contract; any trace missing an element is a
   failure that teaches that element. contract_completeness + outcome_reward kept on the
   row as provenance. Restores + strengthens the result.

### Tested on NEW skills (real bucket)
- docs-update (NEW curated `docs_update_reflects_change_v1`): Dsel 0.000→1.000,
  Dtest 1.000, recommended, addressable = changelog, no_stale.
- architecture-patterns (NEW skill via GENERIC archetype): Dsel 0.000→1.000, Dtest
  1.000, accepted, **recommended=False** (generic → human approval), addressable =
  produced_changes, verification_run.
- All 4 curated (goal-forge/tdd/review/docs-update) + generic now in DEFAULT_EXAMPLES;
  `runs/skill-verifier-factory/` regenerated with 5 packages.

### Verification
Focused: 105 passed (verifier ×3 files incl. 7 new tests + skill_opt + skill_intelligence
+ skill_detection + trace_index). New tests cover: generic fallback + skill-namespaced
markers, Codex exec_command command-family classification, generic packaging of an
unregistered skill, propose-draft human-approval gate, gate_basis/recommended + grader
proxy-honesty. Full `pytest tests/ -q` re-run in progress.

Constraints still honored: additive-only schema; read-only bucket; strict Dsel gate +
held-out Dtest; manual/default-off promotion; no network/live-agent in default CI; otbox
test-only; no auto verifier generation/promotion without human approval (generic drafts
explicitly approval_required + recommended=False).

### Full regression after Phase 4
`pytest tests/ -q` → **3081 passed, 168 skipped, 2 xfailed, 2 failed** (1100s). Same
pre-existing/environmental failures as Phase 3 (one fewer — the capture-hot-path perf
test recovered this run, confirming load-flakiness):
- `test_trace_trails_corpus.py::test_trace_trails_corpus_fixture_is_current` — PRE-EXISTING
  branch fixture drift (modified at session start, unrelated to verifier_factory).
- `test_core_perf.py::test_core_perf[inverse-blame-smoke]` — ENVIRONMENTAL (395ms vs 350ms
  budget under load; passes in isolation). Not on any path this slice touched.
Net new tests all green (3073→3081). Phase 4 complete.

---

## Phase 5 — Declarative archetypes + verifier-creator skill (in progress)

Goal: make archetype detectors DATA (so an agent can author one contextualized
verifier per skill) and ship a verifier-creator skill, all behind the existing trust
boundary (agent PROPOSES, factory SCORES, human APPROVES). Two ordered deliverables.

### Deliverable 1 — DECLARATIVE ARCHETYPES (detectors are data, not code)
- **New `detectors.py`.** `DetectorSpec` (frozen, data-only) + `detector_matches`
  interpreter (fixed, safe, NO eval / NO callable). The command-family table moved here
  as the single source of truth (`classify_command`, `KNOWN_COMMAND_FAMILIES`). Fields:
  the four named in the spec (`text_any`, `command_families`, `file_globs`, `tool_any`)
  PLUS the minimal additional pure-data predicates lossless migration requires
  (`command_any`, `has_files`, `has_commands`, `committed`, `outcome_reward_min`,
  `outcome_label_in`). An element is present when ANY populated predicate matches — which
  is exactly the OR shape every legacy closure had. `validate_detector_dict` rejects
  unknown keys (the trust boundary in practice: a spec cannot carry reward/gate/split)
  and unknown command families; `detector_permissiveness_flags` soft-lints broad
  detectors (sole presence-check, or substring token < 3 chars).
- **`archetypes.py` migrated.** `ContractElement.detect` (Callable) → `detectors:
  DetectorSpec` (kept a thin `.detect()` method delegating to the interpreter, so the
  single call site `scorers.py:53` is unchanged — minimal churn). All 4 curated + the
  generic archetype rewritten with `DetectorSpec` literals. The regex `_TEST_FILE_RE`
  was DROPPED safely: it is fully subsumed by `file_globs=("test",)` substring (any file
  the regex matched contains "test"). Added archetype serialization
  (`archetype_to_dict` / `archetype_from_dict` / `validate_archetype_dict`,
  `ARCHETYPE_SCHEMA_VERSION = opentraces.skill_verifier_archetype.v1`) — round-trips to
  YAML/JSON, rejects unknown top-level keys.
- **Detection parity proof.** `test_verifier_factory_declarative.py` embeds the legacy
  closures VERBATIM as a reference oracle and compares them element-by-element against
  the new declarative detectors across a generated **4320-case** evidence corpus (incl.
  the `latest_release.py` regex-False/substring-True edge). Every element fires AND
  abstains at least once (corpus is non-vacuous). 0 divergences.
- **Identical Dsel/Dtest on worked examples.** The pre-existing parametrized suite
  (exact Dsel 0→1, Dtest 1, exact present/deficient markers for all 5 examples) stayed
  green unmodified. Real-bucket parity reconfirmed (see below).
- **Self-describing packages.** `spec.yaml` now carries each element's declarative
  `detectors` + a full `archetype_spec`, so a shipped package round-trips back to an
  archetype with no eval.

### Deliverable 2 — VERIFIER-CREATOR SKILL
- **`authoring.py`** — the agent tool surface, 5 tools mapping the end-to-end loop:
  `list_candidates`, `get_skill_examples` (real example/counterexample trace refs +
  `opentraces trace slice/get` commands, optional `slice_fetcher` for the live path),
  `draft_archetype` (approval-required editable spec), `author_archetype` (validate +
  lint → `AuthoredArchetype`, raises only on structurally unsafe specs), `score_authored`
  (deterministic gate → package + report). `emit_verifier_package` gained an
  `archetype=` param so an authored archetype is scored directly.
- **`skill/verifier-creator/SKILL.md`** (skills.sh convention) — documents the trust
  boundary, the data-only detector vocabulary, the 5 tools, the spec shape, and the
  must-nots. Registered in `pyproject.toml` force-include.

### Trust boundary (br/56) — enforced + tested
- A spec cannot set/alter reward or gate: `validate_archetype_dict` /
  `validate_detector_dict` reject `reward`/`gate`/`split`/`automatic_promotion` and any
  unknown key (tested). The binary full-contract reward is computed mechanically in
  packaging regardless of archetype content (tested: `contract_pass == (completeness ≥
  0.999)` on every row).
- Over-permissive detectors are flagged, never silently accepted: surfaced as
  `permissive_detector:` limitations AND **product fix** — `recommended` is now forced
  False when any permissiveness flag is present (a broad authored spec cannot be
  auto-recommended even if gate plumbing passes).
- Agent never self-promotes: `AuthoredArchetype.auto_promote=False`, promotion stays
  `manual_required_default_off`.

### Evidence (Dsel/Dtest per skill, parity)
- Focused: **57 passed** — `test_verifier_factory.py` (26) + `_declarative.py` (17, incl.
  4320-case parity) + `_creator_skill.py` (12) + `_workflow.py` (2) + otbox journey (2)
  [counts pre self-describing test; now 58 with the round-trip test].
- Real-bucket `opentraces workflow verifier-factory --json` regenerated `runs/`:
  goal-forge / tdd / review / docs-update → Dsel 0.000→1.000, Dtest 1.000,
  recommended=True; architecture-patterns (generic) → Dsel 0.000→1.000, Dtest 1.000,
  recommended=False. **Parity with Phase 4 exact.**
- Agent-authored spec (no curated archetype) → Dsel 0.000→1.000, Dtest 1.000, accepted.

### Constraints honored
Detectors are DATA (no eval/callable); additive-only schema (one new
`opentraces.skill_verifier_archetype.v1`; zero edits to opentraces_schema/Trail/Context
Tree write contracts); bucket read-only; reused skill_intelligence projection +
trace_index reads + skill_opt strict-Dsel + held-out-Dtest gate verbatim; default CI
network-free / live-agent-free (live legs default_enabled=False); deterministic CLI
works with no agent; curated behaviour preserved losslessly (parity oracle); no otbox
end-user surface; no auto-generation/auto-promotion without human approval.

### Full regression (complete)
`pytest tests/ -q` → **3108 passed, 168 skipped, 2 xfailed, 2 failed** (1183.68s). Both
failures are the SAME documented pre-existing/environmental ones from Phases 3-4, neither
on any path this slice touched (my source changes are isolated to the untracked
`consumers/verifier_factory/` dir + `skill/verifier-creator/` + 2 new test files + a
one-line `pyproject.toml` force-include):
- `test_trace_trails_corpus.py::test_trace_trails_corpus_fixture_is_current` — PRE-EXISTING
  branch fixture drift (corpus fixtures were already modified at session handoff).
- `test_core_perf.py::test_core_perf[inverse-blame-smoke]` — ENVIRONMENTAL (391ms vs 350ms
  under full-suite load); **passes in isolation: `1 passed in 2.90s`**.

Phase 5 complete. Both deliverables shipped behind the trust boundary, detection parity
proven over a 4320-case corpus, real-bucket parity exact, project memory updated.

---

## Rubric-centric redesign (the verifier measures *effectiveness*, not coverage)

### Why (the user's reframe + measured evidence)
selfcheck.py proved the marker-coverage gate is (a) GAMEABLE — a garbage skill stuffed with
`rule[<marker>]` tokens scores 1.0 — and (b) NON-DISCRIMINATING — process-marker completeness
0.667 vs 0.667 for tdd success/fail; 4/5 skills have no negative class. Root cause (user):
**a trace is EVIDENCE, not the verdict.** The judgment of "was this skill used effectively?"
belongs in a bespoke RUBRIC the agent authors (alone or with the user) in conjunction with the
skill definition; trace evidence + git lineage GROUND it. Calibration against human labels +
git survival is what earns a rubric the right to feed reward.

### Design (workflow: 4 architect lenses → 4 adversarial critics → synthesis)
Authoritative spec at `runs/skill-verifier-factory/RUBRIC_DESIGN.md` (17 holes found + closed).
Architects PROBED the live bucket (1078 records) and proved the data reality: `survival_state`,
`outcome.reward/label` are None on every record; `success` is never False; the ONLY natural
negative class is `committed=False` (26 records). **Honest v1 outcome = BLOCKED for all 5 seed
skills + a human-labeling worklist, never a hollow Dsel 0→1.0.** Central contradiction resolved:
the rubric LABELS held-out traces (text-invariant ground truth); SkillOpt's reward = candidate
COVERAGE of failure markers proven on calibration-gated criteria (text-sensitive, so Dsel can
still move); the rubric's PASS/BLOCK status is provably text-invariant (closes stuffing).

### Locked user decisions
- Emulate the user's labeling to drive ONE skill to `calibrated` (transparently flagged as
  emulated — a pipeline demo, not a real gold claim).
- `provisional_weak_only` rubrics MAY feed reward (flagged, recommended=False).
- G4 judge-repeatability is advisory until a label/turn budget exists.
- Judges pooled; `judge_id` recorded per verdict; warn on mixed judges.

### Phase 0 — data-model spine (COMPLETE, adversarially verified)
`rubric.py`: Rubric ⊃ VerifierArchetype; Criterion = ContractElement + {judge_method ∈
{deterministic,agent,human}, evidence: EvidenceSpec, direction, rubric_text}; Verdict (append-only
ledger row), CalibrationPolicy, CalibrationReport. `schema.py` +4 additive schema_versions.
Serialization + trust-boundary validators: a legacy archetype round-trips byte-identically as a
degenerate all-deterministic rubric (proven for all 5 seeds via the dict path); criteria/verdicts
reject reward/gate/split/value/calibration; deterministic vs agent detector/evidence rules; the
ctx_reads-alone rejection; structural un-self-gradeable floor. `Rubric.score` well-defined at the
BLOCKED edges. Marker-stuffing exploit pinned as a permanent RED sentinel.
- Adversarial review workflow (3 lenses + triage) found 1 BLOCKER + 1 major + 3 minors; ALL fixed:
  floor now requires ≥1 deterministic criterion carrying ≥20% of total weight (closes the
  100%-agent + weight-to-zero collapses); int-weight byte-identity; generic dict-path round-trip
  test; output-only-calibration comment.
- Evidence: `pytest tests/test_verifier_rubric.py + test_verifier_factory{,_declarative,_workflow}.py
  + test_verifier_creator_skill.py -q` → **81 passed**.

### Remaining (decisions settled; next)
Phase 1 evidence resolution (bounded read-only resolvers + digest/epoch); Phase 2 calibration math
(per-criterion P/R/discrimination, Mann-Whitney AUC vs gold, Spearman vs weak `committed` class,
status machine with explicit n_neg==0 / all-demoted guards); Phase 3 two HARD adversarial gates;
Phase 4 agent-judge protocol + emulated human labels; Phase 5 reward swap behind `reward_basis`
(no SkillOpt fork); Phase 6 SKILL.md rewrite + CLI + interactive emulated-label demo to `calibrated`.

### Phase 2 + 3 — calibration core + adversarial gate (COMPLETE, validated on real bucket)
`calibration.py` (pure-Python, deterministic, network-free): `weak_label` (committed=False is the
only natural negative), per-criterion precision/recall/discrimination vs combined truth (human gold
weight 1.0, weak `committed`-derived weight 0.4), Mann-Whitney `mann_whitney_auc` vs gold, `spearman`
vs the weak class with the non-independence guard (outcome-derived deterministic criteria excluded
from the rho sub-score), per-criterion demotion (precision/discrimination floors → effective_weight 0),
the G0..G5 status machine with explicit guards (n_neg==0 → auc None → blocked_insufficient_labels;
all-demoted → blocked_non_discriminating; negative auc/rho → blocked_inverted; presence-only/>95%-firing
floor → blocked_no_floor; same-session self-judge without gold → blocked_needs_human_labels), the
`adversarial_probe` (G3a status-flip invariance: verdicts are a pure function of evidence, never skill
text + legacy `score_skill(stuffed)≈1.0` sentinel; G3b permissive-floor rejection), and
`emulate_human_labels` (transparent stand-in, flagged, recommended stays False — per the user's
"emulate the user labelling").
- Tests (`test_verifier_calibration.py`, 16): calibrated-on-separable; blocked on
  uncorrelated/anticorrelated/insufficient-labels/no-floor; provisional via the weak negative;
  self-judge-without-gold blocks; AUC/Spearman match hand fixtures; adversarial invariance. KEY
  finding from the fixtures: per-criterion demotion catches a wrong-pointing criterion BEFORE the
  rubric-level inversion check — so an anticorrelated rubric lands in `blocked_non_discriminating`
  (honest), and `blocked_inverted` is a defensive branch (rarely reached post-demotion).
- **Real-bucket validation** (`calib_realbucket.py`, 1080 records) — the spec's headline finding
  reproduced empirically AND the gate shown to genuinely discriminate:
  | skill | no labels | + emulated labels | AUC |
  |---|---|---|---|
  | goal-forge | blocked_insufficient_labels | calibrated | 1.00 (+31/−3) |
  | tdd | blocked_insufficient_labels | blocked_insufficient_labels | only 1 neg |
  | review | blocked_insufficient_labels | blocked_insufficient_labels | — (0 neg) |
  | docs-update | blocked_insufficient_labels | blocked_non_discriminating | 0.59 < 0.70 |
  | architecture-patterns | blocked_insufficient_labels | calibrated | 1.00 (+39/−7) |
  No-labels → BLOCKED for all five (honest default, no silent 1.0). With (emulated) labels the gate
  reaches calibrated ONLY where a real negative class AND separation exist (goal-forge,
  architecture-patterns), refuses on too-few-negatives (tdd 1, review 0) and weak separation
  (docs-update AUC 0.59). Emulated labels stay flagged; recommended=False.
- Regression: all verifier suites green — **94 passed**.

### Phase 1+4 + TWO MODES — alignment session (manual) + autoverify (self-align) [COMPLETE]
User reframe accepted + extended: "expose tools + a procedure for the agent (alone or with the
user) to author a bespoke rubric in conjunction with the skill definition" — PLUS an **autoverify
mode** that self-aligns to the skill's goal to identify the rubric, on top of manual mode.
- `evidence.py` — `resolve_evidence`: bounded, read-only EvidenceBundle (episode projection +
  outcome) with visible truncation + sha256 digest + epoch; rich sources (trace_slice/diff/ctx)
  surface `{"status":"unavailable"}` → judge ABSTAINS, never silent-passes.
- `judge.py` — agent-as-judge protocol: `build_judge_packet` (EVIDENCE-BLIND: no skill text, no
  markers), `post_verdict` (groundedness: evidence_quote must be a verbatim substring of the
  bound evidence; judge_id stamped agent:/deterministic:, cannot impersonate human), append-only
  content-addressed verdict ledger, `record_human_label` (the ONLY writer to the gold ledger,
  refuses without `human_confirm=True`; `emulated=True` writes a flagged stand-in).
- Two modes in `authoring.py`: `read_skill_definition` (the skill's stated goal),
  `autoverify_draft_rubric` (self-align: generic deterministic scaffold = un-self-gradeable floor
  + external-anchor discriminators, PLUS one `agent` "effective_outcome" criterion seeded from the
  skill goal), `autoverify(skill)` (self-align → calibrate with `same_session_self_judge=True`),
  `align_session(skill)` (manual scaffold: desired-outcome prompt + editable draft + traces to
  label + label tally).
- **Trust ceiling (the crux of the user's ask)**: `calibration._status` refined — autoverify
  (same-session) can reach `provisional_weak_only` ONLY via a DETERMINISTIC criterion separating
  the external weak class (`any_disc_det`), NEVER via the agent's own verdicts, and NEVER
  `calibrated` without human gold; `recommended=False` always. A perfect self-judge does not lift
  the ceiling (tested). Manual + human gold → `calibrated`.
- Bug fixed (caught by a mode test): `Rubric.score` checked blocked-verdict BEFORE weight, so an
  unjudged but demoted (weight-0) agent criterion blocked the whole score. Reordered: weighted-
  before-blocked (a demoted criterion neither contributes nor blocks; a WEIGHTED missing verdict
  still blocks — not silent).
- SKILL.md rewritten → `opentraces-skill-verifier`: the reframe, the trust boundary, BOTH modes,
  the criterion vocabulary, the judge protocol, the calibration statuses, the loop, the MUST-NOTs.
- Tests: `test_verifier_modes.py` (9) — autoverify draft valid + self-aligned; autoverify caps at
  provisional via external anchor; perfect self-judge still not calibrated; autoverify+human gold
  → calibrated; manual scaffold; judge packet evidence-blind; post_verdict groundedness; agent
  can't write gold + human_confirm required; resolve_evidence bounded + unavailable-marks. All
  verifier suites: **103 passed**.
- Real-bucket two-mode demo (`modes_realbucket.py`, 1081 records): autoverify → blocked for ALL
  skills (no weak-negative class exists in real data — honest), recommended=False; manual +
  emulated gold → calibrated for goal-forge/docs-update/architecture-patterns, blocked for
  tdd/review (too few negatives). The provisional path is exercised in unit tests where a negative
  class exists. Trust ceiling holds on real data.

### Adversarial verification of the two modes — 10 trust defects found + ALL fixed
A 4-agent review workflow (trust-ceiling / judge-integrity / regression lenses + triage) confirmed
**2 blockers + 4 majors + 4 minors**, every one reproduced. All fixed; each pinned as a permanent
sentinel in `test_verifier_trust_fixes.py` (M1..M10):
- **M1 (blocker)** emulated labels could launder into `calibrated`. Fix: emulated labels go to a
  SEPARATE `labels/emulated.jsonl` (never the gold ledger); `read_human_labels` excludes emulated;
  `calibrate_rubric(gold_is_emulated=True)` CAPS status at `provisional_weak_only`. The gold ledger
  has exactly one writer (human_confirm=True).
- **M2 (blocker)** a self-judged agent criterion earned dominant reward weight vs the weak label it
  was aligned to. Fix: an `agent` criterion is validated ONLY against independent HUMAN gold; with
  no gold it is demoted to effective_weight 0 (a perfect self-judge earns nothing — tested).
- **M3 (major)** the `any_disc_det` external anchor counted outcome-derived detectors (= the weak
  label re-read). Fix: external anchor must be a DETERMINISTIC, NON-outcome-derived criterion.
- **M4 (major)** the rho non-independence guard fell through when ALL criteria were outcome-derived.
  Fix: all-outcome-derived → `rho_secondary=None` (no circular rho).
- **M5 (minor, root of M2)** the floor weight fraction was checked only on DECLARED weights. Fix:
  re-assert ≥20% independent-deterministic EFFECTIVE weight post-demotion, else `blocked_no_floor`.
- **M6/M8 (major/minor)** groundedness was bypassable with an empty/single-char quote. Fix: the
  quote must be a verbatim span (≥4 chars) of evidence VALUES (not JSON structure).
- **M7 (major)** marker tokens in the skill goal rode into the judge packet via rubric_text. Fix:
  strip `rule[...]` at authoring AND defensively in `build_judge_packet`; sentinel test injects markers.
- **M9 (minor)** string `episode_fields` were unbounded. Fix: per-field cap + visible truncation.
- **M10 (minor)** `evidence_available` defaulted True. Fix: defaults False → a tampered packet
  abstains, never silently passes.
- Evidence: all verifier suites **112 passed**. Corrected real-bucket two-mode demo
  (`modes_realbucket.py`): emulated gold now caps at `provisional_weak_only` (was falsely
  `calibrated` pre-fix — the laundering); autoverify blocked/provisional only; recommended=False
  everywhere. The honesty property holds end to end.

### Full regression after the trust fixes
`pytest tests/ -q` → **3162 passed, 168 skipped, 2 xfailed, 3 failed** (1298s). All 3 failures are
the documented pre-existing/environmental ones, none in any file this work touched (my source
changes are confined to `consumers/verifier_factory/*` + `skill/verifier-creator/` + the verifier
test files + a one-line pyproject entry):
- `test_trace_trails_corpus_fixture_is_current` — PRE-EXISTING branch fixture drift.
- `test_core_perf[inverse-blame-smoke]` + `test_bench_capture_hot_path` — ENVIRONMENTAL perf
  budgets under full-suite load; **both pass in isolation** (`2 passed in 6.04s`).
Net verifier tests added this redesign: ~+80 (3081→3162 passing while the 4320-case parity and all
legacy suites stay green).

### State of the rubric-centric redesign
Built + adversarially verified: the data-model spine (Phase 0), the calibration core + adversarial
gate (Phase 2/3), evidence resolution + the agent-judge protocol (Phase 1/4), and the TWO MODES
(manual alignment session + autoverify self-alignment) with the trust ceiling — hardened against 10
confirmed trust defects. The verifier DEFINITION (what makes a skill verifier) is complete and
trustworthy: agent proposes (self-align or alignment session) → factory scores against evidence +
calibration → human approves; emulated/self-judged signal can never reach `calibrated`.

### Phase 5 — reward swap (no SkillOpt fork) [COMPLETE]
`authoring.score_rubric`: the rubric LABELS each trace (text-invariant: reward=1.0 iff
`rubric.score(verdicts) >= pass_threshold`); SkillOpt then optimizes toward COVERING the failure
markers of CALIBRATION-GATED criteria deficient on TRAIN failures (text-sensitive → Dsel moves;
marker SET fixed by evidence → stuffing a non-gated marker scores nothing). Reuses
`run_optimization` + `FakeReRolloutRunner` + `make_rerollout_gate` VERBATIM (the "no fork" test
asserts identity of the imported objects, not a git diff — skill_opt IS this branch's feature so it
differs from main independently). BLOCKED short-circuit: a non-usable rubric writes a BLOCKED
package (reward=null, recommended=False), never a passing optimization. `calibration.trace_verdicts`
/ `trace_failure_markers` added as public helpers. Recommended only when status==calibrated (real
gold) AND gate accepted; emulated/provisional never recommended. Tests
(`test_verifier_reward_swap.py`, 4): calibrated→Dsel moves; blocked→short-circuit valid package;
emulated→provisional not recommended; skill_opt object-identity reuse.

### Phase 6 — CLI [COMPLETE]
`opentraces skill-verifier {autoverify, align, score, status}` (`cli/skill_verifier.py`, registered
in `cli/__init__.py`). autoverify = self-align + calibrate; align = manual alignment-session
scaffold; score = drive the reward swap (with `--emulate-labels` for a flagged demo); status =
feasibility triage. Read-only over the bucket; never promotes. Tests (`test_skill_verifier_cli.py`, 3).

### Case study (parallel workflow) — fully-worked autoverify, as retrospective manual QA
`runs/skill-verifier-factory/CASE_STUDY.md` produced by the `autoverify-case-study` parallel
workflow (8 agents): fan out autoverify across the 5 skills → deep-dive `docs-update` (the richest
machinery) → 2 adversarial QA reviewers. The doc walks every stage (self-aligned rubric →
per-criterion calibration bare vs +emulated-gold → honest verdict) and ships a 12-item manual-QA
checklist with the expected answer + whether the real data meets it. Headline (honest): bare
autoverify is BLOCKED on all 5 skills (no natural negative class); +emulated-gold reaches at most
`provisional_weak_only` (goal-forge/docs-update/architecture-patterns), never `calibrated`,
recommended=False throughout.
- BOTH QA reviewers independently reproduced EVERY number and rated the doc honest=True,
  usable_as_qa=True. They found: 1 HIGH (no reproduction command + the dump driver was broken),
  1 LOW (stale ctx 14/1078 → 17/1081 ~1.6%), rest info/verified-honest.
- Fixes applied: `autoverify_case.py` bug fixed (passed rubric_id as archetype_id to
  get_skill_examples → now `None`; verified runs clean, numbers match); CASE_STUDY.md §6 Reproduce
  section added (driver + `opentraces skill-verifier {autoverify,status,score,align}` commands); ctx
  number corrected. The verifier numbers were always correct (calibration path never touched the
  buggy line); the QA caught a real artifact-script + doc-runnability gap.

### Status: rubric-centric redesign COMPLETE (Phases 0-6)
All verifier suites incl. Phase 5/6 + CLI: **118 passed** (115 + 3 CLI).
Full `pytest tests/ -q` → **3167 passed, 168 skipped, 2 xfailed, 5 failed** (1376s). 2 of the 5
were NEW and caused by registering the `skill-verifier` root: `test_flat_verbs` /
`test_help_renderer` assert every registered root appears in the curated `--help` layout. Fixed by
adding `skill-verifier` to `COMMAND_SECTIONS` (Workflow section); re-verified **tests/cli + tests/
release: 540 passed, 4 skipped**. The remaining 3 are the documented pre-existing/environmental
failures (trace_trails corpus fixture drift; `inverse-blame-smoke` + `bench_capture_hot_path` perf
budgets under full-suite load — both pass in isolation). Net: the redesign is green; no failure is
in any file this work touched.

Remaining nicety (optional, deferred): an otbox journey driving the `skill-verifier` CLI
network-free (the JTBD/063 SSoT command-map row is skipped in this kb-less worktree per memory).
