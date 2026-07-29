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
import json
import os
import posixpath
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ...enrichment.attribution import _norm, _parse_diff_hunks_with_content
from .capture_limitations import (
    BACKGROUND_PROCESS_OVERLAP,
    CONCURRENT_WRITER_OVERLAP,
    UNBOUNDED_MUTATION_WINDOW,
)
from .event_log import (
    _ref_head as _event_log_head,
)
from .event_log import (
    append_event_batch,
    read_events_scoped,
    read_events_since,
)
from .ids import (
    SNAPSHOT_CANONICALIZATION,
    TRACE_PATCH_CANONICALIZATION,
    content_ref,
    trace_patch_ref,
    trace_snapshot_ref,
)
from .models import TrailEvent, TrailEventDraft, sha256_text

# The reconciler only ever consumes these five event types (see
# ``_index_events``). Reading the scoped slice instead of the whole log keeps
# the watcher daemon's per-tick RSS bounded to a tiny fraction of the event log
# (#45) — the daemon never exits, so a full-log materialisation per tick
# compounds. No ``commit_filter``: none of these types is commit-keyed, so all
# five are kept in full and ordering matches a full ``read_events`` (the scoped
# reader sorts by ``event_sequence``).
_RECONCILER_EVENT_TYPES = frozenset(
    {
        "filesystem_mutation_observed",
        "trace_step_window_opened",
        "trace_step_window_closed",
        "trace_patch_created",
        "watcher_observation_attributed",
    }
)

RECONCILER_CAPTURE_METHOD = ["watcher_backstop"]
RECONCILER_WRITER = "watcher-reconciler"
RECONCILER_LOCK_BASENAME = "opentraces-reconciler.lock"

# --- incremental projection (#65) -------------------------------------------
#
# The scoped read above still walks the ENTIRE log every run: none of the five
# reconciler event types is commit-keyed, so on a mature repo (~872K events
# observed live) every step window / patch / observation in history is listed,
# read, and pydantic-validated per tick. That full-log materialisation was the
# named allocator behind the daemon's unbounded RSS (#65: 8GB inside one tick).
#
# The reconciler's work is inherently incremental — an observation is processed
# exactly once (its ``watcher_observation_attributed`` event, emitted even for
# "unattributed" results, permanently retires it). So we persist a small
# projection between runs and read only the appended batch suffix
# (``read_events_since``):
#
#   * pending observations    — retired on attribution, usually empty
#   * window open/close pairs — matching needs ``obs_interval ⊆ win_interval``
#     and a pending observation's ``observed_at_end`` is never older than its
#     emission, so pairs closed before (now - retention) can never match a
#     future observation; they are pruned at save
#   * recent patches          — only correlated against matched (recent)
#     windows; same horizon
#   * upgraded patch ids      — cheap strings, kept on a longer horizon so a
#     re-emitted patch can never double-upgrade
#
# Crash-safety: the watermark saved is the head AS OF THE READ (our own
# appended batch sits after it), and the projection is only saved after a
# successful append. A crash between append and save replays our own
# attribution events on the next run, which retire the already-processed
# observations — idempotent by the same mechanism that made full reads
# idempotent. ``OT_RECONCILER_FULL_READ=1`` bypasses the projection entirely
# (legacy full-read behavior, used by the equivalence tests).

PROJECTION_SCHEMA_VERSION = "opentraces.reconciler_projection.v1"
PROJECTION_BASENAME = "reconciler_projection.json"
# Window pairs are only matchable by observations whose interval they CONTAIN,
# and a pending observation's interval ends at its emission tick — so the
# provable need is (sweep interval + step duration + skew), i.e. hours. 48h is
# already generous; anything older can only match retroactively-timestamped
# observations, which take the floor-fallback full read instead. Measured on
# the #65 repo, a 7d horizon retained 76K events / 622MB of projection — the
# horizon IS the memory bound, keep it tight.
CLOSED_WINDOW_RETENTION = timedelta(hours=48)
# Strictly wider than the window horizon: a patch correlated to a retained
# window (close >= floor) was emitted within that window's step, so it can
# trail the close by at most one step duration — 72h vs 48h leaves no band
# where a window survives pruning but its hook patch doesn't (which would
# flip an "upgrade" into a duplicate "create").
PATCH_RETENTION = timedelta(hours=72)
OPEN_WINDOW_RETENTION = timedelta(days=90)
UPGRADED_ID_RETENTION = timedelta(days=90)


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


class _RebuildSink:
    """Streaming consumer for the cold projection rebuild (#65).

    Applies the SAME retention predicates as ``_retained_events`` while the
    scoped read streams, so the first run on a mature repo never materialises
    the full five-type slice (the original 8GB allocator). State held here is
    bounded by: pending (unattributed) observations + window pairs within the
    retention floor + unmatched opens + recent patches + upgraded-id strings.
    """

    def __init__(self, *, floor: datetime, patch_floor: datetime) -> None:
        self.floor = floor
        self.patch_floor = patch_floor
        self.observations: dict[str, TrailEvent] = {}
        self.windows_open: dict[tuple, TrailEvent] = {}
        self.windows_close: dict[tuple, TrailEvent] = {}
        self.patches: list[TrailEvent] = []
        self.upgraded_ids: set[str] = set()

    @staticmethod
    def _ts(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            ts = _parse_iso(value)
        except (ValueError, TypeError):
            return None
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts

    def __call__(self, event: TrailEvent) -> None:
        etype = event.event_type
        if etype == "filesystem_mutation_observed":
            self.observations[event.event_id] = event
        elif etype == "watcher_observation_attributed":
            obs_id = event.payload.get("observation_event_id")
            if obs_id:
                self.observations.pop(obs_id, None)
        elif etype == "trace_step_window_opened":
            key = _window_key(event)
            if key is not None:
                self.windows_open[key] = event
        elif etype == "trace_step_window_closed":
            key = _window_key(event)
            if key is None:
                return
            ts = self._ts(event.event_time)
            if ts is not None and ts < self.floor:
                # Pair (or orphan close) is older than anything a future
                # observation can match — drop both sides immediately.
                self.windows_open.pop(key, None)
                return
            self.windows_close[key] = event
        elif etype == "trace_patch_created":
            if "watcher_backstop" in event.capture_method:
                trace_patch_id = event.payload.get("trace_patch_id")
                if trace_patch_id:
                    self.upgraded_ids.add(trace_patch_id)
            if event.trace_id is None or event.step_index is None:
                return
            if _normalize_path(event.payload.get("file_path")) is None:
                return
            ts = self._ts(event.event_time)
            if ts is not None and ts < self.patch_floor:
                return
            self.patches.append(event)

    def pending_floor_breach(self) -> datetime | None:
        """Oldest pending observation end older than the floor, if any."""
        oldest: datetime | None = None
        for obs in self.observations.values():
            ts = self._ts(obs.payload.get("observed_at_end"))
            if ts is None:
                continue
            if ts < self.floor and (oldest is None or ts < oldest):
                oldest = ts
        return oldest

    def assembled_events(self, *, now: datetime) -> list[TrailEvent]:
        events: list[TrailEvent] = list(self.observations.values())
        for key, opened in self.windows_open.items():
            closed = self.windows_close.get(key)
            if closed is not None:
                events.append(opened)
                events.append(closed)
                continue
            ts = self._ts(opened.event_time)
            if ts is None or now - ts <= OPEN_WINDOW_RETENTION:
                events.append(opened)
        events.extend(self.patches)
        events.sort(key=lambda event: event.event_sequence)
        return events


def _rebuild_projection_events(
    repo: Path,
) -> tuple[list[TrailEvent], set[str]]:
    """Cold rebuild: stream the scoped slice into a bounded sink.

    Returns ``(curated_events, upgraded_patch_ids)``. When a pending
    observation predates the retention floor (retroactive timestamps), the
    walk re-runs once with the floor lowered to cover it — still bounded,
    still correct for the retro case the idempotency fixture pins.
    """
    now = datetime.now(timezone.utc)
    floor = now - CLOSED_WINDOW_RETENTION
    patch_floor = now - PATCH_RETENTION
    for _attempt in range(2):
        sink = _RebuildSink(floor=floor, patch_floor=patch_floor)
        read_events_scoped(
            repo, event_types=set(_RECONCILER_EVENT_TYPES), sink=sink
        )
        breach = sink.pending_floor_breach()
        if breach is None:
            return sink.assembled_events(now=now), sink.upgraded_ids
        margin = timedelta(days=1)
        floor = breach - margin
        patch_floor = min(patch_floor, floor - (PATCH_RETENTION - CLOSED_WINDOW_RETENTION))
    return sink.assembled_events(now=now), sink.upgraded_ids


def _projection_path(repo: Path) -> Path | None:
    """``<git-dir>/opentraces/reconciler_projection.json`` or None."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repo / git_dir).resolve()
    return git_dir / "opentraces" / PROJECTION_BASENAME


def _full_read_forced() -> bool:
    return os.environ.get("OT_RECONCILER_FULL_READ", "") not in ("", "0")


def _load_projection(repo: Path) -> dict[str, Any] | None:
    """Load the persisted projection, or None when absent/invalid/stale-schema."""
    path = _projection_path(repo)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        return None
    if not isinstance(data.get("last_head"), str) or not data["last_head"]:
        return None
    try:
        events = [TrailEvent.model_validate(item) for item in data.get("events") or []]
    except Exception:  # noqa: BLE001 — any malformed event invalidates the file
        return None
    upgraded = data.get("upgraded_patch_ids")
    if not isinstance(upgraded, dict):
        upgraded = {}
    floor = data.get("floor")
    if not isinstance(floor, str) or not floor:
        return None
    return {
        "last_head": data["last_head"],
        "events": events,
        "floor": floor,
        "upgraded_patch_ids": {
            str(k): str(v) for k, v in upgraded.items() if k and v
        },
    }


def _pending_within_floor(events: list[TrailEvent], floor_iso: str) -> bool:
    """True when every still-pending observation's interval ends at/after the
    projection's prune floor — i.e. the retained window pairs are sufficient.

    A pending observation with ``observed_at_end`` BEFORE the floor (a
    retroactively-timestamped observation from a replay/import/backdated
    writer) could match a window pair the save-time pruning dropped, so the
    caller must fall back to a full read for that run. Unparseable
    timestamps are treated as out-of-floor (conservative full read).
    """
    try:
        floor = _parse_iso(floor_iso)
        if floor.tzinfo is None:
            floor = floor.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    attributed: set[str] = set()
    for event in events:
        if event.event_type == "watcher_observation_attributed":
            obs_id = event.payload.get("observation_event_id")
            if obs_id:
                attributed.add(obs_id)
    for event in events:
        if event.event_type != "filesystem_mutation_observed":
            continue
        if event.event_id in attributed:
            continue
        try:
            end = _parse_iso(event.payload["observed_at_end"])
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, KeyError):
            return False
        if end < floor:
            return False
    return True


def _event_age_ok(event: TrailEvent, *, now: datetime, horizon: timedelta) -> bool:
    try:
        ts = _parse_iso(event.event_time)
    except (ValueError, TypeError, KeyError):
        return True  # unparseable time: retain (over-include, never miss)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return now - ts <= horizon


def _retained_events(
    index: dict[str, Any],
    *,
    processed_obs_ids: set[str],
    now: datetime,
) -> list[TrailEvent]:
    """The curated event subset the next run needs (see module retention notes)."""
    retained: list[TrailEvent] = []
    for obs in index["observations"]:
        if obs.event_id in processed_obs_ids or obs.event_id in index["attributed"]:
            continue
        retained.append(obs)
    for key, opened in index["windows_open"].items():
        closed = index["windows_close"].get(key)
        if closed is None:
            if _event_age_ok(opened, now=now, horizon=OPEN_WINDOW_RETENTION):
                retained.append(opened)
            continue
        if _event_age_ok(closed, now=now, horizon=CLOSED_WINDOW_RETENTION):
            retained.append(opened)
            retained.append(closed)
    for patch in index["patches"]:
        if _event_age_ok(patch, now=now, horizon=PATCH_RETENTION):
            retained.append(patch)
    retained.sort(key=lambda event: event.event_sequence)
    return retained


def _save_projection(
    repo: Path,
    *,
    last_head: str,
    index: dict[str, Any],
    processed_obs_ids: set[str],
    upgraded_first_seen: dict[str, str],
) -> None:
    """Persist the projection. Best-effort: a failed save degrades to a full
    read next run, never to incorrect attribution."""
    path = _projection_path(repo)
    if path is None:
        return
    now = datetime.now(timezone.utc)
    horizon_floor = now - UPGRADED_ID_RETENTION
    upgraded: dict[str, str] = {}
    for patch_id in index["upgraded_patches"]:
        seen = upgraded_first_seen.get(patch_id) or now.isoformat()
        try:
            seen_ts = _parse_iso(seen)
            if seen_ts.tzinfo is None:
                seen_ts = seen_ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            seen_ts, seen = now, now.isoformat()
        if seen_ts >= horizon_floor:
            upgraded[patch_id] = seen
    payload = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "last_head": last_head,
        "saved_at": now.isoformat(),
        # Window pairs closed before this instant were pruned; a later run
        # seeing a pending observation that ends before it must full-read.
        "floor": (now - CLOSED_WINDOW_RETENTION).isoformat(),
        "events": [
            event.model_dump(mode="json")
            for event in _retained_events(
                index, processed_obs_ids=processed_obs_ids, now=now
            )
        ],
        "upgraded_patch_ids": upgraded,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            path.unlink()


def reconcile_watcher_observations(
    repo: Path,
    *,
    writer: str = RECONCILER_WRITER,
    event_sink: Callable[[TrailEvent], None] | None = None,
) -> dict[str, Any]:
    """Process unattributed watcher observations against step windows.

    Returns a summary describing what the reconciler did. The caller can use
    this for telemetry; the source of truth is the appended events in the
    canonical event log. When ``event_sink`` is supplied, each event appended
    by this run is also handed to the caller after the append succeeds. This
    lets hot ingest reuse its bounded delta without rereading global history.

    Concurrency: the per-repo file lock prevents two reconcilers from
    double-emitting attribution for the same observation. The append-only
    log's CAS retry continues to handle inter-batch ordering races
    (see ``append_event_batch``).
    """
    repo = repo.resolve()
    with _reconciler_lock(repo):
        # #65: incremental-by-default. Resolve the watermark candidate BEFORE
        # any read so events appended mid-read are re-read (idempotently) on
        # the next run rather than silently skipped.
        head_at_read = _event_log_head(repo)
        projection = None if _full_read_forced() else _load_projection(repo)
        events: list[TrailEvent] | None = None
        if projection is not None:
            inc_head, new_events = read_events_since(repo, projection["last_head"])
            if new_events is not None:
                candidate = projection["events"] + new_events
                if _pending_within_floor(candidate, projection["floor"]):
                    head_at_read = inc_head
                    events = candidate
                else:
                    # Retroactively-timestamped pending observation: retained
                    # windows may be insufficient — full read for this run.
                    projection = None
            else:
                projection = None  # rewritten/unknown history → full rebuild
        rebuild_upgraded: set[str] = set()
        if events is None:
            if _full_read_forced():
                events = read_events_scoped(
                    repo, event_types=set(_RECONCILER_EVENT_TYPES)
                )
            else:
                # Cold rebuild (#65): stream into a bounded sink instead of
                # materialising the full slice — first run on a mature repo
                # stays inside the same memory envelope as a warm run.
                events, rebuild_upgraded = _rebuild_projection_events(repo)
        index = _index_events(events)
        upgraded_first_seen = dict(
            (projection or {}).get("upgraded_patch_ids") or {}
        )
        index["upgraded_patches"].update(upgraded_first_seen)
        index["upgraded_patches"].update(rebuild_upgraded)
        processed_obs_ids: set[str] = set()

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
            processed_obs_ids.add(observation.event_id)
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
            written = append_event_batch(repo, drafts, writer=writer)
            if event_sink is not None:
                for event in written:
                    event_sink(event)
        # Save AFTER a successful append (or when there was nothing to append):
        # the watermark is the head as of the read, so our own batch is re-read
        # next run and retires these observations even if this save fails.
        if head_at_read is not None and not _full_read_forced():
            _save_projection(
                repo,
                last_head=head_at_read,
                index=index,
                processed_obs_ids=processed_obs_ids,
                upgraded_first_seen=upgraded_first_seen,
            )
        return summary
