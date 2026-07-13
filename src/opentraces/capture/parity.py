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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .portable import CaptureResult


@dataclass(frozen=True)
class PlacementParityReport:
    matches: bool
    view_completeness_match: bool
    canonical_trace_match: bool
    context_companion_match: bool
    trail_companion_match: bool
    security_match: bool
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
            "canonical_trace_match": self.canonical_trace_match,
            "context_companion_match": self.context_companion_match,
            "trail_companion_match": self.trail_companion_match,
            "security_match": self.security_match,
            "path_normalization_applied": self.path_normalization_applied,
            "persistent_digest": self.persistent_digest,
            "leased_digest": self.leased_digest,
            "differences": list(self.differences),
            "limitations": list(self.limitations),
            "span_match": self.span_match,
            "display_label_match": self.display_label_match,
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
    view_match = {
        view.name: view.completeness for view in persistent.views
    } == {view.name: view.completeness for view in leased.views}
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

    persistent_norm = _normalize(
        persistent_trace, persistent_replacements, domain="trace"
    )
    leased_norm = _normalize(leased_trace, leased_replacements, domain="trace")
    trace_match = persistent_norm == leased_norm
    security_match = _normalize(
        persistent_trace.get("security") or {},
        persistent_replacements,
        domain="semantic",
    ) == _normalize(
        leased_trace.get("security") or {},
        leased_replacements,
        domain="semantic",
    )

    persistent_context_raw = _read_jsonl_gz(
        persistent_path.with_name("context.jsonl.gz")
    )
    leased_context_raw = _read_jsonl_gz(leased_path.with_name("context.jsonl.gz"))
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
    persistent_trail_raw = _read_jsonl_gz(
        persistent_path.with_name("trail.jsonl.gz")
    )
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
    context_match = persistent_context == leased_context
    trail_match = persistent_trail == leased_trail

    differences: list[str] = []
    if not view_match:
        differences.append("view_completeness")
    if not trace_match:
        differences.append("canonical_trace")
    if not context_match:
        differences.append("context_companion")
    if not trail_match:
        differences.append("trail_companion")
    if not security_match:
        differences.append("security")
    companions_required = (
        persistent.source("bucket").required and leased.source("bucket").required
    )
    if companions_required:
        if not persistent_context_raw or not leased_context_raw:
            differences.append("empty_context_companion")
        if not persistent_trail_raw or not leased_trail_raw:
            differences.append("empty_trail_companion")

    limitations: list[str] = []
    span_match: bool | None = None
    display_label_match: bool | None = None
    if persistent_spans is not None or leased_spans is not None:
        left_spans = persistent_spans or []
        right_spans = leased_spans or []
        span_match = _span_coordinates(left_spans) == _span_coordinates(right_spans)
        if not span_match:
            differences.append("slicer_spans")
        if _matching_pinned_provenance(
            persistent_labeler_provenance, leased_labeler_provenance
        ):
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
            limitations.append(
                "display labels were not compared because labeler provenance differs"
            )

    persistent_digest = _digest(
        {
            "trace": persistent_norm,
            "context": persistent_context,
            "trail": persistent_trail,
        }
    )
    leased_digest = _digest(
        {
            "trace": leased_norm,
            "context": leased_context,
            "trail": leased_trail,
        }
    )
    matches = not differences
    return PlacementParityReport(
        matches=matches,
        view_completeness_match=view_match,
        canonical_trace_match=trace_match,
        context_companion_match=context_match,
        trail_companion_match=trail_match,
        security_match=security_match,
        path_normalization_applied=True,
        persistent_digest=persistent_digest,
        leased_digest=leased_digest,
        differences=tuple(differences),
        limitations=tuple(limitations),
        span_match=span_match,
        display_label_match=display_label_match,
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


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object at {path}")
    return data


def _path_aliases(path: Path) -> set[str]:
    """Return declared/resolved roots plus macOS's public symlink spelling."""

    aliases = {str(path), str(path.resolve())}
    for value in tuple(aliases):
        if value.startswith("/private/"):
            aliases.add(value.removeprefix("/private"))
    return aliases


def _read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _sanitize_companion_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize comparison material without rewriting canonical bucket bytes."""

    from ..security.pipeline import sanitize_companion_dict

    projected: list[dict[str, Any]] = []
    for row in rows:
        safe = dict(row)
        payload, _manifest = sanitize_companion_dict(dict(safe.get("payload") or {}))
        safe["payload"] = payload
        projected.append(safe)
    return projected


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
            if (
                key == "content_hash"
                and isinstance(child, str)
                and "messages" in path
            ):
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
            if Path(raw).is_absolute():
                normalized = normalized.replace(raw, replacement)
            elif replacement == "<trace-id>":
                normalized = _normalize_trace_join(
                    normalized,
                    raw,
                    replacement,
                    domain=domain,
                    path=path,
                )
            elif path == ("metadata", "project") and normalized == raw:
                normalized = replacement
        return normalized
    return value


_TRACE_REF_VALUE_KEYS = frozenset(
    {"map_ref", "resource_ref", "trace_ref", "world_ref"}
)


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
    if domain == "companion" and path[-2:] == ("payload", "trace_id"):
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
        if path and path[-1].endswith("_ref") and key in {
            "id",
            "display_id",
            "ref",
        }:
            return True
    return False


def _span_coordinates(spans: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: span.get(key)
            for key in ("start", "end", "kind")
            if key in span
        }
        for span in spans
    ]


def _display_labels(spans: Iterable[dict[str, Any]]) -> list[Any]:
    return [span.get("label") for span in spans]


def _pinned_provenance(value: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("model")
        and value.get("version")
    )


def _matching_pinned_provenance(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> bool:
    return _pinned_provenance(left) and _pinned_provenance(right) and left == right


def _digest(value: Any) -> str:
    material = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(material).hexdigest()
