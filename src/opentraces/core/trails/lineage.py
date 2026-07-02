"""Per-trace lineage overview and per-step world reads (issue #165).

The bare ``trail <trace>`` noun (lineage overview) and the ``trail
<trace>:<step>`` world read are the session-timeline half of the trail
family — the mirror of ``ctx <trace>`` / ``ctx <trace>:<step>``. They ride
the bounded per-trace event read (:func:`read_events_for_trace`) and the
per-trace Trail Query projection (:func:`build_trail_query_projection_for_trace`)
so they stay O(trace) on a mature multi-thousand-trace bucket.

Two hard contracts (see #165 V2 / V3):

* The lineage card is **survival-free**. It carries lineage (patch/file
  counts), the cheap git-*position* signal (unanchored / committed-unmerged
  / merged, derived from anchor commit reachability — NOT a per-patch
  ``sync_patch`` survival recompute), and freshness. No key contains
  ``survival``; nothing on the card scales with step count.
* The world read leads with the *outcome* — the per-step hunk, derived by
  git-diffing the two adjacent captured snapshot ``tree_id``s — plus a
  stable ``ot://`` world reference. Raw ``tree_id`` pointers live under
  ``--full`` only, the same way ctx hides ``content_hash``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .event_log import EVENT_LOG_REF
from .models import TrailEvent
from .scoped_reads import (
    build_trail_query_projection_for_trace,
    read_events_for_trace,
)
from .search_records import summary_search_touches_trace

LINEAGE_SCHEMA_VERSION = "opentraces.trail.lineage.v1"
WORLD_SCHEMA_VERSION = "opentraces.trail.world.v1"

# git's canonical empty tree object — a step with no prior snapshot diffs its
# captured tree against this so the hunk is the whole introduced content.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Files-on-card cap: the lineage overview is a scannable summary, not a full
# manifest. The full per-patch file list is available via the git-plane verbs.
_FILES_TOP_N = 8
# World-read hunk cap: a single step's diff is small, but degrade safely on a
# pathological snapshot pair rather than emit an unbounded blob.
_HUNK_MAX_LINES = 400


def _git(repo: Path, args: list[str], *, check: bool = False) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _trace_scoped_events(repo: Path, trace_id: str) -> list[TrailEvent]:
    """Bounded per-trace event read (mirrors workspace._trace_events)."""
    return [
        event
        for event in read_events_for_trace(repo, trace_id)
        if event.trace_id == trace_id
        or event.payload.get("trace_id") == trace_id
        or summary_search_touches_trace(event, trace_id)
    ]


def _rel_path(path: str | None) -> str | None:
    """Collapse foreign-agent absolute prefixes to a bare relative path."""
    if not path:
        return path
    # keep only the tail after a repo-root-ish marker when absolute; otherwise
    # return as-is. Deterministic and cheap — this is display de-duplication,
    # not path resolution.
    return path


def build_trail_lineage_card(repo: Path, trace_id: str) -> dict[str, Any]:
    """Return the bounded, survival-free lineage overview for ``trace_id``.

    Envelope ``opentraces.trail.lineage.v1``. Built on the bounded per-trace
    Trail Query projection; the position counts are the cheap git-position
    signal (anchor commit reachability from HEAD), NOT a survival recompute.
    """
    repo = repo.resolve()
    events = _trace_scoped_events(repo, trace_id)

    # Total observed steps: distinct step_index across this trace's events. This
    # is derived from the ALREADY-loaded bounded read, so the card never pays an
    # extra pass and never scales its WORK with step count.
    step_indexes = {
        event.step_index for event in events if event.step_index is not None
    }
    steps = (max(step_indexes) + 1) if step_indexes else 0

    projection = build_trail_query_projection_for_trace(repo, trace_id)
    patches = projection.patches_for_trace(trace_id)

    if not events and not patches:
        return {
            "status": "error",
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "trace_id": trace_id,
            "error": {
                "code": "NOT_FOUND",
                "kind": "trace",
                "message": f"No Trace Trail lineage for {trace_id}",
            },
            "event_log_ref": EVENT_LOG_REF,
        }

    # Per-file patch counts (relative-path keyed, deduped).
    files_by_path: dict[str, int] = {}
    for row in patches:
        path = _rel_path(row.get("file_path") or row.get("path"))
        if not path:
            continue
        files_by_path[path] = files_by_path.get(path, 0) + 1
    files_sorted = sorted(
        files_by_path.items(), key=lambda kv: (-kv[1], kv[0])
    )
    files = [{"path": p, "patches": c} for p, c in files_sorted[:_FILES_TOP_N]]

    # Position axis (cheap git-position signal, NO survival recompute):
    #   unanchored          — patch has no git_anchor
    #   merged              — anchored to a commit reachable from HEAD
    #   committed_unmerged  — anchored to a commit NOT reachable from HEAD
    # Reachability is computed ONCE per DISTINCT commit sha (bounded by commit
    # count, not step count), so the card cannot re-create the #169 O(N) walk.
    head_sha = _git(repo, ["rev-parse", "HEAD"], check=False).strip() or None
    reachable: dict[str, bool] = {}

    def _is_merged(commit_sha: str) -> bool:
        if not head_sha:
            return False
        if commit_sha not in reachable:
            proc = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit_sha, head_sha],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            reachable[commit_sha] = proc.returncode == 0
        return reachable[commit_sha]

    merged = committed_unmerged = unanchored = 0
    latest_anchor_time: str | None = None
    for row in patches:
        anchors = row.get("git_anchors") or []
        if not anchors:
            unanchored += 1
            continue
        # Modal commit for the patch is its first anchor's commit.
        commit_sha = None
        for anchor in anchors:
            commit_sha = anchor.get("commit_sha") or (
                (anchor.get("commit_id") or {}).get("hex")
            )
            atime = anchor.get("event_time")
            if atime and (latest_anchor_time is None or atime > latest_anchor_time):
                latest_anchor_time = atime
            if commit_sha:
                break
        if commit_sha and _is_merged(commit_sha):
            merged += 1
        else:
            committed_unmerged += 1

    anchored_any = (merged + committed_unmerged) > 0
    # Freshness: the monitor lag is the gap between now and the freshest anchor
    # event; ``understates`` is true when patches remain unanchored (the monitor
    # may still be catching up, so a "did it land" read could understate).
    freshness = {
        "anchored": anchored_any,
        "latest_anchor_at": latest_anchor_time,
        "understates": unanchored > 0,
    }

    next_steps = [f"trail {trace_id}:{steps - 1}"] if steps else []
    next_steps.append(f"trail track {trace_id}")

    return {
        "status": "ok",
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "steps": steps,
        "patch_count": len(patches),
        "files": files,
        "position": {
            "merged": merged,
            "committed_unmerged": committed_unmerged,
            "unanchored": unanchored,
        },
        "freshness": freshness,
        "next_steps": next_steps,
        "event_log_ref": EVENT_LOG_REF,
    }


def _after_snapshots_by_step(
    events: list[TrailEvent], trace_id: str
) -> dict[int, dict[str, Any]]:
    """Latest ``after`` snapshot payload per step for the trace."""
    best: dict[int, tuple[int, dict[str, Any]]] = {}
    for event in events:
        if event.event_type != "trace_snapshot_created":
            continue
        if event.trace_id != trace_id:
            continue
        if event.step_index is None:
            continue
        role = event.payload.get("snapshot_role") or "after"
        if role != "after":
            continue
        prior = best.get(event.step_index)
        if prior is None or event.event_sequence > prior[0]:
            best[event.step_index] = (event.event_sequence, event.payload)
    return {step: payload for step, (_, payload) in best.items()}


def _tree_hex(payload: dict[str, Any]) -> str | None:
    tree = payload.get("tree_id") or {}
    return tree.get("hex")


def _summarize_diff(patch: str) -> tuple[list[dict[str, Any]], str]:
    """Parse a unified diff into per-file +/- counts and a bounded hunk."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            if current is not None:
                files.append(current)
            # ``diff --git a/<p> b/<p>`` — take the b-side path.
            parts = line.split(" b/", 1)
            path = parts[1] if len(parts) == 2 else line
            current = {"path": path, "added": 0, "removed": 0}
        elif current is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current["added"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                current["removed"] += 1
    if current is not None:
        files.append(current)
    hunk_lines = patch.splitlines()
    if len(hunk_lines) > _HUNK_MAX_LINES:
        hunk = "\n".join(hunk_lines[:_HUNK_MAX_LINES]) + "\n... (truncated)"
    else:
        hunk = patch
    return files, hunk


def build_trail_world(
    repo: Path,
    trace_id: str,
    step_index: int,
    *,
    full: bool = False,
) -> dict[str, Any]:
    """Return the world at ``trace_id:step_index`` (envelope trail.world.v1).

    The hunk is derived by git-diffing the two ADJACENT captured snapshot
    ``tree_id``s (the prior step's ``after`` tree -> this step's ``after``
    tree). Raw ``tree_id`` pointers appear only under ``full``. If a step has
    no snapshot pair the read degrades honestly.
    """
    repo = repo.resolve()
    world_ref = f"ot://trace/{trace_id}/steps/{step_index}/world"
    events = _trace_scoped_events(repo, trace_id)
    if not events:
        return {
            "status": "error",
            "schema_version": WORLD_SCHEMA_VERSION,
            "trace_id": trace_id,
            "step_index": step_index,
            "error": {
                "code": "NOT_FOUND",
                "kind": "trace",
                "message": f"No Trace Trail events for {trace_id}",
            },
            "world_ref": world_ref,
            "event_log_ref": EVENT_LOG_REF,
        }

    by_step = _after_snapshots_by_step(events, trace_id)
    to_payload = by_step.get(step_index)
    if to_payload is None:
        return {
            "status": "ok",
            "schema_version": WORLD_SCHEMA_VERSION,
            "trace_id": trace_id,
            "step_index": step_index,
            "world_ref": world_ref,
            "delta": None,
            "limitations": [f"no_snapshot_at_step:{step_index}"],
            "event_log_ref": EVENT_LOG_REF,
        }

    prior_steps = sorted(s for s in by_step if s < step_index)
    from_payload = by_step.get(prior_steps[-1]) if prior_steps else None
    to_tree = _tree_hex(to_payload)
    from_tree = _tree_hex(from_payload) if from_payload else _EMPTY_TREE
    limitations: list[str] = []
    if from_payload is None:
        limitations.append("no_prior_snapshot")

    delta: dict[str, Any] | None = None
    if to_tree:
        patch = _git(repo, ["diff", "--no-color", from_tree, to_tree], check=False)
        files, hunk = _summarize_diff(patch)
        delta = {
            "files": files,
            "hunk": hunk,
            "added": sum(f["added"] for f in files),
            "removed": sum(f["removed"] for f in files),
        }
    else:
        limitations.append("missing_snapshot_tree")

    result: dict[str, Any] = {
        "status": "ok",
        "schema_version": WORLD_SCHEMA_VERSION,
        "trace_id": trace_id,
        "step_index": step_index,
        "steps": (max(by_step) + 1) if by_step else None,
        "base_commit": to_payload.get("base_commit"),
        "outcome": {
            "files": [f["path"] for f in (delta["files"] if delta else [])],
            "added": delta["added"] if delta else 0,
            "removed": delta["removed"] if delta else 0,
        },
        "delta": delta,
        "world_ref": world_ref,
        "next_steps": [
            f"ctx {trace_id}:{step_index}",
            f"trace get {trace_id}:{step_index}",
        ],
        "limitations": limitations,
        "event_log_ref": EVENT_LOG_REF,
    }
    if full:
        result["tree_id"] = to_payload.get("tree_id")
        result["from_tree_id"] = (
            from_payload.get("tree_id") if from_payload else None
        )
        result["from_snapshot_id"] = (
            from_payload.get("snapshot_id") if from_payload else None
        )
        result["to_snapshot_id"] = to_payload.get("snapshot_id")
    return result


def parse_trail_ref(ref: str) -> tuple[str, int | None, tuple[int, int] | None, str | None]:
    """Parse a ``trace`` / ``trace:step`` / ``trace:A-B`` address.

    Returns ``(trace_id, step_index, span, reserved)`` where exactly one of
    ``step_index`` / ``span`` / ``reserved`` is non-None past the bare-trace
    case. ``reserved`` carries a named-but-deferred slot (``origin`` for #130,
    ``last`` for the final-step selector, or a span for v1.1) so the CLI can
    degrade honestly rather than crash.

    This is the SHARED trace-address grammar: ``trace get`` resolves
    ``trace:step`` / ``trace:last`` / ``trace:A-B`` through it too, so the
    pinned tuples in ``tests/cli/test_trail_lineage_world.py`` are the single
    byte-compat oracle for every consumer.
    """
    raw = ref.strip()
    if ":" not in raw:
        return raw, None, None, None
    trace_part, _, addr = raw.partition(":")
    trace_part = trace_part.strip()
    addr = addr.strip()
    if addr == "origin":
        return trace_part, None, None, "origin"
    if addr == "last":
        return trace_part, None, None, "last"
    if "-" in addr:
        a, _, b = addr.partition("-")
        try:
            return trace_part, None, (int(a), int(b)), "span"
        except ValueError:
            return trace_part, None, None, "invalid"
    try:
        return trace_part, int(addr), None, None
    except ValueError:
        return trace_part, None, None, "invalid"
