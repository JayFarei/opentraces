"""Bounded, read-only evidence resolution for the agent-judge packet.

A criterion's :class:`EvidenceSpec` is the ONLY way it sees a trace. ``resolve_evidence``
assembles a deterministic, canonical-JSON-serializable bundle from what the bucket cheaply
provides (the EpisodeEvidence projection + the record's outcome), with VISIBLE truncation
markers (never a silent drop) and a stable content digest. Rich sources that aren't cheaply
available on this bucket (trace_slice / diff / ctx_reads) resolve to an explicit
``{"status": "unavailable"}`` so a judge ABSTAINS rather than silently passing.

No writes, no network, no shell. The digest pins exactly the bytes a judge saw, so a verdict
is auditable and re-derivable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .archetypes import evidence_from_episode
from .rubric import EvidenceSpec

_TRUNC = "...[truncated]"
_MAX_FIELD_CHARS = 2000


def _bounded_list(values: tuple[str, ...] | list, cap: int) -> list:
    out = list(values)[:cap]
    if len(values) > cap:
        out.append(f"{_TRUNC}:{len(values) - cap} more")
    return out


def _bounded_str(value: Any) -> Any:
    """Cap a string evidence field with a visible truncation marker (M9), never silent."""
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[:_MAX_FIELD_CHARS] + _TRUNC
    return value


def resolve_evidence(
    spec: EvidenceSpec,
    *,
    trace_id: str,
    episode: dict[str, Any],
    record: Any = None,
    epoch: str = "e0",
) -> dict[str, Any]:
    """Assemble a bounded read-only evidence bundle for one (criterion, trace).

    Returns ``{payload, digest, refs, epoch, available}``. ``available`` is False when every
    requested source was unavailable (the judge must ABSTAIN / the verdict is blocked).
    """
    ev = evidence_from_episode(episode, record)
    payload: dict[str, Any] = {}
    available = False

    if spec.episode_fields:
        fields: dict[str, Any] = {}
        for f in spec.episode_fields:
            if f == "files_touched_json":
                fields[f] = _bounded_list(ev.files, 20)
            elif f == "tools_used_json":
                fields[f] = _bounded_list(ev.tools, 20)
            else:
                fields[f] = _bounded_str(episode.get(f))
        payload["episode_fields"] = fields
        available = True

    if spec.trace_get:
        payload["trace_get"] = {
            "outcome_label": ev.outcome_label,
            "outcome_reward": ev.outcome_reward,
            "committed": ev.committed,
            "n_files": len(ev.files),
            "n_commands": len(ev.commands),
            "command_families": sorted(ev.command_families),
            "step_count": ev.step_count,
        }
        available = True

    if spec.trail_survival:
        outcome = getattr(record, "outcome", None) if record is not None else None
        survival = getattr(outcome, "survival_state", None)
        payload["trail_survival"] = {"survival_state": survival or "unresolved",
                                     "committed": getattr(outcome, "committed", None)}
        available = available or survival is not None

    # Rich sources: best-effort. On this bucket they are not cheaply resolvable, so they are
    # surfaced as explicitly unavailable -> the judge abstains, never silently passes.
    for key, present in (("trace_slice", spec.trace_slice), ("diff", spec.diff),
                         ("ctx_reads", spec.ctx_reads), ("produced_artifact", spec.produced_artifact)):
        if present:
            payload[key] = {"status": "unavailable",
                            "note": "not resolvable from the episode projection on this bucket"}

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "payload": payload,
        "digest": digest,
        "refs": {"trace_id": trace_id, "unit_id": str(episode.get("source_unit_id") or "")},
        "epoch": epoch,
        "available": available,
    }
