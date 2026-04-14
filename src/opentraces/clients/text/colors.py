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
}


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
