#!/usr/bin/env python3
"""Emit skill-verifier-candidates-v1 rows (one per skill x archetype).

Reads the run packet's scope (project / skills / min_usable_episodes), calls the
Skill Verifier Factory miner, and writes one row per archetype candidate to
``OT_DATASET_OUTPUT``. The miner reads only the existing skill_invocation read side
(Trace Index + bucket); nothing is written to the bucket.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROW_SCHEMA_VERSION = "opentraces.skill_verifier_candidates_row.v1"


def _env_scope() -> tuple[dict, Path] | None:
    packet = os.environ.get("OT_RUN_PACKET")
    output = os.environ.get("OT_DATASET_OUTPUT")
    if not packet or not output:
        return None
    payload = json.loads(Path(packet).read_text(encoding="utf-8"))
    return dict(payload.get("scope") or {}), Path(output)


def _rows_from_candidates(candidates: dict) -> list[dict]:
    rows: list[dict] = []
    for skill in candidates.get("skills", []):
        for arch in skill.get("archetypes", []):
            rows.append(
                {
                    "schema_version": ROW_SCHEMA_VERSION,
                    "skill_id": skill["skill_id"],
                    "archetype_id": arch["archetype_id"],
                    "title": arch["title"],
                    "semantic_target": arch.get("semantic_target", ""),
                    "usable_episodes": int(skill.get("usable_episodes", 0)),
                    "distinct_source_traces": int(skill.get("distinct_source_traces", 0)),
                    "leakage_safe_split": bool(skill.get("leakage_safe_split", False)),
                    "evidence_strength": float(arch.get("evidence_strength", 0.0)),
                    "recommended": bool(arch.get("recommended", False)),
                    "markers_json": json.dumps(arch.get("markers", []), sort_keys=True),
                    "deficient_marker_frequency_json": json.dumps(
                        arch.get("deficient_marker_frequency", {}), sort_keys=True
                    ),
                    "creator_questions_json": json.dumps(
                        arch.get("creator_questions", []), sort_keys=True
                    ),
                    "live_legs_json": json.dumps(arch.get("live_legs", []), sort_keys=True),
                    "source_refs_json": json.dumps(skill.get("source_refs", []), sort_keys=True),
                }
            )
    rows.sort(key=lambda r: (r["skill_id"], r["archetype_id"]))
    return rows


def main() -> int:
    scoped = _env_scope()
    if scoped is None:
        print("OT_RUN_PACKET / OT_DATASET_OUTPUT not set", flush=True)
        return 2
    scope, output = scoped

    from opentraces.consumers.verifier_factory import mine_verifier_candidates

    skills = scope.get("skills")
    candidates = mine_verifier_candidates(
        project=scope.get("project"),
        skills=list(skills) if skills else None,
        min_usable_episodes=int(scope.get("min_usable_episodes", 30)),
    )
    rows = _rows_from_candidates(candidates)
    output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
