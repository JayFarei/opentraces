"""Skill Verifier Factory orchestrator.

Ties the pieces together: mine candidates from the bucket, then emit one verifier
package per requested (skill, archetype) example, then write an index + README. The
default example set is the three bucket-derived skills (goal-forge, tdd, review).

This is an internal consumer (``consumers/verifier_factory``); its user-facing verb
is ``opentraces workflow verifier-factory`` (a dry-run by default). No verifier is
promoted and no skill is changed: every artifact is advisory and human-gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archetypes import DEFAULT_EXAMPLES, resolve_archetype
from .mining import mine_verifier_candidates
from .packaging import VerifierPackageResult, emit_verifier_package
from .schema import APPROVAL_STATE
from .schema import validate_candidates


@dataclass(frozen=True)
class VerifierFactoryRequest:
    out_dir: Path
    project: str | None = None
    examples: tuple[tuple[str, str], ...] = DEFAULT_EXAMPLES
    min_usable_episodes: int = 30
    seed: str = "verifier-factory"
    decisions: dict[str, dict[str, str]] = field(default_factory=dict)
    #: test-only injection: {skill: [episode rows]}
    episodes_by_skill: dict[str, list[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class VerifierFactoryResult:
    out_dir: Path
    candidates_path: Path
    index_path: Path
    packages: tuple[VerifierPackageResult, ...]
    candidates: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_factory(request: VerifierFactoryRequest) -> VerifierFactoryResult:
    out_dir = Path(request.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    skills = sorted({skill for skill, _ in request.examples})
    candidates = mine_verifier_candidates(
        project=request.project,
        skills=skills,
        min_usable_episodes=request.min_usable_episodes,
        episodes_by_skill=request.episodes_by_skill,
    )
    validate_candidates(candidates)
    candidates_path = out_dir / "skill-verifier-candidates-v1.json"
    _write_json(candidates_path, candidates)

    packages: list[VerifierPackageResult] = []
    for skill, archetype_id in request.examples:
        resolve_archetype(skill, archetype_id)  # validate (skill, archetype) resolves
        episodes = (
            request.episodes_by_skill.get(skill)
            if request.episodes_by_skill is not None
            else None
        )
        pkg = emit_verifier_package(
            skill=skill,
            archetype_id=archetype_id,
            out_dir=out_dir / "packages" / archetype_id,
            project=request.project,
            decisions=request.decisions.get(archetype_id),
            episodes=episodes,
            seed=request.seed,
        )
        packages.append(pkg)

    index = {
        "schema_version": "opentraces.skill_verifier_index.v1",
        "generated_at": _utc_now(),
        "project": request.project,
        "candidates": "skill-verifier-candidates-v1.json",
        "approval_state": APPROVAL_STATE,
        "automatic_promotion": False,
        "packages": [
            {
                "archetype_id": p.archetype_id,
                "skill_id": p.skill_id,
                "spec": str(p.spec_path.relative_to(out_dir)),
                "report": str(p.report_path.relative_to(out_dir)),
                "dsel_before": p.dsel_before,
                "dsel_after": p.dsel_after,
                "dtest_score": p.dtest_score,
                "accepted": p.accepted,
                "recommended": p.recommended,
                "gate_basis": p.gate_basis,
                "is_generic": p.is_generic,
                "split": p.split_counts,
                "limitations": list(p.limitations),
            }
            for p in packages
        ],
    }
    index_path = out_dir / "index.json"
    _write_json(index_path, index)
    (out_dir / "README.md").write_text(_render_readme(index), encoding="utf-8")

    return VerifierFactoryResult(
        out_dir=out_dir,
        candidates_path=candidates_path,
        index_path=index_path,
        packages=tuple(packages),
        candidates=candidates,
    )


def _write_json(path: Path, payload: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _render_readme(index: dict[str, Any]) -> str:
    lines = [
        "# Skill Verifier Factory output",
        "",
        f"Generated: {index['generated_at']}",
        "",
        "Trace-grounded verifier packages mined from OpenTraces skill_invocation "
        "evidence. Every package is advisory: "
        f"`{index['approval_state']}`, automatic_promotion="
        f"{index['automatic_promotion']}.",
        "",
        "| archetype | skill | Dsel before→after | Dtest | accepted |",
        "| --- | --- | --- | --- | --- |",
    ]
    for pkg in index["packages"]:
        lines.append(
            f"| `{pkg['archetype_id']}` | `{pkg['skill_id']}` | "
            f"{pkg['dsel_before']:.3f} → {pkg['dsel_after']:.3f} | "
            f"{pkg['dtest_score']:.3f} | {pkg['accepted']} |"
        )
    lines += [
        "",
        f"Candidates summary: `{index['candidates']}`.",
        "",
    ]
    return "\n".join(lines)
