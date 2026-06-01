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

import getpass
import re
from pathlib import Path
from typing import Any

from ...security.pipeline import sanitize_dict
from .contract import REDACTION_MANIFEST_SCHEMA_VERSION

# The zero-dependency, in-tree detector floor. Always available regardless of
# whether the project opted into trufflehog / llm_pii / privacy_filter extras.
# Plan 090 adds ``business_logic`` (internal hostnames / collab-tool URLs / DB
# connection strings / AWS account ids) to the mandatory floor for capsules: a
# usage episode that shares a real session must scrub business-logic signals, not
# just generic secrets. It runs via an explicit ``tools=`` list, so it is on for
# every capsule regardless of per-tool opt-in.
REDACTION_FLOOR: tuple[str, ...] = ("regex", "entropy", "business_logic")

try:  # SECURITY_VERSION is informative provenance, not load-bearing.
    from ...security import SECURITY_VERSION as _SECURITY_VERSION
except Exception:  # pragma: no cover - defensive
    _SECURITY_VERSION = "unknown"


class RedactionGateError(RuntimeError):
    """The mandatory redaction floor did not run. Publishing is blocked."""


# Identity scrub: the detector floor catches secrets and PII, but the OPERATOR's
# local username is structurally invisible to a prefix-regex + entropy floor (a
# short lowercase token), and it leaks via git-author lines, ``whoami`` output,
# prose, and home paths. We scrub it three ways:
#   1. the literal home dir string                  (/Users/jane -> ~)
#   2. the username inside a Unix OR Windows path    (.../Users/jane/...,  C:\Users\jane\...)
#   3. the bare username token anywhere, word-bounded (git author jane -> <user>)
# Over-redaction here is safe; under-redaction is a public leak.
_PATH_USER_RE = re.compile(r"(?i)([/\\](?:Users|home)[/\\])([^/\\\s\"']+)")


def _identity_tokens() -> list[str]:
    tokens: set[str] = set()
    try:
        tokens.add(getpass.getuser())
    except Exception:  # pragma: no cover - no login name
        pass
    try:
        tokens.add(Path.home().name)
    except Exception:  # pragma: no cover
        pass
    # Only scrub tokens long enough that a word-boundary match won't shred prose.
    return [t for t in tokens if t and len(t) >= 3]


def _scrub_identity(obj: Any, home: str, token_res: list[re.Pattern[str]]) -> tuple[Any, int]:
    """Recursively scrub home dir, path-embedded usernames, and bare username tokens."""

    count = 0
    if isinstance(obj, str):
        new = obj
        if home and home in new:
            occ = new.count(home)
            new = new.replace(home, "~")
            count += occ
        new, n = _PATH_USER_RE.subn(r"\1<user>", new)
        count += n
        for pat in token_res:
            new, n = pat.subn("<user>", new)
            count += n
        return new, count
    if isinstance(obj, list):
        out_list = []
        for item in obj:
            red, n = _scrub_identity(item, home, token_res)
            out_list.append(red)
            count += n
        return out_list, count
    if isinstance(obj, dict):
        out_dict = {}
        for key, value in obj.items():
            red, n = _scrub_identity(value, home, token_res)
            out_dict[key] = red
            count += n
        return out_dict, count
    return obj, count


def build_redaction_manifest(
    report: Any,
    *,
    tools_applied: list[str],
    home_paths_scrubbed: int,
    fields_excluded: int = 0,
    excluded_field_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Counts + types ONLY. Never serializes ``Finding.matched_text``.

    ``fields_excluded`` / ``excluded_field_paths`` (plan 090) record where the
    capsule_scope EXCLUSION zeroed whole prompt-bearing subtrees — field PATHS
    only (content-free, like ``by_field_path``), so a reviewer sees what was
    withheld without seeing it. Additive within ``opentraces.capsule.redaction.v1``.
    """

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
        "fields_excluded": int(fields_excluded),
        "excluded_field_paths": sorted(set(excluded_field_paths or [])),
        "by_tool": by_tool,
        "by_severity": by_severity,
        "by_field_path": by_field_path,
    }


def redact_envelope(
    payload: dict[str, Any],
    *,
    exclude_paths: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the always-on floor over every string leaf of ``payload``.

    When ``exclude_paths`` is given, matching subtrees are EXCLUDED (zeroed to a
    ``[EXCLUDED:<path>]`` marker) BEFORE the detector floor runs — exclusion is
    unbounded, redaction is detection-bounded. Returns ``(redacted_payload,
    manifest)``; the manifest is counts+types only.

    The excluded-field count/paths are recovered by SCANNING the result for
    markers, so the manifest is correct on the export path (where exclusion is
    applied) and idempotent on the publish path (``ensure_redacted`` re-runs
    without re-applying exclusion, but the markers are already present).
    """

    from ...security.tools.capsule_scope_tool import (
        apply_field_exclusion,
        scan_excluded_paths,
    )

    if exclude_paths:
        payload, _ = apply_field_exclusion(payload, exclude_paths)

    redacted, report = sanitize_dict(payload, tools=list(REDACTION_FLOOR))
    home = str(Path.home())
    token_res = [re.compile(r"\b" + re.escape(t) + r"\b") for t in _identity_tokens()]
    redacted, scrub_count = _scrub_identity(redacted, home, token_res)
    excluded = scan_excluded_paths(redacted)
    manifest = build_redaction_manifest(
        report,
        tools_applied=list(report.tools_applied),
        home_paths_scrubbed=scrub_count,
        fields_excluded=len(excluded),
        excluded_field_paths=excluded,
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


def ensure_redacted(capsule: dict[str, Any]) -> dict[str, Any]:
    """Idempotently guarantee the floor ran over the whole envelope before share.

    ``export_capsule`` already redacts, but a capsule assembled directly via
    ``freeze_capsule`` does NOT — that gap leaked a home path on 2026-05-31. Every
    publish path runs this so an un-redacted envelope can never leave the machine,
    regardless of how it was built. Re-running on already-clean text is a no-op
    (the floor finds nothing new); it then asserts the gate (plan 089 R7).
    """

    redacted, manifest = redact_envelope(capsule)
    redacted = dict(redacted)
    redacted["redaction"] = {**(capsule.get("redaction") or {}), "manifest": manifest}
    assert_redaction_gate(manifest)
    return redacted


__all__ = [
    "REDACTION_FLOOR",
    "RedactionGateError",
    "assert_redaction_gate",
    "build_redaction_manifest",
    "ensure_redacted",
    "redact_envelope",
]
