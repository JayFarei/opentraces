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
    persistent_trace = _read_json(persistent_path)
    leased_trace = _read_json(leased_path)
    replacements = {
        str(persistent_path.parents[4]): "<bucket-root>",
        str(leased_path.parents[4]): "<bucket-root>",
        persistent.trace_refs[0]: "<trace-id>",
        leased.trace_refs[0]: "<trace-id>",
    }
    for root in (*persistent_roots, *leased_roots):
        resolved_root = Path(root).resolve()
        replacements[str(resolved_root)] = "<workspace>"
        replacements[resolved_root.name] = "<workspace-name>"

    persistent_norm = _normalize(persistent_trace, replacements)
    leased_norm = _normalize(leased_trace, replacements)
    trace_match = persistent_norm == leased_norm
    security_match = _normalize(
        persistent_trace.get("security") or {}, replacements
    ) == _normalize(leased_trace.get("security") or {}, replacements)

    persistent_context = _normalize(
        _read_jsonl_gz(persistent_path.with_name("context.jsonl.gz")), replacements
    )
    leased_context = _normalize(
        _read_jsonl_gz(leased_path.with_name("context.jsonl.gz")), replacements
    )
    persistent_trail = _normalize(
        _read_jsonl_gz(persistent_path.with_name("trail.jsonl.gz")), replacements
    )
    leased_trail = _normalize(
        _read_jsonl_gz(leased_path.with_name("trail.jsonl.gz")), replacements
    )
    context_match = persistent_context == leased_context
    trail_match = persistent_trail == leased_trail

    differences: list[str] = []
    if not trace_match:
        differences.append("canonical_trace")
    if not context_match:
        differences.append("context_companion")
    if not trail_match:
        differences.append("trail_companion")
    if not security_match:
        differences.append("security")

    limitations: list[str] = []
    span_match: bool | None = None
    display_label_match: bool | None = None
    if persistent_spans is not None or leased_spans is not None:
        left_spans = persistent_spans or []
        right_spans = leased_spans or []
        span_match = _span_coordinates(left_spans) == _span_coordinates(right_spans)
        if not span_match:
            differences.append("slicer_spans")
        if persistent_labeler_provenance == leased_labeler_provenance:
            display_label_match = _display_labels(left_spans) == _display_labels(right_spans)
            if not display_label_match:
                differences.append("display_labels")
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


def _read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


_IDENTITY_ONLY_KEYS = frozenset(
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


def _normalize(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(child, replacements)
            for key, child in sorted(value.items())
            if key not in _IDENTITY_ONLY_KEYS
        }
    if isinstance(value, list):
        return [_normalize(child, replacements) for child in value]
    if isinstance(value, str):
        normalized = value
        for raw, replacement in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            normalized = normalized.replace(raw, replacement)
        return normalized
    return value


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


def _digest(value: Any) -> str:
    material = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(material).hexdigest()
