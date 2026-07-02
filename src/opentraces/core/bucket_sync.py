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


class BucketPushWithheldError(Exception):
    """A real bucket push would egress not-yet-cleared traces — refuse.

    Egress safety (#162): ``sync push`` copies the raw substrate, so it must
    NEVER egress a trace that is not positively cleared for sync. The
    bucket-level ``sync.eligible`` flag is an all-or-nothing guard that misses
    the ``status_unknown`` case (a row whose accelerator status is absent does
    NOT increment ``unfiltered_count``), so a bucket could report
    ``eligible == True`` while still holding withheld traces. When the FRESH
    push-time manifest still partitions any trace into ``withheld`` this is
    raised BEFORE a single byte leaves the machine, carrying the partition so
    the CLI can emit the auditable ``status='refused'`` envelope.
    """

    def __init__(self, partition: dict[str, Any]) -> None:
        self.partition = partition
        withheld = partition.get("withheld") or []
        super().__init__(
            f"bucket push refused: {len(withheld)} trace(s) not cleared for sync"
        )


def partition_from_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Partition an in-hand manifest dict (the fresh push-time recompute).

    Unlike :func:`sync_push_partition` (which re-reads the PERSISTED manifest),
    this partitions a manifest the caller ALREADY holds. The push path calls it
    on the manifest it just recomputed, so enforcement judges the SAME snapshot
    it is about to egress — never a stale pre-read.
    """

    rows: list[dict[str, Any]] = []
    if isinstance(manifest, dict):
        rows = [r for r in (manifest.get("traces") or []) if isinstance(r, dict)]
    return push_withhold_partition(rows)


def enforce_push_clearance(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Refuse egress unless EVERY trace in ``manifest`` is cleared for sync.

    Returns the partition (``pushed`` populated, ``withheld`` empty) when the
    push may proceed. Raises :class:`BucketPushWithheldError` — carrying the
    partition — when any trace is withheld, so ZERO bytes egress for a
    not-cleared trace. This is the per-trace egress gate that the bucket-level
    ``sync.eligible`` flag misses (``status_unknown`` rows do not increment
    ``unfiltered_count``).
    """

    partition = partition_from_manifest(manifest)
    if partition["withheld"]:
        raise BucketPushWithheldError(partition)
    return partition


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
