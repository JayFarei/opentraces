"""Pure helper functions for ``ot trail`` — no Click decorators here."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import click

from ..clients.text.colors import Role, detect_color, paint, render_handle


def _parse_track_since(value: str) -> datetime:
    """Parse a duration like ``12h`` / ``30m`` / ``2d`` / ``60s`` or an ISO
    timestamp into a UTC ``datetime`` cutoff for batch ``trail track``.

    Mirrors the trace-index parser but adds ``s`` (seconds) so the batch
    surface can be exercised with short windows in tests.
    """
    stripped = value.strip()
    now = datetime.now(timezone.utc)
    duration = re.fullmatch(r"(\d+)([smhd])", stripped.lower())
    if duration:
        amount = int(duration.group(1))
        unit = duration.group(2)
        if unit == "s":
            return now - timedelta(seconds=amount)
        if unit == "m":
            return now - timedelta(minutes=amount)
        if unit == "h":
            return now - timedelta(hours=amount)
        return now - timedelta(days=amount)
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "--since must be an ISO date/time or duration like 12h, 30m, 2d, 60s"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_time_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _read_patches_from_file(path: Path) -> list[str]:
    """Accept either ``one id per line`` or JSONL with ``patch_id`` field."""
    text = path.read_text()
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            patch_id = (
                obj.get("patch_id")
                or obj.get("trace_patch_id")
                or obj.get("id")
            )
            if isinstance(patch_id, str) and patch_id and patch_id not in seen:
                out.append(patch_id)
                seen.add(patch_id)
            continue
        if line not in seen:
            out.append(line)
            seen.add(line)
    return out


def _collect_event_log_patch_ids(
    repo: Path,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return patch ids from the project's TrailEvent log.

    When ``since`` is provided, only Trace Patches whose ``event_time`` is
    on/after the cutoff are included. Order preserves event_sequence so
    the most recent patches appear last.

    When ``limit`` is provided, the returned list is bounded to the ``limit``
    MOST RECENT patches (the tail). This bounds the caller's per-patch git
    survival WORK, not just the emitted rows: the oldest patches are the most
    expensive to resolve (longest history to walk to HEAD) and the least
    interesting, so a recent-N budget is both fast and useful. Because
    ``read_events_scoped`` is event_sequence-ascending, the tail is the newest
    ``limit`` patches and their relative "most recent appear last" ordering is
    preserved.

    Plan 087 — scoped to ``trace_patch_created`` blobs via the raw-bytes
    prefilter, so enumerating every patch id parses a tiny fraction of a large
    event log (the ~500k ``git_anchor_search_completed`` + context events are
    never JSON-parsed) instead of the whole history. ``read_events_scoped``
    returns event_sequence-sorted, so ordering is unchanged.
    """
    from ..core.trails import read_events_scoped

    events = read_events_scoped(repo, event_types={"trace_patch_created"})
    out: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event.event_type != "trace_patch_created":
            continue
        if since is not None:
            event_dt = _event_time_to_dt(event.event_time)
            if event_dt is None or event_dt < since:
                continue
        patch_id = event.payload.get("trace_patch_id")
        if isinstance(patch_id, str) and patch_id and patch_id not in seen:
            out.append(patch_id)
            seen.add(patch_id)
    if limit is not None and limit >= 0 and len(out) > limit:
        out = out[-limit:]
    return out


def _emit_batch_track(
    repo: Path,
    patch_ids: list[str],
    *,
    history_limit: int | None,
) -> None:
    """Run ``sync_patch`` for every id and emit one JSON line per patch.

    Emitting JSONL here (not pretty-printed JSON) is the contract: each
    line is a wrapped sync payload, friendly to ``jq -s`` and to scripted
    summaries. The raw input id is preserved at top level so callers can
    correlate rows with their input list even after normalization. Errors
    per-patch are returned in ``error`` instead of crashing the whole batch.

    Reads the project's TrailEvent log once and threads the result into
    ``sync_patch`` so a batch of 100+ patches doesn't pay the read cost
    100+ times.

    Plan 087 — the single read is SCOPED to exactly the event types
    ``sync_patch`` consults (``trace_patch_created`` + ``git_anchor_created`` +
    the ``patch_survival_cached`` survival cache); on a large log this skips
    JSON-parsing the ~500k commit-keyed anchor-search events and all context
    events, bounding the read to a tiny fraction of the history. The threaded
    list is a strict superset of every event ``sync_patch`` reads, so survival
    output is byte-identical to a full ``read_events`` thread.

    Cluster F D1: each row also carries ``trace_id`` (looked up from the
    ``trace_patch_created`` event payload) so JSONL consumers can group
    rows by trace without a sidecar projection. Cluster F D2/D3: a
    ``lost_attribution_cache`` is threaded across all patches so a batch
    of patches on the same file pays one ``git log --diff-filter=D``
    lookup, not N.
    """
    from ..core.trails import read_events_scoped, sync_patch

    # The exact, complete set sync_patch + build_survival_cache_index read from
    # the threaded events list (verified against the sync.py call graph).
    _patch_sync_event_types = {
        "trace_patch_created",
        "git_anchor_created",
        "patch_survival_cached",
    }
    try:
        cached_events = read_events_scoped(
            repo, event_types=_patch_sync_event_types
        )
    except Exception:
        cached_events = None

    # Build trace_id lookup once (Cluster F D1).
    trace_id_for_patch: dict[str, str] = {}
    if cached_events is not None:
        for event in cached_events:
            if event.event_type != "trace_patch_created":
                continue
            payload_patch_id = event.payload.get("trace_patch_id")
            if not isinstance(payload_patch_id, str) or not payload_patch_id:
                continue
            tid = event.trace_id or event.payload.get("trace_id")
            if isinstance(tid, str) and tid:
                trace_id_for_patch.setdefault(payload_patch_id, tid)

    # Single attribution cache shared across the whole batch
    # (keyed by (path, anchor, head_sha)) so two lost patches on the
    # same file resolve their killer commit with one git log call.
    lost_attribution_cache: dict[tuple[str, str, str], tuple[str | None, str]] = {}

    for patch_id in patch_ids:
        try:
            payload = sync_patch(
                repo,
                patch_id,
                history_limit=history_limit,
                events=cached_events,
                lost_attribution_cache=lost_attribution_cache,
            )
        except Exception as exc:  # noqa: BLE001 — keep the batch alive
            payload = {
                "trace_patch_id": patch_id,
                "error": str(exc),
                "current_survival": {"survival_state": "unknown"},
            }
        if isinstance(payload, dict):
            # Always echo back the caller-supplied id so JSONL consumers
            # can group rows back to their input even when sync_patch
            # normalizes the canonical id.
            payload["trace_patch_id"] = patch_id
            # D1: trace_id at top level, looked up via the event log.
            tid = trace_id_for_patch.get(patch_id)
            if tid:
                payload["trace_id"] = tid
            else:
                payload.setdefault("trace_id", None)
        # Emit one JSON object per line (no indent) so jq -s can stream.
        click.echo(json.dumps(payload, sort_keys=True))


def _audit_trail_capture(repo: Path, trace_id: str) -> dict[str, Any]:
    """Compare ``file_edit`` events to ``trace_patch_created`` for one trace.

    Returns a dict with ``file_edits_count``, ``patch_created_count``, and
    ``incomplete`` (True when file_edits > 0 AND patch_created == 0). This
    is the C-4 signal that surfaces missing-capture bugs early.
    """
    from ..core.trails import read_events

    file_edits = 0
    patches = 0
    for event in read_events(repo):
        if event.trace_id != trace_id:
            continue
        if event.event_type == "file_edit":
            file_edits += 1
        elif event.event_type == "trace_patch_created":
            patches += 1
    return {
        "file_edits_count": file_edits,
        "patch_created_count": patches,
        "incomplete": file_edits > 0 and patches == 0,
    }


def _render_sync_summary(payload: dict[str, Any]) -> str:
    """Compact render of a sync_patch / sync_anchor payload."""
    current = payload.get("current_survival") or {}
    label = (
        _anchor_handle(payload, color=False)
        if payload.get("git_anchor_id")
        else _patch_handle(payload, color=False)
    )
    lines = [f"Trail track {label}"]
    lines.append(f"  survival: {current.get('survival_state') or 'unknown'}")
    path = current.get("path")
    line_range = current.get("range") or {}
    if path:
        lines.append(f"  at: {path}:{line_range.get('start_line') or '?'}")
    for limitation in (
        payload.get("trail_limitations") or current.get("limitations") or []
    ):
        lines.append(f"  limitation: {limitation}")
    return "\n".join(lines)


def _render_explain_step(payload: dict[str, Any]) -> str:
    """Compact render of an explain_trace_step payload (the --step branch)."""
    lines = [f"Trace {payload['trace_id']} {payload['step_id']}"]
    if payload.get("relation") == "anchored_in_git":
        anchor = payload.get("git_anchor") or {}
        lines.append(
            "  anchored in git: "
            f"{(anchor.get('commit_sha') or '')[:12]} "
            f"{anchor.get('path')}:{(anchor.get('range') or {}).get('start_line')}"
        )
        lines.append(
            f"  evidence: {payload.get('evidence_tier')} "
            f"({payload.get('evidence_firmness')})"
        )
    elif payload.get("patch_status") == "no_patch":
        lines.append("  patch status: no_patch")
        lines.append("  relation: no_patch")
    else:
        lines.append("  relation: unknown")
        for limitation in payload.get("limitations") or []:
            lines.append(f"  limitation: {limitation}")
    for claim in payload.get("unavailable_stronger_claims") or []:
        lines.append(f"  unavailable: {claim}")
    return "\n".join(lines)


def _load_trace_step_summaries(repo: Path, trace_id: str) -> dict[str, str]:
    from ..core.config import get_project_traces_dir

    try:
        traces_dir = get_project_traces_dir(repo)
    except Exception:
        return {}
    paths = [traces_dir / f"{trace_id}.jsonl"]
    # Per-id filename + prefix fast path instead of a full-dir scan: a present
    # trace is reached directly, and a miss returns empty rather than reading
    # every file in the traces dir.
    paths.extend(
        sorted(path for path in traces_dir.glob(f"{trace_id}*.jsonl") if path not in paths)
    )
    for path in paths:
        if not path.exists():
            continue
        try:
            first_line = path.read_text().splitlines()[0]
            record = json.loads(first_line)
        except Exception:
            continue
        if record.get("trace_id") != trace_id and record.get("session_id") != trace_id:
            continue
        summaries: dict[str, str] = {}
        for step in record.get("steps") or []:
            step_index = step.get("step_index")
            if step_index is None:
                continue
            content = _compact_text(step.get("content") or "")
            if content:
                summaries[f"s{step_index}"] = content
        return summaries
    return {}


def _compact_text(value: str, *, limit: int = 72) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _step_sort_key(step_id: str) -> tuple[int, str]:
    raw = step_id[1:] if step_id.startswith("s") else step_id
    return (int(raw), step_id) if raw.isdigit() else (sys.maxsize, step_id)


def _snapshot_ref_for_command(item: dict[str, Any]) -> str:
    snapshot_ref = item.get("snapshot_ref") or {}
    return snapshot_ref.get("ref") or item.get("snapshot_id") or "unknown"


def _commit_hex(item: dict[str, Any]) -> str | None:
    commit_id = item.get("commit_id") or {}
    if isinstance(commit_id, dict):
        return commit_id.get("hex")
    if isinstance(commit_id, str):
        return commit_id
    return None


def _trail_play_limitations(payload: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    limitations: list[str] = []
    for limitation in payload.get("limitations") or []:
        if limitation not in seen:
            limitations.append(str(limitation))
            seen.add(str(limitation))
    for item in payload.get("timeline") or []:
        for limitation in item.get("limitations") or []:
            if limitation not in seen:
                limitations.append(str(limitation))
                seen.add(str(limitation))
    return limitations


def _group_trail_play_steps(timeline: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in timeline:
        step_id = item.get("step_id") or "unscoped"
        grouped.setdefault(step_id, []).append(item)
    return grouped


def _meaningful_trail_play_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    meaningful = {
        "trace_snapshot_created",
        "trace_patch_created",
        "git_anchor_created",
        "patch_trail_observation_created",
        "trace_step_capture_incomplete",
    }
    return [item for item in items if item.get("event_type") in meaningful]


def _snapshot_handle(row: dict[str, Any], *, color: bool = False) -> str:
    token = f"snap:{_short_digest(row.get('snapshot_id'))}"
    if not color:
        return token
    prefix = paint(Role.ID_PREFIX, "snap:", use_color=True)
    body = paint(Role.TRACE_ID, token[5:], use_color=True)
    return f"{prefix}{body}"


def _step_label(step_id: str) -> str:
    return f"step:{step_id}"


def _tree_label(item: dict[str, Any]) -> str:
    tree_hex = ((item.get("tree_id") or {}).get("hex") or "")[:8]
    return f"tree:{tree_hex or 'unknown'}"


def _trail_play_anchors_by_patch(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    anchors: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("event_type") != "git_anchor_created":
            continue
        trace_patch_id = item.get("trace_patch_id")
        if trace_patch_id:
            anchors[str(trace_patch_id)] = item
    return anchors


def _trail_play_patch_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("trace_patch_id"))
        for item in items
        if item.get("event_type") == "trace_patch_created"
        and item.get("trace_patch_id")
    }


def _trail_play_children(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patch_ids = _trail_play_patch_ids(items)
    children: list[dict[str, Any]] = []
    for item in items:
        if (
            item.get("event_type") == "git_anchor_created"
            and item.get("trace_patch_id") in patch_ids
        ):
            continue
        children.append(item)
    return children


def _trail_play_landing_state(anchor: dict[str, Any] | None) -> str:
    if not anchor:
        return "unanchored"
    return _landing_state(
        {
            "git_anchor_id": anchor.get("git_anchor_id"),
            "commit_sha": _commit_hex(anchor),
            "evidence_tier": anchor.get("evidence_tier"),
        }
    )


def _trail_play_evidence(anchor: dict[str, Any] | None) -> str:
    if not anchor:
        return "unknown evidence · unknown"
    firmness = anchor.get("evidence_firmness") or "unknown"
    return f"{_human_evidence(anchor.get('evidence_tier'))} · {firmness}"


def _trail_play_status(timeline: list[dict[str, Any]]) -> str:
    if not timeline:
        return "missing timeline"
    snapshots = [
        item for item in timeline if item.get("event_type") == "trace_snapshot_created"
    ]
    noun = "snapshot" if len(snapshots) == 1 else "snapshots"
    return f"replayable · resumable from {len(snapshots)} {noun}"


def _trail_play_count_summary(timeline: list[dict[str, Any]]) -> str:
    snapshots = [
        item for item in timeline if item.get("event_type") == "trace_snapshot_created"
    ]
    patch_ids = _trail_play_patch_ids(timeline)
    anchors_by_patch = _trail_play_anchors_by_patch(timeline)
    anchored_count = len(patch_ids & set(anchors_by_patch))
    snapshot_noun = "snapshot" if len(snapshots) == 1 else "snapshots"
    parts = [f"{len(snapshots)} {snapshot_noun}"]
    if patch_ids:
        parts.append(
            _trace_patch_count_label(
                len(patch_ids),
                anchored=anchored_count == len(patch_ids),
            )
        )
    else:
        parts.append("0 Trace Patches")
    return " · ".join(parts)


def _append_trail_play_snapshot_lines(
    lines: list[str],
    item: dict[str, Any],
    *,
    prefix: str,
    branch: str,
    stem: str,
    trace_id: str,
    step_id: str,
) -> None:
    role = item.get("snapshot_role") or "after"
    detail_prefix = f"{prefix}{stem}  "
    lines.append(
        f"{prefix}{branch}◇ {_snapshot_handle(item)}  {_tree_label(item)}  {role}"
    )
    lines.append(
        f"{detail_prefix}checkout: opentraces trail snapshot checkout "
        f"{_snapshot_ref_for_command(item)}"
    )
    if role == "after":
        lines.append(
            f"{detail_prefix}resume: opentraces trail resume {trace_id} --at-step {step_id}"
        )


def _append_trail_play_patch_lines(
    lines: list[str],
    item: dict[str, Any],
    *,
    anchor: dict[str, Any] | None,
    prefix: str,
    branch: str,
    stem: str,
) -> None:
    detail_prefix = f"{prefix}{stem}  "
    path = item.get("file_path") or "unknown"
    lines.append(
        f"{prefix}{branch}◇ {_patch_handle(item, color=False)}  "
        f"{path}  {_trail_play_landing_state(anchor)}"
    )
    lines.append(f"{detail_prefix}│ evidence: {_trail_play_evidence(anchor)}")
    if anchor:
        commit = _commit_handle(_commit_hex(anchor), color=False)
        anchor_handle = _anchor_handle(anchor, color=False)
        lines.append(f"{detail_prefix}╰● {commit}  {anchor_handle}")
    else:
        lines.append(f"{detail_prefix}╰? no reliable landing")


def _render_trail_play_graph(repo: Path, payload: dict[str, Any]) -> str:
    trace_id = payload.get("trace_id") or "unknown"
    timeline = payload.get("timeline") or []
    workspace_source = payload.get("workspace_source") or {}
    step_summaries = _load_trace_step_summaries(repo, str(trace_id))
    workspace_id = workspace_source.get("workspace_id")
    workspace_label = workspace_id or workspace_source.get("type") or "local_project"
    source_repo = (
        "unavailable"
        if workspace_source.get("type") == "trace_workspace"
        else str(repo)
    )
    status = _trail_play_status(timeline)
    trace = _trace_handle(str(trace_id), color=False)

    lines = [
        f"Trace timeline for {trace}",
        f"Workspace: {workspace_label}",
        f"Source repo: {source_repo}",
        _trail_play_count_summary(timeline),
        "",
        f"╭◆ {trace}",
        f"│ status: {status}",
        f"│ workspace: {workspace_label} · source repo {source_repo}",
        "│",
    ]

    grouped = _group_trail_play_steps(timeline)
    step_ids = sorted(grouped, key=_step_sort_key)
    for step_position, step_id in enumerate(step_ids):
        summary = step_summaries.get(step_id)
        meaningful = _meaningful_trail_play_items(grouped[step_id])
        anchors_by_patch = _trail_play_anchors_by_patch(meaningful)
        children = _trail_play_children(meaningful)
        step_is_last = step_position == len(step_ids) - 1
        step_branch = "╰" if step_is_last else "├"
        step_prefix = "   " if step_is_last else "│  "
        step_title = _step_label(step_id)
        if summary:
            lines.append(f"{step_branch}◆ {step_title}  {summary}")
        else:
            lines.append(f"{step_branch}◆ {step_title}")

        for item_position, item in enumerate(children):
            child_is_last = item_position == len(children) - 1
            branch = "╰" if child_is_last else "├"
            stem = " " if child_is_last else "│"
            event_type = item.get("event_type")
            if event_type == "trace_snapshot_created":
                _append_trail_play_snapshot_lines(
                    lines,
                    item,
                    prefix=step_prefix,
                    branch=branch,
                    stem=stem,
                    trace_id=str(trace_id),
                    step_id=step_id,
                )
            elif event_type == "trace_patch_created":
                _append_trail_play_patch_lines(
                    lines,
                    item,
                    anchor=anchors_by_patch.get(str(item.get("trace_patch_id"))),
                    prefix=step_prefix,
                    branch=branch,
                    stem=stem,
                )
            elif event_type == "git_anchor_created":
                lines.append(
                    f"{step_prefix}{branch}● "
                    f"{_commit_handle(_commit_hex(item), color=False)}  "
                    f"{_anchor_handle(item, color=False)}  "
                    f"{_trail_play_evidence(item)}"
                )
            elif event_type == "trace_step_capture_incomplete":
                lines.append(
                    f"{step_prefix}{branch}? limitation trace_step_capture_incomplete"
                )
            elif event_type == "patch_trail_observation_created":
                state = item.get("survival_state") or "observed"
                lines.append(f"{step_prefix}{branch}◇ patch trail {state}")
        if not children:
            lines.append(f"{step_prefix}╰? no replayable events")
        if not step_is_last:
            lines.append("│")

    limitations = _trail_play_limitations(payload)
    if limitations:
        lines.append("")
        lines.append(f"Limitations: {', '.join(limitations)}")
    return "\n".join(lines)


def _trail_play_limitations_cell(item: dict[str, Any]) -> str:
    limitations = item.get("limitations") or []
    return ",".join(str(limitation) for limitation in limitations) or "-"


def _trail_play_table_row(
    seq: str,
    step: str,
    kind: str,
    obj: str,
    file_commit: str,
    evidence: str,
    limitations: str,
) -> str:
    return (
        f"{seq:<4} {step:<5} {kind:<13} {obj:<12} "
        f"{file_commit:<12} {evidence:<20} {limitations}"
    )


def _render_trail_play_table(payload: dict[str, Any]) -> str:
    rows = [
        "SEQ  STEP  KIND          OBJECT        FILE/COMMIT  "
        "EVIDENCE             LIMITATIONS"
    ]
    for item in _meaningful_trail_play_items(payload.get("timeline") or []):
        seq = str(item.get("event_sequence") or "")
        step = item.get("step_id") or "-"
        event_type = item.get("event_type")
        limitations = _trail_play_limitations_cell(item)
        if event_type == "trace_snapshot_created":
            tree_hex = ((item.get("tree_id") or {}).get("hex") or "")[:8] or "-"
            role = item.get("snapshot_role") or "after"
            capture_status = item.get("capture_status") or "unknown"
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "snapshot",
                    _snapshot_handle(item),
                    f"tree:{tree_hex}",
                    f"{role} {capture_status}",
                    limitations,
                )
            )
        elif event_type == "trace_patch_created":
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "trace_patch",
                    _patch_handle(item, color=False),
                    item.get("file_path") or "-",
                    "-",
                    limitations,
                )
            )
        elif event_type == "git_anchor_created":
            commit = (_commit_hex(item) or "")[:8] or "-"
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "git_anchor",
                    _anchor_handle(item, color=False),
                    f"c:{commit}",
                    _trail_play_evidence(item),
                    limitations,
                )
            )
        elif event_type == "patch_trail_observation_created":
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "patch_trail",
                    _patch_handle(item, color=False),
                    item.get("path") or "-",
                    item.get("survival_state") or "observed",
                    limitations,
                )
            )
        elif event_type == "trace_step_capture_incomplete":
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "limitation",
                    "-",
                    "-",
                    "trace_step_capture_incomplete",
                    limitations,
                )
            )
    return "\n".join(rows)


EVIDENCE_LABELS = {
    "exact_blob_hash": "exact blob match",
    "exact_range_hash": "exact range match",
    "git_clean_range_hash": "normalized range match",
    "patch_id": "patch-id match",
    "structural_symbol": "structural symbol match",
    "formatter_divergent": "formatter-divergent match",
    "overlapping_hunk": "overlapping hunk",
    "time_file_overlap": "weak time/file overlap",
    "orphan": "no reliable landing",
    "unknown": "unknown evidence",
}

SURVIVAL_GLYPHS = {
    "alive_on_path": "✓",
    "alive_moved": "↷",
    "alive_transformed": "~",
    "partially_preserved": "◐",
    "repaired": "!",
    "reverted": "×",
    "lost": "×",
    "unknown": "?",
}


def _short_digest(value: str | None, length: int = 8) -> str:
    if not value:
        return "unknown"
    tail = value.rsplit(":", 1)[-1]
    return tail[:length] or value[:length]


def _patch_handle(row: dict[str, Any], *, color: bool) -> str:
    token = f"tp:{_short_digest(row.get('trace_patch_id'))}"
    if not color:
        return token
    prefix = paint(Role.ID_PREFIX, "tp:", use_color=True)
    body = paint(Role.TRACE_ID, token[3:], use_color=True)
    return f"{prefix}{body}"


def _anchor_handle(row: dict[str, Any], *, color: bool) -> str:
    token = f"ga:{_short_digest(row.get('git_anchor_id'))}"
    if not color:
        return token
    prefix = paint(Role.ID_PREFIX, "ga:", use_color=True)
    body = paint(Role.TRACE_ID, token[3:], use_color=True)
    return f"{prefix}{body}"


def _trace_handle(row_or_trace_id: dict[str, Any] | str | None, *, color: bool) -> str:
    trace_id = (
        row_or_trace_id.get("trace_id")
        if isinstance(row_or_trace_id, dict)
        else row_or_trace_id
    )
    return render_handle("t", trace_id or "unknown", use_color=color)


def _commit_handle(row_or_sha: dict[str, Any] | str | None, *, color: bool) -> str:
    sha = (
        row_or_sha.get("commit_sha")
        if isinstance(row_or_sha, dict)
        else row_or_sha
    )
    return render_handle("c", sha or "unknown", use_color=color)


def _human_evidence(tier: str | None) -> str:
    return EVIDENCE_LABELS.get(tier or "unknown", (tier or "unknown").replace("_", " "))


def _landing_state(row: dict[str, Any]) -> str:
    if not row.get("git_anchor_id") or not row.get("commit_sha"):
        return "orphan"
    tier = row.get("evidence_tier")
    if tier in {"exact_blob_hash", "exact_range_hash", "patch_id"}:
        return "landed_exact"
    if tier == "git_clean_range_hash":
        return "landed_normalized"
    return "landed_divergent"


def _survival_state(row: dict[str, Any]) -> str:
    current = row.get("current_survival") or {}
    state = row.get("survival_state") or current.get("survival_state")
    if state:
        return state
    if row.get("commit_sha"):
        return "alive_on_path"
    return "unknown"


def _git_subject(repo: Path, sha: str | None) -> str:
    if not sha:
        return ""
    proc = subprocess.run(
        ["git", "show", "-s", "--format=%s", sha],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _render_search_table(results: list[dict[str, Any]], *, color: bool) -> str:
    lines = [
        "TRACE PATCH  STEP   FILE                            LANDING     EVIDENCE             STATE"
    ]
    for row in results:
        trace_patch = _patch_handle(row, color=color)
        step = str(row.get("step_index") if row.get("step_index") is not None else "?")
        path = row.get("file_path") or row.get("path") or "?"
        commit = _commit_handle(row, color=color) if row.get("commit_sha") else "-"
        evidence = _human_evidence(row.get("evidence_tier"))
        state = _survival_state(row)
        lines.append(
            f"{trace_patch:<12} {step:<6} {path[:31]:<31} {commit:<11} "
            f"{evidence[:19]:<19} {state}"
        )
    return "\n".join(lines)


def _trace_patch_count_label(count: int, *, anchored: bool | None = None) -> str:
    noun = f"{count} Trace Patch{'' if count == 1 else 'es'}"
    if anchored is True:
        return f"{noun} anchored in Git"
    if anchored is False:
        return f"{noun} not anchored in Git"
    return noun


def _trace_patch_branch_for_trace(
    row: dict[str, Any],
    *,
    color: bool,
    last: bool,
) -> list[str]:
    trace_patch = _patch_handle(row, color=color)
    commit = _commit_handle(row, color=color)
    survival = _survival_state(row)
    glyph = SURVIVAL_GLYPHS.get(survival, "?")
    evidence = _human_evidence(row.get("evidence_tier"))
    firmness = row.get("firmness") or row.get("evidence_firmness") or "unknown"
    path = row.get("file_path") or row.get("path") or "?"
    step = row.get("step_index") if row.get("step_index") is not None else "?"
    line_count = row.get("line_count") or 0
    count_text = f"+{line_count}" if line_count else "change"
    branch = "╰" if last else "├"
    stem = " " if last else "│"
    lines = [
        f"{branch}◇ {trace_patch}  {path}  {_landing_state(row)}",
        f"{stem}  │ step: {step} · {count_text}",
        f"{stem}  │ evidence: {evidence} · {firmness}",
    ]
    if row.get("commit_sha"):
        lines.append(f"{stem}  ╰● {commit}  {glyph} {survival}")
    else:
        lines.append(f"{stem}  ╰? no reliable landing")
    return lines


def _trace_patch_branch_for_commit(
    row: dict[str, Any],
    *,
    color: bool,
    last: bool,
) -> list[str]:
    trace_patch = _patch_handle(row, color=color)
    trace = _trace_handle(row, color=color)
    evidence = _human_evidence(row.get("evidence_tier"))
    firmness = row.get("firmness") or row.get("evidence_firmness") or "unknown"
    path = row.get("file_path") or row.get("path") or "?"
    step = row.get("step_index") if row.get("step_index") is not None else "?"
    survival = _survival_state(row)
    branch = "╰" if last else "├"
    stem = " " if last else "│"
    return [
        f"{branch}◇ {trace_patch}  {path}  {_landing_state(row)}  {survival}",
        f"{stem}  │ evidence: {evidence} · {firmness}",
        f"{stem}  ╰◆ {trace}  step {step}",
    ]


def _trace_patch_branch_for_path(
    row: dict[str, Any],
    *,
    color: bool,
    last: bool,
) -> list[str]:
    trace_patch = _patch_handle(row, color=color)
    trace = _trace_handle(row, color=color)
    commit = _commit_handle(row, color=color)
    evidence = _human_evidence(row.get("evidence_tier"))
    firmness = row.get("firmness") or row.get("evidence_firmness") or "unknown"
    path = row.get("file_path") or row.get("path") or "?"
    step = row.get("step_index") if row.get("step_index") is not None else "?"
    survival = _survival_state(row)
    branch = "╰" if last else "├"
    stem = " " if last else "│"
    return [
        f"{branch}◇ {trace_patch}  {path}  {_landing_state(row)}  {survival}",
        f"{stem}  │ step: {step}",
        f"{stem}  │ evidence: {evidence} · {firmness}",
        f"{stem}  ├◆ {trace}",
        f"{stem}  ╰● {commit}",
    ]


def _render_trace_search(
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    trace_id = query.get("trace_id") or ""
    if table:
        lines = [
            f"Trace trail for {_trace_handle(trace_id, color=color)}",
            "",
            _trace_patch_count_label(len(results)),
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)

    trace = _trace_handle(trace_id or results[0].get("trace_id"), color=color)
    anchored_count = sum(1 for row in results if row.get("commit_sha"))
    anchored_label = (
        _trace_patch_count_label(len(results), anchored=True)
        if anchored_count == len(results)
        else _trace_patch_count_label(len(results))
    )
    lines = [
        f"Trace trail for {trace}",
        "",
        anchored_label,
        "",
        f"╭◆ {trace}",
        "│",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_trace(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("│")
    first = results[0]
    first_step = (
        first.get("step_index") if first.get("step_index") is not None else "?"
    )
    lines.extend(
        [
            "",
            "Next:",
            f"  otd trail explain --trace {first.get('trace_id')} --step {first_step}",
            f"  otd trail sync --patch {first.get('trace_patch_id')}",
        ]
    )
    return "\n".join(lines)


def _render_commit_search(
    repo: Path,
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    commit_sha = query.get("commit_sha") or ""
    commit = _commit_handle(commit_sha, color=color)
    subject = _git_subject(repo, commit_sha)
    trace_count = len({row.get("trace_id") for row in results if row.get("trace_id")})
    file_count = len({row.get("file_path") for row in results if row.get("file_path")})
    if table:
        lines = [
            f"Trace trail evidence in {query.get('commit')}",
            f"Resolved {query.get('commit')} -> {commit}",
            "",
            (
                f"{len(results)} anchored Trace Patch"
                f"{'' if len(results) == 1 else 'es'} · "
                f"{trace_count} trace{'' if trace_count == 1 else 's'} · "
                f"{file_count} file{'' if file_count == 1 else 's'}"
            ),
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)

    row = results[0]
    lines = [
        f"Trace trail evidence in {query.get('commit')}",
        f"Resolved {query.get('commit')} -> {commit}",
        "",
        (
            f"{len(results)} anchored Trace Patch"
            f"{'' if len(results) == 1 else 'es'} · "
            f"{trace_count} trace{'' if trace_count == 1 else 's'} · "
            f"{file_count} file{'' if file_count == 1 else 's'}"
        ),
        "",
        f"● {commit}  {subject}",
        "│",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_commit(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("│")
    return "\n".join(lines)


def _render_path_search(
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    path = query.get("path") or "?"
    if table:
        lines = [
            f"Trace trail for path {path}",
            "",
            (
                f"{len(results)} committed Trace Patch"
                f"{'' if len(results) == 1 else 'es'} touching this file"
            ),
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)

    row = results[0]
    lines = [
        f"Trace trail for path {path}",
        "",
        (
            f"{len(results)} committed Trace Patch"
            f"{'' if len(results) == 1 else 'es'} touching this file"
        ),
        "",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_path(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("│")
    first = results[0]
    first_step = (
        first.get("step_index") if first.get("step_index") is not None else "?"
    )
    lines.extend(
        [
            "",
            "Next:",
            f"  otd trail explain --trace {first.get('trace_id')} --step {first_step}",
        ]
    )
    return "\n".join(lines)


def _render_survival_search(
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    survival = query.get("survival") or "unknown"
    if table:
        lines = [
            f"Trace trails with survival {survival}",
            "",
            f"{len(results)} Trace Patch{'' if len(results) == 1 else 'es'}",
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)
    lines = [
        f"Trace trails with survival {survival}",
        "",
        f"{len(results)} Trace Patch{'' if len(results) == 1 else 'es'}",
        "",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_path(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("│")
    return "\n".join(lines)


def _render_empty_search(
    query: dict[str, str | None],
    limitations: list[str],
    *,
    color: bool,
) -> str:
    qtype = query.get("type")
    if qtype == "patches_per_trace":
        lines = [
            f"Trace trail for {_trace_handle(query.get('trace_id'), color=color)}",
            "",
            "No committed Trace Patches found.",
            "",
            "Possible reasons:",
            "  - this was a research-only or review session",
            "  - the work has not landed in local Git history",
            "  - the patch was transformed beyond current matching thresholds",
            "  - TrailEvents are unavailable for this project",
        ]
    elif qtype == "anchors_per_commit":
        lines = [
            f"Trace trail evidence in {query.get('commit')}",
            "",
            "No anchored Trace Patches found for this commit.",
        ]
    elif qtype == "patches_touching_file":
        lines = [
            f"Trace trail for path {query.get('path')}",
            "",
            "No committed Trace Patches touching this file were found.",
        ]
    else:
        lines = [
            f"Trace trails with survival {query.get('survival')}",
            "",
            "No Trace Patches found in this survival state.",
        ]
    for limitation in limitations:
        lines.append(f"  limitation: {limitation}")
    return "\n".join(lines)


def _render_search_results(
    repo: Path,
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    limitations: list[str],
    *,
    color: bool,
    graph_mode: bool,
    table_mode: bool,
) -> str:
    if not results:
        return _render_empty_search(query, limitations, color=color)
    table = table_mode
    qtype = query.get("type")
    if qtype == "patches_per_trace":
        return _render_trace_search(query, results, color=color, table=table)
    if qtype == "anchors_per_commit":
        return _render_commit_search(repo, query, results, color=color, table=table)
    if qtype == "patches_touching_file":
        return _render_path_search(query, results, color=color, table=table)
    return _render_survival_search(query, results, color=color, table=table)
