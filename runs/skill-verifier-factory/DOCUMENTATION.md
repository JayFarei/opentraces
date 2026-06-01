# Skill Verifier

The Skill Verifier turns "was this agent *skill* used *effectively* in a session?" into a reward signal that can feed SkillOpt to improve the skill's instruction text. The hard part is not producing a number; it is producing a number you are allowed to trust.

> ## Status (honest)
>
> - **Built and adversarially hardened — but BLOCKED on current data by design.** The full rubric / criterion / calibration stack is built and 10 confirmed trust defects were found and closed (the highest-risk ones — M1–M4, M6, M7, M9, M10 — carry named sentinels in `tests/test_verifier_trust_fixes.py`). "Built" does not mean "certifies a skill today" — see the next two points.
> - **Correctly BLOCKED on current data.** Run today against the real bucket (~1078–1081 records; the count drifts as the bucket grows, so figures here and in `CASE_STUDY.md` are per-run snapshots), every seed skill returns `blocked_*`. That is the theoretically correct answer under near-one-class data, not an unfinished feature. The bucket has no independent negative class (`survival_state` empty everywhere, `outcome.success`/`committed` effectively all-positive), so there is nothing for a verifier to separate. A confident green here would be the dishonest outcome.
> - **The real bottleneck is verified labels, not the framework.** Making the verifier certify a skill needs trustworthy negatives. Agent "this looks wrong" flags are *not* trustworthy labels (proven below); only deterministic code-fact checks or human ratification qualify. Producing those is the open work. The framework flips to `calibrated` the moment real human negatives arrive.

## Where things live

- Design spec, data model, calibration math, 17 closed design holes: `runs/skill-verifier-factory/RUBRIC_DESIGN.md`
- Worked behavior + measured numbers + per-finding experiment: `runs/skill-verifier-factory/CASE_STUDY.md`
- Build log: `runs/skill-verifier-factory/log.md`
- Literature backing each design choice: `kb/br/69-skill-verifier-factory-reward-design.md`
- Agent procedure (the skill): `skill/verifier-creator/SKILL.md` (`opentraces-skill-verifier`)
- Code: `src/opentraces/consumers/verifier_factory/`
- CLI: `src/opentraces/cli/skill_verifier.py` (verbs); registration in `src/opentraces/cli/__init__.py`
- Per-finding experiment artifacts: `runs/skill-verifier-factory/process_experiment.py`, `per_finding_dataset.json`

## Overview

### The reframe: trace = evidence, rubric = judgment, calibration = trust

A trace is **evidence** only: the log of what the agent did plus the git lineage of what persisted. It is never, by itself, the verdict of success. The verdict lives in a per-skill **rubric**, a set of weighted criteria, each judged against bounded, read-only evidence by one of three methods:

- `deterministic` — a data-only detector read off the trace; cheap and tamper-resistant.
- `agent` — the in-loop model judges an *evidence-blind* packet, never the skill text.
- `human` — gold labels; the only writer to the gold ledger.

A rubric that merely looks "green" earns nothing. **Calibration is what converts a rubric into trust**: its verdicts must demonstrably *separate* effective from ineffective use, measured against human gold and the one tamper-resistant signal git provides, and a stuffed skill must provably fail to move the verdict. Until that bar is cleared, the rubric returns BLOCKED with a named remedy, never a silent `1.0`.

### The trust ladder

Every rubric resolves to exactly one status, derived mechanically (never author-set):

- **`blocked_<reason>`** — the rubric cannot feed reward; `reward=null`. The reason names the remedy (`blocked_insufficient_labels`, `blocked_no_floor`, `blocked_non_discriminating`, `blocked_inverted`, `blocked_needs_human_labels`).
- **`provisional_weak_only`** — a deterministic, non-outcome-derived criterion separates the weak git signal, but there is no human gold. Reward is usable but flagged; `recommended` stays `False`.
- **`calibrated`** — verdicts separate good from bad against *human* gold and clear the adversarial gate. The only fully-trusted status, and it is always human-gated.

Emulated or self-judged signal can never reach `calibrated`, and no rubric is ever auto-recommended. A human approves promotion (`manual_required_default_off`).

### Two modes

- **AUTOVERIFY** (`opentraces skill-verifier autoverify <skill>`) is the fast path: the agent reads the target skill's stated goal, self-aligns a marker-structured rubric, judges its own semantic criterion, and calibrates, all in one call. Because self-alignment plus self-judgment has no human anchor, its trust ceiling is `provisional_weak_only`. Use it to bootstrap and triage.
- **MANUAL alignment** (`opentraces skill-verifier align <skill>`) lifts the ceiling: in one sitting a human co-establishes the desired outcome, edits the draft criteria, and labels a handful of real example/counterexample traces. Those labels are the gold that unlocks `calibrated`.

### Why BLOCKED is the correct answer on current data

The bucket has no usable negative class: `survival_state` is empty everywhere and essentially everything "succeeded" and committed, so there is nothing for a verifier to separate. A metric that returned a confident green here would be the dishonest outcome.

One result is load-bearing for anyone tempted to shortcut the labeling work. A per-finding experiment on the `review` skill recovered candidate negatives by zooming from whole-trace to per-finding granularity, but **agent "this looks wrong" flags are not trustworthy as labels**: independent fresh-agent verification overturned the dominant flagged finding, which turned out to be a *real* catch, not a hallucination. The verifier cannot yet tell a real catch from a hallucination. Trustworthy labels require independent verification (deterministic code-fact checks or human ratification), and producing them is the real bottleneck, not the framework, which is complete and adversarially hardened.

## Usage

> **Registration caveat (read first).** The verb implementations live in `src/opentraces/cli/skill_verifier.py`, which *is* in commit `0f09450103`. But the lines that wire the group into the CLI (`from .skill_verifier import skill_verifier_group …` and `main.add_command(_skill_verifier_group, name="skill-verifier")` in `src/opentraces/cli/__init__.py`, plus the `workflow.py` edit) are present **only in the working tree, not in `0f09450103`** (both files show `M` / uncommitted). On a clean checkout of that commit `opentraces skill-verifier …` will not resolve until those two files are committed. Everything below assumes the working-tree state where the group is registered.

### The four CLI verbs

All verbs are read-only over the bucket, never promote, and accept `--project <slug>` and `--json`. Run from the repo with the venv active.

```bash
cd /Users/jayfarei/src/tries/community-traces-skillopt && source .venv/bin/activate

opentraces skill-verifier status <skill>                  # feasibility triage: status + episode count + blockers
opentraces skill-verifier autoverify <skill> --json       # self-align a rubric to the skill goal + calibrate (bare)
opentraces skill-verifier align <skill> --json            # scaffold a manual alignment session (desired outcome + draft + traces to label)
opentraces skill-verifier score <skill> --out <dir>       # drive SkillOpt with the rubric; emit a package
opentraces skill-verifier score <skill> --emulate-labels  # pipeline demo with EMULATED gold (caps at provisional, never calibrated)
```

- **`status`** — fastest signal. Prints e.g. `docs-update: blocked_needs_human_labels (recommended=False, 43 episodes)`. Use it to decide whether a skill even has enough evidence to bother.
- **`autoverify`** — the fast path. The agent reads the skill's goal, self-aligns a marker-structured rubric (the generic scaffold + one `agent` criterion), judges it, and calibrates in one call. On real data this correctly returns `blocked_*` with named blockers. Trust ceiling is `provisional_weak_only` and `recommended` is **always False** — self-judgment earns no `calibrated` status.
- **`align`** — entry to MANUAL mode. Emits the desired-outcome prompt, an editable draft rubric, and the worklist of traces to label (`label >= N traces (>= M/class) via record_human_label`). This is where labeling *happens*, not a separate chore.
- **`score`** — runs the rubric through the SkillOpt reward swap and writes a package (default `runs/skill-verifier/<skill>`). It short-circuits to a `BLOCKED:` report rather than a fake `Dsel 0->1` when the rubric isn't `calibrated`/`provisional`. `--emulate-labels` is a transparent stand-in (flagged `gold_is_emulated=True`) for demonstrating the pipeline only.

### The verifier-creator skill loop

`skill/verifier-creator/SKILL.md` (`opentraces-skill-verifier`) defines the agent procedure. The governing trust boundary: **the agent PROPOSES** a rubric, **the factory SCORES** it mechanically against evidence + calibration, **a human APPROVES** promotion (`manual_required_default_off`).

**Criterion vocabulary.** Each criterion declares a `judge_method`:

- `deterministic` — a data-only `DetectorSpec` read off evidence. Cheap, tamper-resistant. The un-self-gradeable **floor**: every rubric needs ≥1 such criterion carrying ≥20% weight, firing on 5–95% of traces (not presence-only).
- `agent` — the in-loop agent judges an *evidence-blind* packet (no skill text, no markers) and posts a grounded verdict. Earns weight only after calibration shows it discriminates against human gold.
- `human` — gold labels; the calibration anchor and the **only** writer to the gold ledger.

A criterion with `direction: negative` (e.g. "claimed success but nothing committed") manufactures a discriminating negative class from data; its *presence* is the failure.

**Alignment session → human ratification.** In MANUAL mode the agent co-establishes the desired outcome with the user, edits the draft criteria, then judges semantic criteria via `build_judge_packet` → reason → `post_verdict(evidence_quote=<verbatim span>)`. A human supplies gold through a separate, confirmation-gated verb: `record_human_label(package_dir, …, label=0|1, human_confirm=True)` raises unless `human_confirm` is True and requires an explicit keystroke. Those labels are what lift status to `calibrated`. The agent path physically cannot write `human.jsonl` (closing the gold-spoofing leak). On `blocked_*`, the named blocker is the remedy ("gather N more negatives"); never present a passing score on a blocked rubric.

### Reproduce the BLOCKED state

```bash
opentraces skill-verifier autoverify docs-update --json              # honest BLOCKED on real data
opentraces skill-verifier score docs-update --emulate-labels --json  # provisional demo only
pytest tests/test_verifier_*.py -q                                   # the trust-fix sentinels
```

## Design and honest limitations

### The rubric / criterion data model

A `Criterion` is a strict additive superset of the legacy declarative archetype's `ContractElement` (three new fields: `judge_method`, `evidence`, `direction`), so the archetype path round-trips byte-identically as a degenerate all-deterministic rubric (`consumers/verifier_factory/rubric.py`). The `value`/`reward`/`gate`/`split`/`calibration` keys deliberately **do not exist** in the authored spec — the agent can propose *what* to judge but can never *set* a score. The validator rejects unknown keys, which is how the *agent proposes → factory scores → human approves* boundary is enforced in practice. Full data model, evidence-binding caps, and the 17 closed design holes are in `RUBRIC_DESIGN.md` §2–§5.

### Calibration: why provisional ≠ calibrated

Calibration is what earns a rubric the right to feed reward. The status machine (`RUBRIC_DESIGN.md` §5.5) gates on label sufficiency, discrimination, an adversarial probe, judge repeatability, and a self-grading guard. The trust ceiling is structural: emulated or self-judged signal can reach `provisional_weak_only` only when a *deterministic, non-outcome-derived* criterion separates the external git signal — it can **never** reach `calibrated`, which requires real human gold (default ≥8 labels, ≥3 per class). `recommended` is always `False` for self-aligned rubrics; promotion is always human-gated. The worked `docs-update` case in `CASE_STUDY.md` §3 shows this: emulated negatives lift it to `provisional` (AUC 1.0) but the M1 cap holds it there and the agent criterion stays demoted (`n_labels=0`).

Two calibration choices follow `br/69`. First, **PR-space metrics over point-AUC**: under near-one-class data ROC-AUC is deceptive, so discrimination is reported as AUPRC + MCC with prevalence, and "beats the weak signal" uses a paired significance test (Williams/DeLong) rather than two point estimates (`br/69` Challenge 2). Second, the **adversarial probe** is recast as status-flip invariance — `verdicts(stuffed_skill) == verdicts(empty_skill)` — which is meaningful even when every rubric is BLOCKED, because the assertion is about status invariance, not a reward delta.

### Trust boundary and the 10 fixed defects

The calibration verdict never reads skill text — a deterministic verdict runs over the trace's evidence bundle; an agent verdict runs over an evidence-blind packet carrying no skill prose and no markers — so marker-stuffing the optimized skill cannot move any verdict. Ten confirmed trust defects were found and closed with permanent sentinels (`tests/test_verifier_trust_fixes.py`), spanning self-judge laundering (M1/M2), outcome-derived non-anchors (M3), the deterministic floor-weight re-assertion (M4), groundedness bypasses via empty/single-char quotes (M6/M8), marker tokens riding into the judge packet through `rubric_text` (M7), and unbounded/silently-passing evidence (M9/M10). The legacy `score_skill(stuffed) ≈ 1.0` regression sentinel is kept to prove the *old* gate was gameable while the new evidence-computed verdicts are invariant (`RUBRIC_DESIGN.md` §6.2).

### Honest limitations

**Label scarcity is the real bottleneck, not tooling.** On the real bucket `survival_state` is `None` across all ~1078 records, `outcome.success`/`committed` are effectively one-class (the only natural negatives are ~26 `committed=False` traces), and `outcome.reward`/`label` are absent. With near-zero negatives, AUC and MCC are mathematically undefined, so the only honest verdict is BLOCKED with a named labeling worklist (`RUBRIC_DESIGN.md` §0). Growing the weak-negative class (e.g. running `trail track` to populate `survival_state`) keeps the system in the non-identifiable one-class regime; the higher-value lever is a small, actively-chosen human anchor amplified by prediction-powered inference (`br/69` Challenge 6). Capping weak-only signal at `provisional` is the theoretical ceiling for this data, not conservatism to relax later.

**Per-finding granularity surfaces candidates but agent flags are not labels.** The per-finding experiment (`process_experiment.py`, `per_finding_dataset.json`) re-scored the `review` skill at sub-finding granularity: **81 findings across 10 traces vs ~10 whole-trace labels**, with 7 agent-suspected negatives. Granularity *does* surface candidate negatives the whole-trace binary label discarded — but the load-bearing result is the opposite of comfortable: independent fresh-agent code-fact verification against the reviewed base commit (`b2d504c`) **overturned the dominant flagged finding**. The highest-confidence `suspect_misread` (the markdown/code-fence injection flag on PR-body intent rendering in `_pick_headline_intent`) was a *real* catch, not a hallucination. The deterministic citation-groundedness leg (`groundedness.py`) can catch fabricated *citations* with zero circularity, but it cannot tell a real semantic catch from a confident misread. The decision record: *the negative class is recovered by per-finding granularity, surfaced by an agent, legitimized by deterministic groundedness or human ratification — never by trusting the agent's own verdict.*

**PPI++ is specced, not built.** `br/69`'s highest-EV experiment (prediction-powered inference over the ~8-label alignment session, Challenge 6 / Exp 1) is documented but unimplemented: `ppi_py`, `sklearn`, and `scipy` are absent and `pr_metrics.py` is pure-numpy. It is the next analytical step once trustworthy labels exist — not a capability shipped today.

## References

- `runs/skill-verifier-factory/RUBRIC_DESIGN.md` — authoritative implementation spec: data model, calibration math, status machine (§5.5), anti-hacking gates (§6), 17 closed design holes, measured ground truth (§0).
- `runs/skill-verifier-factory/CASE_STUDY.md` — fully-worked autoverify case: cross-skill summary (§2), `docs-update` deep dive (§3), known limitations (§5), reproduce steps (§6).
- `runs/skill-verifier-factory/log.md` — build log.
- `kb/br/69-skill-verifier-factory-reward-design.md` — reward-design research brief: literature backing each choice (PR-space metrics, paired significance, PPI++).
- `skill/verifier-creator/SKILL.md` (`opentraces-skill-verifier`) — the agent procedure: trust boundary, criterion vocabulary, the both-modes loop, MUST-NOT list.
