"""Static ordered registry of trace slicers (issue #141).

Mirrors ``security/tools/_registry.py``: a static tuple of slicer modules, each
exposing ``NAME`` / ``LABEL`` / ``TIER`` and a ``slice(steps, *, answers)``
callable returning a :class:`SliceResult`. The set is small and frozen; the
``opentraces.slicing.v1`` envelope is the downstream contract, so changing a
slicer's boundary semantics is a reviewed contract change.
"""
from __future__ import annotations

from typing import Any, Iterator, Protocol, Sequence

from .models import SliceResult
from .slicers import s1_user_turn, s2_change_burst, s3_milestone, s4_subgoal


class Slicer(Protocol):
    NAME: str
    LABEL: str
    TIER: str

    def slice(self, steps: list[Any], *, answers: dict | None = ...) -> SliceResult: ...


_SLICERS: tuple[Any, ...] = (
    s1_user_turn,
    s2_change_burst,
    s3_milestone,
    s4_subgoal,
)

_BY_NAME: dict[str, Any] = {m.NAME: m for m in _SLICERS}


def all_slicers() -> Sequence[Any]:
    return _SLICERS


def iter_slicers() -> Iterator[Any]:
    yield from _SLICERS


def names() -> list[str]:
    return [m.NAME for m in _SLICERS]


def get(name: str) -> Any:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"unknown slicer: {name!r} (known: {', '.join(names())})"
        ) from None
