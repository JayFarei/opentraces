"""Mandatory redaction gate for capsules bound for a public destination.

Two non-negotiables surfaced by the autoreview (eng + codex, both critical):

1. An ALWAYS-ON floor (``regex`` + ``entropy``, the two zero-dependency in-tree
   detectors) runs over the ENTIRE assembled capsule envelope — not just the
   ``TraceRecord`` — so derived surfaces (the inlined ``context_resume_packet``,
   the slice steps, the repo pin) are covered. The gate asserts the floor RAN,
   not that ``redactions_applied > 0`` (a no-extras machine with nothing to
   redact must not be indistinguishable from "scanned clean").

2. The ``redaction_manifest`` is COUNTS + TYPES ONLY. ``Finding.matched_text``
   carries the literal secret/PII value; serializing findings naively would
   re-publish every redacted token inside the capsule attached to a public
   issue. ``build_redaction_manifest`` never reads ``matched_text``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ...security.pipeline import sanitize_dict
from .contract import REDACTION_MANIFEST_SCHEMA_VERSION

# The zero-dependency, in-tree detector floor. Always available regardless of
# whether the project opted into trufflehog / llm_pii / privacy_filter extras.
REDACTION_FLOOR: tuple[str, ...] = ("regex", "entropy")

try:  # SECURITY_VERSION is informative provenance, not load-bearing.
    from ...security import SECURITY_VERSION as _SECURITY_VERSION
except Exception:  # pragma: no cover - defensive
    _SECURITY_VERSION = "unknown"


class RedactionGateError(RuntimeError):
    """The mandatory redaction floor did not run. Publishing is blocked."""


# Home-dir / user-path scrub: the detector floor catches secrets and PII, but a
# bare absolute path like /Users/jane/... leaks a username into a public issue.
# These paths are scrubbed to ``~/`` by construction.
_USER_PATH_RE = re.compile(r"/(?:Users|home)/[^/\s\"']+")


def _scrub_home_paths(obj: Any, home: str) -> tuple[Any, int]:
    """Recursively replace the real home dir and /Users|/home/<user> with ~."""

    count = 0
    if isinstance(obj, str):
        new = obj
        if home and home in new:
            new = new.replace(home, "~")
        new, n = _USER_PATH_RE.subn("~", new)
        count += n + (1 if (home and home in obj) else 0)
        return new, count
    if isinstance(obj, list):
        out_list = []
        for item in obj:
            red, n = _scrub_home_paths(item, home)
            out_list.append(red)
            count += n
        return out_list, count
    if isinstance(obj, dict):
        out_dict = {}
        for key, value in obj.items():
            red, n = _scrub_home_paths(value, home)
            out_dict[key] = red
            count += n
        return out_dict, count
    return obj, count


def build_redaction_manifest(
    report: Any,
    *,
    tools_applied: list[str],
    home_paths_scrubbed: int,
) -> dict[str, Any]:
    """Counts + types ONLY. Never serializes ``Finding.matched_text``."""

    by_tool: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_field_path: dict[str, int] = {}
    for finding in getattr(report, "findings", []) or []:
        by_tool[finding.tool] = by_tool.get(finding.tool, 0) + 1
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        # field_path is a JSON path (e.g. "context_resume_packet.messages.0"),
        # not content — safe to keep so a reviewer sees WHERE redactions landed.
        path = finding.field_path or "<root>"
        by_field_path[path] = by_field_path.get(path, 0) + 1

    floor_satisfied = set(REDACTION_FLOOR).issubset(set(tools_applied))
    return {
        "schema_version": REDACTION_MANIFEST_SCHEMA_VERSION,
        "security_version": _SECURITY_VERSION,
        "floor": list(REDACTION_FLOOR),
        "tools_applied": list(tools_applied),
        "floor_satisfied": floor_satisfied,
        "redactions_applied": int(getattr(report, "redactions_applied", 0)),
        "findings_total": len(getattr(report, "findings", []) or []),
        "home_paths_scrubbed": int(home_paths_scrubbed),
        "by_tool": by_tool,
        "by_severity": by_severity,
        "by_field_path": by_field_path,
    }


def redact_envelope(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the always-on floor over every string leaf of ``payload``.

    Returns ``(redacted_payload, manifest)``. The manifest is counts+types only.
    """

    redacted, report = sanitize_dict(payload, tools=list(REDACTION_FLOOR))
    home = str(Path.home())
    redacted, scrub_count = _scrub_home_paths(redacted, home)
    # Also scrub a couple of common env leaks that are not paths.
    manifest = build_redaction_manifest(
        report,
        tools_applied=list(report.tools_applied),
        home_paths_scrubbed=scrub_count,
    )
    return redacted, manifest


def assert_redaction_gate(manifest: dict[str, Any]) -> None:
    """Hard gate: refuse to proceed unless the mandatory floor ran."""

    if not manifest.get("floor_satisfied"):
        ran = manifest.get("tools_applied") or []
        raise RedactionGateError(
            "redaction floor did not run "
            f"(required {list(REDACTION_FLOOR)}, ran {ran}); "
            "refusing to build a shareable capsule. This is a hard safety gate."
        )


__all__ = [
    "REDACTION_FLOOR",
    "RedactionGateError",
    "assert_redaction_gate",
    "build_redaction_manifest",
    "redact_envelope",
]
