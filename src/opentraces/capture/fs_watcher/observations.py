"""Append ``filesystem_mutation_observed`` TrailEvents.

The observation payload is intentionally agent-agnostic. It records the
*observed* file mutation as a four-field tuple ``(path, before_blob,
after_blob, observed_at_start, observed_at_end)``. It carries no
``trace_id``, ``step_index``, or ``agent_step_id`` fields. Attribution
happens in the reconciler, not at observation time.

Observation timestamps and path are validated at write time so the
reconciler can trust the canonical event log without re-validating each
event during planning.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ...core.trails.capture_limitations import assert_known_capture_limitations
from ...core.trails.event_log import append_event_batch
from ...core.trails.models import GitObjectID, TrailEvent, TrailEventDraft

WATCHER_CAPTURE_METHOD = ["watcher_backstop"]
WATCHER_DEFAULT_WRITER = "fs-watcher"


def _parse_iso(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("watcher timestamp must be a non-empty ISO-8601 string")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"watcher timestamp not ISO-8601: {value!r}") from exc


def _normalize_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("watcher path must be a non-empty string")
    return posixpath.normpath(value.replace("\\", "/"))


@dataclass(frozen=True)
class FilesystemMutationObservation:
    """One observed file mutation.

    Attribution is *not* part of this record. The reconciler is responsible
    for deciding whether the mutation falls inside a writer's step window.
    """

    path: str
    observed_at_start: str
    observed_at_end: str
    before_blob_id: GitObjectID | None = None
    after_blob_id: GitObjectID | None = None
    concurrent_activity: bool = False

    def to_payload(self) -> dict[str, Any]:
        before = (
            self.before_blob_id.model_dump(mode="json")
            if isinstance(self.before_blob_id, GitObjectID)
            else self.before_blob_id
        )
        after = (
            self.after_blob_id.model_dump(mode="json")
            if isinstance(self.after_blob_id, GitObjectID)
            else self.after_blob_id
        )
        payload: dict[str, Any] = {
            "path": self.path,
            "observed_at_start": self.observed_at_start,
            "observed_at_end": self.observed_at_end,
            "before_blob_id": before,
            "after_blob_id": after,
        }
        if self.concurrent_activity:
            payload["concurrent_activity"] = True
        return payload


def append_filesystem_mutation_observed(
    repo: Path,
    *,
    path: str,
    observed_at_start: str,
    observed_at_end: str,
    before_blob_id: GitObjectID | dict[str, str] | None = None,
    after_blob_id: GitObjectID | dict[str, str] | None = None,
    concurrent_activity: bool = False,
    capture_limitations: list[str] | None = None,
    writer: str = WATCHER_DEFAULT_WRITER,
) -> TrailEvent:
    """Append one ``filesystem_mutation_observed`` event.

    The event carries no trace_id, step_index, or agent_step_id. The
    reconciler is responsible for assigning attribution after the fact.

    Validates the observation payload at write time so the reconciler can
    trust the canonical log: rejects malformed timestamps, empty paths, and
    intervals where ``observed_at_end`` precedes ``observed_at_start``.
    ``capture_limitations`` may contain ``watcher_buffer_overflow`` when the
    daemon detected lost events; the closed vocabulary is enforced here.
    """
    if capture_limitations:
        assert_known_capture_limitations(capture_limitations)

    normalized_path = _normalize_path(path)
    start_ts = _parse_iso(observed_at_start)
    end_ts = _parse_iso(observed_at_end)
    if end_ts < start_ts:
        raise ValueError(
            "observed_at_end must not precede observed_at_start "
            f"(got {observed_at_start} → {observed_at_end})"
        )

    if isinstance(before_blob_id, dict):
        before_blob_id = GitObjectID.model_validate(before_blob_id)
    if isinstance(after_blob_id, dict):
        after_blob_id = GitObjectID.model_validate(after_blob_id)

    observation = FilesystemMutationObservation(
        path=normalized_path,
        observed_at_start=observed_at_start,
        observed_at_end=observed_at_end,
        before_blob_id=before_blob_id,
        after_blob_id=after_blob_id,
        concurrent_activity=concurrent_activity,
    )
    payload = observation.to_payload()
    if capture_limitations:
        payload["capture_limitations"] = list(capture_limitations)

    draft = TrailEventDraft(
        event_type="filesystem_mutation_observed",
        trace_id=None,
        generation_index=0,
        step_index=None,
        capture_method=list(WATCHER_CAPTURE_METHOD),
        payload=payload,
    )
    events = append_event_batch(repo, [draft], writer=writer)
    return events[0]
