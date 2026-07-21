"""Agent-facing Trace Trails JSON contract helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .ids import (
    git_anchor_ref,
    git_commit_ref,
    id_from_payload,
    normalize_id,
    source_session_ref,
    trace_patch_ref,
    trace_ref,
    trace_step_ref,
)

SEARCH_SCHEMA_VERSION = "opentraces.trail.search.v1"
# Event-payload envelope for the anchor-search SUMMARY event written by
# reconcile_commit_anchors (plan 090). v2 collapses the per-patch
# git_anchor_search_completed events (one per patch, ~N/commit) into ONE
# summary event per (commit, reconcile-run) carrying a per-patch ``results``
# list. This is the EVENT payload schema_version and is intentionally distinct
# from SEARCH_SCHEMA_VERSION above (the unrelated ``trail search`` projection
# envelope). Bump only when the summary payload shape changes — three callers
# (anchors.py's write path, maturation.py's periodic flush, search_compaction.py's
# rollup) share this constant, so it identifies the full-mixed-``results[]``
# shape all three still emit; do not repoint it at a shape only one of them
# produces.
ANCHOR_SEARCH_SCHEMA_VERSION = "opentraces.trail.anchor_search.v2"
# v3 (issue #358): v2's ``results[]`` carried one dict per SEARCHED patch,
# unknown outcomes included — on a mature repo that fanned ~26GB of mostly
# never-anchored unknown dicts into every trace companion the summary
# touched. v3 keeps ``results[]`` ANCHORED-ONLY and replaces the dropped
# unknown dicts with one ``coverage`` through-pointer claim (see
# ``search_records.iter_coverage_claims``) — the dedup and fan-out surfaces
# consumers need from an unknown outcome (was it searched? did it touch this
# trace?) come from the claim and the scalars, not a per-patch dict. A
# SEPARATE constant, not a bump of ANCHOR_SEARCH_SCHEMA_VERSION above: only
# anchors.py's live coverage-bearing write path emits this shape today.
# maturation.py's periodic flush and search_compaction.py's rollup still
# build full-mixed ``results[]`` (no ``coverage``, unknown dicts included) —
# stamping this label on their output would mislabel a v2 payload as v3,
# and the planned compaction pass (design: "v3 events pass through
# unchanged") would then skip those fat events forever instead of compacting
# them. Repoint a caller here only once it actually emits the v3 shape.
ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION = "opentraces.trail.anchor_search.v3"
RESOLVE_SCHEMA_VERSION = "opentraces.trail.resolve.v1"
# ``trail blame`` --json envelopes (ADR-0007 lint L5). Three distinct versions
# because a schema_version identifies a SHAPE and the three blame forms share
# no top-level structure: commit-mode ({commit, coverage, traces, files, ...}),
# trace-mode t:<id> ({trace, commits, files}), and file:line
# ({target, relation, trailEvidence, ...}). The header is ADDITIVE — every
# pre-existing key is unchanged; bump on any shape change.
BLAME_SCHEMA_VERSION = "opentraces.trail.blame.v1"
BLAME_TRACE_SCHEMA_VERSION = "opentraces.trail.blame_trace.v1"
BLAME_LINE_SCHEMA_VERSION = "opentraces.trail.blame_line.v1"
TRACE_TRAILS_METADATA_SCHEMA_VERSION = "opentraces.trace_trails.v1"
SNAPSHOT_RESUME_SCHEMA_VERSION = "opentraces.snapshot_resume.v1"
SNAPSHOT_RESUME_METADATA_KEY = "trace_trails.snapshot_resume_contract"
SNAPSHOT_RESUME_MIN_OPENTRACES_VERSION = "0.4.0"
PROJECTION_NAME = "trace_trail"
PROJECTION_VERSION = "v1"

EVIDENCE_LABELS = {
    "exact_blob_hash": "exact blob match",
    "exact_range_hash": "exact range match",
    "git_clean_range_hash": "normalized range match",
    "patch_id": "patch-id match",
    "structural_symbol": "structural symbol match",
    "formatter_divergent": "formatter-divergent match",
    "overlapping_hunk": "overlapping hunk",
    "time_file_overlap": "weak time/file overlap",
    "orphan": "no reliable landing",
    "unknown": "unknown evidence",
}

EVIDENCE_RANKS = {
    "exact_blob_hash": 1,
    "exact_range_hash": 1,
    "git_clean_range_hash": 2,
    "patch_id": 2,
    "structural_symbol": 3,
    "formatter_divergent": 4,
    "overlapping_hunk": 5,
    "time_file_overlap": 6,
    "orphan": 99,
    "unknown": 100,
}

EVIDENCE_REASONS = {
    "exact_blob_hash": ["committed_blob_hash_matches_trace_patch_blob_hash"],
    "exact_range_hash": ["committed_range_hash_matches_trace_patch_range_hash"],
    "git_clean_range_hash": ["normalized_committed_range_matches_trace_patch"],
    "patch_id": ["git_patch_id_matches_trace_patch"],
    "structural_symbol": ["structural_symbol_match_supports_trace_patch_anchor"],
    "formatter_divergent": ["formatter_divergence_allowed_by_anchor_rule"],
    "overlapping_hunk": ["commit_hunk_overlaps_trace_patch_range"],
    "time_file_overlap": ["trace_step_time_window_overlaps_commit_file_change"],
    "orphan": ["no_reliable_git_anchor_observed"],
    "unknown": ["no_reliable_evidence_observed"],
}

FIRMNESS_MAP = {
    "firm": "firm_observed",
    "provisional": "provisional",
    "human": "human_asserted",
    "human_asserted": "human_asserted",
    "inferred": "rule_inferred",
    "rule_inferred": "rule_inferred",
    "model_inferred": "model_inferred",
    "statistically_inferred": "statistically_inferred",
    "unknown": "unknown",
}

LIMITATION_ACTIONS = {
    "git_anchor_unknown": "Run trail maturation or attach the Trace Patch to a commit.",
    "trail_event_log_unavailable": "Run otd trail rebuild after TraceEvents are available.",
    "unsupported_survival_filter": "Use --survival reverted for the Phase 7 search surface.",
    "no_git_anchor_for_line": "Run otd trail search --path for nearby committed Trace Patches.",
    "maturation_stale": "Run otd trail rebuild.",
}


def snapshot_resume_trace_metadata() -> dict[str, str]:
    return {
        "schema_version": TRACE_TRAILS_METADATA_SCHEMA_VERSION,
        "snapshot_resume_contract": SNAPSHOT_RESUME_SCHEMA_VERSION,
    }


def required_snapshot_resume_capability() -> dict[str, str]:
    return {
        "metadata_key": SNAPSHOT_RESUME_METADATA_KEY,
        "schema_version": SNAPSHOT_RESUME_SCHEMA_VERSION,
        "min_opentraces_version": SNAPSHOT_RESUME_MIN_OPENTRACES_VERSION,
    }


def satisfied_snapshot_resume_contract() -> dict[str, str]:
    return {
        "metadata_key": SNAPSHOT_RESUME_METADATA_KEY,
        "schema_version": SNAPSHOT_RESUME_SCHEMA_VERSION,
        "status": "satisfied",
    }


def has_snapshot_resume_contract(metadata: dict[str, Any] | None) -> bool:
    metadata = metadata if isinstance(metadata, dict) else {}
    trace_trails = metadata.get("trace_trails")
    if not isinstance(trace_trails, dict):
        return False
    return trace_trails.get("snapshot_resume_contract") == SNAPSHOT_RESUME_SCHEMA_VERSION


def evidence_label(tier: str | None) -> str:
    key = tier or "unknown"
    return EVIDENCE_LABELS.get(key, key.replace("_", " "))


def normalize_firmness(value: str | None) -> str:
    return FIRMNESS_MAP.get(value or "unknown", value or "unknown")


def typed_limitations(limitations: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in limitations or []:
        if isinstance(item, dict):
            out.append(item)
            continue
        code = str(item)
        limitation = {
            "code": code,
            "severity": "warning",
            "affects": _limitation_affects(code),
            "message": code.replace("_", " "),
        }
        action = LIMITATION_ACTIONS.get(code)
        if action:
            limitation["recommended_action"] = action
        out.append(limitation)
    return out


def project_identity(repo: Path) -> dict[str, Any]:
    marker = repo / ".opentraces.json"
    project_id = None
    if marker.exists():
        try:
            raw = json.loads(marker.read_text())
            project_id = raw.get("project_id")
        except (OSError, json.JSONDecodeError):
            project_id = None
    return {
        "project_id": project_id,
        "repo_root": str(repo),
        "vcs": "git",
        "ref": "ot://repo/local",
    }


def projection_digest(events: list[Any]) -> str | None:
    if not events:
        return None
    payload = "\n".join(
        f"{event.event_sequence}:{event.event_id}:{event.event_type}" for event in events
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def projection_watermark(
    *,
    events_seen: int,
    last_event_id: str | None,
    digest: str | None,
) -> dict[str, Any]:
    return {
        "events_seen": events_seen,
        "last_event_id": last_event_id,
        "projection_digest": digest,
    }


def enrich_trail_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add the v1 agent contract fields while preserving legacy flat keys."""
    out = dict(row)
    out["lineage_key"] = lineage_key(out)
    out["origin"] = origin_object(out)
    out["trace_patch"] = trace_patch_object(out)
    out["landing"] = landing_object(out)
    out["evidence"] = evidence_object(out)
    out["current_state"] = current_state_object(out)
    out["ranges"] = lineage_ranges(out)
    out["line_origin_refs"] = line_origin_refs(out)
    out["limitation_details"] = typed_limitations(
        list(out.get("limitations") or []) + list(out.get("trail_limitations") or [])
    )
    return out


def lineage_key(row: dict[str, Any]) -> dict[str, Any]:
    trace_id = row.get("trace_id")
    step_id = row.get("step_id")
    step_index = row.get("step_index")
    trace_patch_id = _trace_patch_id(row)
    git_anchor_id = _git_anchor_id(row)
    commit = git_commit_ref(row.get("commit_id") or row.get("commit_sha"))
    out: dict[str, Any] = {}
    if trace_id:
        out["trace"] = trace_ref(trace_id)
    if row.get("session_id"):
        out["source_session"] = source_session_ref(
            row.get("source_provider") or "unknown",
            row["session_id"],
        )
    if trace_id and step_id:
        out["step"] = trace_step_ref(trace_id, step_id, index=step_index)
    if trace_patch_id:
        out["trace_patch"] = trace_patch_ref(trace_patch_id)
    if git_anchor_id:
        out["git_anchor"] = git_anchor_ref(
            git_anchor_id,
            relation=row.get("relation") or "anchored_in_git",
        )
    if commit:
        out["commit"] = commit
    return out


def origin_object(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": trace_step_ref(
            row.get("trace_id"),
            row.get("step_id"),
            index=row.get("step_index"),
        ) if row.get("trace_id") and row.get("step_id") else None,
        "kind": "trace_step",
        "trace": trace_ref(row["trace_id"]) if row.get("trace_id") else None,
        "step_id": row.get("step_id"),
        "step_index": row.get("step_index"),
        "generation_index": row.get("generation_index"),
    }


def trace_patch_object(row: dict[str, Any]) -> dict[str, Any]:
    resource_refs = row.get("resource_refs") or {}
    trace_patch_id = _trace_patch_id(row)
    ref = trace_patch_ref(trace_patch_id) if trace_patch_id else None
    return {
        "kind": "trace_patch",
        "ref": ref,
        "trace_patch_id": trace_patch_id,
        "file_path": row.get("file_path") or row.get("path"),
        "patch_status": row.get("patch_status"),
        "ranges": _ranges_for(
            row.get("file_path") or row.get("path"),
            row.get("affected_range"),
            side="trace_patch",
        ),
        "resource_ref": resource_refs.get("trace_patch_trail")
        or ((ref or {}).get("ref") + "/trail" if ref else None),
    }


def landing_object(row: dict[str, Any]) -> dict[str, Any] | None:
    git_anchor_id = _git_anchor_id(row)
    if not git_anchor_id and not row.get("commit_sha"):
        return None
    resource_refs = row.get("resource_refs") or {}
    anchor_ref = (
        git_anchor_ref(git_anchor_id, relation=row.get("relation") or "anchored_in_git")
        if git_anchor_id
        else None
    )
    return {
        "kind": "git_anchor",
        "ref": anchor_ref,
        "relation": "landed_in_commit",
        "anchor_relation": row.get("relation"),
        "git_anchor_id": git_anchor_id,
        "commit_sha": row.get("commit_sha"),
        "commit_ref": git_commit_ref(row.get("commit_id") or row.get("commit_sha")),
        "file_path": row.get("file_path") or row.get("path"),
        "ranges": _ranges_for(
            row.get("file_path") or row.get("path"),
            row.get("range"),
            side="landing",
        ),
        "resource_ref": resource_refs.get("git_anchor") or (anchor_ref or {}).get("ref"),
    }


def evidence_object(row: dict[str, Any]) -> dict[str, Any]:
    tier = row.get("evidence_tier") or "unknown"
    resource_refs = row.get("resource_refs") or {}
    evidence_refs = [
        resource_refs.get("trace_patch_trail"),
        resource_refs.get("git_anchor"),
    ]
    return {
        "tier": tier,
        "label": evidence_label(tier),
        "firmness": normalize_firmness(row.get("evidence_firmness") or row.get("firmness")),
        "rank": EVIDENCE_RANKS.get(tier, EVIDENCE_RANKS["unknown"]),
        "reason_codes": EVIDENCE_REASONS.get(tier, EVIDENCE_REASONS["unknown"]),
        "evidence_refs": [ref for ref in evidence_refs if ref],
    }


def current_state_object(row: dict[str, Any]) -> dict[str, Any]:
    current = row.get("current_survival") or {}
    observed_commit = _hex(current.get("observed_commit_id")) or row.get("commit_sha")
    state = row.get("survival_state") or current.get("survival_state") or "unknown"
    path = current.get("current_path") or current.get("path") or row.get("file_path") or row.get("path")
    ranges = _ranges_for(path, current.get("range") or row.get("range"), side="current")
    return {
        "survival_state": state,
        "label": state.replace("_", " "),
        "observed_at_ref": current.get("observed_ref"),
        "observed_at_commit": observed_commit,
        "path": path,
        "ranges": ranges,
        "firmness": normalize_firmness(
            current.get("evidence_firmness")
            or row.get("evidence_firmness")
            or row.get("firmness")
        ),
        "limitations": typed_limitations(current.get("limitations") or []),
    }


def lineage_ranges(row: dict[str, Any]) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    ranges.extend(
        _ranges_for(
            row.get("file_path") or row.get("path"),
            row.get("affected_range"),
            side="trace_patch",
        )
    )
    ranges.extend(
        _ranges_for(
            row.get("file_path") or row.get("path"),
            row.get("range"),
            side="landing",
        )
    )
    return _dedupe_ranges(ranges)


def line_origin_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
    origin = row.get("line_origin")
    if isinstance(origin, dict) and origin.get("resource_ref"):
        return [
            {
                "path": origin.get("path"),
                "line": origin.get("line"),
                "resource_ref": origin.get("resource_ref"),
            }
        ]
    refs = row.get("resource_refs") or {}
    line_ref = refs.get("file_line_origin")
    line_range = row.get("range") or row.get("affected_range") or {}
    line = line_range.get("start_line")
    path = row.get("file_path") or row.get("path")
    if not line_ref or line is None:
        return []
    return [{"path": path, "line": line, "resource_ref": line_ref}]


def _trace_patch_id(row: dict[str, Any]) -> str | None:
    ref = row.get("trace_patch_ref")
    if isinstance(ref, dict) and ref.get("id"):
        return normalize_id(ref["id"])
    if row.get("trace_patch_id"):
        return normalize_id(row["trace_patch_id"])
    return id_from_payload(row, "trace_patch")


def _git_anchor_id(row: dict[str, Any]) -> str | None:
    ref = row.get("git_anchor_ref")
    if isinstance(ref, dict) and ref.get("id"):
        return normalize_id(ref["id"])
    if row.get("git_anchor_id"):
        return normalize_id(row["git_anchor_id"])
    return id_from_payload(row, "git_anchor")


def _ranges_for(
    path: str | None,
    line_range: dict[str, Any] | None,
    *,
    side: str,
) -> list[dict[str, Any]]:
    if not path or not isinstance(line_range, dict):
        return []
    start = _int_or_none(line_range.get("start_line"))
    end = _int_or_none(line_range.get("end_line"))
    if start is None or end is None:
        return []
    return [{"path": path, "start_line": start, "end_line": end, "side": side}]


def _dedupe_ranges(ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for item in ranges:
        key = (
            item.get("path"),
            item.get("start_line"),
            item.get("end_line"),
            item.get("side"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hex(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("hex")
    if isinstance(value, str):
        return value
    return None


def _limitation_affects(code: str) -> list[str]:
    if "survival" in code or "maturation" in code:
        return ["current_state", "survival_state"]
    if "anchor" in code or "commit" in code:
        return ["landing", "evidence"]
    return ["lineage"]
