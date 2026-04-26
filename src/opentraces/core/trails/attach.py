"""Manual ``trail attach`` recovery for Phase 5.

When the post-commit correlator misses (hook failure, daemon crash,
sessions backfilled out of order), users can run ``opentraces trail
attach --trace <id> --commit <sha>`` to retroactively connect that
trace's evidence to a specific Git commit.

The contract is strictly append-only: source TrailEvents are byte-
identical after attach, and new events carry
``capture_method=["manual_attach"]`` so downstream consumers can
distinguish manually-attached anchors from automatic ones. Idempotency
is provided by ``reconcile_commit_anchors`` skipping (trace_patch_id,
commit) pairs that already have either an anchor or a search event in
the log.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .anchors import reconcile_commit_anchors

ATTACH_CAPTURE_METHOD = ["manual_attach"]
ATTACH_WRITER = "manual-attach"


def attach_trace_to_commit(
    repo: Path,
    trace_id: str,
    commit_ref: str,
    *,
    writer: str = ATTACH_WRITER,
) -> list[dict[str, Any]]:
    """Search ``trace_id``'s patches against ``commit_ref`` and append anchors.

    Returns the list of created anchor payloads. Re-running on an already-
    attached trace produces no new events and returns ``[]``.
    """
    return reconcile_commit_anchors(
        repo,
        commit_ref,
        writer=writer,
        capture_method=ATTACH_CAPTURE_METHOD,
        trace_id=trace_id,
    )
