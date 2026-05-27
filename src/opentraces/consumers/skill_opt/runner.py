"""SkillOpt consumer runner: workflow rows -> optimized skill (the contract's
``run`` entrypoint).

This is the one place that wires the scored-rollout workflow to the offline
optimizer: it builds the scope, runs the ``skill-opt-v1`` workflow via the
shared :func:`opentraces.consumers.contract.run_workflow_rows` primitive, parses
rows, runs the propose-and-rank loop (multi-epoch with the epoch-boundary
slow/meta update), exports ``best_skill.md`` + ``edit_apply_report.json``, and,
on a non-dry-run, promotes the winning skill to a managed location.

The live agent re-rollout and the task-outcome scorer remain the next slice; the
held-out gate here uses the deterministic proxy in :mod:`.engine`, and the edit
proposer is the deterministic :func:`.proposers.default_proposer` (swap in
:func:`.proposers.make_llm_proposer` at the same seam once a model client and a
real scorer exist).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core import paths as ot_paths
from ..contract import ConsumerArtifact, run_workflow_rows
from .engine import Proposer, RolloutRow, default_slow_update, run_optimization
from .proposers import default_proposer

DEFAULT_WORKFLOW = "skill-opt-v1"

DEFAULT_INITIAL_SKILL = """# Project skill (SkillOpt-managed)

Procedural rules learned from scored rollouts. Edits below this line are
proposed by the optimizer and accepted only when they improve the held-out
selection score.

<!-- SLOW_UPDATE_START -->
<!-- SLOW_UPDATE_END -->
"""


def _skill_opt_root() -> Path:
    return ot_paths.OPENTRACES_DIR / "skill_opt"


def promoted_skill_dir(workflow_name: str) -> Path:
    """Versioned promotion directory for a workflow's optimized skills."""
    return _skill_opt_root() / "skills" / workflow_name


def promoted_skill_path(workflow_name: str) -> Path:
    """The ``current`` pointer to the latest promoted skill for a workflow."""
    return promoted_skill_dir(workflow_name) / "current.md"


def lineage_path() -> Path:
    """Consumer-owned append-only skill-version lineage log."""
    return _skill_opt_root() / "lineage.jsonl"


def _promote(workflow_name: str, best_skill_path: Path, *, best_score: float, accepted: int) -> tuple[Path, int]:
    """Write a new immutable skill version, update ``current.md``, and append a
    lineage record. Returns ``(current_pointer_path, version)``.

    Lineage is recorded in a consumer-owned append-only JSONL rather than the
    canonical Trail event ref: emitting a new Trail event type would touch the
    frozen Trail contract, which this slice's boundary forbids. The record reuses
    a TrailEvent-shaped envelope so a later slice can replay it into the Trail ref
    additively.
    """
    skill_text = best_skill_path.read_text(encoding="utf-8")
    skills_dir = promoted_skill_dir(workflow_name)
    skills_dir.mkdir(parents=True, exist_ok=True)
    version = 1 + sum(1 for p in skills_dir.glob("v*.md"))
    version_path = skills_dir / f"v{version}.md"
    version_path.write_text(skill_text, encoding="utf-8")
    current = promoted_skill_path(workflow_name)
    current.write_text(skill_text, encoding="utf-8")

    record = {
        "schema_version": "opentraces.skill_opt.lineage.v1",
        "event_type": "skill_promoted",
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "workflow": workflow_name,
        "version": version,
        "parent_version": version - 1 if version > 1 else None,
        "best_score": best_score,
        "accepted_edits": accepted,
        "skill_sha256": hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
        "skill_path": str(version_path),
    }
    lp = lineage_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    with lp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return current, version


@dataclass(frozen=True)
class SkillOptRequest:
    out_dir: Path
    workflow_name: str = DEFAULT_WORKFLOW
    project: str | None = None
    budget: float = 4
    budget_floor: float = 2
    schedule: str = "cosine"
    selection_fraction: float = 0.4
    seed: str = "skillopt"
    max_steps: int = 8
    epochs: int = 1
    initial_skill: str = DEFAULT_INITIAL_SKILL
    dry_run: bool = True
    slow_update: bool = True
    proposer: Proposer = default_proposer


@dataclass(frozen=True)
class SkillOptOutcome:
    artifact: ConsumerArtifact
    rollout_rows: int
    initial_score: float
    best_score: float
    steps: int
    accepted: int
    rejected: int
    best_skill_path: Path
    report_path: Path
    promoted_path: Path | None
    metadata: dict[str, Any] = field(default_factory=dict)


# Satisfies the WorkflowConsumer Protocol structurally.
name = "skill_opt"
workflow_name = DEFAULT_WORKFLOW


def run(request: SkillOptRequest) -> SkillOptOutcome:
    """Run the SkillOpt consumer end to end (the contract's ``run``)."""
    out_dir = Path(request.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scope: dict[str, Any] = {"kind": "skill_opt"}
    if request.project:
        scope["project_slug"] = request.project

    workflow_rows = run_workflow_rows(
        request.workflow_name,
        scope=scope,
        output_path=out_dir / "rollout_rows.jsonl",
    )
    rows = [RolloutRow.from_json(row) for row in workflow_rows.rows]

    opt = run_optimization(
        request.initial_skill,
        rows,
        propose=request.proposer,
        budget=request.budget,
        budget_floor=request.budget_floor,
        schedule=request.schedule,
        selection_fraction=request.selection_fraction,
        seed=request.seed,
        max_steps=request.max_steps,
        epochs=request.epochs,
        slow_update=default_slow_update if request.slow_update else None,
    )
    artifacts = opt.state.export(out_dir)
    best_skill_path = Path(artifacts["best_skill"])
    report_path = Path(artifacts["report"])

    # Promote only when the run actually improved the skill. An empty bucket, a
    # wrong --project, or a no-improving-edits run must never overwrite the
    # managed skill with the unoptimized starter.
    promoted_path: Path | None = None
    promote_skipped_reason: str | None = None
    promoted_version: int | None = None
    if not request.dry_run:
        if opt.accepted > 0:
            promoted_path, promoted_version = _promote(
                request.workflow_name, best_skill_path,
                best_score=opt.best_score, accepted=opt.accepted,
            )
        elif workflow_rows.row_count == 0:
            promote_skipped_reason = "no rollout rows (empty bucket or wrong --project)"
        else:
            promote_skipped_reason = "no improving edits accepted"

    metadata = {
        "workflow": request.workflow_name,
        "rollout_rows": workflow_rows.row_count,
        "initial_score": opt.initial_score,
        "best_score": opt.best_score,
        "steps": opt.steps,
        "accepted_edits": opt.accepted,
        "rejected_edits": opt.rejected,
        "promoted": str(promoted_path) if promoted_path else None,
        "promoted_version": promoted_version,
        "promote_skipped_reason": promote_skipped_reason,
    }
    return SkillOptOutcome(
        artifact=ConsumerArtifact(
            kind="skill", path=best_skill_path, text=opt.best_skill, metadata=metadata
        ),
        rollout_rows=workflow_rows.row_count,
        initial_score=opt.initial_score,
        best_score=opt.best_score,
        steps=opt.steps,
        accepted=opt.accepted,
        rejected=opt.rejected,
        best_skill_path=best_skill_path,
        report_path=report_path,
        promoted_path=promoted_path,
        metadata=metadata,
    )
