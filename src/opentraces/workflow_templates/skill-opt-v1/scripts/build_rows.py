"""Builder for the skill-opt-v1 workflow: scored-rollout rows from the bucket.

Reads ``OT_RUN_PACKET`` (scope may carry ``project_slug``/``project`` to narrow
the bucket scan) and writes one JSONL row per already-captured trace to
``OT_DATASET_OUTPUT``. Each row is a *scored rollout*: the trace's
``quality.engine`` ``overall_utility`` becomes the rollout ``score`` (in [0,1]),
and the failed / passed quality checks become ``failure_tags`` / ``success_tags``
for the optimizer's reflection and the held-out gate.

This is the forward-pass-over-already-captured-evidence projection. The live
agent re-rollout that would re-execute a candidate skill is a later slice; here
the rows are the fixed evidence the offline optimizer reasons over.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from opentraces.core.bucket_store import iter_corpus_trace_records
from opentraces.consumers.skill_opt.engine import outcome_reward
from opentraces.quality.engine import assess_trace

_MAX_TAGS = 24


def _survival_state(record: Any, project_slug: str | None) -> str | None:
    """Best-effort Trail survival of the trace's commit. Returns a survival
    state string (e.g. ``alive_on_path``/``reverted``) or ``None`` when it can't
    be resolved (no commit, no repo, or any error) so reward degrades cleanly.
    """
    commit_sha = getattr(getattr(record, "outcome", None), "commit_sha", None)
    if not commit_sha or not project_slug:
        return None
    try:
        from opentraces.core.bucket_store import _iter_opted_in_projects
        from opentraces.enrichment.git.liveness import commit_reachable

        repo = next((p for p, slug in _iter_opted_in_projects() if slug == project_slug), None)
        if repo is None:
            return None
        return "alive_on_path" if commit_reachable(commit_sha, repo) else "reverted"
    except Exception:
        return None


def _tags(assessment: Any) -> tuple[list[str], list[str]]:
    failure: list[str] = []
    success: list[str] = []
    for persona_name, persona in assessment.persona_scores.items():
        if getattr(persona, "skipped", False):
            continue
        for item in persona.items:
            if getattr(item, "skipped", False):
                continue
            tag = f"{persona_name}.{item.name}"
            (success if item.passed else failure).append(tag)
    return failure[:_MAX_TAGS], success[:_MAX_TAGS]


def main() -> int:
    packet_path = os.environ.get("OT_RUN_PACKET")
    output_path = os.environ.get("OT_DATASET_OUTPUT")
    if not packet_path or not output_path:
        print("OT_RUN_PACKET and OT_DATASET_OUTPUT must be set", file=sys.stderr)
        return 2

    packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))
    scope = packet.get("scope") or {}
    project_slug = scope.get("project_slug") or scope.get("project") or None

    out_lines: list[str] = []
    for obj in iter_corpus_trace_records(project_slug=project_slug):
        try:
            assessment = assess_trace(obj.record)
        except Exception as exc:  # never let one bad trace abort the projection
            print(f"skip {obj.trace_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        failure_tags, success_tags = _tags(assessment)
        utility = float(assessment.overall_utility or 0.0)
        oc = getattr(obj.record, "outcome", None)
        survival = _survival_state(obj.record, obj.project_slug)
        reward = outcome_reward(
            success=getattr(oc, "success", None),
            committed=bool(getattr(oc, "committed", False)),
            survival_state=survival,
        )
        row = {
            "trace_id": obj.trace_id,
            "project_slug": obj.project_slug,
            "session_id": obj.record.session_id,
            "reward": reward,
            "outcome_success": getattr(oc, "success", None),
            "outcome_committed": bool(getattr(oc, "committed", False)),
            "survival_state": survival,
            "score": round(utility / 100.0, 6),
            "overall_utility": round(utility, 4),
            "failure_tags": failure_tags,
            "success_tags": success_tags,
            "summary": (obj.record.task.description or "")[:200],
            "skill_digest": None,
        }
        out_lines.append(json.dumps(row, sort_keys=True))

    Path(output_path).write_text(
        ("\n".join(out_lines) + "\n") if out_lines else "",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
