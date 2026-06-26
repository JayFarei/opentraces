# Trace Slicer Library — Phase 0 prototype REPORT (issue #141)

> THROWAWAY prototype validation. Deliverable = this report + the per-slicer GO/NO-GO verdict. Code under `experiments/slicer-prototype/` is disposable and NOT wired into `src/`.

## Verdict summary

| Slicer | Tier | Reliability (independent verifier) | Idempotent | Bounded+Det (cheap-LLM) | Mean conformance | Mean utility (ml/obs/eval) | **Verdict** |
|---|---|---|---|---|---|---|---|
| S1 user-turn (deterministic) | deterministic | 200/200 (1.000) | 200/200 | n/a (deterministic) | 0.799 | 0.676/0.68/0.684 | **GO** |
| S2 change-burst (deterministic) | deterministic | 200/200 (1.000) | 200/200 | n/a (deterministic) | 0.676 | 0.581/0.608/0.577 | **GO** |
| S3 milestone (cheap-LLM) | cheap_llm | 200/200 (1.000) | 200/200 | 200/200 bounded, 200/200 det | 0.495 | 0.506/0.524/0.5 | **GO** |
| S4 subgoal (cheap-LLM) | cheap_llm | 200/200 (1.000) | 200/200 | 200/200 bounded, 200/200 det | 0.552 | 0.488/0.506/0.494 | **GO** |

## Sample (committed deterministic manifest)

- Deterministic-slicer sweep (S1/S2 + S3/S4 mechanical checks): **200 traces** (`manifest_det.json`). The full bucket is **1896 traces**.

  - by agent: `{'claude-code': 111, 'codex-cli': 87, 'pi': 2}`
  - by length bucket: `{'huge': 16, 'medium': 75, 'tiny': 109}`
  - projects represented: **32** (not only community-traces-hf)
  - nasty-case coverage: `{'autonomous': 79, 'error_heavy': 26, 'no_edits': 79, 'subagent_heavy': 33, 'resume': 10, 'control_leak': 62}`
- Cheap-LLM / reviewer subset: **36 traces** (`manifest_llm.json`), stratified from the det sample.
  - by agent: `{'claude-code': 20, 'codex-cli': 15, 'pi': 1}`; by length: `{'huge': 3, 'medium': 13, 'tiny': 20}`; nasty: `{'autonomous': 17, 'error_heavy': 4, 'no_edits': 14, 'subagent_heavy': 4, 'resume': 3, 'control_leak': 11}`

## Mechanical reliability (every trace, deterministic — the HARD gate)

Recomputed by an INDEPENDENT verifier (`verify.recompute_tiling`) from the emitted `trajectories[]` alone — it never reads any self-reported `tiling.valid`. A crash on any trace counts as a FAIL (it shrinks no denominator).

- **S1 user-turn (deterministic)**: tiling valid 200/200, idempotent 200/200, crashed 0, requests bounded 200/200. → reliability **PASS**.
- **S2 change-burst (deterministic)**: tiling valid 200/200, idempotent 200/200, crashed 0, requests bounded 200/200. → reliability **PASS**.
- **S3 milestone (cheap-LLM)**: tiling valid 200/200, idempotent 200/200, crashed 0, requests bounded 200/200, deterministic-under-recorded-answers 200/200. → reliability **PASS**.
- **S4 subgoal (cheap-LLM)**: tiling valid 200/200, idempotent 200/200, crashed 0, requests bounded 200/200, deterministic-under-recorded-answers 200/200. → reliability **PASS**.

## Mechanical worst-cases surfaced (anti-cherry-pick, independent of the LLM panel)

- **s1**: single-trajectory (over-coarse) traces = 4 (e.g. `00416c26-20d`); leaky openers (control msgs that slipped the LOCKED S1 blocklist, e.g. `<system-reminder>`/`[Request interrupted]`) = 25; mean trajectories/100 steps = 11.284.
- **s2**: single-trajectory (over-coarse) traces = 2 (e.g. `01b84873-19a`); leaky openers (control msgs that slipped the LOCKED S1 blocklist, e.g. `<system-reminder>`/`[Request interrupted]`) = 0; mean trajectories/100 steps = 14.656.
- **s3**: single-trajectory (over-coarse) traces = 0 (e.g. ``); leaky openers (control msgs that slipped the LOCKED S1 blocklist, e.g. `<system-reminder>`/`[Request interrupted]`) = 0; mean trajectories/100 steps = 21.077.
- **s4**: single-trajectory (over-coarse) traces = 4 (e.g. `00416c26-20d`); leaky openers (control msgs that slipped the LOCKED S1 blocklist, e.g. `<system-reminder>`/`[Request interrupted]`) = 0; mean trajectories/100 steps = 11.284.

## Reviewer-agent panel (advisory — conformance + 3-persona utility, NEVER gates)

Independent reviewer agents over the 36-trace subset (one per trace, all four slicers each). Sentiment conformance is grounded for S1/S2 (mechanical vs the inlined rule); utility is advisory.

### S1 user-turn (deterministic)
- mean conformance **0.799**; utility ml/obs/eval **0.676 / 0.68 / 0.684** (n=36).
- lowest-conformance examples (worst surfaced, linked to real trace IDs):
  - `417c4f06-122` conformance=0.05: 399 of 414 trajectories are opened by Stop hook feedback (390), /loop control messages (8, including step 642 '# /loop'), or the session-scope activation notice (step 0), directly violating the rule that control/notification messages must never open a trajectory; only 15 of 414 trajectories are opened by genuine human asks.
  - `4a41455f-a93` conformance=0.3: The single trajectory spans 0-3 including the turn_aborted control message at step 3, directly violating s1's rule that control messages must never be part of a work trajectory; compounding this, no genuine human ask anchors the trajectory since steps 0-2 are all agent exec_command steps with no visible user turn.
  - `0e157ea2-540` conformance=0.3: Step 0 is an automation control message ('Automation: Daily bug scan Automation ID: daily-bug-scan') which opens the sole [0-48] trajectory, directly violating the s1 rule that control/notification messages must never open a trajectory; for fully autonomous traces s1 produces a degenerate single blob with no genuine human-ask anchor.
  - `00eec9af-3d6` conformance=0.4: Trajectory [0,0] captures only the first user message with zero agent work; steps 2-44 that respond to that ask are bundled under trajectory [1,68] which also carries the second user task, so the first user turn's work is entirely mis-attributed to the second turn's trajectory.
  - `ecb84bc9-12b` conformance=0.45: Trajectories [20,22] and [23,25] are opened by explicit coordinator notification messages ('The coordinator sent a message while you were working'), and trajectories [12,19], [26,33], [34,58] are opened by subagent-injected 'I am building a curated resource list' prompts, all of which are control/coordinator messages that must never open a trajectory per s1 sentiment — approximately 5 of 14 trajectories are mis-cut.
  - `469ef199-636` conformance=0.5: Steps 37 and 53 are `<codex_internal_context source='goal'>` loop-control signals injected by the Codex system, not genuine human asks, so trajectories [37,52] and [53,67] open on control messages in direct violation of s1's core rule; only step 68 ('remind me the goal?') is a genuine human ask.

### S2 change-burst (deterministic)
- mean conformance **0.676**; utility ml/obs/eval **0.581 / 0.608 / 0.577** (n=36).
- lowest-conformance examples (worst surfaced, linked to real trace IDs):
  - `03eae497-a4c` conformance=0.18: Trajectory [9,129] spans 121 steps and collapses at least four distinct change bursts (merge conflict resolution, demo relocation+adaptation, post-adversarial-review fixes, user-requested header edits) plus multiple pure-exploration and browser-QA phases into a single burst labeled only by the first three files touched, completely defeating the slicer's purpose.
  - `0ba8f840-7d4` conformance=0.2: Trajectory [22-251] is a 229-step mega-burst that collapses all six U0-U5 feature implementations (each with its own read/patch/verify cycle, separated by plan updates at steps 53, 87, 103, 141, 185) into one undifferentiated blob, defeating the core isolation purpose of change-burst slicing.
  - `832bba15-6cf` conformance=0.22: S2 trajectory [48-247] is labeled 'burst: soft-painting-cray.md, MEMORY.md, feedback_security_tools_no_tiers.md' (3 plan/memory files) but actually spans all 10 implementation phases - walker, 6 tool wrappers, pipeline rewiring, CLI, tests, legacy deletion, version bump, and privacy-filter - completely defeating the 'isolate a coherent code-change burst' invariant.
  - `19f1a2f4-4fc` conformance=0.28: The [82,166] trajectory is an 85-step mis-cut that lumps at least four distinct change bursts, an initial fix wave (82-93), a test-repair loop (97-129), a new tmux-session-tracking feature (130-145), and a CI fail-fast fix (158-163), into a single label despite clear explore gaps between them.
  - `00416c26-20d` conformance=0.4: Burst trajectory [27, 92] conflates 7+ distinct edit-test cycles with fully interleaved test/verify spans (e.g., steps 32-37, 51-67, 74-80) into one 66-step block instead of isolating each coherent change burst with just its surrounding verify, while those inter-burst test spans should be separate explore trajectories per s2 sentiment.
  - `67204cda-73d` conformance=0.48: Steps 4-11 are mislabeled 'explore' despite containing a failed deploy (step 5), a forced npm CLI upgrade (step 7), a successful deploy (step 8), and live-site verification (step 10) -- none of which are pure exploration -- leaving the isolated 1-step burst (step 3) with no accompanying verify tail.

### S3 milestone (cheap-LLM)
- mean conformance **0.495**; utility ml/obs/eval **0.506 / 0.524 / 0.5** (n=36).
- lowest-conformance examples (worst surfaced, linked to real trace IDs):
  - `4a41455f-a93` conformance=0.2: The single milestone trajectory 0-3 ends on a turn_aborted user interrupt rather than a verified outcome, making the milestone framing actively incorrect; no verified patch, test pass, render, or answered question was produced.
  - `ecb84bc9-12b` conformance=0.2: The workspace build phase (steps 42-57) is catastrophically over-segmented into 12 single-step trajectories one per Write call (MISSION.md, RESOURCES.md, GLOSSARY.md, etc.), and each lesson then generates 4-6 singleton trajectories, yielding 51 total for a 98-step trace and violating the core rule that intermediate successes on the same artifact should not over-segment.
  - `15a19ab1-248` conformance=0.2: Trajectory [0,1] labeled 'milestone: <test>' is a clear mis-cut: step 1 is the agent planning and issuing its first exec_command, not a verified outcome, and the placeholder label '<test>' does not correspond to any confirmed artifact, violating the 'verified outcome' requirement and over-segmenting a single-task trace.
  - `0006e1e2-7a2` conformance=0.28: Steps 15, 16, 17, and 18 are each isolated as single-Write milestones for individual project files, violating the 'intermediate successes on the SAME artifact should not over-segment' rule since all four serve the single bootstrapped-project deliverable; additionally the label 'milestone: <test>' on trajectory [0,8] maps to nothing in the trace (those steps are pure research and vault-querying, not a test outcome).
  - `50c0b1a2-004` conformance=0.28: Steps 30-35 are atomized into six single-step trajectories (e.g. [30,30] labeled 'milestone: test' for one exec_command consistency-pass read) despite being intermediate steps on the same KB-doc artifact, directly violating the 'intermediate successes on the SAME artifact should not over-segment' rule.
  - `010034bf-a46` conformance=0.28: The cut at step 19 (a bare 'Chunk ID: 363ee1 ... exit code 0' exec_command result) is not a verified outcome milestone; the real milestones are the live-repo discovery at step 12 and the final findings at step 22, making the resulting 3-step 'milestone: <final>' trajectory [20-22] incoherent and the label 'milestone: <test>' for [0-19] uninformative.

### S4 subgoal (cheap-LLM)
- mean conformance **0.552**; utility ml/obs/eval **0.488 / 0.506 / 0.494** (n=36).
- lowest-conformance examples (worst surfaced, linked to real trace IDs):
  - `417c4f06-122` conformance=0.1: S4 is byte-identical to S1 (programmatically confirmed: S1==S4: True), and the first trajectory [0,138] merges codebase exploration, VFS hook registry implementation, auto-invoke feature implementation, unit test writing, and integration eval launch into a single sub-goal despite clear internal pivot points a correct judge would have cut on.
  - `0ba8f840-7d4` conformance=0.15: The judge added zero pivots producing output identical to s1, leaving trajectory [13-251] as a 238-step undifferentiated blob spanning six distinct deliverables (U0-U5) each with verified plan-update completions at approximately steps 52, 87, 103, 141, and 185 that a correct judge would cut on.
  - `469ef199-636` conformance=0.2: S4 is byte-identical to s1 (same 4 spans, same labels) because the conservative default judge added zero pivots; trajectory [0,36] collapses at least five distinct sub-goals (exploration, core feature implementation, testing gate, docs update, blocked-deploy documentation) into one 37-step 'subgoal', completely missing the internal agent pivots a correct judge should cut on.
  - `0e157ea2-540` conformance=0.2: The trace has at least three clear internal subgoal pivots a real judge should cut on: step 23 (investigate-to-fix), step 26 (fix-to-verify), and step 39 (verify-to-report-writing); the conservative default judge answered 'stay' to all 13 pivot-check requests and produced a single [0-48] trajectory identical to s1, adding no value.
  - `54d7d4ef-3fd` conformance=0.22: The trace contains at least four clear agent pivots a good subgoal judge would cut - commit audit (0-3), bug reproduction and confirmation (4-14), `_scan` code fix and test verify (15-20), automation memory update (21-24) - but the conservative default judge produced a single [0,25] trajectory identical to s1, making s4 useless as a subgoal decomposition for this session.
  - `0484f99d-e9d` conformance=0.22: Trajectory [3-72] spans 70 steps and contains at least four clear agent pivots (exploration to implementation at step 22, pack generation complete at step 29, P4-T1 fix committed at step 36, R7 final pass at step 53) that the conservative default judge failed to cut on, producing an output identical to s1 with no sub-goal granularity.

## Per-slicer GO/NO-GO verdict + rationale

### S1 user-turn (deterministic) → **GO**
- HARD gate PASS: reliability 1.000, idempotent, no crashes over the stratified sample. Tiling recomputed independently.
- Advisory: conformance 0.799, utility 0.676/0.68/0.684 (3 reviewer worst-cases).
- Strong (conformance grounded/mechanical). Caveat: control messages NOT in the LOCKED blocklist (`<system-reminder>`, `[Request interrupted]`, and `<turn_aborted>` when a trace has no genuine user turn) open trajectories — 25 leaky openers across the 200-sample. The S1 rule is LOCKED for v1; widening the blocklist is a v1.1 follow-up. No reliability impact.

### S2 change-burst (deterministic) → **GO**
- HARD gate PASS: reliability 1.000, idempotent, no crashes over the stratified sample. Tiling recomputed independently.
- Advisory: conformance 0.676, utility 0.581/0.608/0.577 (4 reviewer worst-cases).
- Reliable + deterministic. Advisory: on no-edit traces S2 degrades to an S1 copy with `explore` labels, and long edit sessions can lump multiple bursts into one trajectory (reviewer mis-cuts surfaced). Phase 1 reuses `core/bursts.py` clustering verbatim (gap=35); mis-cuts are a tuning/utility matter, not a tiling defect.

### S3 milestone (cheap-LLM) → **GO**
- HARD gate PASS: reliability 1.000, idempotent, bounded + deterministic-under-answers, no crashes over the stratified sample. Tiling recomputed independently.
- Advisory: conformance 0.495, utility 0.506/0.524/0.5 (15 reviewer worst-cases).
- Reliable + deterministic + bounded, BUT lowest advisory conformance (0.50, 15/36 worst cases). The PROTOTYPE's deterministic success-detector was too loose — it closed trajectories on test FAILURES ('3 pass 1 fail') and fabricated `<test>` artifacts. PHASE-1 REMEDIATION (mandatory): tighten success detection to require an UNAMBIGUOUS deliverable signal and NEVER close on a failure; the same-outcome collapse is the agent-loop judgment. Conformance is advisory and does not gate GO; reliability is the gate and it holds.

### S4 subgoal (cheap-LLM) → **GO**
- HARD gate PASS: reliability 1.000, idempotent, bounded + deterministic-under-answers, no crashes over the stratified sample. Tiling recomputed independently.
- Advisory: conformance 0.552, utility 0.488/0.506/0.494 (10 reviewer worst-cases).
- Reliable + deterministic + bounded. With the prototype's conservative default judge S4 is identical to S1 (reviewers confirmed real internal pivots a good judge would catch). S4's value is ENTIRELY the agent-loop pivot judgment, which Phase 1 ships (the prototype only stubbed it). GO on the hard gate; utility realised by the real `agent` judge.

## Gate decision

- **GO slicers (enter Phase 1):** s1, s2, s3, s4
- **NO-GO slicers (excluded from Phase 1):** (none)
