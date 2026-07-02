"""``bucket sync push`` egress-safety partition (issue #162).

``sync push`` egresses the raw substrate, so it is the HIGH-blast-radius verb
and must make its withhold decision AUDITABLE, not just narrated. This module
computes the process-state partition of a bucket into ``pushed`` (cleared for
sync) and ``withheld`` (not cleared) rows straight from the plan-087 per-row
status accelerator, so a ``--dry-run`` can prove the egress-safety property
without egressing a single byte.

The gate is a PROCESS state, never a content verdict:

* A trace is eligible to push ONLY when its accelerator row is positively
  ``syncable == True``.
* A trace is WITHHELD when its row is ``syncable == False`` (sub-reason
  ``syncable_false``) OR its per-row status is ABSENT / not-yet-populated
  (sub-reason ``status_unknown``). Withholding on unknown is conservative by
  design: absence of a recorded clearance is NEVER coerced to "safe to push".

The withhold reason is always ``not_cleared_for_sync`` — never "secrets found"
/ "unfiltered". A sparse-accelerator bucket withholding most of its traces
means "not yet cleared", not "these traces contain secrets".
"""

from __future__ import annotations

from typing import Any

WITHHOLD_REASON = "not_cleared_for_sync"


def push_withhold_partition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Partition accelerator ``rows`` into ``pushed`` / ``withheld`` (pure).

    Returns ``{"pushed": [trace_id, ...], "withheld": [{trace_id, reason,
    sub_reason}, ...]}``. ``set(pushed) ∩ set(withheld trace_ids) == ∅`` by
    construction: every row lands in exactly one partition.
    """

    pushed: list[str] = []
    withheld: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trace_id = str(row.get("trace_id") or "")
        status = row.get("status") if isinstance(row.get("status"), dict) else None
        known = bool(status and status.get("known"))
        syncable = bool(status and status.get("syncable") is True)
        if known and syncable:
            pushed.append(trace_id)
        else:
            withheld.append(
                {
                    "trace_id": trace_id,
                    "reason": WITHHOLD_REASON,
                    "sub_reason": "syncable_false" if known else "status_unknown",
                }
            )
    return {"pushed": pushed, "withheld": withheld}


def sync_push_partition() -> dict[str, Any]:
    """Read the persisted manifest accelerator ONCE and partition it.

    ``O(rows-in-memory)``; never scans the bucket. An absent / unreadable
    manifest yields an empty partition (nothing cleared, nothing withheld).
    """

    from .bucket_store import read_persisted_manifest_capped

    state, manifest = read_persisted_manifest_capped()
    if state == "ok" and isinstance(manifest, dict):
        rows = [r for r in (manifest.get("traces") or []) if isinstance(r, dict)]
    else:
        rows = []
    return push_withhold_partition(rows)
