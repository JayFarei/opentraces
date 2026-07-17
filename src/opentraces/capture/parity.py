"""Raw-material placement parity checks for portable Capture.

Normalization lives here in the verification suite, never in the capture path
under test.  The report therefore compares bytes read from each placement's
stored trace and companions while making only explicit identity/path noise
comparable.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .portable import CaptureResult


@dataclass(frozen=True)
class PlacementParityReport:
    matches: bool
    view_completeness_match: bool
    source_completeness_match: bool
    canonical_trace_match: bool
    context_companion_match: bool
    trail_companion_match: bool
    security_match: bool
    query_behavior_match: bool
    query_behavior: dict[str, Any]
    path_normalization_applied: bool
    persistent_digest: str
    leased_digest: str
    differences: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    span_match: bool | None = None
    display_label_match: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matches": self.matches,
            "view_completeness_match": self.view_completeness_match,
            "source_completeness_match": self.source_completeness_match,
            "canonical_trace_match": self.canonical_trace_match,
            "context_companion_match": self.context_companion_match,
            "trail_companion_match": self.trail_companion_match,
            "security_match": self.security_match,
            "query_behavior_match": self.query_behavior_match,
            "query_behavior": self.query_behavior,
            "path_normalization_applied": self.path_normalization_applied,
            "persistent_digest": self.persistent_digest,
            "leased_digest": self.leased_digest,
            "differences": list(self.differences),
            "limitations": list(self.limitations),
            "span_match": self.span_match,
            "display_label_match": self.display_label_match,
        }


@dataclass(frozen=True)
class PersistentCompatibilityReport:
    matches: bool
    serialized_artifact_bytes_match: bool
    semantic_material_match: bool
    query_behavior_match: bool
    query_behavior: dict[str, Any]
    legacy_artifact_digest: str
    capture_artifact_digest: str
    legacy_semantic_digest: str
    capture_semantic_digest: str
    differences: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "matches": self.matches,
            "serialized_artifact_bytes_match": self.serialized_artifact_bytes_match,
            "semantic_material_match": self.semantic_material_match,
            "query_behavior_match": self.query_behavior_match,
            "query_behavior": self.query_behavior,
            "legacy_artifact_digest": self.legacy_artifact_digest,
            "capture_artifact_digest": self.capture_artifact_digest,
            "legacy_semantic_digest": self.legacy_semantic_digest,
            "capture_semantic_digest": self.capture_semantic_digest,
            "differences": list(self.differences),
            "limitations": list(self.limitations),
        }


def compare_placements(
    persistent: CaptureResult,
    leased: CaptureResult,
    *,
    persistent_spans: list[dict[str, Any]] | None = None,
    leased_spans: list[dict[str, Any]] | None = None,
    persistent_labeler_provenance: dict[str, Any] | None = None,
    leased_labeler_provenance: dict[str, Any] | None = None,
    persistent_roots: tuple[Path, ...] = (),
    leased_roots: tuple[Path, ...] = (),
) -> PlacementParityReport:
    """Compare stored raw materials, normalizing only declared placement noise."""
    if persistent.placement != "persistent" or leased.placement != "leased":
        raise ValueError("compare_placements expects persistent then leased results")
    persistent_path = _trace_path(persistent)
    leased_path = _trace_path(leased)
    view_match = {view.name: view.completeness for view in persistent.views} == {
        view.name: view.completeness for view in leased.views
    }
    source_match = {source.name: source.completeness for source in persistent.sources} == {
        source.name: source.completeness for source in leased.sources
    }
    persistent_trace = _read_json(persistent_path)
    leased_trace = _read_json(leased_path)
    persistent_replacements = {
        str(persistent_path.parents[4]): "<bucket-root>",
        persistent.trace_refs[0]: "<trace-id>",
    }
    leased_replacements = {
        str(leased_path.parents[4]): "<bucket-root>",
        leased.trace_refs[0]: "<trace-id>",
    }
    for root in persistent_roots:
        resolved_root = Path(root).resolve()
        for alias in _path_aliases(Path(root)):
            persistent_replacements[alias] = "<workspace>"
        persistent_replacements[resolved_root.name] = "<workspace-name>"
    for root in leased_roots:
        resolved_root = Path(root).resolve()
        for alias in _path_aliases(Path(root)):
            leased_replacements[alias] = "<workspace>"
        leased_replacements[resolved_root.name] = "<workspace-name>"

    persistent_norm = _normalize(persistent_trace, persistent_replacements, domain="trace")
    leased_norm = _normalize(leased_trace, leased_replacements, domain="trace")
    trace_match = persistent_norm == leased_norm
    persistent_security = _normalize(
        {
            "capture_result": persistent.security,
            "trace_record": persistent_trace.get("security") or {},
        },
        persistent_replacements,
        domain="semantic",
    )
    leased_security = _normalize(
        {
            "capture_result": leased.security,
            "trace_record": leased_trace.get("security") or {},
        },
        leased_replacements,
        domain="semantic",
    )
    security_match = persistent_security == leased_security

    persistent_context_raw = _read_jsonl_gz(persistent_path.with_name("context.jsonl.gz"))
    leased_context_raw = _read_jsonl_gz(leased_path.with_name("context.jsonl.gz"))
    _add_context_node_replacements(persistent_context_raw, persistent_replacements)
    _add_context_node_replacements(leased_context_raw, leased_replacements)
    persistent_context = _sanitize_companion_projection(
        _normalize(
            _resolve_content_references(
                persistent_context_raw,
                persistent_path,
                persistent_replacements,
            ),
            persistent_replacements,
            domain="companion",
        )
    )
    leased_context = _sanitize_companion_projection(
        _normalize(
            _resolve_content_references(
                leased_context_raw,
                leased_path,
                leased_replacements,
            ),
            leased_replacements,
            domain="companion",
        )
    )
    persistent_trail_raw = _read_jsonl_gz(persistent_path.with_name("trail.jsonl.gz"))
    leased_trail_raw = _read_jsonl_gz(leased_path.with_name("trail.jsonl.gz"))
    persistent_trail = _sanitize_companion_projection(
        _normalize(
            persistent_trail_raw,
            persistent_replacements,
            domain="companion",
        )
    )
    leased_trail = _sanitize_companion_projection(
        _normalize(
            leased_trail_raw,
            leased_replacements,
            domain="companion",
        )
    )
    context_proven = _canonical_companion_evidence(
        persistent_context_raw,
        trace_id=str(persistent_trace.get("trace_id") or ""),
        family="context",
    ) and _canonical_companion_evidence(
        leased_context_raw,
        trace_id=str(leased_trace.get("trace_id") or ""),
        family="context",
    )
    trail_proven = _canonical_companion_evidence(
        persistent_trail_raw,
        trace_id=str(persistent_trace.get("trace_id") or ""),
        family="trail",
    ) and _canonical_companion_evidence(
        leased_trail_raw,
        trace_id=str(leased_trace.get("trace_id") or ""),
        family="trail",
    )
    context_match = context_proven and persistent_context == leased_context
    trail_match = trail_proven and persistent_trail == leased_trail

    persistent_query = _query_behavior(
        trace=persistent_trace,
        trace_path=persistent_path,
        roots=persistent_roots,
        replacements=persistent_replacements,
    )
    leased_query = _query_behavior(
        trace=leased_trace,
        trace_path=leased_path,
        roots=leased_roots,
        replacements=leased_replacements,
    )
    query_proven = persistent_query["proven"] and leased_query["proven"]
    query_match = query_proven and persistent_query["result"] == leased_query["result"]

    differences: list[str] = []
    limitations: list[str] = []
    if not view_match:
        differences.append("view_completeness")
    if not source_match:
        differences.append("source_completeness")
    if not trace_match:
        differences.append("canonical_trace")
    if not context_match:
        differences.append("context_companion")
    if not trail_match:
        differences.append("trail_companion")
    if not security_match:
        differences.append("security")
    if not query_match:
        differences.append("query_behavior")
    if not query_proven:
        differences.append("unproven_query_behavior")
        limitations.append(
            "query behavior parity is unproven because one or both placements "
            "could not execute Trace, Ctx, and Trail reads against canonical state"
        )
    if not context_proven:
        differences.append("unproven_context_companion")
        limitations.append(
            "context companion parity is unproven because one or both placements "
            "did not produce canonical context evidence"
        )
    if not trail_proven:
        differences.append("unproven_trail_companion")
        limitations.append(
            "trail companion parity is unproven because one or both placements "
            "did not produce canonical trail evidence"
        )
    companions_required = persistent.source("bucket").required and leased.source("bucket").required
    if companions_required:
        if not persistent_context_raw or not leased_context_raw:
            differences.append("empty_context_companion")
        if not persistent_trail_raw or not leased_trail_raw:
            differences.append("empty_trail_companion")

    persistent_sources = {source.name: source for source in persistent.sources}
    leased_sources = {source.name: source for source in leased.sources}
    for source_name in sorted(set(persistent_sources) & set(leased_sources)):
        if persistent_sources[source_name].limitations != leased_sources[source_name].limitations:
            limitations.append(f"placement-specific source limitations differ: {source_name}")
    span_match: bool | None = None
    display_label_match: bool | None = None
    if persistent_spans is not None or leased_spans is not None:
        left_spans = persistent_spans or []
        right_spans = leased_spans or []
        span_match = _span_coordinates(left_spans) == _span_coordinates(right_spans)
        if not span_match:
            differences.append("slicer_spans")
        if _matching_pinned_provenance(persistent_labeler_provenance, leased_labeler_provenance):
            display_label_match = _display_labels(left_spans) == _display_labels(right_spans)
            if not display_label_match:
                differences.append("display_labels")
        elif not _pinned_provenance(persistent_labeler_provenance) or not _pinned_provenance(
            leased_labeler_provenance
        ):
            limitations.append(
                "display labels were not compared because labeler provenance is not pinned"
            )
        else:
            limitations.insert(
                0, "display labels were not compared because labeler provenance differs"
            )

    persistent_digest = _digest(
        {
            "trace": persistent_norm,
            "context": persistent_context,
            "trail": persistent_trail,
            "security": persistent_security,
            "query_behavior": persistent_query["result"],
        }
    )
    leased_digest = _digest(
        {
            "trace": leased_norm,
            "context": leased_context,
            "trail": leased_trail,
            "security": leased_security,
            "query_behavior": leased_query["result"],
        }
    )
    matches = not differences
    return PlacementParityReport(
        matches=matches,
        view_completeness_match=view_match,
        source_completeness_match=source_match,
        canonical_trace_match=trace_match,
        context_companion_match=context_match,
        trail_companion_match=trail_match,
        security_match=security_match,
        query_behavior_match=query_match,
        query_behavior={
            "persistent": persistent_query,
            "leased": leased_query,
        },
        path_normalization_applied=True,
        persistent_digest=persistent_digest,
        leased_digest=leased_digest,
        differences=tuple(differences),
        limitations=tuple(limitations),
        span_match=span_match,
        display_label_match=display_label_match,
    )


def compare_persistent_compatibility(
    *,
    legacy_trace_path: Path,
    legacy_project: Path,
    captured: CaptureResult,
    legacy_roots: tuple[Path, ...] = (),
    captured_roots: tuple[Path, ...] = (),
) -> PersistentCompatibilityReport:
    """Compare Capture persistent output with the direct legacy canonical chain."""
    if captured.placement != "persistent":
        raise ValueError("persistent compatibility requires a persistent CaptureResult")
    legacy_trace_path = Path(legacy_trace_path)
    captured_trace_path = _trace_path(captured)
    legacy_trace = _read_json(legacy_trace_path)
    captured_trace = _read_json(captured_trace_path)
    legacy_replacements = _compatibility_replacements(legacy_trace_path, legacy_trace, legacy_roots)
    captured_replacements = _compatibility_replacements(
        captured_trace_path, captured_trace, captured_roots
    )
    legacy_material = _persistent_material(legacy_trace_path, legacy_trace, legacy_replacements)
    captured_material = _persistent_material(
        captured_trace_path, captured_trace, captured_replacements
    )
    legacy_artifacts = _serialized_artifact_bytes(legacy_trace_path)
    captured_artifacts = _serialized_artifact_bytes(captured_trace_path)
    artifact_bytes_match = legacy_artifacts == captured_artifacts
    semantic_material_match = legacy_material == captured_material
    legacy_query = _query_behavior(
        trace=legacy_trace,
        trace_path=legacy_trace_path,
        roots=(legacy_project, *legacy_roots),
        replacements=legacy_replacements,
    )
    captured_query = _query_behavior(
        trace=captured_trace,
        trace_path=captured_trace_path,
        roots=captured_roots,
        replacements=captured_replacements,
    )
    query_match = (
        legacy_query["proven"]
        and captured_query["proven"]
        and legacy_query["result"] == captured_query["result"]
    )
    differences = tuple(
        name
        for name, matches in (
            ("serialized_artifact_bytes", artifact_bytes_match),
            ("semantic_material", semantic_material_match),
            ("query_behavior", query_match),
        )
        if not matches
    )
    limitations: tuple[str, ...] = ()
    if not legacy_query["proven"] or not captured_query["proven"]:
        limitations = (
            "persistent query compatibility is unproven because the supplied source "
            "material did not materialize substantive Trace, Ctx, and Trail reads",
        )
    return PersistentCompatibilityReport(
        matches=not differences,
        serialized_artifact_bytes_match=artifact_bytes_match,
        semantic_material_match=semantic_material_match,
        query_behavior_match=query_match,
        query_behavior={"legacy": legacy_query, "capture": captured_query},
        legacy_artifact_digest=_artifact_digest(legacy_artifacts),
        capture_artifact_digest=_artifact_digest(captured_artifacts),
        legacy_semantic_digest=_digest(legacy_material),
        capture_semantic_digest=_digest(captured_material),
        differences=differences,
        limitations=limitations,
    )


def write_parity_report(report: PlacementParityReport, path: Path) -> Path:
    """Persist the acceptance report atomically for review evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def _trace_path(result: CaptureResult) -> Path:
    if not result.trace_refs:
        raise ValueError("capture result has no trace reference")
    path = result.source("bucket").details.get("trace_path")
    if not path:
        raise ValueError("bucket source did not record trace_path")
    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _query_behavior(
    *,
    trace: dict[str, Any],
    trace_path: Path,
    roots: tuple[Path, ...],
    replacements: dict[str, str],
) -> dict[str, Any]:
    """Execute the public/core Trace, Ctx, and Trail query projections."""
    from opentraces_schema import TraceRecord

    from ..core.context_tree.query import build_context_tree_projection_for_trace
    from ..core.trace_map import build_trace_map
    from ..core.trails.event_log import EVENT_LOG_REF
    from ..core.trails.query import build_trail_query_projection_for_trace

    trace_id = str(trace.get("trace_id") or "")
    project = _project_root(roots)
    errors: list[str] = []
    try:
        # Normalize declared placement roots before Trace Map builds previews;
        # normalizing after its bounded truncation would preserve path-length
        # differences as false behavioral drift.
        record = TraceRecord.model_validate(_normalize_query_input(trace, replacements))
        trace_map = build_trace_map(record)
        node_indexes = {node.node_id: index for index, node in enumerate(trace_map.nodes)}
        trace_result: Any = {
            "nodes": [
                {
                    key: value
                    for key, value in node.model_dump(mode="json").items()
                    if key
                    in {
                        "action_type",
                        "step_index",
                        "tool_name",
                        "files_read",
                        "files_modified",
                        "text_preview",
                        "metadata",
                    }
                }
                for node in trace_map.nodes
            ],
            "edges": [
                {
                    "source": node_indexes.get(edge.source_node_id),
                    "target": node_indexes.get(edge.target_node_id),
                    "relation": edge.edge_type,
                }
                for edge in trace_map.edges
            ],
            "limitations": list(trace_map.limitations),
        }
    except Exception as exc:  # noqa: BLE001 - verification must record failure
        trace_result = {"error": type(exc).__name__}
        errors.append("trace")

    if project is None:
        ctx_result: Any = {"error": "project_root_unavailable"}
        trail_result: Any = {"error": "project_root_unavailable"}
        errors.extend(("ctx", "trail"))
        event_ref_present = False
    else:
        ref = subprocess.run(
            ["git", "rev-parse", "--verify", EVENT_LOG_REF],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
        )
        event_ref_present = ref.returncode == 0
        try:
            ctx = build_context_tree_projection_for_trace(project, trace_id)
            ctx_result = {
                "nodes": [
                    _selected_fields(
                        ctx.nodes_by_id[node_id].model_dump(mode="json"),
                        {
                            "step_index",
                            "transcript_offset",
                            "branch_type",
                            "capture_completeness",
                            "capture_method",
                        },
                    )
                    for node_id in ctx.nodes_by_trace.get(trace_id, [])
                    if node_id in ctx.nodes_by_id
                ],
                "layers": [
                    _selected_fields(
                        layer.model_dump(mode="json"),
                        {
                            "layer_type",
                            "content",
                            "capture_completeness",
                            "capture_method",
                        },
                    )
                    for _, layer in sorted(ctx.layers_by_id.items())
                ],
                "active_path": [
                    _selected_fields(
                        node.model_dump(mode="json"),
                        {"step_index", "transcript_offset", "branch_type"},
                    )
                    for node in ctx.active_path(trace_id)
                ],
                "limitations": ctx.capture_limitations_by_trace.get(trace_id, []),
            }
        except Exception as exc:  # noqa: BLE001 - verification must record failure
            ctx_result = {"error": type(exc).__name__}
            errors.append("ctx")
        try:
            trail = build_trail_query_projection_for_trace(project, trace_id)
            trail_result = {
                "summary": _selected_fields(
                    trail.to_summary(),
                    {"patch_count", "anchor_count", "limitations"},
                ),
                "patches": [
                    _selected_fields(
                        row,
                        {
                            "step_index",
                            "generation_index",
                            "patch_status",
                            "relation",
                            "file_path",
                            "affected_range",
                            "line_count",
                            "evidence_tier",
                            "evidence_firmness",
                            "limitations",
                        },
                    )
                    for row in trail.patches_for_trace(trace_id)
                ],
                "anchors": [
                    _selected_fields(
                        row,
                        {
                            "step_index",
                            "generation_index",
                            "relation",
                            "file_path",
                            "range",
                            "line_count",
                            "evidence_tier",
                            "evidence_firmness",
                            "limitations",
                        },
                    )
                    for row in trail.anchors_for_trace(trace_id)
                ],
            }
        except Exception as exc:  # noqa: BLE001 - verification must record failure
            trail_result = {"error": type(exc).__name__}
            errors.append("trail")

    normalized = _normalize(
        {
            "trace": trace_result,
            "ctx": ctx_result,
            "trail": trail_result,
        },
        replacements,
        domain="query",
    )
    trace_nodes = trace_result.get("nodes") if isinstance(trace_result, dict) else None
    ctx_nodes = ctx_result.get("nodes") if isinstance(ctx_result, dict) else None
    trail_patches = trail_result.get("patches") if isinstance(trail_result, dict) else None
    trail_anchors = trail_result.get("anchors") if isinstance(trail_result, dict) else None
    proof = {
        "trace_id_present": bool(trace_id),
        "trace_artifact_present": trace_path.is_file(),
        "event_ref_present": event_ref_present,
        "trace_substantive": isinstance(trace_nodes, list) and bool(trace_nodes),
        "ctx_substantive": isinstance(ctx_nodes, list) and bool(ctx_nodes),
        "trail_substantive": (
            isinstance(trail_patches, list)
            and isinstance(trail_anchors, list)
            and bool(trail_patches or trail_anchors)
        ),
    }
    proven = not errors and all(proof.values())
    return {
        "proven": proven,
        "proof": proof,
        "errors": errors,
        "result": normalized,
    }


def _project_root(roots: tuple[Path, ...]) -> Path | None:
    for root in roots:
        candidate = Path(root).resolve()
        if (candidate / ".git").exists():
            return candidate
    return None


def _selected_fields(value: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(keys) if key in value}


_QUERY_INPUT_PATH_FIELDS = frozenset({"file_path", "path", "cwd", "workdir", "working_directory"})
_QUERY_RESULT_PATH_FIELDS = frozenset(
    {
        "file_path",
        "files_read",
        "files_modified",
        "cwd",
        "workdir",
        "working_directory",
    }
)


def _normalize_query_input(
    value: Any,
    replacements: dict[str, str],
    *,
    path: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_query_input(child, replacements, path=(*path, key))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_normalize_query_input(child, replacements, path=(*path, "[]")) for child in value]
    if isinstance(value, str) and _query_input_path_slot(path):
        normalized = value
        for raw, replacement in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if Path(raw).is_absolute():
                # Trace Map resolves typed paths. Keep the normalized value
                # absolute so ``Path.resolve`` cannot re-anchor it under each
                # placement's cwd and manufacture behavioral drift.
                typed_replacement = (
                    f"/{replacement}" if replacement == "<workspace>" else replacement
                )
                normalized = normalized.replace(raw, typed_replacement)
        return normalized
    return value


def _query_input_path_slot(path: tuple[str, ...]) -> bool:
    """Normalize only typed tool-input path fields, never semantic prose."""

    return (
        bool(path)
        and path[-1] in _QUERY_INPUT_PATH_FIELDS
        and "tool_calls" in path
        and "input" in path
    )


def _query_result_path_slot(path: tuple[str, ...]) -> bool:
    """Recognize typed path fields in the Trace/Trail query projections."""

    leaf = next((part for part in reversed(path) if part != "[]"), "")
    return leaf in _QUERY_RESULT_PATH_FIELDS


def _compatibility_replacements(
    trace_path: Path,
    trace: dict[str, Any],
    roots: tuple[Path, ...],
) -> dict[str, str]:
    replacements = {
        str(trace_path.parents[4]): "<bucket-root>",
        str(trace.get("trace_id") or ""): "<trace-id>",
    }
    for root in roots:
        resolved = Path(root).resolve()
        for alias in _path_aliases(Path(root)):
            replacements[alias] = "<workspace>"
        replacements[resolved.name] = "<workspace-name>"
    return replacements


def _persistent_material(
    trace_path: Path,
    trace: dict[str, Any],
    replacements: dict[str, str],
) -> dict[str, Any]:
    context_raw = _read_jsonl_gz(trace_path.with_name("context.jsonl.gz"))
    trail_raw = _read_jsonl_gz(trace_path.with_name("trail.jsonl.gz"))
    _add_context_node_replacements(context_raw, replacements)
    return {
        "trace": _normalize(trace, replacements, domain="trace"),
        "context": _sanitize_companion_projection(
            _normalize(
                _resolve_content_references(context_raw, trace_path, replacements),
                replacements,
                domain="companion",
            )
        ),
        "trail": _sanitize_companion_projection(
            _normalize(trail_raw, replacements, domain="companion")
        ),
        "sources": _normalize(
            _read_jsonl_gz(trace_path.with_name("sources.jsonl.gz")),
            replacements,
            domain="companion",
        ),
    }


def _serialized_artifact_bytes(trace_path: Path) -> dict[str, bytes]:
    """Read the literal serialized artifacts promised by persistent #268."""

    names = ("trace.json", "context.jsonl.gz", "trail.jsonl.gz", "sources.jsonl.gz")
    return {name: trace_path.with_name(name).read_bytes() for name in names}


def _artifact_digest(artifacts: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, material in sorted(artifacts.items()):
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(material).to_bytes(8, "big"))
        digest.update(material)
    return f"sha256:{digest.hexdigest()}"


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object at {path}")
    return data


def _canonical_companion_evidence(
    rows: list[Any],
    *,
    trace_id: str,
    family: str,
) -> bool:
    """Require canonical, trace-scoped TrailEvent evidence for one family."""

    from ..core.context_tree.contract import CONTEXT_EVENT_TYPES
    from ..core.trails.models import (
        TrailEvent,
        expected_event_id,
        payload_content_hash,
    )
    from ..core.trails.search_records import summary_search_touches_trace

    if family not in {"context", "trail"}:
        raise ValueError(f"unknown companion family: {family}")
    if not rows or not trace_id:
        return False
    canonical_fields = set(TrailEvent.model_fields)
    for row in rows:
        if not isinstance(row, dict) or set(row) != canonical_fields:
            return False
        try:
            event = TrailEvent.model_validate(row)
        except (TypeError, ValueError):
            return False
        scoped_to_trace = event.trace_id == trace_id or (
            family == "trail" and summary_search_touches_trace(event, trace_id)
        )
        if not scoped_to_trace:
            return False
        if event.content_hash != payload_content_hash(event.payload):
            return False
        if event.event_id != expected_event_id(event):
            return False
        is_context = event.event_type in CONTEXT_EVENT_TYPES
        if (family == "context") != is_context:
            return False
    return True


def _path_aliases(path: Path) -> set[str]:
    """Return declared/resolved roots plus macOS's public symlink spelling."""

    aliases = {str(path), str(path.resolve())}
    for value in tuple(aliases):
        if value.startswith("/private/"):
            aliases.add(value.removeprefix("/private"))
    return aliases


def _read_jsonl_gz(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    rows: list[Any] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        rows.append({"__malformed_json__": line.rstrip("\n")})
    except (OSError, EOFError, UnicodeError) as exc:
        rows.append({"__malformed_companion__": type(exc).__name__})
    return rows


def _sanitize_companion_projection(rows: list[Any]) -> list[Any]:
    """Sanitize comparison material without rewriting canonical bucket bytes."""

    from ..security.pipeline import sanitize_companion_dict

    projected: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            projected.append({"__invalid_companion_row__": row})
            continue
        safe = dict(row)
        raw_payload = safe.get("payload")
        if not isinstance(raw_payload, dict):
            projected.append(safe)
            continue
        payload, _manifest = sanitize_companion_dict(dict(raw_payload))
        safe["payload"] = payload
        projected.append(safe)
    return projected


def _add_context_node_replacements(
    rows: list[Any],
    replacements: dict[str, str],
) -> None:
    """Map content-addressed node joins by observed order, retaining topology."""

    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        node_id = payload.get("node_id")
        if isinstance(node_id, str) and node_id not in replacements:
            replacements[node_id] = (
                f"<context-node:{sum(1 for value in replacements.values() if value.startswith('<context-node:'))}>"
            )


def _resolve_content_references(
    value: Any,
    trace_path: Path,
    replacements: dict[str, str],
    *,
    path: tuple[str, ...] = (),
) -> Any:
    """Replace message content hashes with normalized referenced identity.

    Trail envelope hashes remain transport identity and are normalized away
    later.  A hash inside a ``messages`` manifest is semantic, so parity must
    dereference its raw blob, normalize placement paths in the content, and
    compare the resulting digest. Missing/corrupt references remain visible as
    unresolved identity rather than silently disappearing.
    """
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}
        for key, child in value.items():
            child_path = (*path, key)
            if key == "content_hash" and isinstance(child, str) and "messages" in path:
                content = _read_referenced_content(trace_path, child)
                if content is None:
                    resolved["unresolved_content_hash"] = child
                else:
                    normalized_content = _normalize(
                        content,
                        replacements,
                        domain="referenced_content",
                    )
                    resolved["referenced_content_digest"] = _digest(normalized_content)
                continue
            resolved[key] = _resolve_content_references(
                child,
                trace_path,
                replacements,
                path=child_path,
            )
        return resolved
    if isinstance(value, list):
        return [
            _resolve_content_references(
                child,
                trace_path,
                replacements,
                path=(*path, "[]"),
            )
            for child in value
        ]
    return value


def _read_referenced_content(trace_path: Path, content_hash: str) -> Any | None:
    prefix = "sha256:"
    if not content_hash.startswith(prefix):
        return None
    digest = content_hash.removeprefix(prefix)
    if len(digest) != 64:
        return None
    project_slug = trace_path.parent.parent.name
    blob = (
        trace_path.parents[4]
        / "blobs"
        / "v1"
        / project_slug
        / "raw"
        / digest[:2]
        / f"{digest}.json.gz"
    )
    try:
        with gzip.open(blob, "rb") as handle:
            material = handle.read()
        if hashlib.sha256(material).hexdigest() != digest:
            return None
        return json.loads(material)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


_COMPANION_ENVELOPE_IDENTITY_KEYS = frozenset(
    {
        "trace_id",
        "content_hash",
        "event_id",
        "event_sequence",
        "event_time",
        "batch_id",
        "projected_at",
        "previous_event_id",
        "written_at",
    }
)


def _normalize(
    value: Any,
    replacements: dict[str, str],
    *,
    domain: str = "semantic",
    path: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(
                child,
                replacements,
                domain=domain,
                path=(*path, key),
            )
            for key, child in sorted(value.items())
            if not _is_transport_identity(key, domain=domain, path=path)
        }
    if isinstance(value, list):
        return [
            _normalize(
                child,
                replacements,
                domain=domain,
                path=(*path, "[]"),
            )
            for child in value
        ]
    if isinstance(value, str):
        normalized = value
        for raw, replacement in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            # #309: normalization is deliberate, BOUNDED substitution of the
            # resolved placement roots declared in ``replacements`` — never
            # general path-shaped-string rewriting. Only an absolute *declared*
            # root is substituted; a non-root absolute path embedded in prose is
            # left byte-identical, so semantically distinct prose survives. The
            # adversarial proof of this boundary is
            # tests/capture/test_parity_bounded_normalization_309.py.
            if Path(raw).is_absolute() and (domain != "query" or _query_result_path_slot(path)):
                normalized = normalized.replace(raw, replacement)
            elif replacement == "<trace-id>":
                normalized = _normalize_trace_join(
                    normalized,
                    raw,
                    replacement,
                    domain=domain,
                    path=path,
                )
            elif (
                replacement.startswith("<context-node:")
                and path
                and (
                    (
                        domain == "companion"
                        and path[-1] in {"node_id", "parent_node_id", "active_path_leaf_id"}
                    )
                    or (
                        domain == "trace"
                        and (
                            path == ("context_tree_summary", "active_path_leaf_id")
                            or path == ("steps", "[]", "context_node_id")
                        )
                    )
                )
                and normalized == raw
            ):
                normalized = replacement
            elif path == ("metadata", "project") and normalized == raw:
                normalized = replacement
        return normalized
    return value


_TRACE_REF_VALUE_KEYS = frozenset({"map_ref", "resource_ref", "trace_ref", "world_ref"})


def _normalize_trace_join(
    value: str,
    raw_trace_id: str,
    replacement: str,
    *,
    domain: str,
    path: tuple[str, ...],
) -> str:
    """Normalize a trace identity only at a declared join-coordinate slot."""

    if domain == "trace" and path == ("context_tree_summary", "trace_id"):
        return replacement if value == raw_trace_id else value
    if domain == "trace" and path == (
        "attribution",
        "files",
        "[]",
        "conversations",
        "[]",
        "url",
    ):
        prefix = f"opentraces://{raw_trace_id}/"
        if value.startswith(prefix):
            return f"opentraces://{replacement}/{value.removeprefix(prefix)}"
        return value
    if domain == "companion" and path and path[-1] == "trace_id" and "payload" in path:
        return replacement if value == raw_trace_id else value
    if not _is_trace_ref_uri_slot(path):
        return value
    prefix = f"ot://trace/{raw_trace_id}"
    if value == prefix or value.startswith(prefix + "/"):
        return replacement.join(value.split(raw_trace_id, 1))
    return value


def _is_trace_ref_uri_slot(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    if path[-1] in _TRACE_REF_VALUE_KEYS:
        return True
    if len(path) >= 2 and path[-1] == "ref":
        return path[-2] in {"source_ref", "trace", "trace_ref"}
    if len(path) >= 2 and path[-1] == "uri":
        return path[-2] in {"bucket_pointer", "source_ref", "trace_ref"}
    return False


def _is_transport_identity(
    key: str,
    *,
    domain: str,
    path: tuple[str, ...],
) -> bool:
    """Suppress identity only at a known transport-envelope boundary.

    A TraceRecord's own ``trace_id``/``content_hash`` vary by capture placement.
    A projected companion row carries canonical TrailEvent envelope identity.
    The same field names below those boundaries are domain data: notably
    ``AttributionRange.content_hash`` and any nested original-range hash.
    """
    if domain == "trace" and not path:
        return key in {"trace_id", "content_hash"}
    if domain == "trace" and path == ("patches", "[]"):
        return key in {
            "patch_id",
            "snapshot_before_id",
            "snapshot_after_id",
        }
    if domain == "companion" and path == ("[]",):
        return key in _COMPANION_ENVELOPE_IDENTITY_KEYS
    if domain == "companion" and "payload" in path:
        if key in {
            "snapshot_id",
            "snapshot_before_id",
            "snapshot_after_id",
            "trace_patch_id",
        }:
            return True
        if (
            path
            and path[-1].endswith("_ref")
            and key
            in {
                "id",
                "display_id",
                "ref",
            }
        ):
            return True
    return False


def _span_coordinates(spans: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: span.get(key) for key in ("start", "end", "kind") if key in span} for span in spans
    ]


def _display_labels(spans: Iterable[dict[str, Any]]) -> list[Any]:
    return [span.get("label") for span in spans]


def _pinned_provenance(value: dict[str, Any] | None) -> bool:
    return bool(isinstance(value, dict) and value.get("model") and value.get("version"))


def _matching_pinned_provenance(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> bool:
    return _pinned_provenance(left) and _pinned_provenance(right) and left == right


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(material).hexdigest()
