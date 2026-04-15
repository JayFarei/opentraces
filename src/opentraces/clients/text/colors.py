"""Semantic ANSI palette for ``ot blame`` / ``ot graph`` output.

One role per colour, 16-color ANSI only. Palette table (plan 043 post-audit,
expanded patch):

    Role                       Colour            Weight
    COMMIT_ID + c: glyph       yellow            bold
    TRACE_ID + t: glyph        magenta           bold
    COMMIT_SUBJECT             default           bold
    TRACE_NAME                 cyan              bold
    LINE_COUNT                 blue              normal
    TURN_COUNT                 green             normal
    ENTITY_COUNT               bright-blue       normal
    ADDED (+)                  green             normal
    MODIFIED (~)               yellow            normal
    DELETED (-)                red               normal
    RENAMED (rotate-glyph)     blue              normal
    COVERAGE_GOOD (>=75%)      green             normal
    COVERAGE_PARTIAL (>=50%)   yellow            normal
    COVERAGE_POOR (<50%)       red               normal
    DIM (paths/timestamps)     bright-black      normal
    PRE_AUDIT                  bright-black      dim italic

``paint(role, text, *, use_color)`` returns the wrapped (or plain) text.
``detect_color(no_color_flag, stream)`` resolves the use_color boolean by
honouring ``--no-color``, ``$NO_COLOR``, and ``stream.isatty()``.
"""
from __future__ import annotations

import os
import sys
from enum import Enum
from typing import IO


class Role(Enum):
    COMMIT_ID = "commit_id"
    TRACE_ID = "trace_id"
    COMMIT_SUBJECT = "commit_subject"
    TRACE_NAME = "trace_name"
    LINE_COUNT = "line_count"
    TURN_COUNT = "turn_count"
    ENTITY_COUNT = "entity_count"
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COVERAGE_GOOD = "coverage_good"
    COVERAGE_PARTIAL = "coverage_partial"
    COVERAGE_POOR = "coverage_poor"
    DIM = "dim"
    PRE_AUDIT = "pre_audit"
    # Plan-043 post-audit: 3-part handle rendering. The "t:" / "c:"
    # letters are a dim grey prefix; the 2-char shortcut is bright,
    # bold, and underlined; the remaining chars keep the base colour.
    ID_PREFIX = "id_prefix"
    COMMIT_ID_SHORTCUT = "commit_id_shortcut"
    TRACE_ID_SHORTCUT = "trace_id_shortcut"


RESET = "\x1b[0m"

# role -> ANSI code sequence (SGR). 16-color only (plus bright variants).
_CODES: dict[Role, str] = {
    Role.COMMIT_ID:        "\x1b[1;33m",   # bold yellow
    Role.TRACE_ID:         "\x1b[1;35m",   # bold magenta
    Role.COMMIT_SUBJECT:   "\x1b[1m",      # bold default
    Role.TRACE_NAME:       "\x1b[1;36m",   # bold cyan
    Role.LINE_COUNT:       "\x1b[34m",     # blue
    Role.TURN_COUNT:       "\x1b[32m",     # green
    Role.ENTITY_COUNT:     "\x1b[94m",     # bright blue
    Role.ADDED:            "\x1b[32m",     # green
    Role.MODIFIED:         "\x1b[33m",     # yellow
    Role.DELETED:          "\x1b[31m",     # red
    Role.RENAMED:          "\x1b[34m",     # blue
    Role.COVERAGE_GOOD:    "\x1b[32m",     # green
    Role.COVERAGE_PARTIAL: "\x1b[33m",     # yellow
    Role.COVERAGE_POOR:    "\x1b[31m",     # red
    Role.DIM:              "\x1b[90m",     # bright-black
    Role.PRE_AUDIT:        "\x1b[2;3;90m", # dim italic bright-black
    Role.ID_PREFIX:           "\x1b[2;90m",    # dim bright-black
    Role.COMMIT_ID_SHORTCUT:  "\x1b[1;4;93m",  # bold underline bright-yellow
    Role.TRACE_ID_SHORTCUT:   "\x1b[1;4;95m",  # bold underline bright-magenta
}


def render_handle(kind: str, full_id: str, *,
                  use_color: bool, shortcut_len: int = 2) -> str:
    """Render a 3-part ``t:b73af9c8`` style handle.

    - ``kind`` is one of:
        * ``"t"`` — canonical opentraces trace_id (ingested, clickable
          via ``ot show``). Rendered with the bright trace palette.
        * ``"c"`` — commit sha. Yellow palette.
        * ``"s"`` — attribution-only upstream session id (NOT in the
          opentraces inbox). Dimmed so users can see at a glance that
          this id won't resolve via ``ot show``. See
          ``TraceContribution.canonical`` for the semantic split.
      Case-insensitive. Unknown kinds fall back to ``"t"``.
    - ``full_id`` is the full hash/id; only the first ~10 chars are used.
    - Layout: ``<kind>:`` (ID_PREFIX) + ``<first-N>`` (ID_SHORTCUT) +
      ``<rest>`` (COMMIT_ID / TRACE_ID), emitted as a single contiguous
      token. The bright bold+underline on the shortcut handles visual
      segmentation when colour is on; in ``--no-color`` the ID reads as
      one token, which matches how users paste it back on the command line.
    """
    k = (kind or "").lower()[:1]
    if k not in ("t", "c", "s"):
        k = "t"
    if k == "c":
        head_role = Role.COMMIT_ID_SHORTCUT
        tail_role = Role.COMMIT_ID
    elif k == "s":
        # Attribution-only session id: no dedicated palette, reuse DIM so
        # the whole handle reads as muted next to canonical ``t:`` rows.
        head_role = Role.DIM
        tail_role = Role.DIM
    else:
        head_role = Role.TRACE_ID_SHORTCUT
        tail_role = Role.TRACE_ID
    # Take 2 for shortcut, then up to 6 more for tail (total 8 chars).
    fid = full_id or ""
    shortcut = fid[:shortcut_len]
    tail = fid[shortcut_len:shortcut_len + 6]
    prefix_txt = f"{k}:"
    if not use_color:
        return f"{prefix_txt}{shortcut}{tail}" if tail else f"{prefix_txt}{shortcut}"
    pfx = paint(Role.ID_PREFIX, prefix_txt, use_color=True)
    sc = paint(head_role, shortcut, use_color=True)
    tl = paint(tail_role, tail, use_color=True)
    return f"{pfx}{sc}{tl}" if tail else f"{pfx}{sc}"


def paint(role: Role, text: str, *, use_color: bool) -> str:
    """Wrap ``text`` in the ANSI code for ``role`` if ``use_color`` is True."""
    if not use_color or not text:
        return text
    code = _CODES.get(role)
    if not code:
        return text
    return f"{code}{text}{RESET}"


def coverage_role(pct: float) -> Role:
    """Return the coverage role for a percentage in [0, 100]."""
    if pct >= 75.0:
        return Role.COVERAGE_GOOD
    if pct >= 50.0:
        return Role.COVERAGE_PARTIAL
    return Role.COVERAGE_POOR


def detect_color(no_color_flag: bool, stream: IO[str] | None = None) -> bool:
    """Decide whether ANSI output should be emitted.

    Returns False if:
    - ``no_color_flag`` is set
    - ``NO_COLOR`` env var is set (any non-empty value)
    - ``stream`` is non-TTY (defaults to stdout)
    """
    if no_color_flag:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    s = stream if stream is not None else sys.stdout
    try:
        return bool(s.isatty())
    except (AttributeError, ValueError):
        return False
