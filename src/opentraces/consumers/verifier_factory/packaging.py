"""Emit a trace-grounded verifier package and score it through the SkillOpt gate.

A verifier package mirrors the skillgrade eval shape (br/53): a ``spec.yaml`` with a
weighted ``graders`` list whose single *deterministic* leg runs in default CI and
whose *live* legs (agent re-rollout, LLM rubric) are declared but opt-in/default-off.
The package also ships fixtures, a self-contained ``scorer.py``, leakage-safe
Dtrain/Dsel/Dtest verifier rows with source trace/unit refs, the optimized
``best_skill.md`` + ``edit_apply_report.json``, and a verifier report.

Dsel/Dtest are computed by running the existing SkillOpt loop
(:func:`consumers.skill_opt.engine.run_optimization`) with the archetype's markers:
the deterministic re-rollout gate (:func:`...rerollout.make_rerollout_gate`) measures
marker coverage on held-out splits, the strict Dsel gate accepts only strict
improvements, and Dtest is reported on a held-out split. No network, no live agent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.bucket_store import iter_trace_record_objects as _iter_records
from ..skill_intelligence import pipeline as si
from ..skill_opt.engine import (
    BucketHarness,
    RolloutRow,
    run_optimization,
    split_success_failure,
)
from ..skill_opt.proposers import default_proposer
from ..skill_opt.rerollout import FakeReRolloutRunner, ReRolloutTask, make_rerollout_gate
from . import archetypes as arch
from . import scorers
from .schema import (
    APPROVAL_STATE,
    PACKAGE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    ROW_SCHEMA_VERSION,
    dump_yaml,
    validate_package,
    validate_report,
    validate_row,
)


@dataclass(frozen=True)
class VerifierPackageResult:
    archetype_id: str
    skill_id: str
    package_dir: Path
    spec_path: Path
    report_path: Path
    dsel_before: float
    dsel_after: float
    dtest_score: float
    accepted: bool
    split_counts: dict[str, int]
    addressable_markers: tuple[str, ...]
    limitations: tuple[str, ...]
    gate_basis: str = "train_failure_minibatch"
    recommended: bool = False
    is_generic: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _leakage_key(seed: str, trace_id: str) -> str:
    return hashlib.sha256(f"{seed}:{trace_id}".encode("utf-8")).hexdigest()[:16]


def _split_label(seed: str, trace_id: str, *, selection_fraction: float, test_fraction: float) -> str:
    """Mirror ``split_rows_three_way`` banding so row labels match the optimizer split."""
    digest = hashlib.sha256(f"{seed}:three:{trace_id}".encode("utf-8")).hexdigest()
    bucket = (int(digest[:8], 16) % 1000) / 1000.0
    sel, tst = selection_fraction, test_fraction
    if sel + tst > 1.0:
        scale = 1.0 / (sel + tst)
        sel *= scale
        tst *= scale
    if bucket < sel:
        return "Dsel"
    if bucket < sel + tst:
        return "Dtest"
    return "Dtrain"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, sort_keys=True, ensure_ascii=True) for r in rows]
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def emit_verifier_package(
    *,
    skill: str,
    archetype_id: str,
    out_dir: Path,
    project: str | None = None,
    decisions: dict[str, str] | None = None,
    episodes: list[dict[str, Any]] | None = None,
    index_path: Path | None = None,
    seed: str = "verifier-factory",
    selection_fraction: float = 0.25,
    test_fraction: float = 0.2,
    archetype: arch.VerifierArchetype | None = None,
) -> VerifierPackageResult:
    """Build and write one verifier package; return its paths + Dsel/Dtest scores.

    ``episodes`` may be injected (tests); otherwise they are loaded from the bucket
    via the skill_intelligence episode projection. ``decisions`` overrides the
    verifier-creator interview defaults (the labeled path); absent keys keep the
    evidence-inferred default (the label-free path).

    ``archetype`` lets the verifier-creator authoring path pass an agent-authored,
    already-validated archetype directly (instead of resolving a registered id). The
    reward and the SkillOpt gate below are computed mechanically and are NOT influenced
    by the archetype's content — an authored spec can only declare WHAT to read off a
    trace, never alter the binary full-contract reward or the held-out gate (br/56).
    """
    if archetype is None:
        archetype = arch.resolve_archetype(skill, archetype_id)
    elif archetype.archetype_id != archetype_id or archetype.skill != skill:
        raise ValueError(
            f"authored archetype ({archetype.skill!r}, {archetype.archetype_id!r}) does not "
            f"match requested ({skill!r}, {archetype_id!r})"
        )
    decisions = decisions or {}

    records: dict[str, Any] | None = None
    if episodes is None:
        episodes = si.build_episode_rows(
            selected_skill=skill, project=project, min_rows=0, index_path=index_path
        )
        # Real shell commands + commit signal for detectors (Codex exec_command opacity).
        records = {
            o.trace_id: o.record
            for o in _iter_records(project_slug=project)
        }
    episodes = sorted(episodes, key=lambda e: str(e.get("source_unit_id") or e.get("episode_id")))

    limitations: list[str] = []
    if not episodes:
        limitations.append("no_episodes_for_skill")
    # Over-permissive detectors are flagged (never silently accepted): a broad detector
    # is surfaced as a limitation so a human reviewer sees it before approval.
    limitations.extend(
        f"permissive_detector: {flag}" for flag in arch.archetype_permissiveness_flags(archetype)
    )

    # 1. Score each episode -> RolloutRow (reward + deficient markers as failure tags).
    rollout_rows: list[RolloutRow] = []
    verifier_rows: list[dict[str, Any]] = []
    for ep in episodes:
        trace_id = str(ep.get("source_trace_id") or ep.get("episode_id") or "unknown")
        record = records.get(trace_id) if records else None
        es = scorers.score_episode(archetype, ep, record)
        split = _split_label(
            seed, trace_id, selection_fraction=selection_fraction, test_fraction=test_fraction
        )
        # SkillOpt split driver: a trace is a verifier "success" only when it satisfies
        # the FULL contract; otherwise it is a failure exhibiting its specific deficient
        # markers (which the optimizer must learn to address). This decouples "has a
        # deficiency" from "low overall completeness": a mostly-good trace (0.83) that
        # still drops honest_stop is a failure FOR THAT ELEMENT and teaches it. Without
        # this, richer evidence pushes most traces above 0.5 and empties the failure
        # minibatch. contract_completeness (graded) and outcome_reward (landed) are kept
        # on the verifier row for provenance.
        split_reward = 1.0 if es.contract_completeness >= 0.999 else 0.0
        rollout_rows.append(
            RolloutRow(
                trace_id=trace_id,
                score=es.contract_completeness,
                reward=split_reward,
                failure_tags=es.deficient_markers,
                success_tags=es.present_markers,
                summary=str(ep.get("task_summary") or ""),
            )
        )
        verifier_rows.append(
            {
                "schema_version": ROW_SCHEMA_VERSION,
                "row_id": "vrow:" + hashlib.sha256(
                    f"{archetype_id}:{trace_id}:{ep.get('source_unit_id')}".encode("utf-8")
                ).hexdigest()[:16],
                "skill_id": skill,
                "archetype_id": archetype_id,
                "split": split,
                "leakage_key": _leakage_key(seed, trace_id),
                "reward": es.contract_completeness,
                "contract_pass": split_reward,
                "outcome_reward": es.outcome_reward,
                "contract_completeness": es.contract_completeness,
                "deficient_markers_json": json.dumps(list(es.deficient_markers), sort_keys=True),
                "present_markers_json": json.dumps(list(es.present_markers), sort_keys=True),
                "outcome_label": str(ep.get("outcome_label") or "unknown"),
                "source_trace_id": trace_id,
                "source_unit_id": str(ep.get("source_unit_id") or ""),
                "episode_id": str(ep.get("episode_id") or ""),
            }
        )

    for row in verifier_rows:
        validate_row(row)

    # 2. Partition rows by split label (leakage-safe: a trace never spans splits).
    by_split: dict[str, list[RolloutRow]] = {"Dtrain": [], "Dsel": [], "Dtest": []}
    row_by_trace = {r.trace_id: r for r in rollout_rows}
    for vr, rr in zip(verifier_rows, rollout_rows):
        by_split[vr["split"]].append(rr)
    train_rows = by_split["Dtrain"] or rollout_rows
    dsel_rows = by_split["Dsel"]
    dtest_rows = by_split["Dtest"]

    # 3. Addressable markers = failure modes the optimizer can actually learn: the
    #    deficiencies on LOW-reward (failure-minibatch) TRAIN traces, exactly what
    #    `default_proposer` reflects over. This keeps the held-out gate honest (only
    #    proven failure modes are enforced) and leakage-safe (TRAIN-only).
    _train_success, train_failure = split_success_failure(train_rows)
    addressable = sorted({t for r in train_failure for t in r.failure_tags})
    gate_basis = "train_failure_minibatch"
    if not addressable:
        addressable = sorted({t for r in train_rows for t in r.failure_tags})
        if addressable:
            gate_basis = "train_union_fallback"
            limitations.append("no_failure_minibatch_used_train_union")
    if not addressable:
        addressable = sorted({t for r in rollout_rows for t in r.failure_tags})
        if addressable:
            gate_basis = "corpus_union_fallback"
            limitations.append("no_train_deficiencies_used_corpus_union")
    if not addressable:
        addressable = list(archetype.markers)
        gate_basis = "full_contract_fallback"
        limitations.append("no_observed_deficiencies_used_full_contract")
    addressable_t = tuple(addressable)

    def _tasks(rows: list[RolloutRow], tag: str) -> list[ReRolloutTask]:
        return [
            ReRolloutTask(
                task_id=f"{tag}:{r.trace_id}",
                prompt=f"Use the {skill} skill so its output satisfies the {archetype.title} contract.",
                required_markers=addressable_t,
                source_trace_id=r.trace_id,
            )
            for r in rows
        ]

    dsel_tasks = _tasks(dsel_rows or train_rows, "dsel")
    dtest_tasks = _tasks(dtest_rows or dsel_rows or train_rows, "dtest")
    if not dsel_rows:
        limitations.append("dsel_split_empty_fell_back")
    if not dtest_rows:
        limitations.append("dtest_split_empty_fell_back")

    runner = FakeReRolloutRunner()
    gate_fn = make_rerollout_gate(runner, dsel_tasks)
    test_fn = make_rerollout_gate(runner, dtest_tasks)

    # 4. Run the real SkillOpt loop: train rows drive proposals; the deterministic
    #    re-rollout gate scores held-out Dsel; Dtest is reported held-out.
    budget = max(1, len(addressable_t))
    harness = BucketHarness(train_rows, selection_fraction=0.0, test_fraction=0.0, seed=seed)
    result = run_optimization(
        archetype.baseline_skill,
        train_rows,
        propose=default_proposer,
        budget=budget,
        budget_floor=budget,
        schedule="constant",
        max_steps=budget + 1,
        epochs=1,
        seed=seed,
        harness=harness,
        gate_fn=gate_fn,
        test_fn=test_fn,
    )

    split_counts = {
        "Dtrain": len(by_split["Dtrain"]),
        "Dsel": len(by_split["Dsel"]),
        "Dtest": len(by_split["Dtest"]),
    }

    # 5. Write the package.
    package_dir = Path(out_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    # 5a. scorer.py (self-contained, deterministic).
    scorer_path = package_dir / "scorer.py"
    scorer_path.write_text(scorers.render_scorer(archetype, addressable_t), encoding="utf-8")

    # 5b. fixtures (one per held-out task).
    fixtures_dir = package_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    fixture_paths: list[str] = []
    for task in dsel_tasks + dtest_tasks:
        fx = {
            "task_id": task.task_id,
            "prompt": task.prompt,
            "required_markers": list(task.required_markers),
            "source_trace_id": task.source_trace_id,
            "verify": {"kind": "marker_coverage", "scorer": "scorer.py"},
        }
        rel = f"fixtures/{task.task_id.replace(':', '_')}.json"
        _write_json(package_dir / rel, fx)
        fixture_paths.append(rel)

    # 5c. rows (leakage-safe split rows with source refs).
    rows_dir = package_dir / "rows"
    for tag in ("Dtrain", "Dsel", "Dtest"):
        split_rows = [r for r in verifier_rows if r["split"] == tag]
        _write_jsonl(rows_dir / f"{tag.lower()}.jsonl", split_rows)

    # 5d. optimized skill + edit audit (provenance) — written by the SkillOpt engine.
    result.state.export(package_dir)

    source_refs = [
        {
            "trace_id": vr["source_trace_id"],
            "unit_id": vr["source_unit_id"],
            "episode_id": vr["episode_id"],
            "split": vr["split"],
        }
        for vr in verifier_rows[:12]
    ]

    creator_decisions = []
    for q in archetype.creator_questions:
        override = decisions.get(q.key)
        creator_decisions.append(
            {
                "key": q.key,
                "question": q.question,
                "decision": override if override is not None else q.inferred_default,
                "source": "labeled" if override is not None else "inferred_default",
                "rationale": q.rationale,
                "options": list(q.options),
            }
        )

    is_generic = archetype_id == arch.GENERIC_ARCHETYPE_ID
    # Honesty (br/56): the deterministic gate proves marker COVERAGE (packaging +
    # gating plumbing), not semantic behavior change. A package is only "recommended"
    # when its markers come from real TRAIN failure modes; semantic quality requires
    # the opt-in live legs and human approval.
    has_permissive_detector = any(lim.startswith("permissive_detector:") for lim in limitations)
    recommended = (
        gate_basis == "train_failure_minibatch"
        and result.accepted > 0
        and result.test_score > 0.0
        and not is_generic
        # An over-permissive detector is flagged, never silently accepted (br/56):
        # a broad spec cannot be auto-recommended even if the gate plumbing passes.
        and not has_permissive_detector
    )

    graders: list[dict[str, Any]] = [
        {
            "id": "deterministic_marker_coverage",
            "kind": "deterministic",
            "default_enabled": True,
            "weight": 1.0,
            "scorer": "scorer.py",
            "required_markers": list(addressable_t),
            "gate_basis": gate_basis,
            "semantics": (
                "CI-safe proxy: fraction of trace-grounded contract markers the skill "
                "addresses. Proves coverage + gating plumbing, NOT semantic behavior "
                "change — that requires the opt-in live legs."
            ),
            "description": "CI-safe: fraction of trace-grounded contract markers the skill addresses.",
        }
    ]
    for leg in archetype.live_legs:
        graders.append(
            {
                "id": leg.leg_id,
                "kind": leg.kind,
                "default_enabled": False,
                "weight": 0.0,
                "requires": list(leg.requires),
                "description": leg.description,
            }
        )

    spec = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "archetype_id": archetype_id,
        "skill_id": skill,
        "generated_at": _utc_now(),
        "title": archetype.title,
        "description": archetype.description,
        "semantic_target": archetype.semantic_target,
        "markers": list(archetype.markers),
        "addressable_markers": list(addressable_t),
        # Self-describing: the declarative (data-only) detector ships with each element
        # so the package round-trips back to an archetype with no eval/callable.
        "contract_elements": [el.to_dict() for el in archetype.contract_elements],
        "archetype_spec": archetype.to_dict(),
        "graders": graders,
        "creator_decisions": creator_decisions,
        "split": split_counts,
        "scorer": "scorer.py",
        "fixtures": fixture_paths,
        "rows": {
            "Dtrain": "rows/dtrain.jsonl",
            "Dsel": "rows/dsel.jsonl",
            "Dtest": "rows/dtest.jsonl",
        },
        "report": "report.json",
        "best_skill": "best_skill.md",
        "is_generic": is_generic,
        "gate_basis": gate_basis,
        "recommended": recommended,
        "approval_state": APPROVAL_STATE,
        "automatic_promotion": False,
        "limitations": limitations,
        "source_refs": source_refs,
    }
    validate_package(spec)
    spec_path = package_dir / "spec.yaml"
    spec_path.write_text(dump_yaml(spec), encoding="utf-8")

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "archetype_id": archetype_id,
        "skill_id": skill,
        "generated_at": _utc_now(),
        "baseline_digest": _digest_text(result.initial_skill),
        "candidate_digest": _digest_text(result.best_skill),
        "dtrain_count": split_counts["Dtrain"],
        "dsel_count": split_counts["Dsel"],
        "dtest_count": split_counts["Dtest"],
        "dsel_before": result.initial_score,
        "dsel_after": result.best_score,
        "dtest_score": result.test_score,
        "accepted": result.accepted > 0,
        "accepted_edits": result.accepted,
        "rejected_edits": result.rejected,
        "addressable_markers": list(addressable_t),
        "gate_basis": gate_basis,
        "recommended": recommended,
        "is_generic": is_generic,
        "approval_state": APPROVAL_STATE,
        "automatic_promotion": False,
        "limitations_json": json.dumps(limitations, sort_keys=True),
        "source_refs": source_refs,
    }
    validate_report(report)
    report_path = package_dir / "report.json"
    _write_json(report_path, report)
    (package_dir / "report.md").write_text(_render_report_md(spec, report), encoding="utf-8")

    return VerifierPackageResult(
        archetype_id=archetype_id,
        skill_id=skill,
        package_dir=package_dir,
        spec_path=spec_path,
        report_path=report_path,
        dsel_before=result.initial_score,
        dsel_after=result.best_score,
        dtest_score=result.test_score,
        accepted=result.accepted > 0,
        split_counts=split_counts,
        addressable_markers=addressable_t,
        limitations=tuple(limitations),
        gate_basis=gate_basis,
        recommended=recommended,
        is_generic=is_generic,
    )


def _render_report_md(spec: dict[str, Any], report: dict[str, Any]) -> str:
    lines = [
        f"# Verifier package — {spec['archetype_id']}",
        "",
        f"- **Skill**: `{spec['skill_id']}`",
        f"- **Semantic target**: {spec['semantic_target']}",
        f"- **Split**: Dtrain={report['dtrain_count']} Dsel={report['dsel_count']} Dtest={report['dtest_count']}",
        f"- **Dsel**: {report['dsel_before']:.3f} -> {report['dsel_after']:.3f}",
        f"- **Dtest (held-out)**: {report['dtest_score']:.3f}",
        f"- **Accepted edits**: {report['accepted_edits']} (rejected {report['rejected_edits']})",
        f"- **Promotion**: {report['approval_state']} (automatic_promotion={report['automatic_promotion']})",
        "",
        "## Addressable markers (trace-grounded failure modes)",
        "",
    ]
    for marker in spec["addressable_markers"]:
        lines.append(f"- `{marker}`")
    lines += ["", "## Graders", ""]
    for grader in spec["graders"]:
        state = "default-on (CI)" if grader.get("default_enabled") else "opt-in"
        lines.append(f"- `{grader['id']}` ({grader['kind']}, {state}) — {grader['description']}")
    lines += ["", "## Verifier-creator decisions", ""]
    for dec in spec["creator_decisions"]:
        lines.append(f"- **{dec['key']}** [{dec['source']}]: {dec['decision']}")
    lines += [
        "",
        "## Limitations",
        "",
    ]
    for lim in spec["limitations"] or ["none"]:
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines)
