"""The ONE shared egress clearance predicate (issue #183, seal-family M1).

Exactly one predicate decides whether a trace's bytes may leave the private
bucket (ADR-0008 §3). PR #174 first grew that gate inside
``bucket_sync.push_withhold_partition`` for Door A (``bucket sync push``); this
module EXTRACTS the per-row test into a neutral leaf so the two other egress
doors — dataset publish (#194) and capsule publish (#198) — adopt the SAME
predicate in M2 instead of re-implementing a third lock.

Leaf discipline (load-bearing): this module imports NOTHING from
``bucket_sync`` / ``datasets`` / ``capsule`` — those import IT. The only
inbound dependency is a LAZY, in-function import of ``trace_v2_summary_by_id``
from ``bucket_envelope`` (the existing single-trace, no-manifest lookup
primitive), so Doors A/B/C can all import this module without a cycle.

Vocabulary (deliberate): the three-way return
(``"cleared"`` / ``"not_cleared"`` / ``"unknown"``) is byte-for-byte the
``sync_state`` vocabulary ``bucket_list.py`` already speaks, so a future
consolidation of the duplicate status readers is not fighting a fourth
vocabulary. ``bucket_sync`` keeps its own Door-A envelope words
(``pushed`` / ``withheld`` + ``sub_reason``) by translating this three-way at
its boundary.

The gate is a PROCESS state, never a content verdict:

* ``cleared`` — the accelerator row is known and positively ``syncable == True``.
* ``not_cleared`` — known, but ``syncable`` is not ``True`` (an unfiltered or
  security-stale row).
* ``unknown`` — the row's status is ABSENT / not-yet-populated (``known`` is
  falsey). Withholding on unknown is conservative by design: absence of a
  recorded clearance is NEVER coerced to "safe to leave".
"""

from __future__ import annotations

from typing import Any

CLEARED = "cleared"
NOT_CLEARED = "not_cleared"
UNKNOWN = "unknown"


def clearance_state(status: dict[str, Any] | None) -> str:
    """Classify ONE accelerator ``status`` block into the three-way clearance.

    Mirrors ``bucket_list._project_row``'s ``sync_state`` exactly:

    * ``unknown`` when ``status`` is missing / not a dict / ``known`` is falsey;
    * ``not_cleared`` when ``known`` but ``syncable`` is not positively ``True``;
    * ``cleared`` when ``known`` and ``syncable is True``.
    """

    if not isinstance(status, dict) or not status.get("known"):
        return UNKNOWN
    return CLEARED if status.get("syncable") is True else NOT_CLEARED


def is_row_cleared(status: dict[str, Any] | None) -> bool:
    """``True`` iff ``status`` is positively cleared for egress.

    Exactly ``clearance_state(status) == "cleared"`` — the boolean convenience
    wrapper Doors B/C call when they only need a yes/no.
    """

    return clearance_state(status) == CLEARED


def clearance_for_trace(
    trace_id: str, *, manifest: dict[str, Any] | None = None
) -> str:
    """Resolve ONE trace's clearance by id, returning the three-way state.

    When ``manifest`` is supplied the caller already holds a push-time snapshot:
    index ``manifest["traces"]`` by ``trace_id`` and classify that row's
    ``status`` — no I/O, and every row of a publish run authorizes against the
    SAME snapshot (the no-TOCTOU path). When ``manifest`` is ``None`` fall back
    to :func:`~opentraces.core.bucket_envelope.trace_v2_summary_by_id`, the
    existing single-trace, no-manifest-read lookup primitive. Returns
    ``"unknown"`` when the trace is not found in either source.
    """

    if manifest is not None:
        rows = manifest.get("traces") if isinstance(manifest, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and str(row.get("trace_id") or "") == trace_id:
                    return clearance_state(
                        row.get("status")
                        if isinstance(row.get("status"), dict)
                        else None
                    )
        return UNKNOWN

    from .bucket_envelope import trace_v2_summary_by_id

    summary = trace_v2_summary_by_id(trace_id)
    if not isinstance(summary, dict):
        return UNKNOWN
    status = summary.get("status")
    return clearance_state(status if isinstance(status, dict) else None)
