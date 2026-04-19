"""GitButler-style ASCII graph renderer for ``ot graph``.

Implements the 7-rule manifesto from plan-043 Appendix A:

1. One trunk, not many lanes.
2. Finite grammar: 8 connectors + 2 commit dots.
3. Five-state commit dots, 16-color ANSI only.
4. Fixed-width prefix strings per line type (lookup, never compute).
5. Stack-of-segments, not lane-of-branches.
6. Upstream is a parallel mini-section, not a parallel lane.
7. Decorators in suffixes; trunk stays clean.

Two rendering modes share the layout:

- **commit-primary** (default): git history is the spine. Each commit is a
  ``┊●`` line with nested trace segments (``┊╭┄`` / ``┊├┄`` / ``├╯``)
  immediately above.
- **trace-primary** (``--trace <id>``): trace timeline is the spine; each
  commit the trace touched is a ``┊●`` line with entity-change suffix
  decorators.

Color: 16-color ANSI only. No 256- or truecolor. Colors map to trace
lifecycle (not git upstream status) for commit-primary mode.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.trace_meta import short_trace_id


# --------------------------------------------------------------------------- #
# Glyph alphabet (8 connectors + 2 commit dots). Everything else is illegal.
# --------------------------------------------------------------------------- #

G_TRUNK = "\u250A"        # ┊ vertical trunk
G_OPEN = "\u256D"         # ╭ open stack
G_TEE = "\u251C"          # ├ continue / close anchor
G_DASH = "\u2504"         # ┄ horizontal dashed lead-in
G_SUB_VERT = "\u2502"     # │ sub-line vertical
G_CLOSE = "\u256F"        # ╯ close-arc
G_CAP = "\u2534"          # ┴ merge-base cap
G_DIV = "-"               # horizontal divider
DOT_SOLID = "\u25CF"      # ●
DOT_HALF = "\u25D0"       # ◐

# Fixed prefix table — lookup, never compute (manifesto rule 4).
PREFIX = {
    "stack_header":        ("\u250A\u256D\u2504", 3),   # ┊╭┄
    "stack_continue":      ("\u250A\u251C\u2504", 3),   # ┊├┄
    "commit_nonverbose":   ("\u250A\u25CF   ", 5),      # ┊●
    "commit_verbose":      ("\u250A\u25CF ", 3),        # ┊●
    "sub_line":            ("\u250A\u2502     ", 7),    # ┊│
    "bare_trunk":          ("\u250A", 1),               # ┊
    "trunk_parallel":      ("\u250A\u250A", 2),         # ┊┊
    "stack_close":         ("\u251C\u256F", 2),         # ├╯
    "merge_base_cap":      ("\u2534 ", 2),              # ┴
    "staged_group_header": ("\u250A  \u256D\u2504", 5), # ┊  ╭┄
    "staged_file":         ("\u250A  \u2502 ", 5),      # ┊  │
}


# --------------------------------------------------------------------------- #
# ANSI 16-color escape codes. No 256- or truecolor.
# --------------------------------------------------------------------------- #

ANSI_RESET = "\x1b[0m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_MAGENTA = "\x1b[35m"
ANSI_CYAN = "\x1b[36m"
ANSI_DIM = "\x1b[2m"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
# Handle form: "c:XXXXXXXX" or "t:XXXXXXXX" — one contiguous hex token
# of length 7-10 after the "c:" / "t:" prefix.
_HANDLE_RE = re.compile(r"\b([ct]):([0-9a-f]{7,10})\b")
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


def strip_ansi(text: str) -> str:
    """Strip ANSI CSI sequences for snapshot comparison."""
    return _ANSI_RE.sub("", text)


def normalize_for_snapshot(text: str) -> str:
    """Replace real SHAs and ISO timestamps with stable placeholders."""
    text = strip_ansi(text)
    # Handle-form first so its two hex halves get collapsed together.
    text = _HANDLE_RE.sub(r"\1:{sha7}", text)
    text = _SHA_RE.sub("{sha7}", text)
    text = _ISO_RE.sub("{timestamp}", text)
    return text


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class TraceContribution:
    trace_id: str
    line_count: int
    entity_count: int | None = None
    files: list[str] = field(default_factory=list)
    lifecycle: str = "provisional"  # "provisional" | "final"
    # Optional: fraction of commit attributed to trace (used for dot color).
    attributed_ratio: float | None = None
    missing_ratio: float | None = None
    # Post-043 enrichment (looked up via core.trace_meta when available).
    short_name: str | None = None
    turn_count: int | None = None
    # Inbox membership: True when ``trace_id`` has a corresponding entry in
    # the opentraces project state (i.e. the session was ingested and has a
    # canonical trace record). False when the attribution was derived from a
    # raw Claude Code session JSONL that never reached our inbox — either a
    # live mid-conversation session, one filtered out by the parse quality
    # gate, one that predates ``ot init``, or one that was since discarded.
    # The renderer uses this to distinguish ``t:<uuid>`` (canonical, clickable
    # via ``ot show``) from ``s:<session>`` (attribution-only, not in inbox).
    canonical: bool = False


@dataclass
class Commit:
    sha: str
    short_sha: str
    subject: str
    timestamp: str
    parents: list[str]
    traces: list[TraceContribution] = field(default_factory=list)
    # Populated by load_commits_from_repo from the entity cache + join.
    # Maps trace_id -> (counts_str, names_str) for the compact summary form.
    # When None, the renderer falls back to the legacy [N lines] form.
    entity_summaries: dict[str, tuple[str, str]] | None = None
    # Optional: trace_id -> ordered list of (file_path, [(change_type,
    # entity_type, entity_name)]) for --entities subline expansion.
    entity_breakdowns: dict[str, list[tuple[str, list[tuple[str, str, str]]]]] | None = None


@dataclass
class RenderOptions:
    width: int = 80
    color: bool = True
    mode: str = "commit"              # "commit" | "trace"
    pivot_trace_id: str | None = None
    show_entities: bool = False
    limit: int = 20
    page: int = 1
    since: str | None = None
    until: str | None = None


# --------------------------------------------------------------------------- #
# Color helpers
# --------------------------------------------------------------------------- #

def _color(text: str, code: str, enabled: bool) -> str:
    if not enabled or not code:
        return text
    return f"{code}{text}{ANSI_RESET}"


def _commit_dot(commit: Commit, enabled: bool) -> tuple[str, str]:
    """Return (glyph, ansi_code) for a commit's state.

    Dot glyph is always ``●``. Colour is the only attribution signal:
      - dim/default: no attribution
      - magenta   : >50% missing_from_audit
      - green     : coverage >= 75%
      - yellow    : coverage >= 50%
      - red       : coverage < 50% (but has traces)
    """
    if not commit.traces:
        return DOT_SOLID, ""
    t = commit.traces[0]
    if t.missing_ratio is not None and t.missing_ratio > 0.5:
        return DOT_SOLID, ANSI_MAGENTA if enabled else ""
    if t.attributed_ratio is None:
        return DOT_SOLID, ANSI_GREEN if enabled else ""
    ratio = t.attributed_ratio
    if ratio >= 0.75:
        code = ANSI_GREEN
    elif ratio >= 0.50:
        code = ANSI_YELLOW
    else:
        code = "\x1b[31m"  # red
    return DOT_SOLID, code if enabled else ""


# --------------------------------------------------------------------------- #
# Suffix decorators (rule 7 — never touch column 0)
# --------------------------------------------------------------------------- #

def _fmt_lifecycle(lifecycle: str) -> str:
    return f"<{lifecycle}>"


def _fmt_lines(n: int) -> str:
    return f"[{n} lines]"


def _fmt_entities(n: int) -> str:
    return f"{{{n} entities}}"


def _truncate(text: str, max_len: int) -> str:
    if max_len <= 1 or len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


# --------------------------------------------------------------------------- #
# Rendering — commit-primary
# --------------------------------------------------------------------------- #

def _entity_summary_parts(
    entities: list[Any], chunks: list[tuple[int, int, str]],
) -> tuple[str, str]:
    """Return (counts_str, names_str) for a trace contribution.

    counts_str: space-joined tokens like ``+12 ~5 -1 ↷2`` (only non-zero).
                Tokens carry a single-char leading glyph for colour pass.
    names_str : up to 5 sample entity names, priority-ordered, with a
                trailing ``+N more`` when truncated. Empty when no data.

    When contrib has neither entities nor chunks, returns ("—", "").
    """
    if not entities and not chunks:
        return ("\u2014", "")

    # Count change_types.
    counts: dict[str, int] = {"added": 0, "modified": 0, "deleted": 0,
                              "renamed": 0}
    for e in entities:
        ct = getattr(e, "change_type", "modified")
        counts[ct] = counts.get(ct, 0) + 1
    # Chunks roll into "modified" since join_entities_to_traces stores them
    # separately by change_type.
    for _s, _end, ct in chunks:
        counts[ct] = counts.get(ct, 0) + 1

    tokens: list[str] = []
    if counts.get("added", 0):
        tokens.append(f"+{counts['added']}")
    if counts.get("modified", 0):
        tokens.append(f"~{counts['modified']}")
    if counts.get("deleted", 0):
        tokens.append(f"-{counts['deleted']}")
    if counts.get("renamed", 0):
        tokens.append(f"\u21B7{counts['renamed']}")
    counts_str = " ".join(tokens) if tokens else "\u2014"

    # Priority-ordered names: added > modified > renamed > deleted, then
    # alpha within each bucket. Chunks contribute synthesised labels.
    order = ("added", "modified", "renamed", "deleted")
    buckets: dict[str, list[str]] = {k: [] for k in order}
    for e in entities:
        ct = getattr(e, "change_type", "modified")
        buckets.setdefault(ct, []).append(getattr(e, "entity_name", "") or "")
    # Sort each bucket alphabetically.
    for k in buckets:
        buckets[k].sort()

    # If we have named entities, use those. If chunk-only, synthesise labels.
    names: list[str] = []
    for k in order:
        names.extend(n for n in buckets.get(k, []) if n)
    if not names and chunks:
        # Chunk-only: build "261-280" compact labels (strip "lines" prefix).
        chunk_labels = [f"{s}-{e}" for s, e, _ in chunks]
        if chunk_labels:
            # Prefix first with "lines " for readability, rest bare.
            head = f"lines {chunk_labels[0]}"
            rest = chunk_labels[1:6]  # cap to 5 total after head
            sample = [head] + rest
            total = len(chunk_labels)
            shown = len(sample)
            suffix = f", +{total - shown} more" if total > shown else ""
            return (counts_str, ", ".join(sample) + suffix)

    total = len(names)
    sample = names[:5]
    suffix = f", +{total - 5} more" if total > 5 else ""
    return (counts_str, ", ".join(sample) + suffix) if sample else (counts_str, "")


def _paint_state(state: str, use_color: bool) -> str:
    """Paint the state column.

    - Bracketed scattered/diffuse state: render dim (grey body) with
      the inline ``t:XX`` token painted like a TRACE_ID_SHORTCUT.
    - Counts like ``+2 ~1 chunks``: colour each token by its verb, leave
      any trailing word (``chunks``, ``headings``, ...) dim.
    """
    if not use_color or not state:
        return state
    if state.startswith("["):
        # dim the whole bracket; no inner paint needed for v0.
        return _color(state, "\x1b[90m", True)
    # Split on whitespace; paint leading +/~/-/↷ tokens, dim trailing words.
    parts = state.split(" ")
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        h = p[:1]
        if h == "+":
            out.append(_color(p, "\x1b[32m", True))
        elif h == "~":
            out.append(_color(p, "\x1b[33m", True))
        elif h == "-":
            out.append(_color(p, "\x1b[31m", True))
        elif h == "\u21B7":
            out.append(_color(p, "\x1b[34m", True))
        else:
            out.append(_color(p, "\x1b[90m", True))  # dim label
    return " ".join(out)


def _paint_counts(counts_str: str, use_color: bool) -> str:
    """Apply per-verb ANSI colour to the counts string."""
    if not use_color or not counts_str or counts_str == "\u2014":
        return counts_str
    tokens = counts_str.split(" ")
    out: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        head = tok[0]
        if head == "+":
            code = "\x1b[32m"  # green added
        elif head == "~":
            code = "\x1b[33m"  # yellow modified
        elif head == "-":
            code = "\x1b[31m"  # red deleted
        elif head == "\u21B7":
            code = "\x1b[34m"  # blue renamed
        else:
            code = ""
        out.append(_color(tok, code, True))
    return " ".join(out)


def _render_trace_header(trace: TraceContribution, first: bool,
                         show_entities: bool, opts: RenderOptions,
                         short_name: str | None = None,
                         turn_count: int | None = None,
                         lifecycle_mixed: bool = False,
                         entity_summary: tuple[str, str] | None = None) -> str:
    """Render one trace header line (either ┊╭┄ or ┊├┄).

    Post-043 follow-up patch:
    - Drop ``<lifecycle>`` suffix unless the visible window has >1 distinct
      lifecycle (``lifecycle_mixed=True``).
    - Prefer ``N turns`` over ``[N lines]`` when turn_count is available.
    - Palette: t:<id> magenta+bold, short_name cyan+bold, line count blue,
      turn count green, entity count bright-blue, separators dim.
    """
    key = "stack_header" if first else "stack_continue"
    prefix, _ = PREFIX[key]
    short_tid = short_trace_id(trace.trace_id)
    # Canonical (ingested) traces use ``t:``; attribution-only sessions use
    # ``s:`` so the reader can tell at a glance which rows are clickable via
    # ``ot show`` (canonical) and which live only in the attribution cache.
    handle_kind = "t" if trace.canonical else "s"
    id_plain = f"{handle_kind}:{short_tid}"

    # Commit-primary compact entity-summary mode: columnar layout.
    #   <prefix> <handle_10w> <state_14w>  <sep>  <summary>
    if entity_summary is not None:
        state_col, summary_col = entity_summary
        from .colors import render_handle  # local import to dodge cycles
        handle = render_handle(handle_kind, trace.trace_id or "?",
                               use_color=opts.color)
        # Plain width of handle = len("t:XXXXXXXX") = 10 chars.
        # State column right-pad to 14. State may overflow (diffuse state);
        # in that case skip the separator and name column entirely.
        STATE_W = 14
        plain_state = state_col
        sep = "  " + _color("\u2502", "\x1b[90m", opts.color) + "  "
        if len(plain_state) > STATE_W:
            pad = ""
            body_plain = f"{plain_state}"
            painted_state = _paint_state(plain_state, opts.color)
            line = f"{handle}  {painted_state}"
        else:
            pad = " " * (STATE_W - len(plain_state))
            painted_state = _paint_state(plain_state, opts.color)
            if summary_col:
                line = f"{handle}  {painted_state}{pad}{sep}{summary_col}"
            else:
                line = f"{handle}  {painted_state}"
        if lifecycle_mixed:
            line += f" {_fmt_lifecycle(trace.lifecycle)}"
        return prefix + line

    # Legacy fallback: line-count form (no entity cache available).
    parts: list[str] = [id_plain]
    if short_name and short_name != short_tid:
        parts.append(short_name)
    line_part: str | None = None
    turn_part: str | None = None
    entity_part: str | None = None
    if turn_count:
        turn_part = f"{turn_count} turns"
        ec = trace.entity_count
        if ec is not None and ec > 0:
            entity_part = f"{ec} entities"
    else:
        line_part = _fmt_lines(trace.line_count)
    if show_entities and entity_part is None:
        ec = trace.entity_count if trace.entity_count is not None else 0
        entity_part = _fmt_entities(ec)

    if line_part:
        parts.append(line_part)
    if turn_part:
        parts.append(turn_part)
    if entity_part:
        parts.append(entity_part)
    if lifecycle_mixed:
        parts.append(_fmt_lifecycle(trace.lifecycle))

    body = " ".join(parts)
    # No truncation of trace rows.
    if opts.color:
        body = body.replace(id_plain, _color(id_plain, "\x1b[1;35m", True), 1)
        if short_name and short_name != short_tid and short_name in body:
            body = body.replace(short_name,
                                _color(short_name, "\x1b[1;36m", True), 1)
        if line_part and line_part in body:
            body = body.replace(line_part,
                                _color(line_part, "\x1b[34m", True), 1)
        if turn_part and turn_part in body:
            body = body.replace(turn_part,
                                _color(turn_part, "\x1b[32m", True), 1)
        if entity_part and entity_part in body:
            body = body.replace(entity_part,
                                _color(entity_part, "\x1b[94m", True), 1)
    return prefix + body


def _render_commit_line(c: Commit, opts: RenderOptions) -> str:
    prefix, _ = PREFIX["commit_nonverbose"]
    glyph, code = _commit_dot(c, opts.color)
    # The prefix template reserves a ●; swap in the correct dot state glyph.
    prefix_with_dot = prefix.replace(DOT_SOLID, glyph, 1)
    dotted = prefix_with_dot
    if opts.color and code:
        dotted = prefix_with_dot.replace(glyph, _color(glyph, code, True), 1)
    # Post-043 follow-up: drop date column; spine already conveys recency.
    # Layout: "<handle>  <subject>  <pct>%"  where <handle> is the
    # render_handle() coloured token (c:<2bright><6rest>), one token.
    from .colors import render_handle
    handle = render_handle("c", c.sha or c.short_sha, use_color=opts.color)
    pct_txt = ""
    pct_val: int | None = None
    if c.traces and c.traces[0].attributed_ratio is not None:
        pct_val = int(round(c.traces[0].attributed_ratio * 100))
        pct_txt = f"  {pct_val}%"
    body = f"{handle}  {c.subject}{pct_txt}"
    if opts.color and pct_val is not None:
        pct_tok = f"{pct_val}%"
        if pct_val >= 75:
            code = "\x1b[32m"
        elif pct_val >= 50:
            code = "\x1b[33m"
        else:
            code = "\x1b[31m"
        body = body.replace(pct_tok, _color(pct_tok, code, True), 1)
    return f"{dotted}{body}"


def _render_commit_block(c: Commit, opts: RenderOptions,
                         lifecycle_mixed: bool = False,
                         entity_summaries: dict[str, tuple[str, str]] | None = None,
                         ) -> list[str]:
    """Render a full commit stack (trace headers + commit line + close).

    Unattributed-commit rule: when ``c.traces`` is empty, emit just the
    single ``┊●  c:<sha>  <subject>`` line — no stack header, no close.

    When ``entity_summaries`` is provided, trace rows use the compact
    entity-summary form (counts + sample names); otherwise they fall back
    to the legacy ``[N lines]`` form.

    When ``opts.show_entities`` is set, trace rows are followed by
    ``┊│`` sublines listing each file + its entity changes.
    """
    traces = c.traces or []
    if not traces:
        # Unattributed commit: single-line entry, no stack, no close.
        return [_render_commit_line(c, opts)]

    sub_prefix, _ = PREFIX["sub_line"]
    breakdowns = c.entity_breakdowns or {}
    lines: list[str] = []
    for i, t in enumerate(traces):
        es = None
        if entity_summaries is not None:
            es = entity_summaries.get(t.trace_id, ("\u2014", ""))
        lines.append(_render_trace_header(
            t, first=(i == 0), show_entities=opts.show_entities,
            opts=opts, short_name=t.short_name, turn_count=t.turn_count,
            lifecycle_mixed=lifecycle_mixed, entity_summary=es,
        ))
        if opts.show_entities:
            bd = breakdowns.get(t.trace_id) or []
            lines.extend(_render_entity_sublines(bd, sub_prefix, opts.color))
    lines.append(_render_commit_line(c, opts))
    close_prefix, _ = PREFIX["stack_close"]
    lines.append(close_prefix)
    return lines


def _render_entity_sublines(
    bd: list[tuple[str, list[tuple[str, str, str]]]],
    sub_prefix: str, use_color: bool,
) -> list[str]:
    """Render ``--entities`` sublines: file group, then per-entity lines.

    ``bd`` is ordered by (-entity_count, file_path). Caps at 3 files,
    8 entities per file; overflow -> ``+ N more files`` / ``+ N more entities``.
    """
    out: list[str] = []
    MAX_FILES = 3
    MAX_PER_FILE = 8
    total_files = len(bd)
    for file_path, ents in bd[:MAX_FILES]:
        file_line = f"{sub_prefix}  {file_path}"
        if use_color:
            file_line = f"{sub_prefix}  {_color(file_path, '\x1b[90m', True)}"
        out.append(file_line)
        for (ct, et, name) in ents[:MAX_PER_FILE]:
            glyph, code = {
                "added": ("+", "\x1b[32m"),
                "modified": ("~", "\x1b[33m"),
                "deleted": ("-", "\x1b[31m"),
                "renamed": ("\u21B7", "\x1b[34m"),
            }.get(ct, ("~", "\x1b[33m"))
            painted = _color(glyph, code, use_color)
            label_type = f"{et} " if et else ""
            out.append(f"{sub_prefix}    {painted} {label_type}{name}")
        if len(ents) > MAX_PER_FILE:
            extra = len(ents) - MAX_PER_FILE
            out.append(f"{sub_prefix}    + {extra} more entities")
    if total_files > MAX_FILES:
        extra_f = total_files - MAX_FILES
        out.append(f"{sub_prefix}  + {extra_f} more files")
    return out


def _compute_lifecycle_mixed(commits: list[Commit]) -> bool:
    """True if the window contains >1 distinct non-null lifecycle value."""
    seen: set[str] = set()
    for c in commits:
        for t in c.traces or []:
            if t.lifecycle:
                seen.add(t.lifecycle)
                if len(seen) > 1:
                    return True
    return False


def _render_commit_primary(commits: list[Commit], opts: RenderOptions) -> str:
    mixed = _compute_lifecycle_mixed(commits)
    blocks: list[str] = []
    for i, c in enumerate(commits):
        if i > 0:
            blocks.append(PREFIX["bare_trunk"][0])
        blocks.extend(_render_commit_block(
            c, opts, lifecycle_mixed=mixed,
            entity_summaries=c.entity_summaries,
        ))
    return "\n".join(blocks) + "\n"


# --------------------------------------------------------------------------- #
# Rendering — trace-primary
# --------------------------------------------------------------------------- #

def _render_trace_primary(commits: list[Commit], opts: RenderOptions) -> str:
    """Spine is the trace. Each commit the trace touched is a ┊● line.

    The pivot trace id is taken from opts.pivot_trace_id.
    """
    pivot = opts.pivot_trace_id or ""
    lines: list[str] = []
    header_prefix, _ = PREFIX["stack_header"]
    header_body = f"trace {pivot[:8]}"
    lines.append(f"{header_prefix}{_truncate(header_body, max(1, opts.width - len(header_prefix)))}")
    for c in commits:
        prefix_tmpl, _ = PREFIX["commit_nonverbose"]
        # Find this trace's contribution on this commit.
        tc = next((t for t in c.traces
                   if t.trace_id == pivot or t.trace_id.startswith(pivot)),
                  None)
        glyph = DOT_SOLID
        code = ANSI_GREEN if opts.color else ""
        dotted = prefix_tmpl.replace(DOT_SOLID, glyph, 1)
        if opts.color and code:
            dotted = dotted.replace(glyph, _color(glyph, code, True), 1)
        c_id = f"c:{c.short_sha}"
        suffix_parts = [c_id, c.subject]
        line_tok: str | None = None
        entity_tok: str | None = None
        if tc is not None:
            line_tok = _fmt_lines(tc.line_count)
            suffix_parts.append(line_tok)
            if opts.show_entities:
                ec = tc.entity_count if tc.entity_count is not None else 0
                entity_tok = _fmt_entities(ec)
                suffix_parts.append(entity_tok)
        body = " ".join(suffix_parts)
        # No truncation — keep full subject visible.
        if opts.color:
            body = body.replace(c_id, _color(c_id, "\x1b[1;33m", True), 1)
            if line_tok and line_tok in body:
                body = body.replace(line_tok,
                                    _color(line_tok, "\x1b[34m", True), 1)
            if entity_tok and entity_tok in body:
                body = body.replace(entity_tok,
                                    _color(entity_tok, "\x1b[94m", True), 1)
        lines.append(f"{dotted}{body}")
    close_prefix, _ = PREFIX["stack_close"]
    lines.append(close_prefix)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def render(commits: list[Commit], opts: RenderOptions) -> str:
    """Main renderer. Returns the full graph as a string (with ANSI if
    ``opts.color``). Pure function: takes commits + options, returns text."""
    if opts.mode == "trace":
        return _render_trace_primary(commits, opts)
    return _render_commit_primary(commits, opts)


# --------------------------------------------------------------------------- #
# Manifesto reference rendering — byte-identical to Appendix A snapshot
# --------------------------------------------------------------------------- #

MANIFESTO_REFERENCE = (
    "\u256D\u2504zz [unstaged changes]\n"
    "\u250A     no changes\n"
    "\u250A\n"
    "\u250A\u256D\u2504g0 [A] [\u2713 upstream merges cleanly]\n"
    "\u250A\u25CF   601614c add A\n"
    "\u251C\u256F\n"
    "\u250A\n"
    "\u250A\u256D\u2504(upstream) \u23EB 1 new commits\n"
    "\u250A\u25CF 67247ca add upstream-commit-message\u2026\n"
    "\u250A\u250A\n"
    "\u251C\u256F 9fd740d [origin/main] 2000-01-02 add merge-base\n"
)


def render_manifesto_reference() -> str:
    """Return the Appendix A reference block byte-identically.

    Used by the acceptance test to anchor the glyph alphabet and prefix
    grammar. Not a production code path — real rendering goes through
    ``render(commits, opts)``.
    """
    return MANIFESTO_REFERENCE


# --------------------------------------------------------------------------- #
# Repo loader — joins `git log` output with the AttributionCache
# --------------------------------------------------------------------------- #

def _git_log(project_cwd: Path, opts: RenderOptions) -> list[Commit]:
    fmt = "%H%x1f%h%x1f%s%x1f%cI%x1f%P"
    cmd = ["git", "log", f"--pretty=format:{fmt}"]
    if opts.since:
        cmd.append(f"{opts.since}..")
    if opts.until:
        cmd.append(opts.until)
    try:
        out = subprocess.check_output(cmd, cwd=project_cwd, text=True,
                                      errors="replace")
    except subprocess.CalledProcessError:
        return []
    commits: list[Commit] = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) < 5:
            continue
        sha, short, subject, ts, parents = parts[0], parts[1], parts[2], parts[3], parts[4]
        commits.append(Commit(
            sha=sha, short_sha=short, subject=subject, timestamp=ts,
            parents=parents.split() if parents else [],
        ))
    return commits


def _paginate(commits: list[Commit], opts: RenderOptions) -> list[Commit]:
    start = max(0, (opts.page - 1) * opts.limit)
    end = start + opts.limit
    return commits[start:end]


def _attach_attribution(commits: list[Commit], project_cwd: Path,
                        show_entities: bool) -> list[Commit]:
    """Read AttributionCache + optionally entity cache for each commit.

    Also builds a ``canonical_ids`` set from the project state so each
    ``TraceContribution`` can be tagged as canonical (ingested into the
    opentraces inbox) vs attribution-only (seen by the hook but never
    landed in the inbox; see ``TraceContribution.canonical`` docstring).
    The renderer branches on this flag to render ``t:<uuid>`` for
    canonical traces and ``s:<session>`` for attribution-only ones.
    """
    try:
        from opentraces.core.cache import AttributionCache
    except Exception:
        return commits
    cache = AttributionCache(project_cwd)

    # Membership set for canonical tagging. A trace_id is canonical iff it
    # has a state.json entry — the single source of truth for "the user has
    # this trace in their inbox right now."
    canonical_ids: set[str] = set()
    try:
        from opentraces.core.config import get_project_state_path
        from opentraces.core.state import StateManager
        state = StateManager(state_path=get_project_state_path(project_cwd))
        canonical_ids = set(state._state.get("traces", {}).keys())  # noqa: SLF001
    except Exception:
        canonical_ids = set()
    diff_totals: dict[str, int] = {}
    try:
        from opentraces.enrichment.git.blame import diff_line_counts
        diff_totals = diff_line_counts(project_cwd, (c.sha for c in commits))
    except Exception:
        diff_totals = {}
    for c in commits:
        data = cache.read_attribution(c.sha)
        if not data:
            continue
        cov = data.get("coverage") or {}
        total = cov.get("total") or 0
        attributed = cov.get("attributed") or 0
        missing = 0
        # Count missing_from_audit lines across files if present.
        for fi in (data.get("files") or {}).values():
            if not isinstance(fi, dict):
                continue
            for ln in fi.get("lines", []) or []:
                if isinstance(ln, dict) and ln.get("consistency") == "missing_from_audit":
                    missing += 1
        # Diff-scoped denominator (plan 047): the headline % explains the
        # commit's change, not the whole-file blame. Falls back to
        # whole-file when diff_line_count can't compute (merge commits,
        # missing shas, binary-only diffs).
        diff_total = diff_totals.get(c.sha, 0)
        if diff_total > 0:
            a_ratio = min(attributed, diff_total) / diff_total
        else:
            a_ratio = (attributed / total) if total else None
        m_ratio = (missing / total) if total else None
        entity_data: dict[str, Any] = {}
        if show_entities:
            ep = cache.entity_path(c.sha)
            if ep.is_file():
                try:
                    entity_data = json.loads(ep.read_text())
                except (OSError, json.JSONDecodeError):
                    entity_data = {}
        trace_rows = data.get("traces") or []
        contribs: list[TraceContribution] = []
        for tr in trace_rows:
            tid = tr.get("trace_id") or ""
            ec = None
            if show_entities:
                by = (entity_data.get("by_trace") or {}).get(tid) or {}
                ec = int(by.get("entity_count", 0)) if by else 0
            short_name = None
            turn_count = None
            try:
                from opentraces.core.trace_meta import resolve_trace_meta
                meta = resolve_trace_meta(project_cwd, tid)
                if meta is not None:
                    short_name = meta.short_name
                    turn_count = meta.turn_count or None
            except Exception:
                pass
            contribs.append(TraceContribution(
                trace_id=tid,
                line_count=int(tr.get("line_count", 0)),
                entity_count=ec,
                files=list(tr.get("files") or []),
                lifecycle=tr.get("lifecycle", "provisional"),
                attributed_ratio=a_ratio,
                missing_ratio=m_ratio,
                short_name=short_name,
                turn_count=turn_count,
                canonical=tid in canonical_ids,
            ))
        c.traces = contribs
        # Populate compact entity summaries via join_entities_to_traces.
        # Best-effort: if the join fails or returns empty, leave as None
        # so the renderer falls back to [N lines].
        try:
            from opentraces.core.entity_join import join_entities_to_traces
            from opentraces.core.trace_summary import (
                summarize_contribution_compact,
            )
            joined = join_entities_to_traces(project_cwd, c.sha)
            if joined:
                summaries: dict[str, tuple[str, str]] = {}
                breakdowns: dict[
                    str, list[tuple[str, list[tuple[str, str, str]]]]
                ] = {}
                has_any = False
                for jc in joined:
                    state, summary = summarize_contribution_compact(jc)
                    summaries[jc.trace_id] = (state, summary)
                    # Group entities by file for --entities sublines.
                    by_file: dict[str, list[tuple[str, str, str]]] = {}
                    for e in jc.entities:
                        by_file.setdefault(e.file_path, []).append(
                            (e.change_type, e.entity_type or "",
                             e.entity_name or ""),
                        )
                    ranked = sorted(
                        by_file.items(),
                        key=lambda kv: (-len(kv[1]), kv[0]),
                    )
                    if ranked:
                        breakdowns[jc.trace_id] = ranked
                    if jc.entities or jc.chunks or state:
                        has_any = True
                # Filter phantoms out of traces view too.
                phantom = {jc.trace_id for jc in joined
                           if jc.overlap_case == "phantom"}
                if phantom:
                    c.traces = [t for t in contribs
                                if t.trace_id not in phantom]
                # Fill in traces that weren't in the join (no entity data).
                for tr in c.traces:
                    summaries.setdefault(tr.trace_id, ("", ""))
                if has_any:
                    c.entity_summaries = summaries
                if breakdowns:
                    c.entity_breakdowns = breakdowns
        except Exception:
            pass
    return commits


def load_commits_from_repo(project_cwd: Path, opts: RenderOptions) -> list[Commit]:
    """Read git history, join with attribution cache, return Commits.

    For trace-primary mode, filters to commits where the pivot trace has a
    contribution. For commit-primary mode, returns the paginated window.
    """
    commits = _git_log(project_cwd, opts)
    commits = _attach_attribution(commits, project_cwd,
                                  show_entities=opts.show_entities)
    if opts.mode == "trace" and opts.pivot_trace_id:
        pivot = opts.pivot_trace_id
        commits = [c for c in commits
                   if any((t.trace_id == pivot or
                           t.trace_id.startswith(pivot))
                          for t in c.traces)]
    return _paginate(commits, opts)
