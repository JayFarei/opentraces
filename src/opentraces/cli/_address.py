"""Shared address grammar for address-first root dispatch (v7 F5).

ONE home for the ``<trace>[:<selector>]`` address grammar so the root
dispatcher (F5) and ``trace get``'s step resolution (F1) can never fork.
The colon grammar DELEGATES to ``core.trails.lineage.parse_trail_ref`` —
the trail byte-compat pins stay the single oracle — this module only
layers the conservative root-dispatch gate on top.

Two-tier gate (see ``root_dispatch_token``):

* **Tier 1 — pure syntax, zero shadowing risk** (no Click command name
  can contain ``:`` or be UUID-shaped): ``ot://`` resources, ``t:`` /
  ``tu:`` / ``tmn:`` typed ids, full 36-char UUIDs, legacy
  ``<agent>_<uuid>`` ids, and ``<uuid>:<selector>`` colon forms where
  the selector is an int step, ``last``, or an ``A-B`` span. Forwarded
  verbatim.
* **Tier 2 — bare (or colon-form) short hex prefix, >=8 chars**:
  dispatched ONLY when the prefix uniquely resolves to an existing
  trace via the same read-only resolver the CLI already uses
  (``core.trace_meta.resolve_trace_id_prefix``). The resolved FULL
  trace_id is substituted into the forwarded token because ``trace
  get``'s bare-id path resolves EXACT ids only (sqlite equality +
  direct glob) — forwarding the raw prefix would dead-end at rc=6.
  Unresolved or ambiguous prefixes are NOT addresses: the caller falls
  through to Click's unchanged "No such command" error, preserving
  Did-you-mean suggestions for typos.

Residual documented asymmetry: ``opentraces trace get <short-prefix>``
itself still exits 6 (exact-id resolution) — widening trace get's own
resolver is F1-follow-up territory, not this module's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Union

__all__ = [
    "ParsedAddress",
    "parse_address",
    "is_root_dispatchable",
    "root_dispatch_token",
]

# Full 36-char UUID (8-4-4-4-12 hex). Dispatches unconditionally: no
# Click command is UUID-shaped, and an unknown UUID still gets trace
# get's address-shaped "Trace not found" rather than "No such command".
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Legacy `<agent>_<session_uuid>` trace-id form (deprecated but still
# stored; see core.trace_meta.short_trace_id's migration note).
_LEGACY_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9.-]*_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Tier-2 bare short hex prefix: >=8 chars of [0-9a-f-], starting with a
# hex digit. Long enough that hex-lettered English words ("added",
# "decade") stay below the bar, and gated behind the existence probe
# anyway.
_HEX_PREFIX_RE = re.compile(r"^[0-9a-f][0-9a-f-]{7,}$")

# Typed-id / resource prefixes trace get already resolves natively.
_SCHEME_PREFIXES = ("ot://",)
_ID_PREFIXES = ("t:", "T:", "tu:", "tmn:")

# selector: step index, "last", or an (A, B) span.
Selector = Union[int, str, tuple[int, int]]


@dataclass(frozen=True)
class ParsedAddress:
    """A parsed ``<trace>[:<selector>]`` address."""

    trace_part: str
    selector: Selector | None


def parse_address(token: str) -> ParsedAddress | None:
    """Parse ``<trace>`` / ``<trace>:<N>`` / ``<trace>:last`` / ``<trace>:A-B``.

    Returns ``None`` for empty tokens and for colon forms whose selector
    is not one of the three supported shapes (``origin`` and malformed
    selectors are deliberately NOT addresses here — trail owns those).
    The colon grammar delegates to ``parse_trail_ref`` so the shared
    tuple contract stays the single oracle; F1 taught ``parse_trail_ref``
    the ``last`` selector and this module recognizes it locally too, so
    both spellings agree and the shared oracle stays authoritative.
    """
    raw = (token or "").strip()
    if not raw:
        return None
    if ":" not in raw:
        return ParsedAddress(raw, None)

    from ..core.trails.lineage import parse_trail_ref

    trace_part, step_index, span, reserved = parse_trail_ref(raw)
    trace_part = (trace_part or "").strip()
    if not trace_part:
        return None
    if step_index is not None:
        return ParsedAddress(trace_part, step_index)
    if span is not None:
        return ParsedAddress(trace_part, span)
    addr = raw.partition(":")[2].strip()
    if addr == "last" or reserved == "last":
        return ParsedAddress(trace_part, "last")
    return None


def _probe_resolve(
    prefix: str,
    *,
    cwd: Path | None = None,
    probe: Callable[[Path, str], str | None] | None = None,
) -> str | None:
    """Resolve a short prefix to a full trace_id, or ``None``.

    Any probe failure — no match, :class:`AmbiguousPrefixError`, an
    unreadable meta store — means "not an address" (fail-safe: the
    caller falls through to Click's normal error path). Read-only.
    """
    if probe is None:
        from ..core.trace_meta import resolve_trace_id_prefix as probe_fn
    else:
        probe_fn = probe
    try:
        return probe_fn(Path(cwd) if cwd is not None else Path.cwd(), prefix)
    except Exception:
        return None


def root_dispatch_token(
    token: str,
    *,
    cwd: Path | None = None,
    probe: Callable[[Path, str], str | None] | None = None,
) -> str | None:
    """Return the REF to forward to ``trace get``, or ``None``.

    ``None`` means the token is not a conservatively-dispatchable
    address and the caller must preserve today's behavior byte-identically
    (Click's "No such command"). A non-None return is the token to place
    as ``trace get``'s REF: verbatim for tier-1 syntax, or the
    prefix-resolved full trace_id (with any selector re-attached
    verbatim) for tier-2 short prefixes.
    """
    raw = (token or "").strip()
    if not raw or raw.startswith("-"):
        return None
    if raw.startswith(_SCHEME_PREFIXES) or raw.startswith(_ID_PREFIXES):
        return raw
    parsed = parse_address(raw)
    if parsed is None:
        return None
    left = parsed.trace_part
    if _UUID_RE.match(left) or _LEGACY_RE.match(left):
        # Tier 1: full trace id, bare or with a valid selector. Forward
        # the ORIGINAL token verbatim — selector semantics belong to
        # trace get (F1), never to the dispatcher.
        return raw
    if _HEX_PREFIX_RE.match(left):
        # Tier 2: short hex prefix (bare or colon form). Only a real,
        # uniquely-resolving trace dispatches; the resolved FULL id is
        # substituted so trace get's exact-id resolution succeeds.
        resolved = _probe_resolve(left, cwd=cwd, probe=probe)
        if resolved is None:
            return None
        if parsed.selector is None:
            return resolved
        return f"{resolved}:{raw.partition(':')[2].strip()}"
    return None


def is_root_dispatchable(
    token: str,
    *,
    cwd: Path | None = None,
    probe: Callable[[Path, str], str | None] | None = None,
) -> bool:
    """True when ``token`` passes the two-tier root-dispatch gate."""
    return root_dispatch_token(token, cwd=cwd, probe=probe) is not None
