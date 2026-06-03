"""Candidate mining over latest-generation ``skill_invocation`` evidence.

Produces an ``opentraces.skill_verifier_candidates.v1`` summary: for each skill
that has a registered archetype (and that appears in the bucket), it reports the
usable-episode count, agent/source mix, per-archetype evidence strength and
deficient-marker frequency, a leakage-safe-split feasibility verdict, the
verifier-creator interview (with evidence-inferred defaults), and a bounded sample
of source trace/unit refs (provenance).

Reads only the existing read side: it reuses
:func:`consumers.skill_intelligence.pipeline.audit_skill_invocations` and
``build_episode_rows`` (which themselves read
``trace_index.list_skill_invocation_units_from_records`` and
``bucket_store.iter_trace_record_objects``). Nothing is written to the bucket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..skill_intelligence import pipeline as si
from ...core._time import utc_now_str as _utc_now
from ...core.bucket_store import iter_trace_record_objects
from . import archetypes as arch
from . import scorers
from .schema import CANDIDATES_SCHEMA_VERSION, validate_candidates

#: Cap on provenance refs embedded per archetype candidate (keep summary bounded).
_MAX_SOURCE_REFS = 8


def _load_episodes(
    skill: str,
    *,
    project: str | None,
    index_path: Path | None,
    min_rows: int,
) -> list[dict[str, Any]]:
    return si.build_episode_rows(
        selected_skill=skill,
        project=project,
        min_rows=min_rows,
        index_path=index_path,
    )


def _source_refs(episodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for ep in sorted(episodes, key=lambda e: str(e.get("source_unit_id"))):
        trace_id = str(ep.get("source_trace_id") or "")
        unit_id = str(ep.get("source_unit_id") or "")
        key = unit_id or trace_id
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "trace_id": trace_id,
                "unit_id": unit_id,
                "episode_id": str(ep.get("episode_id") or ""),
            }
        )
        if len(refs) >= _MAX_SOURCE_REFS:
            break
    return refs


def _distinct_traces(episodes: list[dict[str, Any]]) -> int:
    return len({str(e.get("source_trace_id")) for e in episodes if e.get("source_trace_id")})


def _archetype_candidate(
    archetype: arch.VerifierArchetype,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strength = scorers.evidence_strength(archetype, episodes, records)
    deficits = scorers.deficient_markers_for_episodes(archetype, episodes, records)
    distinct = _distinct_traces(episodes)
    is_generic = archetype.archetype_id == arch.GENERIC_ARCHETYPE_ID
    recommended = bool(episodes) and distinct >= archetype.min_traces_for_split and not is_generic
    candidate = {
        "archetype_id": archetype.archetype_id,
        "title": archetype.title,
        "semantic_target": archetype.semantic_target,
        "markers": list(archetype.markers),
        "is_generic": is_generic,
        "evidence_strength": strength,
        "deficient_marker_frequency": dict(sorted(deficits.items())),
        "recommended": recommended,
        "creator_questions": [
            {
                "key": q.key,
                "question": q.question,
                "inferred_default": q.inferred_default,
                "rationale": q.rationale,
                "options": list(q.options),
            }
            for q in archetype.creator_questions
        ],
        "live_legs": [
            {
                "leg_id": leg.leg_id,
                "kind": leg.kind,
                "description": leg.description,
                "requires": list(leg.requires),
                "default_enabled": False,
            }
            for leg in archetype.live_legs
        ],
        "provenance": {
            "source": "trace_index.skill_invocation:latest_generation",
            "episodes_scored": len(episodes),
            "trace_records_loaded": bool(records),
        },
    }
    if is_generic:
        # Generic archetypes are a propose->approve DRAFT, never an emit-able verifier
        # without human approval (br/56: text-adjacent generic markers are the highest
        # reward-hacking risk).
        candidate["draft"] = propose_archetype_draft(archetype, episodes, records)
    return candidate


def propose_archetype_draft(
    archetype: arch.VerifierArchetype,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Propose a human-reviewable archetype DRAFT from trace evidence.

    For each contract element it reports support (episodes exhibiting it) plus a
    bounded example + counterexample trace ref, so a human/agent can approve, refine,
    or discard the proposed verifier rather than receiving an auto-generated one.
    """
    per_element: list[dict[str, Any]] = []
    for element in archetype.contract_elements:
        examples: list[str] = []
        counter: list[str] = []
        present = 0
        for ep in episodes:
            es = scorers.score_episode(archetype, ep, scorers._record_for(ep, records))
            present_marker = element.marker in es.present_markers
            present += 1 if present_marker else 0
            ref = str(ep.get("source_trace_id") or ep.get("episode_id") or "")
            if present_marker and len(examples) < 2:
                examples.append(ref)
            elif not present_marker and len(counter) < 2:
                counter.append(ref)
        per_element.append(
            {
                "element_id": element.element_id,
                "label": element.label,
                "marker": element.marker,
                "weight": element.weight,
                "support_episodes": present,
                "example_trace_refs": examples,
                "counterexample_trace_refs": counter,
            }
        )
    return {
        "draft_archetype_id": archetype.archetype_id,
        "skill_id": archetype.skill,
        "title": archetype.title,
        "semantic_target": archetype.semantic_target,
        "proposed_contract_elements": per_element,
        "creator_questions": [
            {"key": q.key, "question": q.question, "inferred_default": q.inferred_default}
            for q in archetype.creator_questions
        ],
        "approval_required": True,
        "note": (
            "Advisory draft proposed from trace evidence. No verifier is emitted or "
            "promoted from a generic draft without explicit human approval."
        ),
    }


def mine_verifier_candidates(
    *,
    project: str | None = None,
    skills: list[str] | None = None,
    min_usable_episodes: int = 30,
    index_path: Path | None = None,
    episodes_by_skill: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Mine verifier candidates for skills that have a registered archetype.

    ``episodes_by_skill`` lets callers (tests) inject episode rows directly,
    bypassing the bucket; otherwise episodes are loaded from the local bucket.
    """
    target_skills = skills or sorted(arch.ARCHETYPES_BY_SKILL)

    records: dict[str, Any] | None = None
    if episodes_by_skill is None:
        audit = si.audit_skill_invocations(
            project=project,
            index_path=index_path,
            min_usable_episodes=min_usable_episodes,
            candidates=tuple(target_skills),
        )
        counts_by_skill = audit.get("counts_by_skill", {})
        total_units = int(audit.get("total_skill_invocation_units", 0))
        # Load TraceRecords once so detectors can use the real shell commands +
        # commit signal (Codex exec_command opacity) instead of only the projection.
        records = {o.trace_id: o.record for o in iter_trace_record_objects(project_slug=project)}
    else:
        counts_by_skill = {}
        total_units = 0

    skill_entries: list[dict[str, Any]] = []
    for skill in target_skills:
        archetypes = arch.archetypes_for_skill(skill)
        if not archetypes:
            continue
        if episodes_by_skill is not None:
            episodes = list(episodes_by_skill.get(skill, []))
        else:
            episodes = _load_episodes(
                skill, project=project, index_path=index_path, min_rows=0
            )
            total_units += 0  # episodes already counted in audit total
        counts = counts_by_skill.get(skill, {})
        agents = dict(counts.get("agents", {})) if counts else _tally(episodes, "agent")
        sources = dict(counts.get("sources", {})) if counts else _tally(episodes, "skill_source")
        usable = (
            int(counts.get("usable_episodes", 0))
            if counts
            else sum(1 for e in episodes if str(e.get("confidence")) in {"high", "medium"})
        )
        distinct = _distinct_traces(episodes)
        skill_entries.append(
            {
                "skill_id": skill,
                "usable_episodes": usable,
                "episodes_observed": len(episodes),
                "distinct_source_traces": distinct,
                "agents": dict(sorted(agents.items())),
                "sources": dict(sorted(sources.items())),
                "leakage_safe_split": distinct >= min(a.min_traces_for_split for a in archetypes),
                "archetypes": [_archetype_candidate(a, episodes, records) for a in archetypes],
                "source_refs": _source_refs(episodes),
            }
        )

    payload = {
        "schema_version": CANDIDATES_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "project": project,
        "total_skill_invocation_units": total_units,
        "min_usable_episodes": min_usable_episodes,
        "skills": skill_entries,
        "notes": (
            "Mined from latest-generation skill_invocation units. Recommendations are "
            "advisory; no verifier is generated or promoted without explicit human "
            "approval."
        ),
    }
    validate_candidates(payload)
    return payload


def _tally(episodes: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ep in episodes:
        val = str(ep.get(key) or "unknown")
        out[val] = out.get(val, 0) + 1
    return out
