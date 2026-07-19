"""Run-scoped action identity and time allocation shared by every drive."""

from __future__ import annotations

import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..run_store import RunDraft


TIMELINE_REF = "recordings/timeline.jsonl"
TIMELINE_TIMESTAMP_TOLERANCE_MS = 250
ACTION_SURFACES = frozenset({"terminal", "browser", "agent"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ActionAllocation:
    ordinal: int
    started_at: str
    started_monotonic: float
    offset_ms: int
    surface: str
    action_ref: str
    causal_refs: tuple[str, ...]


class RunActionSequence:
    """Own the one monotonic ordinal/time domain for a BenchRun."""

    def __init__(self, *, draft: RunDraft, run_started_monotonic: float) -> None:
        self.draft = draft
        self.run_started_monotonic = run_started_monotonic
        self._ordinal = 0
        self._timeline_sequence = 0
        self._surface: str | None = None
        self._previous_action_ref: str | None = None
        self.draft.write_text(TIMELINE_REF, "")

    @property
    def has_actions(self) -> bool:
        """Whether any public scenario action has been allocated."""

        return self._ordinal > 0

    def _offset_ms(self, observed: float) -> int:
        return max(0, int((observed - self.run_started_monotonic) * 1000))

    def _append(
        self,
        *,
        offset_ms: int,
        recorded_at: str,
        surface: str,
        event: str,
        action_ref: str,
        causal_refs: tuple[str, ...],
    ) -> None:
        self._timeline_sequence += 1
        self.draft.append_jsonl(
            TIMELINE_REF,
            {
                "sequence": self._timeline_sequence,
                "offset_ms": offset_ms,
                "recorded_at": recorded_at,
                "surface": surface,
                "event": event,
                "action_ref": action_ref,
                "causal_refs": list(causal_refs),
            },
        )

    def allocate(self, surface: str) -> ActionAllocation:
        if surface not in ACTION_SURFACES:
            raise ValueError(f"unknown action surface: {surface}")
        self._ordinal += 1
        observed = time.monotonic()
        recorded_at = _utc_now()
        offset_ms = self._offset_ms(observed)
        action_ref = f"actions/{self._ordinal:04d}"
        causal_refs = (
            (self._previous_action_ref,) if self._previous_action_ref is not None else ()
        )
        allocation = ActionAllocation(
            ordinal=self._ordinal,
            started_at=recorded_at,
            started_monotonic=observed,
            offset_ms=offset_ms,
            surface=surface,
            action_ref=action_ref,
            causal_refs=causal_refs,
        )
        if self._surface != surface:
            self._append(
                offset_ms=offset_ms,
                recorded_at=recorded_at,
                surface=surface,
                event="focus_changed",
                action_ref=action_ref,
                causal_refs=causal_refs,
            )
            self._surface = surface
        self._append(
            offset_ms=offset_ms,
            recorded_at=recorded_at,
            surface=surface,
            event="action_started",
            action_ref=action_ref,
            causal_refs=causal_refs,
        )
        self._previous_action_ref = action_ref
        return allocation

    def complete(self, allocation: ActionAllocation) -> int:
        observed = time.monotonic()
        completion_offset_ms = self._offset_ms(observed)
        duration_ms = max(0, completion_offset_ms - allocation.offset_ms)
        self._append(
            offset_ms=completion_offset_ms,
            recorded_at=_utc_now(),
            surface=allocation.surface,
            event="action_completed",
            action_ref=allocation.action_ref,
            causal_refs=(),
        )
        return duration_ms

    @staticmethod
    def duration_ms(allocation: ActionAllocation) -> int:
        return max(0, int((time.monotonic() - allocation.started_monotonic) * 1000))

    def timeline_status(self) -> dict[str, Any]:
        path = self.draft.path / TIMELINE_REF
        if not path.is_file():
            return {
                "complete": False,
                "path": TIMELINE_REF,
                "reason": "execution timeline is missing",
            }
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            previous_offset = -1
            starts: dict[str, dict[str, Any]] = {}
            completions: dict[str, dict[str, Any]] = {}
            focus_changes: dict[str, dict[str, Any]] = {}
            for expected_sequence, row in enumerate(rows, start=1):
                if not isinstance(row, dict) or row.get("sequence") != expected_sequence:
                    raise ValueError("timeline sequence is not contiguous")
                offset = row.get("offset_ms")
                if not isinstance(offset, int) or isinstance(offset, bool) or offset < previous_offset:
                    raise ValueError("timeline offsets are not monotonic integers")
                previous_offset = offset
                if row.get("surface") not in ACTION_SURFACES:
                    raise ValueError("timeline surface is invalid")
                if row.get("event") not in {
                    "focus_changed",
                    "action_started",
                    "action_completed",
                }:
                    raise ValueError("timeline event is invalid")
                action_ref = row.get("action_ref")
                if not isinstance(action_ref, str) or not action_ref.startswith("actions/"):
                    raise ValueError("timeline action reference is missing")
                action_path = self.draft.path / action_ref
                if not (action_path / "invocation.json").is_file():
                    raise ValueError("timeline action reference is missing")
                causal_refs = row.get("causal_refs")
                if not isinstance(row.get("recorded_at"), str) or not isinstance(
                    causal_refs, list
                ):
                    raise ValueError("timeline row has invalid recorded_at or causal_refs")
                for causal_ref in causal_refs:
                    if not isinstance(causal_ref, str) or not (
                        self.draft.path / causal_ref / "invocation.json"
                    ).is_file():
                        raise ValueError("timeline causal reference is dangling")
                event = str(row["event"])
                event_rows = {
                    "focus_changed": focus_changes,
                    "action_started": starts,
                    "action_completed": completions,
                }[event]
                if action_ref in event_rows:
                    raise ValueError(f"timeline has duplicate {event} row")
                event_rows[action_ref] = row

            action_root = self.draft.path / "actions"
            stored_refs = [
                f"actions/{path.name}"
                for path in sorted(action_root.iterdir())
                if path.is_dir()
            ]
            expected_refs = [f"actions/{ordinal:04d}" for ordinal in range(1, len(stored_refs) + 1)]
            if stored_refs != expected_refs or set(starts) != set(stored_refs):
                raise ValueError("timeline action inventory does not match stored actions")
            if set(completions) != set(stored_refs):
                raise ValueError("timeline start/completion pairing is incomplete")

            prior_ref: str | None = None
            prior_surface: str | None = None
            expected_focus_refs: set[str] = set()
            for action_ref in stored_refs:
                action_path = self.draft.path / action_ref
                invocation = json.loads(
                    (action_path / "invocation.json").read_text(encoding="utf-8")
                )
                result = json.loads((action_path / "result.json").read_text(encoding="utf-8"))
                if not isinstance(invocation, dict) or not isinstance(result, dict):
                    raise ValueError("timeline action facts are invalid")
                start = starts[action_ref]
                completion = completions[action_ref]
                declared_surface = invocation.get("surface")
                surface = (
                    str(declared_surface)
                    if declared_surface in ACTION_SURFACES
                    else (
                        "terminal"
                        if isinstance(invocation.get("argv"), list)
                        else "browser"
                    )
                )
                if start["surface"] != surface or completion["surface"] != surface:
                    raise ValueError("timeline surface disagrees with stored action")
                if start["recorded_at"] != invocation.get("started_at"):
                    raise ValueError("timeline started_at disagrees with stored invocation")
                try:
                    start_time = datetime.fromisoformat(
                        str(start["recorded_at"]).replace("Z", "+00:00")
                    )
                    completion_time = datetime.fromisoformat(
                        str(completion["recorded_at"]).replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise ValueError("timeline recorded_at is not RFC3339") from exc
                if (
                    start_time.utcoffset() is None
                    or completion_time.utcoffset() is None
                ):
                    raise ValueError(
                        "timeline recorded_at is not timezone-aware RFC3339"
                    )
                if completion_time < start_time:
                    raise ValueError("timeline completion timestamp precedes action start")
                duration_ms = result.get("duration_ms")
                observed_duration = completion["offset_ms"] - start["offset_ms"]
                if (
                    not isinstance(duration_ms, int)
                    or isinstance(duration_ms, bool)
                    or duration_ms != observed_duration
                ):
                    raise ValueError("timeline duration disagrees with stored action result")
                timestamp_duration_ms = (
                    completion_time - start_time
                ).total_seconds() * 1_000
                if (
                    abs(timestamp_duration_ms - duration_ms)
                    > TIMELINE_TIMESTAMP_TOLERANCE_MS
                ):
                    raise ValueError(
                        "timeline timestamp duration disagrees with stored action result"
                    )
                expected_causal = [] if prior_ref is None else [prior_ref]
                if start["causal_refs"] != expected_causal or completion["causal_refs"] != []:
                    raise ValueError("timeline causal chain disagrees with action order")
                if prior_surface != surface:
                    expected_focus_refs.add(action_ref)
                    focus = focus_changes.get(action_ref)
                    if focus is None or any(
                        focus[key] != start[key]
                        for key in (
                            "offset_ms",
                            "recorded_at",
                            "surface",
                            "action_ref",
                            "causal_refs",
                        )
                    ):
                        raise ValueError("timeline focus change disagrees with action start")
                prior_ref = action_ref
                prior_surface = surface
            if set(focus_changes) != expected_focus_refs:
                raise ValueError("timeline focus changes disagree with surface transitions")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return {
                "complete": False,
                "path": TIMELINE_REF,
                "reason": f"execution timeline is corrupt: {exc}",
            }
        return {"complete": True, "path": TIMELINE_REF, "reason": None}
