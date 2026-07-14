"""Run-scoped action identity and time allocation shared by every drive."""

from __future__ import annotations

import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..run_store import RunDraft


TIMELINE_REF = "recordings/timeline.jsonl"


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
        if surface not in {"terminal", "browser"}:
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

    def complete(self, allocation: ActionAllocation) -> None:
        observed = time.monotonic()
        self._append(
            offset_ms=self._offset_ms(observed),
            recorded_at=_utc_now(),
            surface=allocation.surface,
            event="action_completed",
            action_ref=allocation.action_ref,
            causal_refs=(),
        )

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
            for expected_sequence, row in enumerate(rows, start=1):
                if not isinstance(row, dict) or row.get("sequence") != expected_sequence:
                    raise ValueError("timeline sequence is not contiguous")
                offset = row.get("offset_ms")
                if not isinstance(offset, int) or isinstance(offset, bool) or offset < previous_offset:
                    raise ValueError("timeline offsets are not monotonic integers")
                previous_offset = offset
                if row.get("surface") not in {"terminal", "browser"}:
                    raise ValueError("timeline surface is invalid")
                if row.get("event") not in {
                    "focus_changed",
                    "action_started",
                    "action_completed",
                }:
                    raise ValueError("timeline event is invalid")
                action_ref = row.get("action_ref")
                if not isinstance(action_ref, str) or not (
                    self.draft.path / action_ref / "invocation.json"
                ).is_file():
                    raise ValueError("timeline action reference is missing")
                if not isinstance(row.get("recorded_at"), str) or not isinstance(
                    row.get("causal_refs"), list
                ):
                    raise ValueError("timeline row has invalid recorded_at or causal_refs")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return {
                "complete": False,
                "path": TIMELINE_REF,
                "reason": f"execution timeline is corrupt: {exc}",
            }
        return {"complete": True, "path": TIMELINE_REF, "reason": None}
