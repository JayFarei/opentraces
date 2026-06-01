# Rubric-Centric Skill Verifier Redesign — Authoritative Implementation Spec

Status: authoritative. Supersedes the marker-coverage verifier as the *reward source*.
Scope: ONE skill (`opentraces-skill-verifier`, the renamed verifier-creator) + its toolset,
plus an additive `rubric.py` / `judge.py` / `calibration.py` / `reward.py` layer inside
`consumers/verifier_factory/`. The declarative-archetype path is preserved verbatim as a
strict subset. SkillOpt is not forked.

This spec grafts the four review lenses:
- **datamodel-integration**: Rubric IS the archetype (Criterion = ContractElement + 3 additive
  fields); reward swap at the two `packaging.py` seams; `reward_basis` flag; no fork.
- **judge-calibration**: evidence-blind judge packets; content-addressed append-only verdict
  ledger; gold-wins weak/strong label fusion.
- **measurement-discrimination**: discrimination is the right-to-exist bar; BLOCKED-with-named-
  remedy is the default; selfcheck promoted to hard build gates.
- **trust-antihacking**: the reward never reads skill text to *grade* a trace; reward-invariance
  of the *calibration verdict* is the canonical anti-hacking proof; structural floor of
  un-self-gradeable criteria.

Every blocker/major hole the adversarial reviewers found is listed in §11 with how it is closed.

---

## 0. Empirical ground truth (measured, not assumed)

Run on 2026-05-29 against the real bucket (`iter_trace_record_objects`, 1078 records):

```
records = 1078
TraceRecord.outcome.survival_state : {None: 1078}          # ALL None — Trail not resolved
TraceRecord.outcome.success        : {True: 1052, None: 26}  # never False
TraceRecord.outcome.committed      : {True: 1052, False: 26} # the ONLY natural negative class
TraceRecord.outcome.reward / label : {None: 1078}            # absent on the record
context_tree_summary present       : 14 / 1078 (1.3%)
```

Per-skill episode projection (`build_episode_rows`):

```
review     n=16  labels={success:16}            committed={True:16}   # 0 negatives
goal-forge n=67  labels={success:67}            committed={True:67}   # 0 negatives
tdd        n=48  labels={success:47,unknown:1}  committed={True:47,False:1}  # 1 negative
```

**Three facts that drive the whole design:**

1. **Trail survival is empty.** Every reviewer's "survival weak-label" leg yields zero usable
   labels today. We DEMOTE survival to a best-effort, abstain-loud leg (it becomes load-bearing
   only after `opentraces trail track` resolution populates the bucket), and we make the
   ALWAYS-AVAILABLE weak negative class **`committed=False`** (26 records, present in `tdd`),
   which the episode projection already carries via `outcome_label=="unknown"` and
   `outcome_reward==0.0`. The negative-direction criterion keys off the **claimed-success-vs-
   no-commit contradiction** (a real contradiction in the data) instead of reverted/lost
   (which does not exist).
2. **Human labels are the only path to GREEN.** With ≤1 natural negative per skill, the
   `calibrated` status is reachable ONLY after a human supplies negatives. The v1 deliverable
   is therefore an HONEST BLOCKED verdict + a named labeling worklist for all five seed skills,
   plus the full machinery that flips to `calibrated` the moment labels arrive. We state this
   plainly in the report headline and the SKILL.md; we never present a hollow GREEN.
3. **ctx_reads is ungroundable (1.3% coverage).** No reward-bearing criterion may depend on it.
   `ctx_reads` is allowed only as `judge_method="agent"` advisory evidence, never as the sole
   binding of a reward-bearing criterion.

---

## 1. Thesis

A trace is **evidence** (logs of what the agent did + the git lineage of what persisted), never
the verdict of success. The **rubric** is the judgment artifact: a per-skill set of weighted
criteria, each judged against bounded, read-only evidence by a `deterministic`, `agent`, or
`human` method. **Calibration is trust**: a rubric earns the right to feed reward only after its
verdicts demonstrably separate effective from ineffective skill use — measured against human
gold labels and against the one tamper-resistant signal git provides (commit-landing, with Trail
survival when present) — and only after a hard adversarial gate proves a marker-stuffed skill
cannot move the rubric's verdict. Until then the rubric surfaces **BLOCKED with a named remedy**,
never a silent 1.0. The calibrated rubric labels the held-out traces; SkillOpt then optimizes the
skill text toward covering the failure modes those *calibrated* labels prove — so the reward stays
a function of the artifact being edited while the judgment stays grounded in evidence the agent
did not write.

---

## 2. Data model

New module `consumers/verifier_factory/rubric.py`. A **Rubric is a strict additive superset of
`VerifierArchetype`**; a **Criterion is `ContractElement` + 3 additive fields**. An unchanged
archetype dict round-trips byte-identically as a degenerate all-`deterministic` rubric — this is
how the declarative-archetype path cannot regress. New artifact schemas live in `schema.py`
(additive constants), validated by the same unknown-key-rejecting pattern as
`validate_archetype_dict` / `validate_detector_dict`.

### 2.1 `judge_method` enum

```python
JUDGE_METHODS: frozenset[str] = frozenset({"deterministic", "agent", "human"})
```

- `deterministic` — a `DetectorSpec` (reused verbatim) read off **evidence**, never skill text.
  Cheap, bit-reproducible, tamper-resistant. Default for a migrated `ContractElement`.
- `agent` — the in-loop operating agent (Claude/Codex on the user's subscription) judges an
  evidence-blind packet locally and POSTs a verdict. Default for new *semantic* criteria.
- `human` — gold labels, the calibration anchor and the only writer to the gold ledger.

### 2.2 `Criterion` (replaces/extends `ContractElement`)

```python
@dataclass(frozen=True)
class Criterion:
    # --- existing ContractElement fields (unchanged names/semantics) ---
    element_id: str                       # == criterion_id (alias preserved, see §2.8)
    label: str
    marker: str                           # SkillOpt rule tag, rl.<skill>.<id> (UNCHANGED)
    detectors: DetectorSpec = DetectorSpec()   # reused verbatim
    weight: float = 1.0                   # >0
    # --- 3 additive fields (defaults preserve legacy behaviour) ---
    judge_method: str = "deterministic"   # in JUDGE_METHODS
    evidence: EvidenceSpec = EvidenceSpec()    # §3; empty => detector reads EpisodeEvidence
    direction: str = "positive"           # "positive" | "negative"
    rubric_text: str = ""                 # the natural-language judging question
                                          # REQUIRED for agent/human; optional for deterministic

    def detect(self, evidence) -> bool:   # legacy path: identical to ContractElement.detect
        return detector_matches(self.detectors, evidence)
```

- `direction="negative"`: the criterion's **presence is a failure** (e.g. "verdict claims
  success but no commit landed"). This is how we manufacture a discriminating negative class from
  the data we actually have (`committed=False`). For scoring, a negative-direction criterion
  contributes `1 - verdict.value` to the rubric score; for failure-tagging it emits its marker
  when it FIRES.
- `_CRITERION_KEYS` frozen set = `{element_id, criterion_id, label, marker, detectors, weight,
  judge_method, evidence, direction, rubric_text}`. Forbidden author keys (rejected with the
  existing message): `value, score, verdict, reward, gate, split, calibration, calibrated,
  recommended, confidence, evidence_digest, weak_label`. The `value` field literally does not
  exist in the authored spec — this is *why* an agent can propose WHAT to judge but never SET a
  score (trust boundary, extended from archetype to criterion).

### 2.3 `EvidenceSpec` — see §3 (the per-criterion read-only binding).

### 2.4 `Rubric`

```python
@dataclass(frozen=True)
class Rubric:
    schema_version: str = "opentraces.skill_verifier_rubric.v1"
    rubric_id: str = ""
    skill: str = ""
    title: str = ""
    description: str = ""
    semantic_target: str = ""
    baseline_skill: str = ""
    criteria: tuple[Criterion, ...] = ()
    creator_questions: tuple[CreatorQuestion, ...] = ()   # reused verbatim
    live_legs: tuple[LiveLeg, ...] = ()                   # reused verbatim
    min_traces_for_split: int = 6                         # reused
    min_human_labels: int = 8                             # NEW; see §5 thresholds
    # OUTPUT-ONLY, never author-settable, stripped+recomputed on input (defense in depth):
    calibration: CalibrationReport | None = None

    @property
    def markers(self) -> tuple[str, ...]:
        return tuple(c.marker for c in self.criteria)

    def score(self, verdicts: dict[str, "Verdict"]) -> float | None:
        """Weighted, normalized rubric score over per-criterion verdicts for ONE trace.
        Returns None (BLOCKED) if any contributing criterion is `blocked`, or if every
        weighted criterion was demoted to 0 (all-demoted => non-discriminating; never 0/0)."""
        num = 0.0; den = 0.0
        for c in self.criteria:
            v = verdicts.get(c.id)
            if v is None or v.blocked:
                return None
            w = self._effective_weight(c)   # demoted criteria carry 0 (set by calibration)
            if w <= 0: continue
            contrib = (1.0 - v.value) if c.direction == "negative" else v.value
            num += w * contrib; den += w
        if den <= 0:
            return None                      # all-demoted: non-discriminating, explicit BLOCK
        return round(num / den, 6)
```

- `RUBRIC_KEYS` frozen set = `ARCHETYPE_KEYS ∪ {rubric_id, criteria, min_human_labels}` minus
  nothing; `calibration` is NOT in `RUBRIC_KEYS` (output-only). `contract_elements` is accepted
  as a **deprecated alias** mapping 1:1 to `criteria` (see §2.8 migration).

### 2.5 `EvidenceSpec` (frozen, the bounded read-only binding) — full fields in §3.

### 2.6 `Verdict` (append-only ledger row)

```python
@dataclass(frozen=True)
class Verdict:
    schema_version: str = "opentraces.skill_verifier_verdict.v1"
    verdict_id: str = ""        # see below
    rubric_id: str = ""
    criterion_id: str = ""
    trace_id: str = ""          # the EVIDENCE this verdict is about
    unit_id: str = ""
    judge_method: str = ""
    judge_id: str = ""          # "deterministic:SECURITY_VERSION" | "agent:<model>@<session>"
                                #   | "human:<handle>"  (stamped by the tool, never trusted from body)
    value: float = 0.0          # in [0,1] (deterministic => {0.0,1.0})
    label: str = "pass"         # "pass" | "fail" | "abstain"
    rationale: str = ""         # required for agent/human, <=2000 chars
    evidence_digest: str = ""   # sha256 of the exact bound-evidence bytes the judge saw
    evidence_epoch: str = ""    # bucket maturation epoch (see §4 / hole closure)
    created_at: str = ""
    blocked: bool = False
    blocked_reason: str = ""    # e.g. "evidence_unavailable"

# verdict_id = sha256(rubric_id:criterion_id:trace_id:judge_method:evidence_digest)[:16]
# repeatability_key = sha256(rubric_id:criterion_id:trace_id:evidence_digest)
```

`validate_verdict_dict` rejects unknown keys AND `{reward, gate, split}`. The append-only ledger
lives under the PACKAGE dir, NEVER in the read-only bucket:
`<package>/verdicts/{deterministic,agent,human}.jsonl`. Each file is content-addressed and
de-duped by `verdict_id` at read time. `human.jsonl` has exactly one writer: `record_human_label`
(see §4 / §8 — closes the gold-spoofing leak).

### 2.7 `CalibrationPolicy` + `CalibrationReport`

```python
@dataclass(frozen=True)
class CalibrationPolicy:
    """Named, NON-author-settable thresholds. The single source of truth for the gate.
    Per-rubric override is allowed ONLY downward (stricter), never via the authored spec."""
    schema_version: str = "opentraces.skill_verifier_calibration_policy.v1"
    min_human_labels: int = 8
    min_human_per_class: int = 3
    min_eff_neg: float = 3.0           # n_human_neg + W_WEAK * n_weak_neg
    weak_neg_weight: float = 0.4       # confidence of a weak (committed-derived) negative
    auc_min: float = 0.70
    auc_min_pairs: int = 8             # AUC undefined / BLOCKED below this many pos*neg pairs
    rho_min: float = 0.30              # |Spearman| floor for the secondary discriminator
    rho_min_n: int = 8                 # BLOCKED below this many resolved points (closes hole)
    per_criterion_min_labels: int = 4  # below this a criterion is not calibrated (weight stays
                                       #   provisional; agent criteria => weight 0 until met)
    per_criterion_precision_min: float = 0.60
    per_criterion_discrimination_min: float = 0.15   # selfcheck separation, vs INDEPENDENT truth
    adversarial_margin_min: float = 0.20
    judge_flip_rate_max: float = 0.15

@dataclass(frozen=True)
class CalibrationReport:
    schema_version: str = "opentraces.skill_verifier_calibration.v1"
    rubric_id: str = ""
    generated_at: str = ""
    policy: CalibrationPolicy = CalibrationPolicy()
    n_human_labels: int = 0
    n_human_pos: int = 0
    n_human_neg: int = 0
    n_weak_neg: int = 0          # committed=False (+ reverted/lost when Trail resolves)
    n_eff_neg: float = 0.0
    per_criterion: tuple[dict, ...] = ()   # {criterion_id, precision, recall, f1,
                                           #   discrimination, n_labels, effective_weight, demoted}
    auc_human: float | None = None         # None when undefined (n_neg==0) => not a number
    rho_secondary: float | None = None     # Spearman(rubric_score, committed/weak) when computable
    adversarial: dict = None               # {margin, stuffed_status_flip, trivial_action_delta, passed}
    max_flip_rate: float | None = None
    status: str = "uncalibrated"           # see §5 status machine
    blockers: tuple[str, ...] = ()         # named remedies, e.g. "need 2 more human negatives"
    pass_threshold: float = 0.999          # rubric_score cut for the binary success/failure split
```

Both `CalibrationPolicy` and `CalibrationReport` are produced by the factory in
`calibration.py`. **They are never author-settable.** `calibration` on a `Rubric` is OUTPUT-only:
`rubric_from_dict` strips any `calibration` key on input and the validator rejects it inside
`criteria`.

### 2.8 Additive schema_version + ContractElement→Criterion migration (concrete)

New constants in `schema.py` (additive, alongside the existing four):

```python
RUBRIC_SCHEMA_VERSION       = "opentraces.skill_verifier_rubric.v1"
VERDICT_SCHEMA_VERSION      = "opentraces.skill_verifier_verdict.v1"
CALIBRATION_SCHEMA_VERSION  = "opentraces.skill_verifier_calibration.v1"
CALIBRATION_POLICY_VERSION  = "opentraces.skill_verifier_calibration_policy.v1"
# PACKAGE_SCHEMA_VERSION stays v1; the spec gains additive `rubric`+`calibration`+`reward_basis`
# blocks (additive keys inside an existing object => no consumer break).
```

Migration discipline (Phase 0, BEFORE any new field is added):
- `Criterion.id` is a read-only property returning `criterion_id or element_id`. The dataclass
  keeps `element_id` as the canonical field; `criterion_id` is an optional alias accepted on input.
- `Criterion.to_dict()` emits `element_id` (NOT `criterion_id`) so existing
  `packaging.py:413`-style readers round-trip byte-identically. It emits `judge_method` /
  `evidence` / `direction` / `rubric_text` ONLY when non-default (mirrors `DetectorSpec.to_dict`).
- `_CRITERION_KEYS` widens `_ELEMENT_KEYS`; `validate_archetype_dict` is widened to accept the new
  element keys, and a parallel `validate_rubric_dict` is added. A Phase-0 round-trip test asserts:
  old archetype dict → `rubric_from_dict` → degenerate all-deterministic Rubric →
  `archetype_to_dict`-equivalent dict == original, for all five seed archetypes.

### 2.9 WHY the validator rejects unknown keys (and excludes reward/gate/split)

Rejecting unknown keys is the trust boundary *in practice* (the proven `validate_detector_dict`
pattern, `detectors.py:177`). The agent **PROPOSES** a rubric (criteria + bindings + judge
methods + rubric_text); the factory **SCORES** mechanically; a human **APPROVES**. If a spec could
carry `reward` / `gate` / `split` / `calibration` / `value` / `recommended`, the agent could grade
its own work and set its own optimization target — collapsing the boundary. The frozen key sets
DELIBERATELY EXCLUDE every one of those. Additional structural rules enforced by
`validate_rubric_dict`: `judge_method ∈ JUDGE_METHODS`; `detectors` REQUIRED-and-valid iff
`judge_method=="deterministic"` and MUST be empty for `agent`/`human` (no smuggling a
deterministic auto-pass into a semantic criterion); `rubric_text` required for `agent`/`human`;
a criterion with `judge_method != "deterministic"` and an empty `EvidenceSpec` is rejected (a
semantic judge must be grounded); markers unique + namespaced; weights > 0; **≥1 criterion that
is `deterministic` OR a calibrated negative-direction criterion** (structural floor — no rubric
can be 100% agent-graded, see §4/§11).

---

## 3. Evidence binding (`EvidenceSpec`)

The ONLY way a criterion sees a trace. Every field maps to an EXISTING read-only projection and is
hard-capped. Nothing writes; no shell/network/path/exec source exists.

```python
@dataclass(frozen=True)
class EvidenceSpec:
    episode_fields: tuple[str, ...] = ()   # subset of {user_intent, task_summary,
                                           #   files_touched_json, tools_used_json,
                                           #   outcome_label, outcome_reward} (EpisodeEvidence)
    trace_slice: dict | None = None        # {template in {bursts,around_step,around_patch},
                                           #   max_steps:int<=40, around:int|None}
    trace_get: bool = False                # bounded trace head/outcome summary
    trail_survival: bool = False           # anchors_for_trace_with_survival(trace_id)
    diff: dict | None = None               # {max_bytes:int<=20000} committed-patch text/ranges
    ctx_reads: dict | None = None          # {max_layers:int<=8} — BEST-EFFORT ONLY (1.3% cov)
    produced_artifact: dict | None = None  # {path_glob:str, max_bytes:int<=65536}
```

| field | read-only surface (existing) | bound, bounded by |
|---|---|---|
| `episode_fields` | `archetypes.evidence_from_episode(episode, record)` | fixed projection; subset |
| `trace_slice` | `core.trace_slices.slices_from_bursts` / `slice_around_step` | `max_steps<=40` |
| `trace_get` | bucket_store record head/outcome | bounded summary, not full transcript |
| `trail_survival` | `TrailQueryProjection.anchors_for_trace_with_survival` | per-anchor state; READ-ONLY git |
| `diff` | `TrailQueryProjection.patches_for_trace` (ranges + committed text) | `max_bytes<=20000` |
| `ctx_reads` | `ContextTreeProjection.reads_for_trace` | `max_layers<=8`; ABSTAIN if absent |
| `produced_artifact` | package-produced row/best_skill bytes | `max_bytes<=65536` |

Validator (`validate_evidence_spec`): rejects any key outside the frozen set; clamps every cap to
its maximum; rejects a non-deterministic criterion whose ONLY binding is `ctx_reads` (ungroundable
on this bucket); restricts `deterministic` criteria to `{episode_fields, trace_get, trail_survival,
diff}` (cheap + CI-safe; an agent-only rich source like `trace_slice`/`produced_artifact` cannot
back a deterministic verdict). `resolve_evidence(spec, trace_id, record, projections) ->
EvidenceBundle` assembles a deterministic, canonical-JSON-serializable dict with **visible
truncation markers** (`"...[truncated:N more]"`, never silent), computes
`evidence_digest = sha256(canonical_json(bundle))`, and returns `{payload, digest, refs, epoch}`.
Determinism (sorted keys, bounded sizes, `mtime=0` discipline) makes the digest stable across
machines. An unsatisfiable binding yields a **blocked** verdict, never a silent pass.

---

## 4. Agent-as-judge protocol

The `agent` judge is the in-loop operating agent itself, run LOCALLY, posting back through a tool.
No external LLM endpoint; default CI replays a frozen verdict fixture. New module `judge.py`.

### 4.1 Receive — `build_judge_packet(rubric, criterion_id, trace_id, *, projections) -> JudgePacket`

```python
JudgePacket = {
  "judge_request_id": verdict_id,        # pre-computed idempotent key
  "criterion": {criterion_id, rubric_text, direction, allowed_labels: [pass,fail,abstain]},
  "bound_evidence": <EvidenceBundle.payload>,   # bounded, read-only; NO skill text, NO markers
  "evidence_digest": sha256(bound_evidence),
  "evidence_epoch": <bucket maturation epoch>,
  "instructions": ("Render value in [0,1] and a one-sentence rationale GROUNDED in a verbatim "
                   "span of bound_evidence. Judge the EVIDENCE, not any skill. If evidence is "
                   "insufficient return label='abstain'.")
}
```

The packet contains **no skill text and no marker tokens** — the marker-stuffing channel is closed
at the protocol layer (the judge cannot see the artifact being optimized). One criterion × one
trace per packet (isolation: no halo, no cross-criterion consistency-faking).

### 4.2 Render + Post — `post_verdict(judge_request_id, value, rationale, evidence_quote, judge_id) -> Verdict`

Validation on post (all deterministic, on the agent's OWN output):
1. `value` in `[0,1]` (or in the criterion's scale set), else reject.
2. `rationale` non-empty AND `evidence_quote` is a **verbatim substring** of `bound_evidence`, else
   reject (`"rationale not grounded in bound evidence"`) — the agent cannot post about evidence it
   was not shown. (This catches hallucinated payloads, NOT mis-judgment; mis-judgment is caught by
   calibration — §5.)
3. recomputed `evidence_digest` must equal the packet's, else reject (judged a drifted payload).
4. `judge_id` is stamped from the running session env (`agent:<model>@<session>`), never trusted
   from the body. **A post to `human.jsonl` is impossible from this tool**; human gold has its own
   writer (§8 `record_human_label`) gated by an explicit, separate, human-confirmation CLI verb.

On accept: append a `Verdict` to `verdicts/agent.jsonl`, content-addressed by `verdict_id`.

### 4.3 Record / audit / repeatability

- Append-only, de-duped by `verdict_id`. Every verdict carries `evidence_digest` + `judge_id` +
  `rationale` + `evidence_epoch`, so a reviewer re-resolves the bundle read-only and re-asks.
- Default CI is **network-free and agent-free**: it replays the committed `verdicts/agent.jsonl`
  as a frozen oracle. Live judging is an interactive/opt-in step (same posture as
  `default_enabled=False` live legs).
- **Maturation/epoch reconciliation (closes hole):** `evidence_epoch` pins the bundle to the
  bucket maturation snapshot at first judge. A re-judge after maturation (which can change
  `survival_state`) produces a new digest → new `verdict_id`; the audit verb
  `verdicts_status` flags `evidence_epoch` drift and reports stale-vs-current per criterion rather
  than silently de-duping across epochs. Idempotency holds WITHIN an epoch (stated explicitly).
- **Repeatability (G4):** `recalibrate` accepts N≥2 verdict runs over identical packets and reports
  per-criterion `flip_rate` = fraction of `repeatability_key`s with disagreeing labels. `flip_rate
  > policy.judge_flip_rate_max` is a hard BLOCK (the criterion is too noisy to judge). This is
  human-labor-bearing (real agent turns) and is OUT of default CI; default CI replays the recorded
  runs (§10 scope).

### 4.4 Self-grading collapse — prevented by calibration AND a structural floor (not prohibition)

The locked decision allows the in-loop agent to judge. Collapse is prevented by:
- **(a) Calibration funnel:** an `agent` criterion's verdicts feed reward only after they clear
  per-criterion precision/recall vs human AND the rubric clears discrimination (§5). A rubber-stamp
  judge (all-pass) fails recall against human negatives and fails discrimination against the
  `committed`-derived weak negative.
- **(b) Structural floor (validator-enforced):** ≥1 `deterministic` OR calibrated negative-
  direction criterion, so a non-trivial fraction of every rubric's weight is un-self-gradeable by
  construction. A 100%-agent rubric is rejected before calibration.
- **(c) Hardened co-grounding (closes the permissive-floor hole):** a `deterministic` criterion
  used to satisfy the structural floor must itself pass a non-degeneracy check — its detector must
  fire on `>X%` AND `<Y%` of available traces (default `5% < fire_rate < 95%`) and must NOT rest
  solely on a presence-only signal (`detector_permissiveness_flags` is promoted from advisory to a
  HARD reject for the floor criterion specifically; it stays advisory for non-floor criteria).
  `committed=True` (true for 1052/1078 ≈ 97%) therefore CANNOT launder an agent criterion into
  full weight; the negative-direction `committed=False` criterion (fires ~2.4%, below the 95% cap
  and above 0 once labeled) is the legitimate floor.
- **(d) Evidence-blindness:** the judge never sees skill text/markers.
- **(e) Gold integrity:** the agent cannot write `human.jsonl` (§4.2 / §8).
- **(f) `same_session_self_judge` is BLOCKING when human labels are absent:** if the only semantic
  signal is the agent's own verdicts AND the rubric was authored in the same session, status cannot
  be `calibrated` (it caps at `blocked_needs_human_labels`). Advisory only once human gold exists.

---

## 5. Calibration math

Module `calibration.py`. Pure-Python, no numpy/sklearn, deterministic, network-free. Ground truth =
human gold labels PLUS the weak negative class the bucket actually has.

### 5.1 Weak negative label (what exists today, not what we wish existed)

```python
def weak_label(trace_id, episode, record) -> int | None:
    # Strong negative the data has: claimed success but nothing landed.
    committed = bool(getattr(getattr(record,"outcome",None),"committed",False))
    survival  = getattr(getattr(record,"outcome",None),"survival_state",None)
    if survival in _DEAD_SURVIVAL:        return 0   # load-bearing only once Trail resolves
    if not committed:                     return 0   # the ONLY natural negative today (26 recs)
    if survival in _ALIVE_SURVIVAL:       return 1
    if committed and episode["outcome_label"] == "success":  return 1   # weak positive
    return None                                                          # abstain (dropped)
```

`W_WEAK = policy.weak_neg_weight = 0.4` (matches `outcome_reward`'s commit/survival magnitudes so
the weak-label confidence is consistent with the reward the rest of SkillOpt already trusts).

### 5.2 Per-criterion precision / recall / discrimination (vs INDEPENDENT truth)

Combined per-trace truth `y*`: `human_label` if present (weight 1.0) else `weak_label` (weight
W_WEAK) else drop. For a criterion `c` over traces with a verdict for `c` and a `y*`:

```
prediction p_i = (verdict.value >= 0.5);  for direction=="negative", p_i = (verdict FIRED)
TP = Σ[p=1 ∧ y*=1]  FP = Σ[p=1 ∧ y*=0]  FN = Σ[p=0 ∧ y*=1]  TN = Σ[p=0 ∧ y*=0]   (weighted counts)
precision_c = TP/(TP+FP)  recall_c = TP/(TP+FN)   # 0.0 (UNCALIBRATED) when denom 0, never 1.0
discrimination_c = mean(p | y*=1) - mean(p | y*=0)   # the selfcheck separation, now per-criterion
```

A criterion with `n_labels >= per_criterion_min_labels` and `precision_c < per_criterion_precision_min`
OR `discrimination_c < per_criterion_discrimination_min` is **DEMOTED** to `effective_weight=0`
(advisory) with a named limitation. An `agent` criterion with `n_labels < per_criterion_min_labels`
is also weight-0 (no calibration evidence yet) — closing the "agent leg earns weight before any
human label" leak structurally.

### 5.3 Overall discrimination (two estimators, well-defined at the edges)

```
rubric_score_i = Rubric.score(verdicts_for_trace_i)   # may be None (BLOCKED) — excluded
# (1) vs HUMAN gold (primary): Mann-Whitney AUC, closed form
n_pos, n_neg over GOLD-only labels.
if n_neg == 0 or n_pos == 0 or n_pos*n_neg < auc_min_pairs:
    auc_human = None    # UNDEFINED — never divide by zero; status -> blocked_insufficient_labels
else:
    auc_human = (Σ rank(pos) - n_pos*(n_pos+1)/2) / (n_pos * n_neg)   # ties credited 0.5
# (2) vs WEAK class (secondary, always-attempted): Spearman/point-biserial of rubric_score
#     against y* over traces with a RESOLVED y*, INCLUDING the committed/weak negative.
if n_resolved < rho_min_n or both classes not present:
    rho_secondary = None    # BLOCKED on this leg — closes the "rho on 3 points" hole
else:
    rho_secondary = spearman(rubric_score, y*)
```

**Non-independence guard (closes circularity):** when `rho_secondary` is the discriminator and a
deterministic criterion binds `committed`/`outcome_reward_min`/`outcome_label_in` (i.e. shares
inputs with `y*`), `rho_secondary` is computed over the **agent/human criterion contributions
only** (the rubric sub-score excluding outcome-derived deterministic verdicts). AUC vs gold is
always over the full rubric score (gold is independent of any detector).

### 5.4 Fusion of sparse human + weak labels

Human gold WINS where present (a human "this is garbage" beats "it committed"). For
`combined_label_auc` (coverage estimator), pairs are weighted: `ω_ij = 1.0` if both gold, else
`W_WEAK`. `n_eff_neg = n_human_neg + W_WEAK * n_weak_neg`. The gate uses **gold-only AUC** for the
precision bar and **`rho_secondary`** for the always-on discrimination bar.

### 5.5 The `calibrated` gate + status machine

`status` is DERIVED (never author-set). All `blocked_*` set `reward=null` and surface
`blockers` with NAMED REMEDIES.

```
G0 SHAPE:   ≥1 deterministic-or-calibrated-negative criterion; floor criterion non-degenerate
            (5%<fire<95%, not presence-only) ............................ else blocked_no_floor
G1 LABELS:  n_human_labels >= min_human_labels AND >= min_human_per_class each class
            AND n_eff_neg >= min_eff_neg ................................ else blocked_insufficient_labels
G2 DISCRIM: auc_human is not None AND auc_human >= auc_min
            OR (auc_human None AND rho_secondary is not None AND rho_secondary >= rho_min)
            AND ≥1 weighted criterion with discrimination_c >= per_criterion_discrimination_min
            (else, if every criterion demoted) ......................... else blocked_non_discriminating
            negative rho => blocked_inverted (the selfcheck INVERTED finding is a HARD block)
G3 ADVERS:  §6 hard gate passes ..................................... else blocked_adversarial
G4 REPEAT:  every agent criterion flip_rate <= judge_flip_rate_max ..... else blocked_unstable_judge
G5 SELF:    NOT (same_session_self_judge AND n_human_labels == 0) ...... else blocked_needs_human_labels

status = "calibrated"               iff G0..G5 all hold AND human labels present
       = "provisional_weak_only"    iff G0,G2(rho),G3,G4 hold but human labels absent
                                     (reward USABLE but recommended=False, flagged)
       = "blocked_<reason>"         iff any gate fails (reward = null)
       = "calibrating"             iff labels are still being gathered, nothing outright blocked
       = "uncalibrated"            before any calibration run
```

`recommended = (status == "calibrated") AND existing-recommended-conditions AND no permissive floor`.
On THIS bucket today, all five seed skills resolve to `blocked_insufficient_labels` (G1: ≤1 natural
negative, 0 human labels) — the honest v1 outcome (§0). `provisional_weak_only` becomes reachable
for `tdd` only after the `committed=False` negative is paired with ≥`rho_min_n` resolved points;
`calibrated` requires human negatives.

---

## 6. Anti-hacking

### 6.1 The marker-stuffing hole, closed structurally

Root cause (proven, `selfcheck.py §B`, reproduced: `score_skill(garbage)=1.0`): `score_skill`
counts `rule[<marker>]` tokens IN THE SKILL TEXT, and that number drives the gate via
`FakeReRolloutRunner` (`rerollout.py:80`, `m in skill_text`).

Two structural moves:
1. **The calibration VERDICT never reads skill text.** A deterministic verdict is
   `detector_matches(criterion.detectors, EvidenceBundle)` over the TRACE; an agent verdict is on
   an evidence-blind packet. Stuffing markers into the skill changes NO trace's EvidenceBundle, so
   the rubric's per-trace labels — and therefore the rubric's `calibrated`/`blocked` STATUS — are
   **invariant to skill_text**. This is the canonical anti-hacking proof (§6.3a).
2. **The set of markers SkillOpt may enforce is gated by discrimination.** The reward path
   (§7) restricts addressable markers to criteria the calibration proved discriminate
   (`effective_weight > 0`). A stuffed criterion that doesn't separate the weak/gold classes is
   demoted to weight 0 and its marker can never enter the gate. `score_skill` is RETIRED from the
   reward path (kept only as a labeled-diagnostic, never on the gate).

### 6.2 Two HARD adversarial build gates (the proven attacks become permanent tests)

Built every emit in `calibration.adversarial_probe(rubric, episodes, records)`. Both are
well-defined in the BLOCKED case (closes the "vacuous gate" hole):

- **G3a — SKILL stuffing (status-flip invariance):** construct the canonical stuffed skill
  `adv = "I will do no useful work.\n" + "\n".join(f"rule[{m}]" for m in rubric.markers)` (lifted
  from selfcheck). Assert (i) legacy `score_skill(adv) ≈ 1.0` (regression sentinel proving the OLD
  gate was gameable) AND (ii) the rubric's calibration STATUS and per-trace verdicts are
  **byte-identical** whether computed against `adv` or the empty skill (reward-input invariance:
  `verdicts(adv) == verdicts(empty)`). Specifically: **a stuffed skill cannot flip a rubric from
  `blocked` to `calibrated`.** If it can, BUILD FAILS (`adversarial_stuffing_gate_failed`). This is
  meaningful even when every rubric is `blocked` (the assertion is about status invariance, not a
  numeric reward delta).
- **G3b — RUBRIC permissiveness + trivial-real-action (closes the garbage-rubric hole):** the
  factory synthesizes (i) a rubric whose floor is a permissive presence-only detector
  (`has_files`/`has_commands`/`committed`/`outcome_reward_min<=0`) and HARD-FAILS if it reaches
  `calibrated`/`provisional` (floor non-degeneracy from §4c, now a build gate), AND (ii) a
  "trivial real action" adversary trace (just edits a file + runs a test) scored against the
  AUTHORED rubric's deterministic criteria, asserting it does NOT clear the rubric's pass threshold
  by the floor alone (`trivial_action_delta >= 0` margin to a calibrated pass trace). This exercises
  the cheap-deterministic-floor hole, not only text-stuffing.

### 6.3 The two canonical proofs

- **(a) Reward-VERDICT invariance:** `verdicts(stuffed_skill) == verdicts(empty_skill)` within
  1e-9 (the judgment does not read the optimized text). One assertion closes the marker-stuffing
  CLASS for the judgment layer.
- **(b) Discrimination floor:** `selfcheck §A` (process separation 0.0 for tdd) becomes
  `discrimination_c >= per_criterion_discrimination_min` as a per-criterion demotion AND
  `auc/rho` as a rubric-level BLOCK. The exploit that currently passes is the test that ships RED.

### 6.4 Schema trust boundary (carried forward + extended)

`validate_rubric/criterion/verdict` reject unknown keys; `reward/gate/split/calibration/value/
recommended/evidence_digest` are not author-settable. `detector_permissiveness_flags` HARD-rejects
for floor criteria, advisory elsewhere. The agent proposes WHAT/HOW/WHAT-EVIDENCE; the factory
computes calibration + reward + recommended; a human approves.

---

## 7. SkillOpt integration (reward replacement, no fork)

### 7.1 The contradiction, resolved explicitly

Every reviewer's blocker #1: a reward invariant to `skill_text` cannot drive
`engine.py:664` (`candidate_score > current_score`) → zero edits accepted → no Dsel movement; but
a reward that moves with `skill_text` re-opens stuffing. **Resolution (the locked model):**

> The rubric LABELS the held-out traces (text-invariant ground truth `y`). SkillOpt's reward is
> the candidate skill's COVERAGE of the failure-mode markers proven on the rubric's **calibrated
> negatives** — text-sensitive, but the marker SET is fixed by evidence the skill cannot move.

Concretely: the reward stays the existing `make_rerollout_gate(runner, tasks)` shape, with
`runner` = a NEW `RubricReRolloutRunner` implementing the existing `ReRolloutRunner` Protocol
(`rerollout.py:68`, zero engine edit). Its `.run(skill_text, task)` returns the fraction of the
task's `required_markers` the skill addresses — **but `required_markers` are now ONLY the markers
of CALIBRATED, discriminating criteria deficient on the task's source trace** (the
`addressable_markers` computation in `packaging.py:233` is filtered to `effective_weight>0`
criteria). So:
- Reward IS a function of `skill_text` (coverage) → Dsel can improve → headline preserved.
- The enforceable marker SET is gated by discrimination → a stuffed/permissive criterion's marker
  never enters → stuffing the skill with a non-calibrated marker scores nothing.
- The rubric's PASS/BLOCK STATUS (the thing reviewers feared was hollow) is text-invariant (§6.3a).

This is exactly the trust-antihacking must-fix #1 hybrid: the LABEL is evidence-grounded; the
optimization signal is text-dependent coverage of evidence-proven failure modes. It needs NO
RealClaude runner in default CI (the markers are pre-derived; the fake runner counts coverage of a
FIXED, calibration-gated marker set — not arbitrary text tokens).

### 7.2 The two seams (the only changes)

`packaging.py`, behind `reward_basis ∈ {marker_coverage(legacy), rubric(new)}` (default `rubric`
for a Rubric with judge_method/evidence, `marker_coverage` for a legacy archetype — strict
superset, no regress):

1. **`RolloutRow.reward` (the success/failure split driver):** replace
   `1.0 if completeness>=0.999 else 0.0` with `1.0 if rubric.score(verdicts_for_trace) >=
   calibration.pass_threshold else 0.0`; `failure_tags` = markers of criteria whose verdict failed
   (positive) or fired (negative). `split_success_failure(threshold=0.5)` reused verbatim.
2. **`gate_fn`/`test_fn`:** `make_rerollout_gate(RubricReRolloutRunner(rubric, calibration),
   tasks)` where `tasks.required_markers` are the calibration-gated addressable markers. Same
   `Callable[[str],float]` contract; `run_optimization`, `BucketHarness`, `split_rows_three_way`,
   `default_proposer`, `RolloutRow` — ALL UNTOUCHED (Phase-5 verify: `git diff` under `skill_opt/`
   is empty).

### 7.3 BLOCKED short-circuit

If `calibration.status` starts with `blocked_`, `emit_verifier_package` does NOT run the
optimization as a passing reward: it writes the BLOCKED report (`reward=null`, `recommended=False`,
`accepted=False`, `limitations=blockers`) and the report headline reads `BLOCKED: <reasons>`
instead of `Dsel 0->1.0`. The single most important behavioural change: the green headline can no
longer appear for a non-discriminating or under-evidenced rubric.

### 7.4 `scorer.py` regeneration

`render_scorer` gains a `calibrated` mode embedding the frozen calibrated-criterion weights + the
pass threshold + the committed verdict fixtures, so the shipped `scorer.py` re-derives the SAME
reward standalone (preserves the `python scorer.py best_skill.md` contract, now scoring evidence-
derived verdicts not token counts). The deterministic grader id flips
`deterministic_marker_coverage -> deterministic_rubric_verdict`; `validate_package` still requires
exactly one default-on deterministic grader (unchanged), and ACCEPTS the additive
`rubric`+`calibration`+`reward_basis` blocks. A BLOCKED rubric still emits a VALID package
(`recommended=False`), never raises.

---

## 8. Interactive SKILL.md procedure

Rewrite `skill/verifier-creator/SKILL.md` → `opentraces-skill-verifier`. Trust boundary restated
first. The reframe stated up front: *"A trace is EVIDENCE; the verdict lives in the RUBRIC you
author, co-designed with the skill's own definition."* Steps 4↔5↔6 are an iterative measurement
loop, not a one-shot.

0. **ORIENT** (with the user if present): read the TARGET skill's own SKILL.md; ask "what does an
   effective `<skill>` invocation actually achieve?" → `semantic_target`.
1. **`list_candidates(skill)`** — which skills have evidence; which markers are deficient; AND
   `n_weak_neg` + `n_human_labels` per skill (calibration feasibility preview). The summary SAYS
   PLAINLY when `n_eff_neg < min_eff_neg`: "this skill has ≈no natural negatives; you must gather
   human counterexamples (step 5) or the rubric will be BLOCKED."
2. **`get_skill_examples(skill, episodes, records)`** — real example (full-contract /
   committed-success) vs counterexample (deficient / `committed=False`) trace refs + slice
   commands. Pull windows: `opentraces trace slice <id> --template bursts --json`,
   `opentraces trace get <id> --json`, `opentraces ctx reads <id> --json` (best-effort),
   `opentraces trail track <id> --json` (best-effort). Read what the traces ACTUALLY did.
3. **`draft_rubric(skill, episodes)`** — editable skeleton: one deterministic criterion per
   universal signal (reuses archetype DetectorSpecs) + suggested agent/semantic criteria with
   bound evidence + a REQUIRED negative-direction criterion ("claims success but no commit
   landed"). Authored ALONGSIDE the target skill's definition.
4. **`author_rubric(rubric_dict)`** — validate (raises `RubricSpecError` on unknown keys / missing
   detector on deterministic / detector on agent / no floor criterion / dup markers); HARD-reject a
   permissive/degenerate floor criterion; WARN if zero negative-direction criteria. Returns
   `AuthoredRubric` + advisory warnings.
5. **`find_counterexamples(skill, rubric)`** then **`judge_pending` + `record_human_label`** — mine
   the scarce negative class: `committed=False` traces, traces where a negative criterion fires,
   low-`outcome_reward` traces; returns refs + a tally of human labels still needed for G1. The
   in-loop agent judges agent criteria (`build_judge_packet` → reason → `post_verdict`, run twice
   for repeatability). The USER supplies gold via a SEPARATE human-confirmation verb
   `record_human_label` (the only writer to `human.jsonl`). Co-judge ambiguous traces together.
6. **`calibrate_rubric(rubric, episodes)`** — per-criterion P/R/discrimination, AUC vs gold,
   Spearman vs weak class, both adversarial gates, flip-rate → `CalibrationReport`. READ THE
   STATUS. On `blocked_insufficient_labels` → step 5 with the named count. On `blocked_inverted` /
   `blocked_non_discriminating` → redesign criteria. NEVER report a passing score on a blocked
   rubric.
7. **`score_rubric(authored, out_dir, episodes)`** — proceeds only if status ∈
   {`calibrated`, `provisional_weak_only`}; runs the SkillOpt gate with the rubric reward
   (§7); emits the package (`spec.yaml` with rubric + calibration + graders, `verdicts/`,
   `labels/`, rows, scorer.py, report). Headline shows AUC + per-criterion discrimination +
   adversarial PASS + status — NOT a bare `Dsel 0->1.0`. Present for HUMAN APPROVAL. Do not promote.

**MUST NOT:** set reward/gate/split/calibration/value/recommended in a rubric; judge the optimized
skill text (judge the evidence); write `human.jsonl` from the agent path; mark recommended or
promote; author a broad/degenerate rubric to pass the gate (HARD-gated); mutate traces/bucket/skill.

---

## 9. Tool surface (function signatures for `authoring.py`)

Additive; the legacy five remain. Read-only on the bucket.

```python
# 1. (existing, extended summary) which skills have evidence + calibration feasibility
def list_candidates(*, project=None, skills=None, min_usable_episodes=30,
                    index_path=None, episodes_by_skill=None) -> dict: ...
# now includes per-skill {n_weak_neg, n_human_labels, n_eff_neg, calibration_feasible}

# 2. (existing) real example/counterexample refs
def get_skill_examples(skill, archetype_id=None, *, episodes, records=None,
                       max_examples=3, slice_fetcher=None) -> dict: ...

# 3. (new) editable rubric skeleton from evidence + the target skill's definition
def draft_rubric(skill, *, episodes, records=None,
                 from_archetype=None) -> dict: ...   # {rubric: <editable dict>, support, refs}

# 4. (new) validate + build + lint a rubric (raises RubricSpecError on unsafe specs)
def author_rubric(spec: dict) -> AuthoredRubric: ...   # .rubric, .warnings, .approval_state

# 5. (new) mine the scarce negative class + tally labels still needed for G1
def find_counterexamples(skill, rubric: Rubric, *, episodes, records=None) -> dict: ...
#   {weak_negatives: [trace_refs], fired_negatives: [...], low_reward: [...], labels_needed: int}

# 6. (new) build an evidence-blind packet for one (criterion, trace)
def build_judge_packet(rubric: Rubric, criterion_id: str, trace_id: str, *,
                       episodes, records=None, projections=None) -> dict: ...   # JudgePacket

# 7. (new) the in-loop agent posts a verdict (stamps digest+epoch+judge_id; tamper-guarded)
def post_verdict(package_dir: Path, judge_request_id: str, value: float, rationale: str,
                 evidence_quote: str, judge_id: str) -> Verdict: ...

# 8. (new) the ONLY writer to human.jsonl — gated by explicit human confirmation
def record_human_label(package_dir: Path, rubric_id: str, criterion_id: str, trace_id: str,
                       label: int, rationale: str, *, human_confirm: bool = False) -> Verdict: ...
#   raises unless human_confirm is True (wired to a separate CLI verb requiring a human keystroke)

# 9. (new) verdict coverage + epoch-drift audit
def verdicts_status(package_dir: Path) -> dict: ...   # {by_method, coverage, stale_epoch, flip_rate}

# 10. (new) run the full calibration math + both adversarial gates
def calibrate_rubric(rubric: Rubric, *, out_dir: Path, episodes, records=None,
                     policy: CalibrationPolicy = CalibrationPolicy()) -> CalibrationReport: ...

# 11. (new) score a CALIBRATED/provisional rubric through the gate; emit package (else BLOCKED report)
def score_rubric(authored: AuthoredRubric, *, out_dir: Path, episodes=None, project=None,
                 index_path=None, seed="skill-verifier") -> VerifierPackageResult: ...
```

`emit_verifier_package` gains `rubric: Rubric | None = None` and `reward_basis: str = "rubric"`
(both additive; `rubric=None` ⇒ exact legacy archetype path).

---

## 10. Phased execution plan (per-phase verification)

**Phase 0 — Anti-regression baseline + attack pin + migration.**
Run existing suites GREEN (the regression floor). Pin `selfcheck §B` as a permanent test
(`score_skill(stuffed)≈1.0`). Add `Criterion`/`Rubric`/`Verdict`/`EvidenceSpec`/`CalibrationPolicy`
/`CalibrationReport` dataclasses with the 3 additive Criterion fields (defaults preserve legacy);
widen `_ELEMENT_KEYS`→`_CRITERION_KEYS`; add `validate_rubric_dict`; `element_id`↔`criterion_id`
alias; `contract_elements`↔`criteria` alias.
*Verify:* full suite green; selfcheck §B reproduced; round-trip test (old archetype dict →
degenerate Rubric → equivalent dict == original) for all 5 seed archetypes; validator rejects
`reward/gate/split/value/calibration` inside a criterion.

**Phase 1 — Evidence binding resolvers (bounded, read-only).**
`resolve_evidence` over `evidence_from_episode` / `trace_slices` / `TrailQueryProjection` /
`ContextTreeProjection` / produced artifact; visible truncation; canonical `evidence_digest`;
`evidence_epoch`.
*Verify:* every resolver caps sizes; digest stable+deterministic across two runs;
`committed=False` correctly read for the 26 negative records; `ctx_reads` returns ABSTAIN on the
~98.7% without `context_tree_summary`; zero write paths (temp-bucket mtime-unchanged assertion);
deterministic-criterion source restriction enforced.

**Phase 2 — Calibration math + weak-label class.**
Per-criterion P/R/discrimination; closed-form Mann-Whitney AUC; Spearman; `committed`-derived weak
negative; non-independence guard; fusion; status machine with EXPLICIT n_neg==0 / all-demoted
guards.
*Verify:* AUC matches a hand-computed fixture; `n_neg==0 ⇒ auc_human=None ⇒ blocked_insufficient_labels`
(not divide-by-zero); all-criteria-demoted ⇒ `blocked_non_discriminating` (not 0/0 NaN);
`rho_secondary` BLOCKED below `rho_min_n`; negative rho ⇒ `blocked_inverted`; the selfcheck tdd
zero-separation case ⇒ BLOCKED; a synthetic separable fixture ⇒ `calibrated`.

**Phase 3 — Both hard adversarial gates.**
`adversarial_probe`: G3a status-flip invariance (`verdicts(stuffed)==verdicts(empty)`,
stuffed cannot flip blocked→calibrated) + G3b permissive-rubric + trivial-real-action.
*Verify:* `test_verifier_factory_adversarial.py` — stuffed skill cannot flip status (meaningful in
the BLOCKED case); permissive-floor rubric HARD-FAILS reaching calibrated/provisional;
trivial-action adversary does not clear pass-by-floor; empty-deterministic floor criterion rejected
at author time; legacy `score_skill(garbage)=1.0` still reproduces (retire-from-reward sentinel).

**Phase 4 — Agent-judge protocol + ledger.**
`build_judge_packet` (no skill text/markers) / `post_verdict` (groundedness + digest + epoch +
stamped judge_id; cannot write human.jsonl) / `record_human_label` (human_confirm-gated) /
`verdicts_status`; append-only de-duped JSONL; structural floor + hardened co-grounding +
`same_session_self_judge` BLOCKING when human labels absent.
*Verify:* packet contains no skill text (assertion); post rejects ungrounded quote / digest
mismatch / out-of-scale; idempotent within epoch; epoch drift flagged not silently de-duped;
agent path cannot write human.jsonl; rubber-stamp agent (all-pass) fails recall/discrimination;
100%-agent rubric rejected by validator.

**Phase 5 — Reward replacement (no fork).**
`RubricReRolloutRunner` (Protocol impl) + calibration-gated `addressable_markers` +
`RolloutRow.reward`=rubric pass + BLOCKED short-circuit + `reward_basis` flag + `scorer.py`
calibrated mode.
*Verify:* `git diff` under `skill_opt/` empty; `reward_basis=marker_coverage` byte-parity with the
4320-case oracle + the 5-example legacy path; a CALIBRATED separable synthetic rubric yields a real
Dsel improvement (text-sensitive coverage of calibration-gated markers); a stuffed skill scores no
coverage of non-calibrated markers; a BLOCKED rubric emits a VALID package with `reward=null`,
`recommended=False`, headline `BLOCKED:` (does not raise); standalone `scorer.py` re-derives the
calibrated reward.

**Phase 6 — SKILL.md + CLI + real-bucket evidence + otbox journey.**
Rewrite SKILL.md to the rubric loop; add `opentraces skill-verifier {label,judge,calibrate,status,
score,counterexamples}` CLI verbs; default-CI otbox journey driving the loop network-free on
recorded verdicts.
*Verify:* `pytest tests/ -q` only documented env-bound skips; real-bucket run reports all 5 seed
skills as `blocked_insufficient_labels` with a named human-label worklist (the honest v1 outcome,
§0); a seeded human-label fixture flips one skill to `calibrated` end-to-end; otbox journey green
asserting BLOCKED for a negative-starved skill and calibrated for a labeled one.

---

## 11. How each blocker/major hole is closed

| # | Reviewer hole (severity) | Closed by |
|---|---|---|
| 1 | **Reward invariant to skill_text ⇒ engine accepts zero edits ⇒ no Dsel** (blocker, all 4 lenses) | §7.1 split model: rubric LABELS traces (text-invariant); reward = candidate's COVERAGE of calibration-GATED failure markers (text-sensitive). Reward IS a function of skill_text; the marker SET is evidence-fixed. STATUS is invariant (§6.3a). No RealClaude needed in CI. |
| 2 | **`test_emit_package_improves_dsel`/FakeReRollout curated tests regress** (blocker) | §7.2 `reward_basis` flag defaults `marker_coverage` for legacy archetypes; Phase-0/5 verify byte-parity (4320-oracle + 5-example). The rubric reward is a SEPARATE entry path; curated archetypes flow through the legacy path until authored as rubrics. |
| 3 | **No min-sample floor on survival/rho bar ⇒ rho on 3 points** (blocker) | §2.7 `rho_min_n=8` + both-classes-present; below ⇒ `rho_secondary=None` ⇒ BLOCKED. Symmetric with the AUC `auc_min_pairs` floor. |
| 4 | **Calibration empirically empty: survival all None, single-class outcomes** (blocker, 3 lenses) | §0 stated plainly in spec body + SKILL.md + report headline: ALL 5 seed skills BLOCK today. Survival DEMOTED to best-effort/abstain-loud. Natural negative class = `committed=False` (the data that exists). v1 deliverable = honest BLOCKED + labeling worklist, not GREEN. |
| 5 | **Negative-direction criterion depends on reverted/lost (empty)** (blocker) | §5.1 negative-direction keys off `committed=False` (claimed-success-vs-no-commit contradiction), which EXISTS in the data (26 recs). reverted/lost is reserved for when Trail resolves. |
| 6 | **AUC/G2 divide-by-zero at n_neg==0; all-demoted 0/0 NaN** (blocker) | §5.3/§5.5 explicit guards: `n_neg==0 ⇒ auc_human=None ⇒ blocked_insufficient_labels`; all-demoted ⇒ `Rubric.score` returns None ⇒ `blocked_non_discriminating`. Phase-2 unit tests for both edges. |
| 7 | **Adversarial gate vacuous when reward=null (BLOCKED)** (major) | §6.2 G3a re-cast as STATUS-FLIP invariance (`verdicts(stuffed)==verdicts(empty)`; stuffed cannot flip blocked→calibrated) — meaningful with no calibrated rubric. |
| 8 | **Permissive/garbage RUBRIC unguarded; co-grounding launders agent criterion** (major, 2 lenses) | §4c + §6.2 G3b: floor criterion must be non-degenerate (5%<fire<95%, not presence-only); `detector_permissiveness_flags` HARD-rejects for the floor; trivial-real-action adversary. `committed=True` (97%) cannot be the floor. |
| 9 | **same_session_self_judge advisory only ⇒ self-grading not gated** (major, 2 lenses) | §4.4f / §5.5 G5: BLOCKING when human labels absent + agent authored. Advisory only once gold exists. |
| 10 | **Survival/outcome non-independence ⇒ circular rho** (major) | §5.3 non-independence guard: when rho is the discriminator and a deterministic criterion binds outcome-derived fields, rho is computed over agent/human contributions only. |
| 11 | **CI replays frozen verdict fixture ⇒ gate skill-insensitive for agent criteria ⇒ no Dsel** (major) | §7.1: the optimization signal is COVERAGE of calibration-gated markers (text-sensitive), NOT the agent verdict value (text-invariant). Agent criteria contribute to the LABEL/marker-set, not to the per-candidate gate value. |
| 12 | **Gold ledger spoofable (judge_id self-asserted string)** (major, trust leak) | §4.2/§8: agent `post_verdict` CANNOT write `human.jsonl`; `record_human_label` is the only writer, `human_confirm`-gated via a separate CLI verb requiring a human keystroke. |
| 13 | **ctx_reads ungroundable (1.3%)** (major) | §3 validator rejects a non-deterministic criterion whose ONLY binding is `ctx_reads`; no reward-bearing criterion may rest on a >90%-empty binding. |
| 14 | **verdict_id idempotency breaks under maturation** (minor, 2 lenses) | §4.3 `evidence_epoch` pins the bundle; idempotency holds within an epoch (stated); `verdicts_status` reconciles drift across epochs. |
| 15 | **Repeatability oracle cost not budgeted; not CI-automatable** (major) | §4.3/§10: G4 flip-rate is human-labor-bearing, OUT of default CI; CI replays recorded runs. Phases 0–3 (deterministic-only) are the network-free deliverable and produce honest BLOCKED. |
| 16 | **ContractElement→Criterion migration unspecified ⇒ silent regress** (minor) | §2.8 concrete: `element_id` canonical + `criterion_id` alias; `to_dict` emits `element_id`; Phase-0 round-trip test before any field added. |
| 17 | **reward_basis default never engages (calibrated unreachable) ⇒ dead code** (minor) | §0/§5.5 stated: v1 ships the machinery; on the current bucket it produces BLOCKED/labeling-worklist, NOT a working reward swap. Not presented as active. |

---

## 12. Residual risks / open decisions for the user

1. **The v1 honest outcome is BLOCKED for all five seed skills.** Reaching `calibrated` requires a
   human to label ≥`min_human_labels` (default 8, ≥3 negatives) per skill. **Decision:** accept
   BLOCKED + a labeling worklist as the v1 deliverable, or first run `opentraces trail track` /
   liveness across the bucket to populate `survival_state` (a prerequisite project, not a step) so
   the weak negative class grows beyond `committed=False`?
2. **Threshold values** (`auc_min=0.70`, `rho_min=0.30`, `rho_min_n=8`, `min_human_labels=8`,
   `per_criterion_precision_min=0.60`, `adversarial_margin_min=0.20`, floor `5%<fire<95%`) are
   judgement calls on small data, surfaced as named `CalibrationPolicy` constants with rationale,
   reported alongside raw metrics, human-approved. **Decision:** lock these v1 defaults or tune
   against the first accumulated label set (a `CalibrationPolicy` version bump)?
3. **`provisional_weak_only`** lets a survival/`committed`-grounded rubric feed reward (flagged,
   `recommended=False`). **Decision:** allow provisional rewards to drive optimization at all, or
   restrict reward strictly to `calibrated`?
4. **Repeatability oracle (G4)** costs real agent turns per (criterion × trace × N≥2) and is not
   CI-automatable. **Decision:** require G4 for `calibrated`, or make it advisory until a label
   budget exists?
5. **Cross-agent / Codex judging:** the in-loop agent may be Codex on some sessions; `judge_id`
   records the model but calibration mixes judges. **Decision:** require per-judge calibration, or
   pool judges and accept the `same_session`/cross-judge warnings?
```
