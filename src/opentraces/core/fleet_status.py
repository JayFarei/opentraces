"""Fleet bucket status - the O(1) safety-first dashboard behind ``opentraces status``.

This is the read model for the top-level ``opentraces status`` command (issue
#161). It answers ONE question fast: "is my private trace bucket safe to sync,
and if not, what is holding it back?"

Design invariants (all load-bearing):

* **O(1) by construction.** It reads the persisted ``bucket/manifest.json``
  once (``read_persisted_manifest_capped`` - byte-capped, side-effect-free) and
  aggregates the per-row status accelerator in memory. It NEVER calls the O(N)
  full-TraceRecord scan (``bucket_status`` / ``bucket_manifest``); an absent
  manifest is reported as an empty bucket, an oversized/unreadable one as a
  degraded read that recommends ``bucket repair`` - neither triggers a scan.
* **Safety-first, process-not-content.** The safe/"clear" verdict is IMPOSSIBLE
  while ``scanned_count < trace_count``: an unscanned trace can hide anything, so
  it is by definition "not cleared for sync". ``scanned_count`` /
  ``unscanned_count`` are first-class top-level safety fields. The verdict is a
  PROCESS state ("not cleared for sync"), never a content claim ("secrets
  found").
* **Counts, not enumeration.** Every per-trace question is answered as a COUNT
  plus an ``opentraces bucket list ...`` handoff string in ``next_steps`` - the
  dashboard never dumps the ``traces[]`` array.

The emitted envelope is the frozen ``opentraces.bucket.status.v1`` contract;
changing its shape requires a schema_version bump.
"""

from __future__ import annotations

from typing import Any

FLEET_STATUS_SCHEMA_VERSION = "opentraces.bucket.status.v1"

# Safety-first readiness gate. ``ready`` (and the ``--short`` clean signal) go
# green ONLY when the SCANNED FRACTION of the bucket reaches this threshold. We
# pin it at 1.0 deliberately: on a safety dashboard, "not mostly-unscanned" is
# read at its strictest - a SINGLE unscanned trace blocks readiness, because an
# unscanned trace is an unknown that cannot be cleared for sync. This keeps
# ``ready`` consistent with the ``verdict == "clear"`` gate (both require every
# trace scanned) instead of letting a 99%-scanned bucket claim green.
READY_MIN_SCANNED_FRACTION = 1.0

# How many per-project rows the ``fleet.by_project`` rollup carries before it
# collapses the tail into ``projects_omitted`` (biggest-first). Bounded output
# is part of the O(1) contract - a 500-project fleet still prints a short table.
FLEET_TOP_N = 10


def _row_status(row: dict[str, Any]) -> dict[str, Any] | None:
    status = row.get("status") if isinstance(row, dict) else None
    if isinstance(status, dict):
        return status
    return None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate the per-row status accelerator into safety/sync counts.

    ``scanned`` == a row whose security state is KNOWN (its object envelope was
    present when the manifest row was written). ``unscanned`` == a row whose
    status was never populated - the honest "we cannot confirm this trace"
    bucket. ``not_cleared`` == every row that is not fully syncable (has secrets,
    a stale scan version, or is unscanned), computed as ``trace_count -
    syncable_count`` so a row that is both unfiltered AND stale is counted once.
    """

    trace_count = len(rows)
    scanned = syncable = unfiltered = security_stale = privacy_off = 0
    for row in rows:
        status = _row_status(row)
        if not status or not status.get("known"):
            continue
        scanned += 1
        if status.get("syncable") is True:
            syncable += 1
        elif status.get("syncable") is False:
            unfiltered += 1
        if status.get("security_stale"):
            security_stale += 1
        if status.get("privacy_off"):
            privacy_off += 1
    return {
        "trace_count": trace_count,
        "scanned_count": scanned,
        "unscanned_count": trace_count - scanned,
        "syncable_count": syncable,
        "unfiltered_count": unfiltered,
        "security_stale_count": security_stale,
        "privacy_off_count": privacy_off,
        "not_cleared_count": trace_count - syncable,
    }


def _freshness(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    unscanned: int,
    current_version: str,
    *,
    absent: bool,
) -> dict[str, Any]:
    """Honest freshness stamp from three O(1) signals (plan 087 semantics).

    Freshness is a read-model property, distinct from safety: it says whether the
    persisted counts can still be trusted. ``understates_safety`` is the safety
    bridge, True when the scanned subset is partial or the read model is behind,
    so a consumer knows the reported not-cleared count may be optimistic.
    """

    if absent:
        # A fresh/empty world has no persisted manifest to be stale against; an
        # empty bucket is a clean resting state, not a stale read.
        return {
            "stale": False,
            "reasons": [],
            "source": "absent",
            "understates_safety": False,
            "rebuild_recommended": False,
            "last_full_sweep_at": None,
        }

    reasons: list[str] = []
    try:
        from .bucket_manifest_state import current_bucket_manifest_dirty_token

        if current_bucket_manifest_dirty_token() is not None:
            reasons.append("out_of_band_security_change")
    except Exception:
        pass
    if manifest.get("security_version") != current_version:
        reasons.append("security_version_skew")
    scalar = manifest.get("trace_records") or {}
    object_count = scalar.get("object_count")
    if isinstance(object_count, int) and object_count != len(rows):
        reasons.append("incomplete_rows")

    # NOTE: unscanned rows are a SAFETY signal (surfaced as safety.unscanned_count
    # + a sync blocker), NOT a read-model-staleness signal — the read model
    # faithfully reports them. So they never mark freshness stale; they only set
    # ``understates_safety`` so a consumer knows the not-cleared count may be
    # optimistic (there could be redactable content hiding in an unscanned trace).
    stale = bool(reasons)
    understates = bool(
        unscanned
        or "incomplete_rows" in reasons
        or "out_of_band_security_change" in reasons
    )
    return {
        "stale": stale,
        "reasons": reasons,
        "source": "row_status",
        "understates_safety": understates,
        # A boolean flag; the terminating remediation verb ('opentraces bucket
        # repair') is named in next_steps - NEVER 'bucket manifest --heal'.
        "rebuild_recommended": stale,
        "last_full_sweep_at": manifest.get("generated_at"),
    }


def _by_project(rows: list[dict[str, Any]], *, top_n: int) -> tuple[list[dict[str, Any]], int]:
    """Per-project rollup, biggest-first, bounded to ``top_n`` rows."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        slug = str(row.get("project_slug") or "")
        groups.setdefault(slug, []).append(row)

    projects: list[dict[str, Any]] = []
    for slug, group_rows in groups.items():
        agg = _aggregate(group_rows)
        projects.append(
            {
                "project": slug,
                "trace_count": agg["trace_count"],
                "scanned_count": agg["scanned_count"],
                "unscanned_count": agg["unscanned_count"],
                "not_cleared_count": agg["not_cleared_count"],
                "syncable_count": agg["syncable_count"],
            }
        )
    # Biggest first (trace_count desc), then by not-cleared desc, then name for
    # a deterministic tie-break.
    projects.sort(key=lambda p: (-p["trace_count"], -p["not_cleared_count"], p["project"]))
    omitted = max(0, len(projects) - top_n)
    return projects[:top_n], omitted


def _verdict(trace_count: int, unscanned: int, not_cleared: int) -> str:
    if trace_count == 0:
        return "empty"
    # Safety-first: green ("clear") is impossible while any trace is unscanned.
    if unscanned or not_cleared:
        return "not_cleared"
    return "clear"


def _blocked_reasons(agg: dict[str, int], *, stale: bool) -> list[str]:
    reasons: list[str] = []
    if agg["unfiltered_count"]:
        reasons.append("unfiltered_records")
    if agg["security_stale_count"]:
        reasons.append("security_version_stale")
    if agg["unscanned_count"]:
        reasons.append("unscanned_records")
    if stale:
        reasons.append("read_model_stale")
    return reasons


def _next_steps(
    *,
    agg: dict[str, int],
    ready: bool,
    verdict: str,
    stale: bool,
    project: str | None,
) -> list[str]:
    """State-aware, runnable-as-written affordances.

    Every per-trace question is a COUNT + an ``opentraces bucket list ...``
    enumerate handoff (``bucket list`` lands in the next slice; the handoff
    strings are the contract regardless). Remediation names verbs that exist
    and terminate.
    """

    scope = f" --project {project}" if project else ""
    steps: list[str] = []
    if verdict == "empty":
        steps.append(
            "No traces captured yet - start a connected agent and traces are "
            "captured automatically."
        )
        return steps
    if agg["unscanned_count"]:
        steps.append(
            f"{agg['unscanned_count']} trace(s) not yet cleared for sync "
            f"(unscanned) - enumerate with 'opentraces bucket list{scope} "
            "--unscanned'."
        )
    if agg["unfiltered_count"]:
        steps.append(
            f"{agg['unfiltered_count']} trace(s) hold unresolved redactable "
            f"content - enumerate with 'opentraces bucket list{scope} "
            "--unfiltered'."
        )
    if agg["security_stale_count"]:
        steps.append(
            f"{agg['security_stale_count']} trace(s) were scanned by an older "
            f"security version - enumerate with 'opentraces bucket list{scope} "
            "--security-stale'."
        )
    if stale:
        steps.append(
            "The bucket read model is stale - rebuild it with 'opentraces "
            "bucket repair'."
        )
    if ready:
        steps.append(
            f"All {agg['trace_count']} trace(s) are cleared for sync - push with "
            "'opentraces bucket remote push'."
        )
    # Core navigation: status is the documented entry point of the affordance
    # graph, so it always names where to go from here regardless of safety state -
    # search, lineage, and dataset publication. Runnable as written.
    steps.append("Search captured traces with 'opentraces trace query <terms>'.")
    steps.append(
        "Explain a commit's trace lineage with "
        "'opentraces trail blame commit <sha>'."
    )
    steps.append(
        "Project traces into a dataset with 'opentraces dataset new' then "
        "'opentraces dataset run' / 'opentraces dataset publish'."
    )
    return steps


def _degraded(state: str, root: str, current_version: str) -> dict[str, Any]:
    """Envelope for an unusable persisted read model (too-large / error).

    Honest and conservative: ``ok`` is False (the dashboard cannot be trusted),
    nothing is reported clear/ready, and the remediation is ``bucket repair``.
    Never triggers a full scan.
    """

    return {
        "ok": False,
        "schema_version": FLEET_STATUS_SCHEMA_VERSION,
        "security_version": current_version,
        "store": {"root": root, "trace_count": None, "project_count": None},
        "ready": False,
        "safety": {
            "scanned_count": None,
            "unscanned_count": None,
            "not_cleared_count": None,
            "verdict": "unknown",
        },
        "sync": {"eligible": False, "blocked_reasons": ["read_model_unavailable"]},
        "fleet": {"by_project": [], "projects_omitted": 0},
        "freshness": {
            "stale": True,
            "reasons": [state],
            "source": "degraded",
            "understates_safety": True,
            "rebuild_recommended": True,
            "last_full_sweep_at": None,
        },
        "next_steps": [
            "The bucket read model is unavailable - rebuild it with 'opentraces "
            "bucket repair'."
        ],
    }


def build_fleet_status(
    *,
    project: str | None = None,
    top_n: int = FLEET_TOP_N,
) -> dict[str, Any]:
    """Build the ``opentraces.bucket.status.v1`` envelope. O(1) in bucket size.

    ``project`` scopes every count + the safety verdict to a single project
    slug; ``top_n`` bounds the ``fleet.by_project`` rollup.
    """

    from . import paths
    from .bucket_store import read_persisted_manifest_capped
    from ..security.version import SECURITY_VERSION

    root = str(paths.bucket_dir())
    state, manifest = read_persisted_manifest_capped()

    if state in ("too_large", "error"):
        return _degraded(state, root, SECURITY_VERSION)

    absent = state != "ok" or not isinstance(manifest, dict)
    if absent:
        # Absent manifest: a fresh/empty world. An empty bucket is a valid,
        # clean resting state; report it O(1) without ever scanning.
        manifest = {}
        rows: list[dict[str, Any]] = []
    else:
        rows = [r for r in (manifest.get("traces") or []) if isinstance(r, dict)]

    # Freshness is a whole-bucket read-model property; compute it against the
    # full row set BEFORE any --project scoping so a scoped view never mislabels
    # itself stale on the full manifest's object_count.
    all_rows = rows
    all_project_count = len({str(r.get("project_slug") or "") for r in all_rows})
    all_agg = _aggregate(all_rows)
    freshness = _freshness(
        manifest, all_rows, all_agg["unscanned_count"], SECURITY_VERSION, absent=absent
    )
    stale = freshness["stale"]

    if project is not None:
        rows = [r for r in all_rows if str(r.get("project_slug") or "") == project]

    agg = _aggregate(rows)

    verdict = _verdict(agg["trace_count"], agg["unscanned_count"], agg["not_cleared_count"])
    blocked = _blocked_reasons(agg, stale=stale)
    eligible = not blocked
    scanned_fraction = (agg["scanned_count"] / agg["trace_count"]) if agg["trace_count"] else 0.0
    ready = bool(
        agg["trace_count"] > 0
        and eligible
        and scanned_fraction >= READY_MIN_SCANNED_FRACTION
    )

    by_project, omitted = _by_project(rows, top_n=top_n)
    project_count = 1 if (project is not None and rows) else (0 if project is not None else all_project_count)

    next_steps = _next_steps(
        agg=agg, ready=ready, verdict=verdict, stale=stale, project=project
    )

    return {
        # ``ok`` (boolean) is the top-level health signal: the read succeeded and
        # is trustworthy. It is NOT readiness - a fully-computed bucket with
        # unscanned traces is ``ok: true, ready: false``.
        "ok": True,
        "schema_version": FLEET_STATUS_SCHEMA_VERSION,
        # Top-level for cross-sibling self-consistency (L6): equals bucket
        # manifest / bucket security status on the same bucket.
        "security_version": SECURITY_VERSION,
        "scope_project": project,
        "store": {
            "root": root,
            "trace_count": agg["trace_count"],
            "project_count": project_count,
        },
        "ready": ready,
        "safety": {
            "scanned_count": agg["scanned_count"],
            "unscanned_count": agg["unscanned_count"],
            "not_cleared_count": agg["not_cleared_count"],
            "verdict": verdict,
        },
        "sync": {"eligible": eligible, "blocked_reasons": blocked},
        "fleet": {"by_project": by_project, "projects_omitted": omitted},
        "freshness": freshness,
        "next_steps": next_steps,
    }
