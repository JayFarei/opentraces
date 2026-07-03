"""Capsule companion redaction (#197) — the FIRST consumer that copies RAW
per-trace companions into a shareable unit, so the F1 companion-leak goes live
HERE (not in the S1 inline envelope, whose floor already covered it).

``redact_companions`` decompresses a raw ``trail/context/sources.jsonl.gz``
companion, runs M1's field-aware ``sanitize_companion_dict`` per line (routing
each leaf through ``companion_field_type`` so a ``cwd`` path-anonymizes, a chat
message goes to NER, a tool description reads as tool input), then re-serializes
deterministically (canonical JSON per line, gzip ``mtime=0``) for cross-machine
byte-identity.

Load-bearing discipline: this module does NOT reimplement sanitization and does
NOT bolt redaction onto ``bucket_trail.py`` (a reader). It is a thin adapter over
the substrate capability — the ONLY sanitizer is
:func:`opentraces.security.pipeline.sanitize_companion_dict`.
"""

from __future__ import annotations

import gzip
import json
from typing import Any, Sequence

from .._bucket_io import _canonical_json, _gzip_deterministic

COMPANION_REDACTION_SCHEMA_VERSION = "opentraces.capsule.companion_redaction.v1"


def _empty_manifest(security_version: str | None) -> dict[str, Any]:
    return {
        "schema_version": COMPANION_REDACTION_SCHEMA_VERSION,
        "security_version": security_version,
        "lines": 0,
        "tools_applied": [],
        "floor_satisfied": True,
        "redactions_applied": 0,
        "findings_total": 0,
        "home_paths_scrubbed": 0,
        "by_tool": {},
    }


def redact_companion_text(
    text: str,
    *,
    tools: Sequence[Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Redact a decompressed JSONL companion body line-by-line.

    Each non-empty line is a JSON object (a TrailEvent / ContextNode / source
    row); it is sanitized via ``sanitize_companion_dict`` and re-emitted as
    canonical JSON so two seals of the same content are byte-identical. Returns
    the redacted body + an aggregate counts+types manifest (never a literal
    secret — the substrate manifest is already ``matched_text``-free).
    """

    # Lazy import keeps the module leaf-light and avoids importing the whole
    # security pipeline at capsule import time.
    from ...security import SECURITY_VERSION
    from ...security.pipeline import COMPANION_FLOOR, sanitize_companion_dict

    tool_list: list[Any] | None = list(tools) if tools is not None else None

    out_lines: list[str] = []
    tools_applied: list[str] = []
    by_tool: dict[str, int] = {}
    redactions = 0
    findings = 0
    home_scrubbed = 0
    floor_satisfied = True
    lines = 0

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        lines += 1
        try:
            obj = json.loads(stripped)
        except ValueError:
            # A non-JSON line is not something the field-aware walker can route;
            # keep it verbatim rather than silently dropping content.
            out_lines.append(stripped)
            continue
        if not isinstance(obj, dict):
            out_lines.append(_canonical_json(obj))
            continue
        redacted, manifest = (
            sanitize_companion_dict(obj, tools=tool_list)
            if tool_list is not None
            else sanitize_companion_dict(obj)
        )
        out_lines.append(_canonical_json(redacted))
        # Aggregate the per-line manifests into one companion-level manifest.
        if not tools_applied:
            tools_applied = list(manifest.get("tools_applied") or [])
        redactions += int(manifest.get("redactions_applied", 0))
        findings += int(manifest.get("findings_total", 0))
        home_scrubbed += int(manifest.get("home_paths_scrubbed", 0))
        floor_satisfied = floor_satisfied and bool(manifest.get("floor_satisfied", False))
        for tool, count in (manifest.get("by_tool") or {}).items():
            by_tool[tool] = by_tool.get(tool, 0) + int(count)

    body = ("\n".join(out_lines) + "\n") if out_lines else ""
    if not tools_applied:
        # An empty companion never runs the walker; still declare the floor set.
        tools_applied = list(COMPANION_FLOOR) if tool_list is None else []
        floor_satisfied = tool_list is None or set(COMPANION_FLOOR).issubset(
            {getattr(t, "name", t) for t in (tool_list or [])}
        )
    manifest = {
        "schema_version": COMPANION_REDACTION_SCHEMA_VERSION,
        "security_version": SECURITY_VERSION,
        "lines": lines,
        "tools_applied": tools_applied,
        "floor_satisfied": floor_satisfied,
        "redactions_applied": redactions,
        "findings_total": findings,
        "home_paths_scrubbed": home_scrubbed,
        "by_tool": by_tool,
    }
    return body, manifest


def redact_companions(
    raw_gz: bytes | None,
    *,
    tools: Sequence[Any] | None = None,
    security_version: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Decompress, redact, re-gzip one raw companion. Returns ``(gz, manifest)``.

    ``raw_gz`` is the gzipped JSONL bytes as stored on the bucket layout (or
    ``None`` / empty for a missing companion). Output gzip is deterministic
    (``mtime=0``) so the same redacted content is byte-identical across machines.
    An empty companion round-trips to an empty companion (byte-stable).
    """

    if not raw_gz:
        from ...security import SECURITY_VERSION

        return b"", _empty_manifest(security_version or SECURITY_VERSION)

    text = gzip.decompress(raw_gz).decode("utf-8")
    body, manifest = redact_companion_text(text, tools=tools)
    if security_version is not None:
        manifest["security_version"] = security_version
    return _gzip_deterministic(body.encode("utf-8")), manifest


__all__ = [
    "COMPANION_REDACTION_SCHEMA_VERSION",
    "redact_companion_text",
    "redact_companions",
]
