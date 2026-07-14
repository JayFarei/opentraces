"""Run-scoped action identity and time allocation shared by every drive."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ActionAllocation:
    ordinal: int
    started_at: str
    started_monotonic: float


class RunActionSequence:
    """Own the one monotonic ordinal/time domain for a BenchRun."""

    def __init__(self) -> None:
        self._ordinal = 0

    def allocate(self) -> ActionAllocation:
        self._ordinal += 1
        return ActionAllocation(
            ordinal=self._ordinal,
            started_at=_utc_now(),
            started_monotonic=time.monotonic(),
        )

    @staticmethod
    def duration_ms(allocation: ActionAllocation) -> int:
        return max(0, int((time.monotonic() - allocation.started_monotonic) * 1000))
