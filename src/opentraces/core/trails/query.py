"""Shared Trail Query projection over canonical TrailEvents.

This module is intentionally read-only. It rebuilds query rows from
``refs/opentraces/local/events/v1`` and does not consult advisory snapshot refs,
the attribution cache, or git notes. ``trail explain`` remains the canonical
evidence explainer; this projection is for consumer surfaces that need the same
identifiers without re-parsing TrailEvents independently.
"""

from __future__ import annotations

import copy
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contract import enrich_trail_row, projection_digest
from .event_log import EVENT_LOG_REF, read_events
from .follow import follow_patch
from .models import TrailEvent
from .slices import resource_refs_for_patch, trace_slice_for_event


def _source_event(event: TrailEvent) -> dict[str, Any]:
    out = {
        "event_id": event.event_id,
        "event_sequence": event.event_sequence,
        "event_type": event.event_type,
        "capture_method": event.capture_method,
    }
    if event.event_type == "git_anchor_search_completed":
        out["result"] = event.payload.get("result")
        out["trace_patch_id"] = event.payload.get("trace_patch_id")
    return out


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _line_count(line_range: dict[str, Any] | None) -> int:
    if not line_range:
        return 0
    try:
        start = int(line_range.get("start_line") or 0)
        end = int(line_range.get("end_line") or 0)
    except (TypeError, ValueError):
        return 0
    if start <= 0 or end <= 0 or end < start:
        return 0
    return end - start + 1


def resolve_commit_ref(repo: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _step_id(event: TrailEvent) -> str | None:
    if event.step_index is None:
        return None
    return f"step_{event.step_index}"


def _event_source_refs(
    *,
    patch_event: TrailEvent | None,
    anchor_event: TrailEvent | None = None,
) -> dict[str, Any]:
    refs: dict[str, Any] = {"event_log_ref": EVENT_LOG_REF}
    if patch_event is not None:
        refs["trace_patch_event_id"] = patch_event.event_id
        refs["trace_patch_event_sequence"] = patch_event.event_sequence
    if anchor_event is not None:
        refs["git_anchor_event_id"] = anchor_event.event_id
        refs["git_anchor_event_sequence"] = anchor_event.event_sequence
    return refs


def _copy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_trail_row(copy.deepcopy(row)) for row in rows]


@dataclass
class TrailQueryProjection:
    """Deterministic query index rebuilt from TrailEvents."""

    repo: Path
    event_log_ref: str = EVENT_LOG_REF
    events_seen: int = 0
    last_event_id: str | None = None
    projection_digest: str | None = None
    patches_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    anchors_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    anchors_by_commit: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    anchors_by_trace: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    search_events_by_patch: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def limitations(self) -> list[str]:
        if self.events_seen:
            return []
        return ["trail_event_log_unavailable"]

    def to_summary(self) -> dict[str, Any]:
        return {
            "event_log_ref": self.event_log_ref,
            "events_seen": self.events_seen,
            "last_event_id": self.last_event_id,
            "projection_digest": self.projection_digest,
            "patch_count": len(self.patches_by_id),
            "anchor_count": len(self.anchors_by_id),
            "limitations": self.limitations,
        }

    def trace_ids(self) -> list[str]:
        trace_ids = {
            row["trace_id"]
            for row in self.patches_by_id.values()
            if row.get("trace_id")
        }
        return sorted(trace_ids)

    def resolve_trace_prefix(self, prefix: str) -> str | None:
        probe = prefix[2:] if prefix.lower().startswith("t:") else prefix
        probe = probe.strip().lower()
        if not probe:
            return None
        matches = [
            trace_id
            for trace_id in self.trace_ids()
            if trace_id.lower().startswith(probe)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def patches_for_trace(
        self,
        trace_id: str,
        *,
        include_unanchored: bool = True,
    ) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.patches_by_id.values()
            if row.get("trace_id") == trace_id
            and (include_unanchored or row.get("git_anchors"))
        ]
        rows.sort(key=lambda row: row.get("source_refs", {}).get("trace_patch_event_sequence", 0))
        return _copy_rows(rows)

    def anchors_for_commit(self, commit_sha: str) -> list[dict[str, Any]]:
        rows = self.anchors_by_commit.get(commit_sha, [])
        return _copy_rows(rows)

    def anchors_for_commit_with_survival(self, commit_sha: str) -> list[dict[str, Any]]:
        return [self.with_current_survival(row) for row in self.anchors_for_commit(commit_sha)]

    def anchors_for_trace(self, trace_id: str) -> list[dict[str, Any]]:
        rows = self.anchors_by_trace.get(trace_id, [])
        return _copy_rows(rows)

    def anchors_for_trace_with_survival(self, trace_id: str) -> list[dict[str, Any]]:
        return [self.with_current_survival(row) for row in self.anchors_for_trace(trace_id)]

    def with_current_survival(self, row: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(row)
        trace_patch_id = out.get("trace_patch_id")
        if not trace_patch_id:
            return enrich_trail_row(out)
        trail = follow_patch(self.repo, trace_patch_id)
        current = trail.get("current_survival") or {}
        out["current_survival"] = current
        out["survival_state"] = current.get("survival_state") or "unknown"
        out["trail_limitations"] = trail.get("trail_limitations") or []
        return enrich_trail_row(out)

    def patches_touching_file(self, file_path: str) -> list[dict[str, Any]]:
        """Return committed patch evidence touching ``file_path``.

        Unanchored patch rows are deliberately excluded because this query is a
        committed-evidence search surface. ``patches_for_trace`` is the explicit
        trace-context query that can show unknown/unanchored rows.
        """
        rows = [
            row
            for row in self.anchors_by_id.values()
            if row.get("file_path") == file_path or row.get("path") == file_path
        ]
        rows.sort(
            key=lambda row: (
                row.get("commit_sha") or "",
                row.get("trace_id") or "",
                row.get("trace_patch_id") or "",
            )
        )
        return _copy_rows(rows)

    def reverted_patch_trails(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for patch_id, patch in sorted(self.patches_by_id.items()):
            if not patch.get("git_anchors"):
                continue
            trail = follow_patch(self.repo, patch_id)
            current = trail.get("current_survival") or {}
            if current.get("survival_state") != "reverted":
                continue
            row = copy.deepcopy(patch)
            row["relation"] = "patch_trail_observed"
            row["current_survival"] = current
            row["trail_limitations"] = trail.get("trail_limitations") or []
            row["observations"] = trail.get("observations") or []
            rows.append(enrich_trail_row(row))
        return rows


def build_trail_query_projection(repo: Path) -> TrailQueryProjection:
    repo = repo.resolve()
    events = read_events(repo)
    projection = TrailQueryProjection(
        repo=repo,
        events_seen=len(events),
        last_event_id=events[-1].event_id if events else None,
        projection_digest=projection_digest(events),
    )

    patch_pairs: dict[str, tuple[dict[str, Any], TrailEvent]] = {}
    anchor_pairs: list[tuple[dict[str, Any], TrailEvent]] = []
    search_pairs_by_patch: dict[str, list[tuple[dict[str, Any], TrailEvent]]] = {}

    for event in events:
        payload = event.payload
        if event.event_type == "trace_patch_created":
            patch_id = payload.get("trace_patch_id")
            if patch_id:
                patch_pairs[patch_id] = (payload, event)
        elif event.event_type == "git_anchor_created":
            anchor_pairs.append((payload, event))
        elif event.event_type == "git_anchor_search_completed":
            patch_id = payload.get("trace_patch_id")
            if patch_id:
                search_pairs_by_patch.setdefault(patch_id, []).append((payload, event))

    anchors_by_patch: dict[str, list[tuple[dict[str, Any], TrailEvent]]] = {}
    for anchor, event in anchor_pairs:
        patch_id = anchor.get("trace_patch_id")
        if patch_id:
            anchors_by_patch.setdefault(patch_id, []).append((anchor, event))
    for pairs in anchors_by_patch.values():
        pairs.sort(key=lambda pair: pair[1].event_sequence)
    for pairs in search_pairs_by_patch.values():
        pairs.sort(key=lambda pair: pair[1].event_sequence)

    for patch_id, pairs in search_pairs_by_patch.items():
        projection.search_events_by_patch[patch_id] = [
            {
                "trace_patch_id": patch_id,
                "search_head": search.get("search_head"),
                "search_head_sha": (search.get("search_head") or {}).get("hex"),
                "algorithms_attempted": search.get("algorithms_attempted") or [],
                "result": search.get("result"),
                "created_anchor_ids": search.get("created_anchor_ids") or [],
                "source_event": _source_event(event),
            }
            for search, event in pairs
        ]

    for patch_id, (patch, patch_event) in patch_pairs.items():
        anchor_pairs_for_patch = anchors_by_patch.get(patch_id, [])
        latest_anchor_id = (
            anchor_pairs_for_patch[-1][0].get("git_anchor_id")
            if anchor_pairs_for_patch
            else None
        )
        trace_slice = trace_slice_for_event(
            patch_event,
            trace_patch_id=patch_id,
            git_anchor_id=latest_anchor_id,
            relation="contains_trace_patch",
        )
        containing_segment_id = (
            trace_slice.get("containing_segment_id") if trace_slice else None
        )
        affected_range = patch.get("affected_range") or {}
        limitations = list(patch.get("limitations") or [])
        if not anchor_pairs_for_patch:
            limitations.append("git_anchor_unknown")
        search_rows = projection.search_events_by_patch.get(patch_id, [])
        source_events = [_source_event(patch_event)] + [
            row["source_event"] for row in search_rows
        ]
        projection.patches_by_id[patch_id] = {
            "trace_id": patch_event.trace_id,
            "step_id": _step_id(patch_event),
            "step_index": patch_event.step_index,
            "generation_index": patch_event.generation_index,
            "trace_patch_id": patch_id,
            "trace_patch_ref": patch.get("trace_patch_ref"),
            "patch_status": "patched",
            "relation": "anchored_in_git" if anchor_pairs_for_patch else "unknown",
            "file_path": patch.get("file_path"),
            "path": patch.get("file_path"),
            "affected_range": affected_range,
            "line_count": _line_count(affected_range),
            "containing_segment_id": containing_segment_id,
            "trace_slice": trace_slice,
            "git_anchor_id": latest_anchor_id,
            "git_anchor_ref": None,
            "git_anchors": [],
            "anchor_searches": search_rows,
            "evidence_tier": "unknown",
            "evidence_firmness": "unknown",
            "firmness": "unknown",
            "limitations": _dedupe(limitations),
            "resource_refs": resource_refs_for_patch(
                trace_id=patch_event.trace_id,
                trace_patch_id=patch_id,
                file_path=patch.get("file_path"),
                start_line=affected_range.get("start_line"),
                git_anchor_id=latest_anchor_id,
            ),
            "source_refs": _event_source_refs(patch_event=patch_event),
            "source_events": source_events,
            "event_log_ref": EVENT_LOG_REF,
        }

    for anchor, anchor_event in sorted(anchor_pairs, key=lambda pair: pair[1].event_sequence):
        anchor_id = anchor.get("git_anchor_id")
        patch_id = anchor.get("trace_patch_id")
        if not anchor_id or not patch_id:
            continue
        patch_pair = patch_pairs.get(patch_id)
        patch, patch_event = patch_pair if patch_pair else ({}, anchor_event)
        trace_id = patch_event.trace_id or anchor_event.trace_id
        step_index = patch_event.step_index
        anchor_range = anchor.get("range") or {}
        file_path = anchor.get("path") or patch.get("file_path")
        trace_slice = trace_slice_for_event(
            patch_event,
            trace_patch_id=patch_id,
            git_anchor_id=anchor_id,
            relation="contains_trace_patch",
        )
        containing_segment_id = (
            trace_slice.get("containing_segment_id") if trace_slice else None
        )
        commit_id = anchor.get("commit_id") or {}
        commit_sha = commit_id.get("hex")
        evidence_tier = anchor.get("evidence_tier") or "unknown"
        evidence_firmness = anchor.get("evidence_firmness") or "unknown"
        limitations = _dedupe(
            list(patch.get("limitations") or []) + list(anchor.get("limitations") or [])
        )
        refs = resource_refs_for_patch(
            trace_id=trace_id,
            trace_patch_id=patch_id,
            file_path=file_path,
            start_line=anchor_range.get("start_line"),
            git_anchor_id=anchor_id,
        )
        line_origin = None
        if file_path and anchor_range.get("start_line") is not None:
            line_origin = {
                "path": file_path,
                "line": anchor_range.get("start_line"),
                "resource_ref": refs.get("file_line_origin"),
            }
        row = {
            "trace_id": trace_id,
            "step_id": f"step_{step_index}" if step_index is not None else None,
            "step_index": step_index,
            "generation_index": patch_event.generation_index,
            "trace_patch_id": patch_id,
            "patch_status": "patched",
            "relation": anchor.get("relation") or "anchored_in_git",
            "git_anchor_id": anchor_id,
            "git_anchor_ref": anchor.get("git_anchor_ref"),
            "commit_id": commit_id,
            "commit_sha": commit_sha,
            "file_path": file_path,
            "path": file_path,
            "affected_range": patch.get("affected_range") or anchor_range,
            "range": anchor_range,
            "line_count": _line_count(anchor_range),
            "line_origin": line_origin,
            "containing_segment_id": containing_segment_id,
            "trace_slice": trace_slice,
            "evidence_tier": evidence_tier,
            "evidence_firmness": evidence_firmness,
            "firmness": evidence_firmness,
            "limitations": limitations,
            "resource_refs": refs,
            "source_refs": _event_source_refs(
                patch_event=patch_event,
                anchor_event=anchor_event,
            ),
            "source_events": [_source_event(patch_event), _source_event(anchor_event)],
            "event_log_ref": EVENT_LOG_REF,
        }
        projection.anchors_by_id[anchor_id] = row
        if commit_sha:
            projection.anchors_by_commit.setdefault(commit_sha, []).append(row)
        if trace_id:
            projection.anchors_by_trace.setdefault(trace_id, []).append(row)
        if patch_id in projection.patches_by_id:
            projection.patches_by_id[patch_id]["git_anchors"].append(copy.deepcopy(row))

    for patch_id, patch_row in projection.patches_by_id.items():
        anchors = patch_row.get("git_anchors") or []
        if anchors:
            latest = anchors[-1]
            patch_row["relation"] = latest.get("relation") or "anchored_in_git"
            patch_row["git_anchor_id"] = latest.get("git_anchor_id")
            patch_row["git_anchor_ref"] = latest.get("git_anchor_ref")
            patch_row["commit_id"] = latest.get("commit_id")
            patch_row["commit_sha"] = latest.get("commit_sha")
            patch_row["evidence_tier"] = latest.get("evidence_tier") or "unknown"
            patch_row["evidence_firmness"] = latest.get("evidence_firmness") or "unknown"
            patch_row["firmness"] = patch_row["evidence_firmness"]
            patch_row["range"] = latest.get("range")
            patch_row["line_origin"] = latest.get("line_origin")
            patch_row["limitations"] = _dedupe(
                list(patch_row.get("limitations") or [])
                + list(latest.get("limitations") or [])
            )
            patch_row["resource_refs"] = latest.get("resource_refs") or patch_row.get(
                "resource_refs",
                {},
            )
            patch_row["source_refs"]["git_anchor_event_id"] = latest["source_refs"].get(
                "git_anchor_event_id"
            )
            patch_row["source_refs"]["git_anchor_event_sequence"] = latest[
                "source_refs"
            ].get("git_anchor_event_sequence")
            patch_row["source_events"] = (
                [patch_row["source_events"][0]]
                + [
                    anchor["source_events"][-1]
                    for anchor in anchors
                    if anchor.get("source_events")
                ]
            )
        projection.patches_by_id[patch_id] = patch_row

    for rows in projection.anchors_by_commit.values():
        rows.sort(
            key=lambda row: (
                row.get("trace_id") or "",
                row.get("step_index") or -1,
                row.get("trace_patch_id") or "",
                row.get("git_anchor_id") or "",
            )
        )
    for rows in projection.anchors_by_trace.values():
        rows.sort(
            key=lambda row: (
                row.get("commit_sha") or "",
                row.get("step_index") or -1,
                row.get("trace_patch_id") or "",
            )
        )

    return projection


def trail_query_summary(repo: Path) -> dict[str, Any]:
    """Return a stable summary for tests and diagnostics."""
    return build_trail_query_projection(repo).to_summary()
