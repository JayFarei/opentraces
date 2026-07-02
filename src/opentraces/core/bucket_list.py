"""Bounded, accelerator-backed per-trace inventory behind ``opentraces bucket list``.

``bucket list`` is the hang-proof read the old ``bucket manifest`` never was
(issue #162). ``manifest`` materialised a per-trace envelope for every trace
and so hung (rc=124) on a real fleet bucket; ``list`` reads the plan-087 per-row
status accelerator from the persisted ``bucket/manifest.json`` ONCE
(``read_persisted_manifest_capped`` — byte-capped, side-effect-free), applies
its predicates BEFORE projection, and pages the survivors, so work is
``O(page)`` not ``O(N)`` envelope materialisation.

Design invariants (load-bearing):

* **Predicates before projection.** Every filter is applied to the cheap
  accelerator rows in memory; only the surviving page is projected to output
  rows. A filtered ``--count`` never materialises a single output row.
* **Accelerator-sparsity contract.** The accelerator is sparse on a fleet
  bucket (only a fraction of traces are security-scanned). An ABSENT / not-yet-
  populated per-row status is an explicit ``unknown`` facet, NEVER silently
  coerced to ``false``. The envelope ALWAYS carries ``unknown_status_count`` so
  a consumer can never mistake "no recorded status" for "clean".
* **Stable sort + opaque cursor.** Rows sort by ``written_at`` then
  ``trace_id`` (ascending); rows with an absent ``written_at`` sort LAST under a
  sentinel and stay enumerable. The page cursor is an opaque base64 encoding of
  the ``(written_at, trace_id)`` position (the encoding shared with
  ``trace query``), so pagination is deterministic.

The emitted envelope is the frozen ``opentraces.bucket.list.v1`` contract;
changing its shape requires a schema_version bump.
"""

from __future__ import annotations

import base64
import json
from typing import Any

LIST_SCHEMA_VERSION = "opentraces.bucket.list.v1"

# Interactive-budget page default (issue #162): pinned at 50, configurable via
# ``--limit``, hard-capped at 1000 so a single page is always ``O(page)``.
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000

# Title truncation ceiling per row.
MAX_TITLE_CHARS = 80


def _row_status(row: dict[str, Any]) -> dict[str, Any] | None:
    status = row.get("status") if isinstance(row, dict) else None
    return status if isinstance(status, dict) else None


def _is_known(row: dict[str, Any]) -> bool:
    status = _row_status(row)
    return bool(status and status.get("known"))


def _is_syncable(row: dict[str, Any]) -> bool:
    status = _row_status(row)
    return bool(status and status.get("known") and status.get("syncable") is True)


def _is_unfiltered(row: dict[str, Any]) -> bool:
    status = _row_status(row)
    return bool(status and status.get("known") and status.get("syncable") is False)


def _is_security_stale(row: dict[str, Any]) -> bool:
    status = _row_status(row)
    return bool(status and status.get("known") and status.get("security_stale"))


def _written_at(row: dict[str, Any]) -> str | None:
    status = _row_status(row)
    if status is not None:
        wa = status.get("written_at")
        if wa:
            return str(wa)
    return None


def _sort_key(row: dict[str, Any]) -> tuple[bool, str, str]:
    """Stable ascending sort key. Absent ``written_at`` sorts LAST (the
    ``True`` component is greater than ``False``), then by ``trace_id``."""
    wa = _written_at(row)
    return (wa is None, wa or "", str(row.get("trace_id") or ""))


def _encode_cursor(row: dict[str, Any]) -> str:
    absent, wa, tid = _sort_key(row)
    raw = json.dumps([absent, wa, tid], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[bool, str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        parsed = json.loads(raw.decode("utf-8"))
        if (
            isinstance(parsed, list)
            and len(parsed) == 3
            and isinstance(parsed[0], bool)
        ):
            return (bool(parsed[0]), str(parsed[1]), str(parsed[2]))
    except (ValueError, TypeError):
        return None
    return None


def _truncate_title(title: Any) -> str | None:
    if title is None:
        return None
    text = str(title)
    if len(text) <= MAX_TITLE_CHARS:
        return text
    return text[: MAX_TITLE_CHARS - 1] + "…"


def _project_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project one accelerator row to a bounded output row.

    ABSENT per-row status renders as an explicit ``"unknown"`` facet, never
    coerced to ``false`` / ``clean``."""
    status = _row_status(row)
    known = _is_known(row)
    if not known:
        sync_state = "unknown"
        security_stale: Any = "unknown"
    else:
        sync_state = "cleared" if status.get("syncable") is True else "not_cleared"
        security_stale = bool(status.get("security_stale"))
    return {
        "trace_id": row.get("trace_id"),
        "project_slug": row.get("project_slug"),
        "title": _truncate_title(row.get("title")),
        "written_at": _written_at(row),
        "lifecycle": row.get("lifecycle"),
        "sync_state": sync_state,
        "security_stale": security_stale,
    }


def _matches(
    row: dict[str, Any],
    *,
    unsynced: bool,
    unfiltered: bool,
    security_stale: bool,
    unscanned: bool,
    project: str | None,
    since: str | None,
) -> bool:
    if project is not None and str(row.get("project_slug") or "") != project:
        return False
    # --unscanned: the honest "we cannot confirm this trace" facet (status
    # absent / not yet populated). Never coerced to a content verdict.
    if unscanned and _is_known(row):
        return False
    # --unsynced: not cleared for sync (a PROCESS state) — anything that is not
    # positively syncable, which is exactly status.not_cleared_count.
    if unsynced and _is_syncable(row):
        return False
    # --unfiltered: scanned AND holding unresolved redactable content.
    if unfiltered and not _is_unfiltered(row):
        return False
    if security_stale and not _is_security_stale(row):
        return False
    if since is not None:
        wa = _written_at(row)
        # Absent written_at is the unknown facet — never silently dropped from
        # --since results (issue #162 edge case); only present-and-older rows
        # are filtered out.
        if wa is not None and wa < since:
            return False
    return True


def build_bucket_list(
    *,
    unsynced: bool = False,
    unfiltered: bool = False,
    security_stale: bool = False,
    unscanned: bool = False,
    project: str | None = None,
    since: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    count_only: bool = False,
) -> dict[str, Any]:
    """Build the ``opentraces.bucket.list.v1`` envelope. ``O(page)`` in bucket size.

    Reads the persisted manifest accelerator once, applies predicates BEFORE
    projection, sorts by ``(written_at, trace_id)``, and returns a bounded page
    plus an opaque cursor. ``count_only`` short-circuits to just the matching
    count (ignoring ``limit``) without materialising rows.
    """

    from .bucket_store import read_persisted_manifest_capped

    limit = max(1, min(int(limit), MAX_LIMIT))
    state, manifest = read_persisted_manifest_capped()
    if state == "ok" and isinstance(manifest, dict):
        rows = [r for r in (manifest.get("traces") or []) if isinstance(r, dict)]
    else:
        # Absent / too-large / unreadable manifest: an empty page, never a scan.
        rows = []

    matching = [
        r
        for r in rows
        if _matches(
            r,
            unsynced=unsynced,
            unfiltered=unfiltered,
            security_stale=security_stale,
            unscanned=unscanned,
            project=project,
            since=since,
        )
    ]
    total = len(matching)
    unknown_status_count = sum(1 for r in matching if not _is_known(r))

    if count_only:
        return {
            "schema_version": LIST_SCHEMA_VERSION,
            "count": total,
            "total": total,
            "unknown_status_count": unknown_status_count,
        }

    matching.sort(key=_sort_key)

    start = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            # Advance past every row whose sort position is <= the cursor.
            for i, r in enumerate(matching):
                if _sort_key(r) > decoded:
                    start = i
                    break
            else:
                start = len(matching)

    page = matching[start : start + limit]
    next_cursor = (
        _encode_cursor(page[-1])
        if page and (start + len(page)) < total
        else None
    )

    return {
        "schema_version": LIST_SCHEMA_VERSION,
        "rows": [_project_row(r) for r in page],
        "cursor": next_cursor,
        "total": total,
        "unknown_status_count": unknown_status_count,
    }
