from __future__ import annotations

from typing import Any


def normalize_bucket_trace_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Return the stable parity shape for one bucket ``traces[]`` row."""

    return {
        "title": row.get("title"),
        "lifecycle": row.get("lifecycle"),
        "summary": _normalize_summary(row.get("summary") or {}),
        "files": row.get("files") or {},
        "remote_sync_eligible": row.get("remote_sync_eligible"),
        "agent": {
            "name_present": _present_string(row.get("agent_name")),
            "version_shape": _optional_string_shape(row.get("agent_version")),
            "model_shape": _optional_string_shape(row.get("agent_model")),
        },
    }


def assert_bucket_trace_parity(
    left: dict[str, Any],
    right: dict[str, Any],
) -> None:
    assert normalize_bucket_trace_summary(left) == normalize_bucket_trace_summary(right)


def _present_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _normalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(summary)
    capture_methods = normalized.pop("capture_methods", [])
    normalized["capture_methods_present"] = bool(capture_methods)
    normalized["capture_method_count"] = (
        len(capture_methods) if isinstance(capture_methods, list) else 0
    )
    return normalized


def _optional_string_shape(value: Any) -> str:
    if value is None:
        return "missing"
    if isinstance(value, str) and value:
        return "present"
    return "invalid"
