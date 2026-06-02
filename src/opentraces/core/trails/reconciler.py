"""Reconciler for watcher observations and step windows.

The Phase 5 reconciler consumes ``filesystem_mutation_observed`` events
together with the ``trace_step_window_opened`` and
``trace_step_window_closed`` events emitted by agent hooks since Phase 2,
and produces attribution under unambiguous conditions only:

* mutation interval fully inside exactly one writer's firm step window →
  emit/upgrade ``trace_patch_created`` with ``capture_method`` extended to
  include ``watcher_backstop``;
* mutation overlaps multiple writers' windows → record
  ``concurrent_writer_overlap``;
* mutation outside any open step window → record
  ``unbounded_mutation_window``;
* mutation inside a window but visible to background processes → record
  ``background_process_overlap``.

Only windows whose payload reports ``boundary_firmness == "firm"`` are
candidates; soft / reconstructed-after-the-fact windows are invisible to
attribution per plan §Phase 5.

The reconciler is idempotent: re-running on the same event set produces the
same attributions. Idempotency is guaranteed by emitting one
``watcher_observation_attributed`` event per processed observation, keyed by
``observation_event_id``. Subsequent runs skip observations whose
attribution event already exists.

Concurrency: a process-level file lock at ``.git/opentraces-reconciler.lock``
serializes runs against the same repo so two concurrent reconcilers cannot
double-emit attribution for the same observation. The append-only event
log's CAS retry handles inter-batch ordering; the lock handles inter-run
planning.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import os
import posixpath
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ...enrichment.attribution import _norm, _parse_diff_hunks_with_content
from .capture_limitations import (
    BACKGROUND_PROCESS_OVERLAP,
    CONCURRENT_WRITER_OVERLAP,
    UNBOUNDED_MUTATION_WINDOW,
)
from .event_log import append_event_batch, read_events
from .ids import (
    SNAPSHOT_CANONICALIZATION,
    TRACE_PATCH_CANONICALIZATION,
    content_ref,
    trace_patch_ref,
    trace_snapshot_ref,
)
from .models import TrailEvent, TrailEventDraft, sha256_text

RECONCILER_CAPTURE_METHOD = ["watcher_backstop"]
RECONCILER_WRITER = "watcher-reconciler"
RECONCILER_LOCK_BASENAME = "opentraces-reconciler.lock"


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _interval_within(inner: tuple[datetime, datetime], outer: tuple[datetime, datetime]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _normalize_path(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return value
    return posixpath.normpath(value.replace("\\", "/"))


def _id(prefix: str, material: dict[str, Any]) -> str:
    kind = {
        "snapshot": "trace_snapshot",
        "tracepatch": "trace_patch",
    }.get(prefix, prefix)
    canonicalization = (
        SNAPSHOT_CANONICALIZATION
        if kind == "trace_snapshot"
        else TRACE_PATCH_CANONICALIZATION
    )
    return content_ref(
        kind=kind,
        canonicalization=canonicalization,
        material=material,
    )["id"]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _object_hex(value: Any) -> str | None:
    if isinstance(value, dict):
        hex_value = value.get("hex")
        return hex_value.lower() if isinstance(hex_value, str) else None
    return None


def _same_object(left: Any, right: Any) -> bool:
    left_hex = _object_hex(left)
    right_hex = _object_hex(right)
    return left_hex is not None and left_hex == right_hex


def _window_key(event: TrailEvent) -> tuple[str, int, int, str | None] | None:
    if event.trace_id is None or event.step_index is None:
        return None
    tool_call_id = event.payload.get("tool_call_id")
    return (
        event.trace_id,
        event.generation_index or 0,
        event.step_index,
        str(tool_call_id) if tool_call_id is not None else None,
    )


def _patch_blob_matches_observation(
    patch_event: TrailEvent,
    observation: TrailEvent,
) -> bool:
    """Return true when watcher blob evidence matches a hook patch.

    Missing watcher ``before_blob_id`` can still corroborate an append-only
    observation when ``after_blob_id`` matches. A mismatched provided blob ID
    means this is a different mutation and must not upgrade the hook patch.
    """
    patch = patch_event.payload
    obs = observation.payload
    checked = False
    for key in ("before_blob_id", "after_blob_id"):
        obs_value = obs.get(key)
        if obs_value is None:
            continue
        checked = True
        if not _same_object(obs_value, patch.get(key)):
            return False
    return checked


def _blob_text(repo: Path, blob_id: Any) -> str:
    blob_hex = _object_hex(blob_id)
    if not blob_hex:
        return ""
    proc = subprocess.run(
        ["git", "show", blob_hex],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or b"\x00" in proc.stdout:
        return ""
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _watcher_added_text(repo: Path, observation: TrailEvent) -> tuple[str, dict[str, int | None]]:
    before_hex = _object_hex(observation.payload.get("before_blob_id"))
    after_hex = _object_hex(observation.payload.get("after_blob_id"))
    if before_hex and after_hex:
        diff = _git(repo, "diff", "--no-color", before_hex, after_hex, check=False)
        hunks = _parse_diff_hunks_with_content(diff)
        added_parts: list[str] = []
        first_start: int | None = None
        last_end: int | None = None
        for file_hunks in hunks.values():
            for hunk in file_hunks:
                added = hunk.get("added_text") or ""
                if added:
                    added_parts.append(added if added.endswith("\n") else f"{added}\n")
                start = hunk.get("added_start")
                end = hunk.get("added_end")
                if isinstance(start, int) and first_start is None:
                    first_start = start
                if isinstance(end, int):
                    last_end = end
        return "".join(added_parts), {
            "start_line": first_start,
            "end_line": last_end,
        }
    authored = _blob_text(repo, observation.payload.get("after_blob_id"))
    if not authored:
        return "", {"start_line": None, "end_line": None}
    return authored, {
        "start_line": 1,
        "end_line": len(authored.splitlines()) or 1,
    }


@contextlib.contextmanager
def _reconciler_lock(repo: Path) -> Iterator[None]:
    """Serialize reconciler runs per-repo using ``fcntl.flock``.

    On platforms without ``fcntl`` (Windows), this falls back to a no-op
    and documents the single-writer assumption. The Phase 5 substrate is
    Unix-first; Windows hardening can come when there is a consumer that
    needs it.
    """
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        yield
        return
    lock_path = git_dir / RECONCILER_LOCK_BASENAME
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:
        yield
        return
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            if exc.errno not in {errno.ENOLCK, errno.ENOTSUP}:
                raise
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _index_events(events: list[TrailEvent]) -> dict[str, Any]:
    """Bucket events by type for the reconciler's attribution decisions.

    Windows missing a ``trace_id`` or ``step_index`` are skipped: a window
    without writer identity cannot be a unique-and-unambiguous match.
    """
    observations: list[TrailEvent] = []
    windows_open: dict[tuple[str, int, int, str | None], TrailEvent] = {}
    windows_close: dict[tuple[str, int, int, str | None], TrailEvent] = {}
    patches: list[TrailEvent] = []
    attributed: set[str] = set()
    upgraded_patches: set[str] = set()
    for event in events:
        if event.event_type == "filesystem_mutation_observed":
            observations.append(event)
        elif event.event_type == "trace_step_window_opened":
            key = _window_key(event)
            if key is None:
                continue
            windows_open[key] = event
        elif event.event_type == "trace_step_window_closed":
            key = _window_key(event)
            if key is None:
                continue
            windows_close[key] = event
        elif event.event_type == "trace_patch_created":
            if event.trace_id is None or event.step_index is None:
                continue
            file_path = _normalize_path(event.payload.get("file_path"))
            if file_path is None:
                continue
            patches.append(event)
            if "watcher_backstop" in event.capture_method:
                trace_patch_id = event.payload.get("trace_patch_id")
                if trace_patch_id:
                    upgraded_patches.add(trace_patch_id)
        elif event.event_type == "watcher_observation_attributed":
            obs_id = event.payload.get("observation_event_id")
            if obs_id:
                attributed.add(obs_id)
    return {
        "observations": observations,
        "windows_open": windows_open,
        "windows_close": windows_close,
        "patches": patches,
        "attributed": attributed,
        "upgraded_patches": upgraded_patches,
    }


def _matching_windows(
    observation: TrailEvent,
    windows_open: dict[tuple, TrailEvent],
    windows_close: dict[tuple, TrailEvent],
) -> list[tuple[tuple, TrailEvent, TrailEvent]]:
    """Return all ``(key, opened, closed)`` tuples that contain the obs.

    Only windows with both an opened and a closed event AND
    ``boundary_firmness == "firm"`` on both boundaries are candidates per
    plan §Phase 5 ("fully inside exactly one writer's firm step window").
    Open-but-unclosed windows and soft / reconstructed-after-the-fact
    windows are invisible to attribution.
    """
    payload = observation.payload
    obs_interval = (
        _parse_iso(payload["observed_at_start"]),
        _parse_iso(payload["observed_at_end"]),
    )
    matches: list[tuple[tuple, TrailEvent, TrailEvent]] = []
    for key, opened in windows_open.items():
        closed = windows_close.get(key)
        if closed is None:
            continue
        if (
            opened.payload.get("boundary_firmness") != "firm"
            or closed.payload.get("boundary_firmness") != "firm"
        ):
            continue
        win_interval = (_parse_iso(opened.event_time), _parse_iso(closed.event_time))
        if _interval_within(obs_interval, win_interval):
            matches.append((key, opened, closed))
    return matches


def _declares_path(window: TrailEvent, path: str | None) -> bool:
    if not path:
        return False
    declared = window.payload.get("declared_write_paths")
    if not isinstance(declared, list):
        return False
    normalized = {_normalize_path(item) for item in declared if isinstance(item, str)}
    return path in normalized


def _is_direct_path_writer(window: TrailEvent) -> bool:
    tool_name = window.payload.get("tool_name")
    if not isinstance(tool_name, str):
        return False
    return tool_name.lower() in {"edit", "write", "multiedit", "notebookedit"}


def _has_declared_path_scope(window: TrailEvent) -> bool:
    declared = window.payload.get("declared_write_paths")
    return isinstance(declared, list) and any(
        isinstance(item, str) and item for item in declared
    )


def _narrow_matches_by_declared_path(
    matches: list[tuple[tuple, TrailEvent, TrailEvent]],
    path: str | None,
) -> tuple[list[tuple[tuple, TrailEvent, TrailEvent]], str | None]:
    """Narrow overlapping direct file-tool windows by declared write path.

    This is deliberately not applied when any candidate is Bash/unknown. Timing
    overlap plus one direct path claim is not strong enough if another writer's
    scope is unbounded.
    """
    if not path or not matches:
        return matches, None
    if not all(_is_direct_path_writer(opened) for _key, opened, _closed in matches):
        return matches, None
    if not all(_has_declared_path_scope(opened) for _key, opened, _closed in matches):
        return matches, None
    scoped = [
        item
        for item in matches
        if _declares_path(item[1], path)
    ]
    if len(scoped) == 1:
        return scoped, "writer_scope_match"
    return matches, None


def _attribution_draft(
    *,
    observation: TrailEvent,
    result: str,
    trace_id: str | None,
    generation_index: int,
    step_index: int | None,
    capture_limitations: list[str],
    candidates: list[dict[str, Any]] | None = None,
    upgraded_trace_patch_id: str | None = None,
    created_trace_patch_id: str | None = None,
    window: TrailEvent | None = None,
    attribution_rule: str | None = None,
) -> TrailEventDraft:
    payload: dict[str, Any] = {
        "observation_event_id": observation.event_id,
        "path": _normalize_path(observation.payload.get("path")),
        "result": result,
        "capture_limitations": capture_limitations,
    }
    if candidates is not None:
        payload["candidate_windows"] = candidates
    if upgraded_trace_patch_id is not None:
        payload["upgraded_trace_patch_id"] = upgraded_trace_patch_id
    if created_trace_patch_id is not None:
        payload["created_trace_patch_id"] = created_trace_patch_id
    if attribution_rule is not None:
        payload["attribution_rule"] = attribution_rule
    if window is not None:
        tool_call_id = window.payload.get("tool_call_id")
        agent_step_id = window.payload.get("agent_step_id")
        if tool_call_id is not None:
            payload["tool_call_id"] = tool_call_id
        if agent_step_id is not None:
            payload["agent_step_id"] = agent_step_id
        session_id = window.payload.get("session_id")
        if session_id is not None:
            payload["session_id"] = session_id
    return TrailEventDraft(
        event_type="watcher_observation_attributed",
        trace_id=trace_id,
        generation_index=generation_index,
        step_index=step_index,
        capture_method=list(RECONCILER_CAPTURE_METHOD),
        payload=payload,
    )


def _upgrade_patch_draft(
    patch_event: TrailEvent,
) -> TrailEventDraft:
    """Re-emit a ``trace_patch_created`` with ``watcher_backstop`` added.

    The replayed payload is identical to the original event's payload byte
    by byte. Only the envelope's ``capture_method`` array changes, recording
    that the watcher corroborated the same mutation.

    Uses ``copy.deepcopy`` so nested payload structures (``affected_range``,
    ``before_blob_id``, ``after_blob_id``) are not aliased with the prior
    event's payload — defensive against future mutation by validators.
    """
    merged = sorted({*patch_event.capture_method, "watcher_backstop"})
    return TrailEventDraft(
        event_type="trace_patch_created",
        trace_id=patch_event.trace_id,
        generation_index=patch_event.generation_index,
        step_index=patch_event.step_index,
        capture_method=merged,
        payload=copy.deepcopy(patch_event.payload),
    )


def _patch_matches_window(
    patch: TrailEvent,
    *,
    trace_id: str,
    generation_index: int,
    step_index: int,
    tool_call_id: str | None,
    path: str | None,
) -> bool:
    if patch.trace_id != trace_id:
        return False
    if (patch.generation_index or 0) != generation_index:
        return False
    if patch.step_index != step_index:
        return False
    if _normalize_path(patch.payload.get("file_path")) != path:
        return False
    patch_tool_call_id = patch.payload.get("tool_call_id")
    if patch_tool_call_id is not None and str(patch_tool_call_id) != tool_call_id:
        return False
    return True


def _find_correlated_patch(
    patches: list[TrailEvent],
    *,
    trace_id: str,
    generation_index: int,
    step_index: int,
    tool_call_id: str | None,
    path: str | None,
    observation: TrailEvent,
) -> TrailEvent | None:
    candidates = [
        patch
        for patch in patches
        if _patch_matches_window(
            patch,
            trace_id=trace_id,
            generation_index=generation_index,
            step_index=step_index,
            tool_call_id=tool_call_id,
            path=path,
        )
        and _patch_blob_matches_observation(patch, observation)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda event: event.event_sequence)


def _watcher_patch_draft(
    repo: Path,
    *,
    observation: TrailEvent,
    opened: TrailEvent,
    trace_id: str,
    generation_index: int,
    step_index: int,
) -> TrailEventDraft | None:
    path = _normalize_path(observation.payload.get("path"))
    after_blob_id = observation.payload.get("after_blob_id")
    if not path or after_blob_id is None:
        return None
    authored_text, affected_range = _watcher_added_text(repo, observation)
    if not authored_text:
        return None
    raw_authored_hash = sha256_text(authored_text)
    identity_material = {
        "trace_id": trace_id,
        "generation_index": generation_index,
        "step_index": step_index,
        "tool_call_id": opened.payload.get("tool_call_id"),
        "file_path": path,
        "before_blob_id": observation.payload.get("before_blob_id"),
        "after_blob_id": after_blob_id,
        "affected_range": affected_range,
        "raw_authored_hash": raw_authored_hash,
    }
    snapshot_before_id = _id(
        "snapshot",
        {**identity_material, "role": "before"},
    )
    snapshot_after_id = _id(
        "snapshot",
        {**identity_material, "role": "after"},
    )
    trace_patch_id = _id("tracepatch", identity_material)
    payload: dict[str, Any] = {
        "trace_patch_id": trace_patch_id,
        "trace_patch_ref": trace_patch_ref(trace_patch_id),
        "snapshot_before_id": snapshot_before_id,
        "snapshot_before_ref": trace_snapshot_ref(snapshot_before_id),
        "snapshot_after_id": snapshot_after_id,
        "snapshot_after_ref": trace_snapshot_ref(snapshot_after_id),
        "file_path": path,
        "affected_range": affected_range,
        "authored_text": authored_text,
        "raw_authored_hash": raw_authored_hash,
        "git_clean_hash": sha256_text(_norm(authored_text)),
        "before_blob_id": observation.payload.get("before_blob_id"),
        "after_blob_id": after_blob_id,
        "limitations": [],
        "source_observation_event_id": observation.event_id,
    }
    for key in (
        "agent_step_id",
        "tool_call_id",
        "session_id",
        "tool_name",
        "declared_write_paths",
    ):
        value = opened.payload.get(key)
        if value is not None:
            payload[key] = value
    return TrailEventDraft(
        event_type="trace_patch_created",
        trace_id=trace_id,
        generation_index=generation_index,
        step_index=step_index,
        capture_method=list(RECONCILER_CAPTURE_METHOD),
        payload=payload,
    )


def reconcile_watcher_observations(
    repo: Path,
    *,
    writer: str = RECONCILER_WRITER,
) -> dict[str, Any]:
    """Process unattributed watcher observations against step windows.

    Returns a summary describing what the reconciler did. The caller can use
    this for telemetry; the source of truth is the appended events in the
    canonical event log.

    Concurrency: the per-repo file lock prevents two reconcilers from
    double-emitting attribution for the same observation. The append-only
    log's CAS retry continues to handle inter-batch ordering races
    (see ``append_event_batch``).
    """
    repo = repo.resolve()
    with _reconciler_lock(repo):
        events = read_events(repo)
        index = _index_events(events)

        drafts: list[TrailEventDraft] = []
        summary = {
            "observations_total": len(index["observations"]),
            "observations_processed": 0,
            "attributed": 0,
            "concurrent_writer_overlap": 0,
            "unbounded_mutation_window": 0,
            "background_process_overlap": 0,
            "patches_upgraded": 0,
            "patches_created": 0,
        }

        for observation in index["observations"]:
            if observation.event_id in index["attributed"]:
                continue
            summary["observations_processed"] += 1
            matches = _matching_windows(observation, index["windows_open"], index["windows_close"])
            path = _normalize_path(observation.payload.get("path"))

            if len(matches) == 0:
                drafts.append(
                    _attribution_draft(
                        observation=observation,
                        result="unattributed",
                        trace_id=None,
                        generation_index=0,
                        step_index=None,
                        capture_limitations=[UNBOUNDED_MUTATION_WINDOW],
                    )
                )
                summary["unbounded_mutation_window"] += 1
                continue

            attribution_rule: str | None = None
            if len(matches) > 1:
                narrowed, attribution_rule = _narrow_matches_by_declared_path(matches, path)
                if len(narrowed) == 1:
                    matches = narrowed
                else:
                    attribution_rule = None
            if len(matches) > 1:
                candidates = [
                    {
                        "trace_id": key[0],
                        "generation_index": key[1],
                        "step_index": key[2],
                        "tool_call_id": key[3],
                        "agent_step_id": opened.payload.get("agent_step_id"),
                        "session_id": opened.payload.get("session_id"),
                        "tool_name": opened.payload.get("tool_name"),
                        "declared_write_paths": opened.payload.get("declared_write_paths"),
                    }
                    for key, opened, _closed in matches
                ]
                drafts.append(
                    _attribution_draft(
                        observation=observation,
                        result="ambiguous",
                        trace_id=None,
                        generation_index=0,
                        step_index=None,
                        capture_limitations=[CONCURRENT_WRITER_OVERLAP],
                        candidates=candidates,
                    )
                )
                summary["concurrent_writer_overlap"] += 1
                continue

            (key, opened, _closed) = matches[0]
            trace_id, generation_index, step_index, tool_call_id = key

            if observation.payload.get("concurrent_activity") is True:
                drafts.append(
                    _attribution_draft(
                        observation=observation,
                        result="ambiguous",
                        trace_id=trace_id,
                        generation_index=generation_index,
                        step_index=step_index,
                        capture_limitations=[BACKGROUND_PROCESS_OVERLAP],
                        window=opened,
                    )
                )
                summary["background_process_overlap"] += 1
                continue

            upgraded_trace_patch_id: str | None = None
            created_trace_patch_id: str | None = None
            existing_patch = _find_correlated_patch(
                index["patches"],
                trace_id=trace_id,
                generation_index=generation_index,
                step_index=step_index,
                tool_call_id=tool_call_id,
                path=path,
                observation=observation,
            )
            if existing_patch is not None:
                trace_patch_id = existing_patch.payload.get("trace_patch_id")
                if trace_patch_id and trace_patch_id not in index["upgraded_patches"]:
                    drafts.append(_upgrade_patch_draft(existing_patch))
                    index["upgraded_patches"].add(trace_patch_id)
                    upgraded_trace_patch_id = trace_patch_id
                    summary["patches_upgraded"] += 1
            else:
                watcher_patch = _watcher_patch_draft(
                    repo,
                    observation=observation,
                    opened=opened,
                    trace_id=trace_id,
                    generation_index=generation_index,
                    step_index=step_index,
                )
                if watcher_patch is not None:
                    trace_patch_id = watcher_patch.payload["trace_patch_id"]
                    if trace_patch_id not in index["upgraded_patches"]:
                        drafts.append(watcher_patch)
                        index["upgraded_patches"].add(trace_patch_id)
                        created_trace_patch_id = trace_patch_id
                        summary["patches_created"] += 1

            drafts.append(
                _attribution_draft(
                    observation=observation,
                    result="attributed",
                    trace_id=trace_id,
                    generation_index=generation_index,
                    step_index=step_index,
                    capture_limitations=[],
                    upgraded_trace_patch_id=upgraded_trace_patch_id,
                    created_trace_patch_id=created_trace_patch_id,
                    window=opened,
                    attribution_rule=attribution_rule,
                )
            )
            summary["attributed"] += 1

        if drafts:
            append_event_batch(repo, drafts, writer=writer)
        return summary
