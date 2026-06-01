"""Verifier-creator authoring API.

The tool surface an agent uses to author ONE contextualized verifier per skill, end
to end, behind the trust boundary (br/56):

    agent PROPOSES a declarative spec  ->  factory SCORES it mechanically  ->  human APPROVES

Nothing in this module promotes a verifier, mutates a skill, or grades the agent's
own work. Every function is read-only with respect to the bucket and additive with
respect to schemas. The five tools map onto the end-to-end loop:

1. :func:`list_candidates` — mine ``skill_invocation`` evidence into candidates
   (which skills have enough trace evidence, which markers are deficient).
2. :func:`get_skill_examples` — surface real example / counterexample trace refs for
   a skill, with the per-element signals an agent needs to contextualize a spec. Pull
   the actual windows with ``opentraces trace slice/get`` on the returned refs (or
   pass a ``slice_fetcher`` to inline them).
3. :func:`draft_archetype` — propose a human-reviewable archetype DRAFT from the
   trace evidence (a starting point the agent edits).
4. :func:`author_archetype` — validate + build a declarative archetype from a spec
   dict, and lint it for over-permissive detectors (warnings, never silently
   accepted). Raises only on structurally unsafe specs (the trust boundary).
5. :func:`score_authored` — run the authored archetype through the deterministic
   SkillOpt gate (strict Dsel + held-out Dtest) and emit the package + report for a
   human to approve. Promotion stays manual / default-off.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: Strip SkillOpt marker tokens from skill-derived prose so they cannot ride into a judge
#: packet via a criterion's rubric_text (M7). Goals are also length-capped.
_MARKER_RE = re.compile(r"rule\[[^\]]*\]")


def _sanitize_goal(goal: str) -> str:
    return _MARKER_RE.sub("", goal or "").strip()[:300]

from . import scorers
from .archetypes import (
    GENERIC_ARCHETYPE_ID,
    VerifierArchetype,
    archetype_from_dict,
    archetype_permissiveness_flags,
    archetype_to_dict,
    generic_archetype_for,
    resolve_archetype,
)
import hashlib
import json as _json

from .calibration import (
    calibrate_rubric,
    emulate_human_labels,
    trace_failure_markers,
    trace_verdicts,
)
from ..skill_opt.engine import RolloutRow, run_optimization, split_success_failure
from ..skill_opt.proposers import default_proposer
from ..skill_opt.rerollout import FakeReRolloutRunner, ReRolloutTask, make_rerollout_gate
from .mining import mine_verifier_candidates, propose_archetype_draft
from .packaging import VerifierPackageResult, emit_verifier_package
from .rubric import (
    CalibrationPolicy,
    CalibrationReport,
    Criterion,
    EvidenceSpec,
    Rubric,
    rubric_from_archetype,
    validate_rubric_dict,
)
from .schema import APPROVAL_STATE


@dataclass(frozen=True)
class AuthoredArchetype:
    """An agent-authored archetype plus its advisory lint, before human approval."""

    archetype: VerifierArchetype
    warnings: tuple[str, ...]  # over-permissive-detector flags (advisory)
    approval_state: str = APPROVAL_STATE
    #: Always False — only a human approves promotion; the agent never self-promotes.
    auto_promote: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "archetype": archetype_to_dict(self.archetype),
            "warnings": list(self.warnings),
            "approval_state": self.approval_state,
            "auto_promote": self.auto_promote,
        }


def list_candidates(
    *,
    project: str | None = None,
    skills: list[str] | None = None,
    min_usable_episodes: int = 30,
    index_path: Path | None = None,
    episodes_by_skill: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Tool 1: mine verifier candidates (read-only). See :func:`mine_verifier_candidates`."""
    return mine_verifier_candidates(
        project=project,
        skills=skills,
        min_usable_episodes=min_usable_episodes,
        index_path=index_path,
        episodes_by_skill=episodes_by_skill,
    )


def _resolve_for_examples(skill: str, archetype_id: str | None) -> VerifierArchetype:
    if archetype_id is None:
        return generic_archetype_for(skill)
    if archetype_id == GENERIC_ARCHETYPE_ID:
        return generic_archetype_for(skill)
    return resolve_archetype(skill, archetype_id)


def get_skill_examples(
    skill: str,
    archetype_id: str | None = None,
    *,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
    max_examples: int = 3,
    slice_fetcher: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Tool 2: real example / counterexample trace refs for contextualizing a spec.

    For the chosen archetype (or the generic baseline), scores each episode and splits
    traces into *examples* (full contract satisfied) and *counterexamples* (one or more
    elements deficient), each annotated with the present/deficient markers and a summary.
    Pull the actual windows with ``opentraces trace slice <trace_id>`` /
    ``opentraces trace get <trace_id>`` on the returned ``trace_id``s; or pass a
    ``slice_fetcher(trace_id) -> slice`` to inline them (the live CLI path).
    """
    archetype = _resolve_for_examples(skill, archetype_id)
    examples: list[dict[str, Any]] = []
    counterexamples: list[dict[str, Any]] = []
    for ep in episodes:
        record = scorers._record_for(ep, records)
        es = scorers.score_episode(archetype, ep, record)
        trace_id = str(ep.get("source_trace_id") or ep.get("episode_id") or "")
        entry = {
            "trace_id": trace_id,
            "unit_id": str(ep.get("source_unit_id") or ""),
            "summary": str(ep.get("task_summary") or ""),
            "contract_completeness": es.contract_completeness,
            "present_markers": list(es.present_markers),
            "deficient_markers": list(es.deficient_markers),
            "slice_command": f"opentraces trace slice {trace_id} --template bursts --json",
        }
        if slice_fetcher is not None and trace_id:
            try:
                entry["slice"] = slice_fetcher(trace_id)
            except Exception as exc:  # pragma: no cover - live path guard
                entry["slice_error"] = str(exc)
        if es.contract_completeness >= 0.999:
            examples.append(entry)
        else:
            counterexamples.append(entry)
    return {
        "skill_id": skill,
        "archetype_id": archetype.archetype_id,
        "semantic_target": archetype.semantic_target,
        "markers": list(archetype.markers),
        "examples": examples[:max_examples],
        "counterexamples": counterexamples[:max_examples],
        "examples_total": len(examples),
        "counterexamples_total": len(counterexamples),
        "note": (
            "Examples satisfy the full contract; counterexamples are deficient. Pull the "
            "real windows with `opentraces trace slice/get` on each trace_id to "
            "contextualize the spec. Advisory only: no verifier is authored or promoted here."
        ),
    }


def draft_archetype(
    skill: str,
    archetype_id: str | None = None,
    *,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tool 3: propose a human-reviewable archetype DRAFT from trace evidence.

    Returns the per-element support + example/counterexample refs and an editable
    serialized spec (``archetype`` key) the agent contextualizes. The draft is
    ``approval_required``; nothing is emitted or promoted from it automatically.
    """
    archetype = _resolve_for_examples(skill, archetype_id)
    draft = propose_archetype_draft(archetype, episodes, records)
    draft["archetype"] = archetype_to_dict(archetype)
    return draft


def author_archetype(spec: dict[str, Any]) -> AuthoredArchetype:
    """Tool 4: validate + build a declarative archetype from a spec, and lint it.

    Raises :class:`ArchetypeSpecError` only on structurally unsafe specs (unknown
    keys, malformed elements, unknown command families) — that is the trust boundary,
    where a spec is prevented from carrying a reward / gate / split field. Over-permissive
    detectors are returned as advisory ``warnings`` (flagged, never silently accepted),
    not raised, so a human reviewer can weigh them before approving.
    """
    archetype = archetype_from_dict(spec)
    warnings = tuple(archetype_permissiveness_flags(archetype))
    return AuthoredArchetype(archetype=archetype, warnings=warnings)


def score_authored(
    authored: AuthoredArchetype | VerifierArchetype,
    *,
    out_dir: Path,
    episodes: list[dict[str, Any]] | None = None,
    project: str | None = None,
    decisions: dict[str, str] | None = None,
    index_path: Path | None = None,
    seed: str = "verifier-creator",
) -> VerifierPackageResult:
    """Tool 5: score an authored archetype through the deterministic gate, emit a package.

    The reward (binary full-contract pass) and the SkillOpt gate are computed
    mechanically inside :func:`emit_verifier_package`; the authored archetype only
    declares WHAT to read off a trace. Promotion stays manual / default-off — the
    returned package's ``recommended`` is advisory and a broad spec is never
    auto-recommended. A human approves; this tool never promotes.
    """
    archetype = authored.archetype if isinstance(authored, AuthoredArchetype) else authored
    return emit_verifier_package(
        skill=archetype.skill,
        archetype_id=archetype.archetype_id,
        out_dir=Path(out_dir),
        project=project,
        decisions=decisions,
        episodes=episodes,
        index_path=index_path,
        seed=seed,
        archetype=archetype,
    )


# ===========================================================================
# Two-mode skill verifier: manual ALIGNMENT SESSION + AUTOVERIFY self-alignment
# ===========================================================================
#
# The verifier is a calibrated rubric. There are two ways to author it:
#   * MANUAL — a human + the agent run an alignment session: co-establish the desired
#     outcome, author a marker-structured rubric, and label a few example/counterexample
#     traces in the same sitting. Those labels are the gold that unlocks `calibrated`.
#   * AUTOVERIFY — the agent SELF-ALIGNS to the skill's own stated goal to derive the rubric
#     automatically, then judges it. Self-alignment + self-judgment has no human anchor, so its
#     TRUST CEILING is `provisional_weak_only`: it may feed reward only when a DETERMINISTIC
#     criterion separates the external git/`committed` signal, and it can NEVER reach
#     `calibrated` (and is never auto-recommended) without human gold. Autoverify proposes;
#     manual labeling certifies; a human always approves promotion.

VERIFY_MODES = frozenset({"manual", "autoverify"})


def read_skill_definition(skill: str, *, skill_root: Path | None = None) -> dict[str, Any]:
    """Best-effort read of the TARGET skill's own definition (its stated goal).

    Looks for ``<skill_root>/<skill>/SKILL.md`` (and a couple of common layouts). Returns
    ``{found, path, goal, definition_text}``; ``goal`` falls back to the curated/generic
    archetype's semantic_target when no skill file is found, so authoring always has a goal.
    """
    roots = [skill_root] if skill_root else [Path("skill"), Path(".agents/skills"), Path("~/.agents/skills").expanduser()]
    for root in roots:
        if root is None:
            continue
        for cand in (root / skill / "SKILL.md", root / skill / "skill.md"):
            try:
                if cand.exists():
                    text = cand.read_text(encoding="utf-8")
                    return {"found": True, "path": str(cand), "goal": _extract_goal(text),
                            "definition_text": text[:8000]}
            except Exception:
                continue
    try:
        arch = resolve_archetype(skill, GENERIC_ARCHETYPE_ID)
        goal = arch.semantic_target
    except Exception:
        goal = f"effective use of the {skill} skill"
    return {"found": False, "path": None, "goal": goal, "definition_text": ""}


def _extract_goal(skill_md: str) -> str:
    """Pull a one-line goal from a SKILL.md (frontmatter description or first prose line)."""
    lines = skill_md.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("description:"):
            return ln.split(":", 1)[1].strip().strip('">') or _first_prose(lines)
    return _first_prose(lines)


def _first_prose(lines: list[str]) -> str:
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith(("#", "---", "name:", "description:")):
            return s[:300]
    return ""


def autoverify_draft_rubric(
    skill: str, *, episodes: list[dict[str, Any]] | None = None,
    records: dict[str, Any] | None = None, skill_definition: dict | None = None,
) -> Rubric:
    """SELF-ALIGN: derive a marker-structured rubric from the skill's own goal (autoverify).

    The deterministic, trace-derived generic scaffold (context/changes/verification/landed/
    honest) provides the un-self-gradeable floor + the external-anchor discriminators; on top,
    one ``agent`` semantic criterion is seeded from the skill's stated goal — the "effectiveness"
    judgment the agent self-aligns to. The agent criterion starts demoted (no calibration
    evidence) and earns weight only after it is judged AND shown to discriminate.
    """
    base = rubric_from_archetype(generic_archetype_for(skill))
    goal = _sanitize_goal((skill_definition or read_skill_definition(skill)).get("goal") or base.semantic_target)
    effective = Criterion(
        element_id="effective_outcome",
        label="Achieved the skill's intended outcome",
        marker=f"rl.{skill}.effective_outcome",
        weight=2.0,
        judge_method="agent",
        evidence=EvidenceSpec(
            episode_fields=("user_intent", "task_summary", "outcome_label"), trace_get=True,
        ),
        direction="positive",
        rubric_text=(
            f"Did this invocation of '{skill}' actually achieve its intended outcome? "
            f"Skill goal: {goal} Judge the EVIDENCE of what was done — not whether the skill's "
            "prescribed steps were followed."
        ),
    )
    rubric = dataclasses.replace(
        base,
        rubric_id=f"{skill}_autoverify_v1",
        title=f"Autoverify rubric ({skill})",
        semantic_target=goal,
        criteria=base.criteria + (effective,),
    )
    validate_rubric_dict(rubric.to_dict())  # the self-aligned rubric must be author-valid
    return rubric


@dataclass(frozen=True)
class RubricScoreResult:
    """Outcome of driving SkillOpt with a calibrated/provisional rubric (Phase 5 reward swap)."""
    rubric_id: str
    status: str
    usable: bool
    dsel_before: float
    dsel_after: float
    dtest_score: float
    accepted: bool
    addressable_markers: tuple[str, ...]
    recommended: bool
    blockers: tuple[str, ...] = ()
    package_dir: Path | None = None


def _split_label(seed: str, trace_id: str, *, sel: float = 0.25, tst: float = 0.2) -> str:
    digest = hashlib.sha256(f"{seed}:three:{trace_id}".encode("utf-8")).hexdigest()
    bucket = (int(digest[:8], 16) % 1000) / 1000.0
    if sel + tst > 1.0:
        scale = 1.0 / (sel + tst); sel *= scale; tst *= scale
    if bucket < sel:
        return "Dsel"
    if bucket < sel + tst:
        return "Dtest"
    return "Dtrain"


def score_rubric(
    rubric: Rubric,
    *,
    out_dir: Path,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
    human_labels: dict[str, int] | None = None,
    agent_verdicts: dict[tuple[str, str], float] | None = None,
    gold_is_emulated: bool = False,
    policy: CalibrationPolicy = CalibrationPolicy(),
    same_session_self_judge: bool = False,
    seed: str = "skill-verifier",
) -> RubricScoreResult:
    """PHASE 5 reward swap (no SkillOpt fork): drive the optimizer with the CALIBRATED rubric.

    The rubric LABELS each trace (text-invariant: reward=1.0 iff ``rubric.score(verdicts) >=
    pass_threshold``). SkillOpt then optimizes the skill text toward COVERING the failure markers
    of CALIBRATION-GATED criteria deficient on TRAIN failures — text-sensitive, so Dsel can move,
    but the marker SET is fixed by evidence the skill cannot move (stuffing a non-gated marker
    scores nothing). A BLOCKED rubric short-circuits: it writes a BLOCKED report and does NOT run a
    passing optimization. Reuses ``run_optimization`` + ``FakeReRolloutRunner`` +
    ``make_rerollout_gate`` verbatim (``git diff`` under skill_opt/ stays empty).
    """
    records = records or {}
    report = calibrate_rubric(
        rubric, episodes=episodes, records=records, human_labels=human_labels,
        agent_verdicts=agent_verdicts, policy=policy,
        same_session_self_judge=same_session_self_judge, gold_is_emulated=gold_is_emulated,
    )
    package_dir = Path(out_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    rubric_cal = dataclasses.replace(rubric, calibration=report)

    # BLOCKED short-circuit: never run a passing optimization on an uncalibrated rubric.
    if not report.is_usable():
        _write_rubric_package(package_dir, rubric_cal, report, blocked=True)
        return RubricScoreResult(
            rubric_id=rubric.rubric_id, status=report.status, usable=False,
            dsel_before=0.0, dsel_after=0.0, dtest_score=0.0, accepted=False,
            addressable_markers=(), recommended=False, blockers=report.blockers,
            package_dir=package_dir,
        )

    # 1. rubric LABELS each trace -> binary reward (text-invariant); failure markers gated.
    rollout_rows: list[RolloutRow] = []
    by_split: dict[str, list[RolloutRow]] = {"Dtrain": [], "Dsel": [], "Dtest": []}
    for ep in episodes:
        tid = str(ep.get("source_trace_id") or ep.get("episode_id") or "")
        rec = records.get(tid)
        verdicts = trace_verdicts(rubric_cal, ep, rec, agent_verdicts)
        score = rubric_cal.score(verdicts)
        if score is None:
            continue  # blocked trace (a weighted criterion had no verdict) — excluded
        reward = 1.0 if score >= report.pass_threshold else 0.0
        fails = trace_failure_markers(rubric_cal, ep, rec, agent_verdicts)
        row = RolloutRow(trace_id=tid, score=score, reward=reward,
                         failure_tags=tuple(fails), success_tags=(),
                         summary=str(ep.get("task_summary") or ""))
        rollout_rows.append(row)
        by_split[_split_label(seed, tid)].append(row)

    train_rows = by_split["Dtrain"] or rollout_rows
    # 2. addressable = gated failure markers on TRAIN FAILURE traces (leakage-safe).
    _ts, train_fail = split_success_failure(train_rows)
    addressable = tuple(sorted({t for r in train_fail for t in r.failure_tags}))
    if not addressable:
        addressable = tuple(sorted({t for r in train_rows for t in r.failure_tags}))

    def _tasks(rows):
        return [ReRolloutTask(task_id=f"t:{r.trace_id}", prompt=f"Use {rubric.skill} effectively.",
                              required_markers=addressable, source_trace_id=r.trace_id) for r in rows]

    runner = FakeReRolloutRunner()
    gate_fn = make_rerollout_gate(runner, _tasks(by_split["Dsel"] or train_rows))
    test_fn = make_rerollout_gate(runner, _tasks(by_split["Dtest"] or by_split["Dsel"] or train_rows))
    budget = max(1, len(addressable))
    result = run_optimization(
        rubric.baseline_skill, train_rows, propose=default_proposer, budget=budget,
        budget_floor=budget, schedule="constant", max_steps=budget + 1, epochs=1, seed=seed,
        gate_fn=gate_fn, test_fn=test_fn,
    )
    # recommended: calibrated (real gold) + gate accepted; provisional/emulated never recommended.
    recommended = report.status == "calibrated" and result.accepted > 0
    _write_rubric_package(package_dir, rubric_cal, report, blocked=False,
                          best_skill=result.best_skill, addressable=addressable)
    return RubricScoreResult(
        rubric_id=rubric.rubric_id, status=report.status, usable=True,
        dsel_before=result.initial_score, dsel_after=result.best_score,
        dtest_score=result.test_score, accepted=result.accepted > 0,
        addressable_markers=addressable, recommended=recommended,
        blockers=report.blockers, package_dir=package_dir,
    )


def _write_rubric_package(package_dir: Path, rubric: Rubric, report: CalibrationReport,
                          *, blocked: bool, best_skill: str | None = None, addressable=()) -> None:
    spec = {
        "schema_version": rubric.schema_version, "reward_basis": "rubric",
        "rubric": rubric.to_dict(), "calibration": report.to_dict(),
        "status": report.status, "blocked": blocked,
        "addressable_markers": list(addressable),
        "approval_state": APPROVAL_STATE, "automatic_promotion": False,
    }
    (package_dir / "spec.yaml").write_text(_json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (package_dir / "report.json").write_text(_json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    headline = (f"BLOCKED: {report.status}" if blocked
                else f"status={report.status} auc={report.auc_human} rho={report.rho_secondary}")
    (package_dir / "report.md").write_text(
        f"# Rubric verifier — {rubric.rubric_id}\n\n- **{headline}**\n"
        f"- blockers: {list(report.blockers)}\n", encoding="utf-8")
    if best_skill is not None:
        (package_dir / "best_skill.md").write_text(best_skill, encoding="utf-8")


@dataclass(frozen=True)
class VerifierResult:
    mode: str
    rubric: Rubric
    calibration: CalibrationReport
    recommended: bool
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "status": self.calibration.status,
            "recommended": self.recommended, "rubric_id": self.rubric.rubric_id,
            "auc_human": self.calibration.auc_human, "rho_secondary": self.calibration.rho_secondary,
            "blockers": list(self.calibration.blockers), "notes": list(self.notes),
            "calibration": self.calibration.to_dict(),
        }


def autoverify(
    skill: str,
    *,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
    agent_verdicts: dict[tuple[str, str], float] | None = None,
    human_labels: dict[str, int] | None = None,
    gold_is_emulated: bool = False,
    policy: CalibrationPolicy = CalibrationPolicy(),
    skill_definition: dict | None = None,
) -> VerifierResult:
    """AUTOVERIFY mode: self-align a rubric to the skill goal, judge it, calibrate it.

    Trust ceiling: ``same_session_self_judge=True`` — without human gold the status caps at
    ``provisional_weak_only`` (reachable only via a deterministic external-anchor discriminator)
    and ``recommended`` is ALWAYS False. Supplying REAL ``human_labels`` lifts the ceiling exactly
    as manual mode does; ``gold_is_emulated=True`` keeps the ceiling at provisional (a demo, not
    real gold — autoverify proposes; real human labels certify).
    """
    rubric = autoverify_draft_rubric(skill, episodes=episodes, records=records,
                                     skill_definition=skill_definition)
    report = calibrate_rubric(
        rubric, episodes=episodes, records=records, human_labels=human_labels,
        agent_verdicts=agent_verdicts, policy=policy, same_session_self_judge=True,
        gold_is_emulated=gold_is_emulated,
    )
    # autoverify is NEVER auto-recommended (human approval always required).
    return VerifierResult(
        mode="autoverify", rubric=dataclasses.replace(rubric, calibration=report),
        calibration=report, recommended=False,
        notes=("self-aligned from the skill goal; recommended=False by construction — a human "
               "approves promotion. Supply human labels (manual mode) to lift the trust ceiling.",),
    )


def align_session(
    skill: str,
    *,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
    skill_definition: dict | None = None,
    max_examples: int = 4,
) -> dict[str, Any]:
    """MANUAL mode scaffold: everything an alignment session needs, in one place.

    Returns the skill's stated goal, an editable draft rubric (the autoverify self-aligned one
    as a starting point), real example/counterexample traces to label together, and the label
    tally still needed to reach `calibrated`. The human drives: confirm the desired outcome,
    edit criteria, then label the surfaced traces via ``record_human_label`` (human-confirmed).
    """
    sd = skill_definition or read_skill_definition(skill)
    draft = autoverify_draft_rubric(skill, episodes=episodes, records=records, skill_definition=sd)
    examples = get_skill_examples(skill, GENERIC_ARCHETYPE_ID, episodes=episodes, records=records,
                                  max_examples=max_examples)
    return {
        "mode": "manual",
        "skill": skill,
        "desired_outcome_prompt": (
            f"Alignment session for '{skill}'. Skill goal: {sd.get('goal')!r}. "
            "State, in outcome terms, what an EFFECTIVE invocation achieves (not the steps it "
            "follows). Then edit the draft criteria to match, and label the traces below."
        ),
        "draft_rubric": draft.to_dict(),
        "label_these": {
            "effective_examples": examples["examples"],
            "ineffective_candidates": examples["counterexamples"],
        },
        "labels_needed": {
            "min_total": CalibrationPolicy().min_human_labels,
            "min_per_class": CalibrationPolicy().min_human_per_class,
            "how": "record_human_label(package_dir, rubric_id=..., trace_id=..., label=0|1, human_confirm=True)",
        },
        "note": "Manual mode reaches `calibrated`; autoverify alone caps at `provisional_weak_only`.",
    }
