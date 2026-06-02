"""Opt-in query boundedness diagnostics (plan 088 U3).

The search-evaluation harness reads these counters to assert the qmd invariant:
the number of documents a query *selects and scores* must track the number of
**matches**, not the size of the **corpus**. A query whose ``docs_selected``
approaches ``corpus_docs`` is doing O(corpus) work (the broad-term / concept
``--semantic`` cliff) and violates boundedness.

Zero overhead unless ``OT_SEARCH_DIAG=1`` is set in the environment: every entry
point short-circuits on :func:`enabled`, and the more expensive ``count(*)``
corpus probes are only issued by callers under that same guard. The counters are
process-local (one ``trace query`` invocation per process), so a plain module
dict is sufficient and lock-free.
"""

from __future__ import annotations

import os
from typing import Any

_DIAG: dict[str, Any] = {}


def enabled() -> bool:
    return os.environ.get("OT_SEARCH_DIAG") == "1"


def reset() -> None:
    if enabled():
        _DIAG.clear()


def record(**kwargs: Any) -> None:
    if enabled():
        _DIAG.update(kwargs)


def incr(key: str, n: int = 1) -> None:
    if enabled():
        _DIAG[key] = _DIAG.get(key, 0) + n


def snapshot() -> dict[str, Any]:
    return dict(_DIAG)
