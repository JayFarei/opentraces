"""Rubric-centric verifier data model (Phase 0 spine).

A **trace is evidence, not the verdict.** The verdict of "was this skill used
effectively?" lives in a **rubric** — a per-skill set of weighted criteria, each judged
against bounded, read-only evidence by a ``deterministic``, ``agent`` (the in-loop
operating agent, posting results locally), or ``human`` method. Calibration (``calibration.py``,
a later phase) earns a rubric the right to feed reward; until then it surfaces BLOCKED.

This module is the additive data spine: the serializable model + trust-boundary
validators + the proof that a **legacy archetype is a degenerate, all-``deterministic``
rubric** (so the declarative-archetype path cannot regress). Behavior (evidence
resolution, calibration math, agent-judge protocol, reward swap) lands in later phases;
the dataclasses + serialization + validators are here.

Trust boundary (br/56, extended from archetype to criterion/verdict): the agent
PROPOSES a rubric (criteria, bindings, judge methods, judging questions); the factory
SCORES mechanically; a human APPROVES. The validators reject unknown keys, and
DELIBERATELY exclude ``reward`` / ``gate`` / ``split`` / ``value`` / ``calibration`` /
``recommended`` — so a spec can declare WHAT to judge but never SET a score or its own
optimization target. See ``runs/skill-verifier-factory/RUBRIC_DESIGN.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .archetypes import (
    ARCHETYPE_KEYS,
    ContractElement,
    CreatorQuestion,
    LiveLeg,
    VerifierArchetype,
)
from .detectors import DetectorSpec, DetectorSpecError, detector_from_dict, detector_matches
from .schema import (
    CALIBRATION_POLICY_VERSION,
    CALIBRATION_SCHEMA_VERSION,
    RUBRIC_SCHEMA_VERSION,
    VERDICT_SCHEMA_VERSION,
)

#: The three ways a criterion can be judged.
JUDGE_METHODS: frozenset[str] = frozenset({"deterministic", "agent", "human"})
DIRECTIONS: frozenset[str] = frozenset({"positive", "negative"})

#: The un-self-gradeable (deterministic) criteria must carry at least this fraction of a
#: rubric's total weight, so the floor cannot be neutralized by weighting it to ~0 (trust
#: boundary "by construction", not merely "present"). Author-time structural rule.
_MIN_FLOOR_WEIGHT_FRACTION = 0.2

#: A criterion can describe WHAT to judge, never the score / reward / gate (trust boundary).
_FORBIDDEN_CRITERION_KEYS: frozenset[str] = frozenset(
    {"value", "score", "verdict", "reward", "gate", "split", "calibration",
     "calibrated", "recommended", "confidence", "evidence_digest", "weak_label"}
)


class RubricSpecError(ValueError):
    """Raised when a rubric / criterion / evidence / verdict dict is structurally invalid."""


# ---------------------------------------------------------------------------
# EvidenceSpec — the per-criterion bounded, read-only binding (behavior in a later phase)
# ---------------------------------------------------------------------------

#: read-only EpisodeEvidence projection fields a criterion may request
_EPISODE_FIELDS: frozenset[str] = frozenset(
    {"user_intent", "task_summary", "files_touched_json", "tools_used_json",
     "outcome_label", "outcome_reward"}
)
_EVIDENCE_KEYS: frozenset[str] = frozenset(
    {"episode_fields", "trace_slice", "trace_get", "trail_survival", "diff",
     "ctx_reads", "produced_artifact"}
)
#: caps (clamped, never trusted from the spec)
_MAX_SLICE_STEPS = 40
_MAX_DIFF_BYTES = 20000
_MAX_CTX_LAYERS = 8
_MAX_ARTIFACT_BYTES = 65536
#: which evidence sources may back a cheap, CI-safe deterministic verdict
_DETERMINISTIC_SOURCES: frozenset[str] = frozenset(
    {"episode_fields", "trace_get", "trail_survival", "diff"}
)


@dataclass(frozen=True)
class EvidenceSpec:
    """Bounded, read-only description of what evidence a criterion sees. Data only."""

    episode_fields: tuple[str, ...] = ()
    trace_slice: dict | None = None
    trace_get: bool = False
    trail_survival: bool = False
    diff: dict | None = None
    ctx_reads: dict | None = None
    produced_artifact: dict | None = None

    def is_empty(self) -> bool:
        return self == EvidenceSpec()

    def populated_sources(self) -> set[str]:
        out: set[str] = set()
        if self.episode_fields:
            out.add("episode_fields")
        if self.trace_slice:
            out.add("trace_slice")
        if self.trace_get:
            out.add("trace_get")
        if self.trail_survival:
            out.add("trail_survival")
        if self.diff:
            out.add("diff")
        if self.ctx_reads:
            out.add("ctx_reads")
        if self.produced_artifact:
            out.add("produced_artifact")
        return out

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.episode_fields:
            out["episode_fields"] = list(self.episode_fields)
        if self.trace_slice:
            out["trace_slice"] = dict(self.trace_slice)
        if self.trace_get:
            out["trace_get"] = True
        if self.trail_survival:
            out["trail_survival"] = True
        if self.diff:
            out["diff"] = dict(self.diff)
        if self.ctx_reads:
            out["ctx_reads"] = dict(self.ctx_reads)
        if self.produced_artifact:
            out["produced_artifact"] = dict(self.produced_artifact)
        return out


def _clamp(d: dict, key: str, cap: int) -> dict:
    out = dict(d)
    v = out.get(key)
    if isinstance(v, int) and v > cap:
        out[key] = cap
    return out


def validate_evidence_dict(payload: Any, *, what: str = "evidence") -> None:
    if not isinstance(payload, dict):
        raise RubricSpecError(f"{what}: expected an object, got {type(payload).__name__}")
    unknown = [k for k in payload if k not in _EVIDENCE_KEYS]
    if unknown:
        raise RubricSpecError(f"{what}: unknown evidence keys {sorted(unknown)}")
    ef = payload.get("episode_fields")
    if ef is not None:
        if not isinstance(ef, list) or any(f not in _EPISODE_FIELDS for f in ef):
            raise RubricSpecError(f"{what}: episode_fields must be a subset of {sorted(_EPISODE_FIELDS)}")
    for b in ("trace_get", "trail_survival"):
        if b in payload and not isinstance(payload[b], bool):
            raise RubricSpecError(f"{what}: {b} must be a boolean")


def evidence_from_dict(payload: Any, *, what: str = "evidence") -> EvidenceSpec:
    if payload is None:
        return EvidenceSpec()
    validate_evidence_dict(payload, what=what)
    slice_ = payload.get("trace_slice")
    if isinstance(slice_, dict):
        slice_ = _clamp(slice_, "max_steps", _MAX_SLICE_STEPS)
    diff = payload.get("diff")
    if isinstance(diff, dict):
        diff = _clamp(diff, "max_bytes", _MAX_DIFF_BYTES)
    ctx = payload.get("ctx_reads")
    if isinstance(ctx, dict):
        ctx = _clamp(ctx, "max_layers", _MAX_CTX_LAYERS)
    art = payload.get("produced_artifact")
    if isinstance(art, dict):
        art = _clamp(art, "max_bytes", _MAX_ARTIFACT_BYTES)
    return EvidenceSpec(
        episode_fields=tuple(payload.get("episode_fields", ()) or ()),
        trace_slice=slice_ if isinstance(slice_, dict) else None,
        trace_get=bool(payload.get("trace_get", False)),
        trail_survival=bool(payload.get("trail_survival", False)),
        diff=diff if isinstance(diff, dict) else None,
        ctx_reads=ctx if isinstance(ctx, dict) else None,
        produced_artifact=art if isinstance(art, dict) else None,
    )


# ---------------------------------------------------------------------------
# Criterion — a ContractElement + {judge_method, evidence, direction, rubric_text}
# ---------------------------------------------------------------------------

_CRITERION_KEYS: frozenset[str] = frozenset(
    {"element_id", "criterion_id", "label", "marker", "detectors", "weight",
     "judge_method", "evidence", "direction", "rubric_text"}
)


@dataclass(frozen=True)
class Criterion:
    element_id: str
    label: str
    marker: str
    detectors: DetectorSpec = DetectorSpec()
    weight: float = 1.0
    judge_method: str = "deterministic"
    evidence: EvidenceSpec = field(default_factory=EvidenceSpec)
    direction: str = "positive"
    rubric_text: str = ""

    @property
    def id(self) -> str:
        return self.element_id

    def detect(self, evidence: Any) -> bool:
        """Legacy deterministic path — identical to ContractElement.detect."""
        return detector_matches(self.detectors, evidence)

    def is_legacy_shape(self) -> bool:
        """True when this criterion is byte-identical to a degenerate ContractElement."""
        return (
            self.judge_method == "deterministic"
            and self.direction == "positive"
            and self.rubric_text == ""
            and self.evidence.is_empty()
        )

    def to_contract_element(self) -> ContractElement:
        return ContractElement(
            element_id=self.element_id, label=self.label, marker=self.marker,
            detectors=self.detectors, weight=self.weight,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "element_id": self.element_id,
            "label": self.label,
            "marker": self.marker,
            "weight": self.weight,
            "detectors": self.detectors.to_dict(),
        }
        # additive fields emitted ONLY when non-default — a degenerate criterion serializes
        # byte-identically to ContractElement.to_dict (no archetype-path regression).
        if self.judge_method != "deterministic":
            out["judge_method"] = self.judge_method
        if self.direction != "positive":
            out["direction"] = self.direction
        if self.rubric_text:
            out["rubric_text"] = self.rubric_text
        if not self.evidence.is_empty():
            out["evidence"] = self.evidence.to_dict()
        return out


def _validate_criterion_dict(el: Any, *, markers: set[str]) -> None:
    if not isinstance(el, dict):
        raise RubricSpecError("criterion: each criterion must be an object")
    forbidden = [k for k in el if k in _FORBIDDEN_CRITERION_KEYS]
    if forbidden:
        raise RubricSpecError(
            f"criterion: forbidden keys {sorted(forbidden)} (a spec cannot set value/score/reward/gate)"
        )
    unknown = [k for k in el if k not in _CRITERION_KEYS]
    if unknown:
        raise RubricSpecError(f"criterion: unknown keys {sorted(unknown)}")
    cid = el.get("element_id") or el.get("criterion_id")
    for key, val in (("element_id/criterion_id", cid), ("label", el.get("label")), ("marker", el.get("marker"))):
        if not isinstance(val, str) or not val:
            raise RubricSpecError(f"criterion: {key} is required")
    if el["marker"] in markers:
        raise RubricSpecError(f"criterion: duplicate marker {el['marker']!r}")
    markers.add(el["marker"])
    weight = el.get("weight", 1.0)
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
        raise RubricSpecError(f"criterion {cid!r}: weight must be a positive number")
    method = el.get("judge_method", "deterministic")
    if method not in JUDGE_METHODS:
        raise RubricSpecError(f"criterion {cid!r}: judge_method must be one of {sorted(JUDGE_METHODS)}")
    direction = el.get("direction", "positive")
    if direction not in DIRECTIONS:
        raise RubricSpecError(f"criterion {cid!r}: direction must be positive|negative")
    evidence = evidence_from_dict(el.get("evidence"), what=f"criterion {cid!r} evidence")
    if method == "deterministic":
        # a deterministic verdict reads a DetectorSpec off cheap, CI-safe evidence
        try:
            detector_from_dict(el.get("detectors", {}), what=f"criterion {cid!r} detectors")
        except DetectorSpecError as exc:
            raise RubricSpecError(str(exc)) from exc
        rich = evidence.populated_sources() - _DETERMINISTIC_SOURCES
        if rich:
            raise RubricSpecError(
                f"criterion {cid!r}: deterministic evidence limited to {sorted(_DETERMINISTIC_SOURCES)}; "
                f"got rich sources {sorted(rich)}"
            )
    else:
        # a semantic (agent/human) criterion must be grounded and must NOT smuggle a detector
        if el.get("detectors"):
            raise RubricSpecError(f"criterion {cid!r}: {method} criterion must not carry a detector")
        if not (el.get("rubric_text") or "").strip():
            raise RubricSpecError(f"criterion {cid!r}: {method} criterion requires rubric_text")
        if evidence.is_empty():
            raise RubricSpecError(f"criterion {cid!r}: {method} criterion must bind evidence")
        if evidence.populated_sources() == {"ctx_reads"}:
            raise RubricSpecError(
                f"criterion {cid!r}: ctx_reads alone is ungroundable on this bucket (≈1.3% coverage)"
            )


def _coerce_weight(w: Any) -> float:
    # Preserve an authored int literal so to_dict round-trips byte-identically (2, not 2.0).
    if isinstance(w, int) and not isinstance(w, bool):
        return w
    return float(w)


def criterion_from_dict(el: dict[str, Any]) -> Criterion:
    method = el.get("judge_method", "deterministic")
    return Criterion(
        element_id=str(el.get("element_id") or el.get("criterion_id")),
        label=el["label"],
        marker=el["marker"],
        detectors=(
            detector_from_dict(el.get("detectors", {})) if method == "deterministic" else DetectorSpec()
        ),
        weight=_coerce_weight(el.get("weight", 1.0)),
        judge_method=method,
        evidence=evidence_from_dict(el.get("evidence")),
        direction=el.get("direction", "positive"),
        rubric_text=el.get("rubric_text", ""),
    )


def criterion_from_element(el: ContractElement) -> Criterion:
    return Criterion(element_id=el.element_id, label=el.label, marker=el.marker,
                     detectors=el.detectors, weight=el.weight)


# ---------------------------------------------------------------------------
# Verdict — append-only ledger row (one criterion × one trace)
# ---------------------------------------------------------------------------

_VERDICT_KEYS: frozenset[str] = frozenset(
    {"schema_version", "verdict_id", "rubric_id", "criterion_id", "trace_id", "unit_id",
     "judge_method", "judge_id", "value", "label", "rationale", "evidence_digest",
     "evidence_epoch", "created_at", "blocked", "blocked_reason"}
)
VERDICT_LABELS: frozenset[str] = frozenset({"pass", "fail", "abstain"})


@dataclass(frozen=True)
class Verdict:
    schema_version: str = VERDICT_SCHEMA_VERSION
    verdict_id: str = ""
    rubric_id: str = ""
    criterion_id: str = ""
    trace_id: str = ""
    unit_id: str = ""
    judge_method: str = ""
    judge_id: str = ""
    value: float = 0.0
    label: str = "pass"
    rationale: str = ""
    evidence_digest: str = ""
    evidence_epoch: str = ""
    created_at: str = ""
    blocked: bool = False
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "verdict_id": self.verdict_id,
            "rubric_id": self.rubric_id, "criterion_id": self.criterion_id,
            "trace_id": self.trace_id, "unit_id": self.unit_id,
            "judge_method": self.judge_method, "judge_id": self.judge_id,
            "value": self.value, "label": self.label, "rationale": self.rationale,
            "evidence_digest": self.evidence_digest, "evidence_epoch": self.evidence_epoch,
            "created_at": self.created_at, "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
        }


def validate_verdict_dict(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise RubricSpecError("verdict: expected an object")
    unknown = [k for k in payload if k not in _VERDICT_KEYS]
    if unknown:
        raise RubricSpecError(f"verdict: unknown keys {sorted(unknown)}")
    forbidden = [k for k in payload if k in {"reward", "gate", "split"}]
    if forbidden:
        raise RubricSpecError(f"verdict: forbidden keys {sorted(forbidden)}")
    val = payload.get("value", 0.0)
    if not isinstance(val, (int, float)) or isinstance(val, bool) or not 0.0 <= float(val) <= 1.0:
        raise RubricSpecError("verdict: value must be a float in [0,1]")
    if payload.get("label", "pass") not in VERDICT_LABELS:
        raise RubricSpecError(f"verdict: label must be one of {sorted(VERDICT_LABELS)}")


def verdict_from_dict(payload: dict[str, Any]) -> Verdict:
    validate_verdict_dict(payload)
    known = {k: payload[k] for k in payload if k in _VERDICT_KEYS}
    return Verdict(**known)


# ---------------------------------------------------------------------------
# CalibrationPolicy + CalibrationReport (output-only; thresholds named, never author-set)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationPolicy:
    schema_version: str = CALIBRATION_POLICY_VERSION
    min_human_labels: int = 8
    min_human_per_class: int = 3
    min_eff_neg: float = 3.0
    weak_neg_weight: float = 0.4
    auc_min: float = 0.70
    auc_min_pairs: int = 8
    rho_min: float = 0.30
    rho_min_n: int = 8
    per_criterion_min_labels: int = 4
    per_criterion_precision_min: float = 0.60
    per_criterion_discrimination_min: float = 0.15
    adversarial_margin_min: float = 0.20
    judge_flip_rate_max: float = 0.15

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class CalibrationReport:
    schema_version: str = CALIBRATION_SCHEMA_VERSION
    rubric_id: str = ""
    generated_at: str = ""
    policy: CalibrationPolicy = field(default_factory=CalibrationPolicy)
    n_human_labels: int = 0
    n_human_pos: int = 0
    n_human_neg: int = 0
    n_weak_neg: int = 0
    n_eff_neg: float = 0.0
    per_criterion: tuple[dict, ...] = ()
    auc_human: float | None = None
    rho_secondary: float | None = None
    adversarial: dict | None = None
    max_flip_rate: float | None = None
    status: str = "uncalibrated"
    blockers: tuple[str, ...] = ()
    pass_threshold: float = 0.999

    def is_usable(self) -> bool:
        """True when the rubric may feed reward (calibrated or provisional)."""
        return self.status in {"calibrated", "provisional_weak_only"}

    def effective_weight(self, criterion: Criterion) -> float:
        """Per-criterion weight after calibration demotion (default = declared weight)."""
        for row in self.per_criterion:
            if row.get("criterion_id") == criterion.id:
                return float(row.get("effective_weight", criterion.weight))
        return criterion.weight

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "rubric_id": self.rubric_id,
            "generated_at": self.generated_at, "policy": self.policy.to_dict(),
            "n_human_labels": self.n_human_labels, "n_human_pos": self.n_human_pos,
            "n_human_neg": self.n_human_neg, "n_weak_neg": self.n_weak_neg,
            "n_eff_neg": self.n_eff_neg, "per_criterion": [dict(r) for r in self.per_criterion],
            "auc_human": self.auc_human, "rho_secondary": self.rho_secondary,
            "adversarial": self.adversarial, "max_flip_rate": self.max_flip_rate,
            "status": self.status, "blockers": list(self.blockers),
            "pass_threshold": self.pass_threshold,
        }


# ---------------------------------------------------------------------------
# Rubric — a strict additive superset of VerifierArchetype
# ---------------------------------------------------------------------------

#: rubric-only additive keys on top of the archetype keys; `calibration` is OUTPUT-only
#: (never author-settable) and is deliberately NOT in this set.
RUBRIC_KEYS: frozenset[str] = ARCHETYPE_KEYS | {"rubric_id", "criteria", "min_human_labels"}


@dataclass(frozen=True)
class Rubric:
    schema_version: str = RUBRIC_SCHEMA_VERSION
    rubric_id: str = ""
    skill: str = ""
    title: str = ""
    description: str = ""
    semantic_target: str = ""
    baseline_skill: str = ""
    criteria: tuple[Criterion, ...] = ()
    creator_questions: tuple[CreatorQuestion, ...] = ()
    live_legs: tuple[LiveLeg, ...] = ()
    min_traces_for_split: int = 6
    min_human_labels: int = 8
    #: OUTPUT-ONLY; rejected on author input by validate_rubric_dict, set by the factory.
    calibration: CalibrationReport | None = None

    @property
    def markers(self) -> tuple[str, ...]:
        return tuple(c.marker for c in self.criteria)

    def is_legacy_archetype(self) -> bool:
        return all(c.is_legacy_shape() for c in self.criteria)

    def to_archetype(self) -> VerifierArchetype:
        """Degenerate view as a VerifierArchetype (legacy fields only)."""
        return VerifierArchetype(
            archetype_id=self.rubric_id,
            skill=self.skill,
            title=self.title,
            description=self.description,
            semantic_target=self.semantic_target,
            contract_elements=tuple(c.to_contract_element() for c in self.criteria),
            creator_questions=self.creator_questions,
            live_legs=self.live_legs,
            baseline_skill=self.baseline_skill,
            min_traces_for_split=self.min_traces_for_split,
        )

    def _effective_weight(self, criterion: Criterion) -> float:
        return self.calibration.effective_weight(criterion) if self.calibration else criterion.weight

    def score(self, verdicts: dict[str, Verdict]) -> float | None:
        """Weighted, normalized rubric score over per-criterion verdicts for ONE trace.

        Returns None (BLOCKED) if any contributing criterion is missing/blocked, or if every
        weighted criterion was demoted to 0 (non-discriminating — never a silent 0/0).
        """
        num = 0.0
        den = 0.0
        for c in self.criteria:
            w = self._effective_weight(c)
            if w <= 0:
                continue  # demoted / zero-weight criteria neither contribute nor block scoring
            v = verdicts.get(c.id)
            if v is None or v.blocked:
                return None  # a WEIGHTED criterion with no verdict blocks the trace (not silent)
            contrib = (1.0 - v.value) if c.direction == "negative" else v.value
            num += w * contrib
            den += w
        if den <= 0:
            return None
        return round(num / den, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rubric_id": self.rubric_id,
            "skill": self.skill,
            "title": self.title,
            "description": self.description,
            "semantic_target": self.semantic_target,
            "baseline_skill": self.baseline_skill,
            "min_traces_for_split": self.min_traces_for_split,
            "min_human_labels": self.min_human_labels,
            "criteria": [c.to_dict() for c in self.criteria],
            "creator_questions": [q.to_dict() for q in self.creator_questions],
            "live_legs": [leg.to_dict() for leg in self.live_legs],
        }


def validate_rubric_dict(payload: Any) -> None:
    """Validate a rubric dict (or a legacy archetype dict via the contract_elements alias)."""
    if not isinstance(payload, dict):
        raise RubricSpecError(f"rubric: expected an object, got {type(payload).__name__}")
    if "calibration" in payload:
        raise RubricSpecError("rubric: 'calibration' is output-only and cannot be authored")
    unknown = [k for k in payload if k not in RUBRIC_KEYS]
    if unknown:
        raise RubricSpecError(
            f"rubric: unknown keys {sorted(unknown)} (a spec cannot set reward/gate/split/calibration)"
        )
    if not isinstance(payload.get("skill"), str) or not payload.get("skill"):
        raise RubricSpecError("rubric: skill is required")
    if not (payload.get("rubric_id") or payload.get("archetype_id")):
        raise RubricSpecError("rubric: rubric_id (or archetype_id alias) is required")
    elements = payload.get("criteria")
    if elements is None:
        elements = payload.get("contract_elements")
    if not isinstance(elements, list) or not elements:
        raise RubricSpecError("rubric: criteria (or contract_elements) must be a non-empty list")
    markers: set[str] = set()
    det_weight = 0.0
    total_weight = 0.0
    has_det_floor = False
    for el in elements:
        _validate_criterion_dict(el, markers=markers)
        w = float(el.get("weight", 1.0))
        total_weight += w
        # The structural floor is UN-SELF-GRADEABLE only if it is `deterministic`. A
        # negative-direction *agent* criterion is still agent-graded, so it does NOT count
        # at author time (a calibrated negative criterion may relax this at calibration time,
        # which is a calibration OUTPUT, never an author-time property). See spec §4.4(b).
        if el.get("judge_method", "deterministic") == "deterministic":
            has_det_floor = True
            det_weight += w
    if not has_det_floor:
        raise RubricSpecError(
            "rubric: requires ≥1 deterministic criterion "
            "(no rubric may be 100% agent-graded — the floor must be un-self-gradeable)"
        )
    if total_weight > 0 and det_weight / total_weight < _MIN_FLOOR_WEIGHT_FRACTION:
        raise RubricSpecError(
            f"rubric: deterministic (un-self-gradeable) criteria must carry "
            f">={_MIN_FLOOR_WEIGHT_FRACTION:.0%} of total weight; got {det_weight / total_weight:.0%} "
            "(the floor cannot be weighted to ~0)"
        )


def rubric_from_dict(payload: Any) -> Rubric:
    """Build a Rubric from a rubric dict OR a legacy archetype dict (degenerate rubric)."""
    validate_rubric_dict(payload)
    raw = payload.get("criteria")
    if raw is None:
        raw = payload.get("contract_elements") or []
    criteria = tuple(criterion_from_dict(el) for el in raw)
    return Rubric(
        rubric_id=str(payload.get("rubric_id") or payload.get("archetype_id")),
        skill=payload["skill"],
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        semantic_target=payload.get("semantic_target", ""),
        baseline_skill=payload.get("baseline_skill") or f"# {payload['skill']} (baseline)\n",
        criteria=criteria,
        creator_questions=tuple(
            CreatorQuestion(
                key=q["key"], question=q.get("question", ""),
                inferred_default=q.get("inferred_default", ""), rationale=q.get("rationale", ""),
                options=tuple(q.get("options", ()) or ()),
            )
            for q in payload.get("creator_questions", []) or []
        ),
        live_legs=tuple(
            LiveLeg(
                leg_id=leg["leg_id"], kind=leg["kind"], description=leg.get("description", ""),
                requires=tuple(leg.get("requires", ()) or ()),
            )
            for leg in payload.get("live_legs", []) or []
        ),
        min_traces_for_split=int(payload.get("min_traces_for_split", 6)),
        min_human_labels=int(payload.get("min_human_labels", 8)),
    )


def rubric_from_archetype(archetype: VerifierArchetype) -> Rubric:
    """A VerifierArchetype is a degenerate, all-deterministic Rubric (lossless)."""
    return Rubric(
        rubric_id=archetype.archetype_id,
        skill=archetype.skill,
        title=archetype.title,
        description=archetype.description,
        semantic_target=archetype.semantic_target,
        baseline_skill=archetype.baseline_skill,
        criteria=tuple(criterion_from_element(el) for el in archetype.contract_elements),
        creator_questions=archetype.creator_questions,
        live_legs=archetype.live_legs,
        min_traces_for_split=archetype.min_traces_for_split,
    )
