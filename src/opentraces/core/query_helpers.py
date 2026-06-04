"""Shared FTS query and pagination helpers (plan 088 U4-U6).

These helpers are shared between :mod:`opentraces.core.trace_index` and
:mod:`opentraces.core.search_projection`.  Having a single source of truth
prevents the silent tokenisation drift that had already occurred between the
two backends (trace_index used ``[a-zA-Z0-9_./-]+`` while search_projection
used ``[A-Za-z0-9_@./-]+``).

Canonical _terms regex: ``[a-zA-Z0-9_@./-]+``
- Includes ``@`` to handle email/mention tokens (plan 088 U6b)
- ``len(tok) > 1`` guard applied consistently (from trace_index, more
  conservative than search_projection's missing guard)
- Sub-token splitting on ``[/.@:]+`` so compound identifiers reach individual
  components (e.g. "user@example.com" -> ["user@example.com", "user",
  "example", "com"])
"""

from __future__ import annotations

import re


def _terms(text: str) -> list[str]:
    """Tokenise *text* into lower-cased index terms.

    Keeps compound URL/identifier tokens whole AND emits their pieces so a
    bare-identifier query reaches a needle embedded inside a URL.

    Regex: ``[a-zA-Z0-9_@./-]+``  (includes ``@`` for email/mention tokens).
    Hyphens and underscores remain intact so hyphenated identifiers match as
    a whole unit.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[a-zA-Z0-9_@./-]+", text):
        tok = raw.lower()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
        if "/" in tok or "." in tok or "@" in tok:
            for sub in re.split(r"[/.@:]+", tok):
                if len(sub) > 1 and sub not in seen:
                    seen.add(sub)
                    out.append(sub)
    return out


def _fts_query(terms: list[str]) -> str:
    """Build a quoted SQLite FTS5 query string from *terms*."""
    quoted: list[str] = []
    for term in terms:
        safe = term.replace('"', "")
        if safe:
            quoted.append(f'"{safe}"')
    return " ".join(quoted)


def _page_offset(page_token: str | None) -> int:
    """Convert an ``offset:<n>`` page token to an integer offset."""
    if not page_token:
        return 0
    if not page_token.startswith("offset:"):
        raise ValueError("page token must have the form offset:<n>")
    try:
        return max(0, int(page_token.split(":", 1)[1]))
    except ValueError as exc:
        raise ValueError("page token must have the form offset:<n>") from exc


def _recency_weighted_sort(scored: list, weight: float, ts_fn) -> list:
    """Sort by relevance blended with a normalised recency term (U5).

    Each item's effective score is ``score + weight * recency_frac`` where
    ``recency_frac`` is the item's timestamp position in [0, 1] across the
    scored set (newest = 1). Deterministic; only applied when ``weight`` > 0,
    so the default (weight=0) ordering is unchanged.
    """
    if not scored:
        return scored
    epochs = [ts_fn(item[3]).timestamp() for item in scored]
    lo = min(epochs)
    span = max(max(epochs) - lo, 1.0)
    decorated = [
        (-(item[0] + weight * (epoch - lo) / span), item[3].trace_id, item[3].unit_id, item)
        for item, epoch in zip(scored, epochs)
    ]
    decorated.sort(key=lambda d: (d[0], d[1], d[2]))
    return [d[3] for d in decorated]
