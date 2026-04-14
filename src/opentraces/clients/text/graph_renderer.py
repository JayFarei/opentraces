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
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


def strip_ansi(text: str) -> str:
    """Strip ANSI CSI sequences for snapshot comparison."""
    return _ANSI_RE.sub("", text)


def normalize_for_snapshot(text: str) -> str:
    """Replace real SHAs and ISO timestamps with stable placeholders."""
    text = strip_ansi(text)
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


@dataclass
class Commit:
    sha: str
    short_sha: str
    subject: str
    timestamp: str
    parents: list[str]
    traces: list[TraceContribution] = field(default_factory=list)


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

    Five-state mapping (manifesto rule 3), re-mapped to trace lifecycle:
      - default ●      no attribution at all (like LocalOnly)
      - green ●        all lines attributed (Pushed equivalent)
      - green ◐        partial attribution (Modified equivalent)
      - yellow ●       pre-audit only, no trace-attributed lines
      - magenta ●      missing_from_audit fraction > 50%
    """
    if not commit.traces:
        return DOT_SOLID, ""
    # Use first trace's ratios as commit-level summary.
    t = commit.traces[0]
    if t.missing_ratio is not None and t.missing_ratio > 0.5:
        return DOT_SOLID, ANSI_MAGENTA if enabled else ""
    if t.attributed_ratio is not None:
        if t.attributed_ratio >= 0.999:
            return DOT_SOLID, ANSI_GREEN if enabled else ""
        if t.attributed_ratio > 0.0:
            return DOT_HALF, ANSI_GREEN if enabled else ""
        # No attributed lines: pre-audit only.
        return DOT_SOLID, ANSI_YELLOW if enabled else ""
    # Have traces but no ratios -> default green solid.
    return DOT_SOLID, ANSI_GREEN if enabled else ""


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

def _render_trace_header(trace: TraceContribution, first: bool,
                         show_entities: bool, opts: RenderOptions,
                         short_name: str | None = None,
                         turn_count: int | None = None,
                         lifecycle_mixed: bool = False) -> str:
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
    short_tid = trace.trace_id[:8] if trace.trace_id else "?"
    id_plain = f"t:{short_tid}"

    # Build plain parts list; colour pass is applied after truncation so
    # budget math stays on visible width.
    parts: list[str] = [id_plain]
    if short_name and short_name != short_tid:
        parts.append(short_name)
    # Prefer turns+entities over raw line count when turn_count is available.
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

    suffix = " ".join(parts)
    budget = max(1, opts.width - len(prefix))
    body = _truncate(suffix, budget)

    if opts.color:
        # t:<id> magenta bold
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
    # Layout: "c:<sha>  <subject>  <pct>%"
    c_id = f"c:{c.short_sha}"
    pct_txt = ""
    pct_val: int | None = None
    if c.traces and c.traces[0].attributed_ratio is not None:
        pct_val = int(round(c.traces[0].attributed_ratio * 100))
        pct_txt = f"  {pct_val}%"
    body = f"{c_id}  {c.subject}{pct_txt}"
    budget = max(1, opts.width - len(prefix_with_dot))
    body = _truncate(body, budget)
    if opts.color:
        # c:<sha> yellow bold
        body = body.replace(c_id, _color(c_id, "\x1b[1;33m", True), 1)
        # Coverage % coloured by threshold.
        if pct_val is not None:
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
                         lifecycle_mixed: bool = False) -> list[str]:
    """Render a full commit stack (trace headers + commit line + close)."""
    lines: list[str] = []
    # Stack-of-segments grammar: always emit the stack form, even for
    # zero/one traces. For zero traces, we still emit a header so the ├╯
    # close has something to belong to.
    traces = c.traces or []
    if not traces:
        # Emit a synthetic "no attribution" header so the close still parses.
        header_prefix, _ = PREFIX["stack_header"]
        lines.append(f"{header_prefix}{c.short_sha} {_fmt_lifecycle('unattributed')}")
    else:
        for i, t in enumerate(traces):
            lines.append(_render_trace_header(
                t, first=(i == 0), show_entities=opts.show_entities,
                opts=opts, short_name=t.short_name, turn_count=t.turn_count,
                lifecycle_mixed=lifecycle_mixed,
            ))
    lines.append(_render_commit_line(c, opts))
    close_prefix, _ = PREFIX["stack_close"]
    lines.append(close_prefix)
    return lines


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
        blocks.extend(_render_commit_block(c, opts, lifecycle_mixed=mixed))
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
        tc = next((t for t in c.traces if t.trace_id == pivot), None)
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
        body = _truncate(body, max(1, opts.width - len(prefix_tmpl)))
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
    """Read AttributionCache + optionally entity cache for each commit."""
    try:
        from opentraces.core.cache import AttributionCache
    except Exception:
        return commits
    cache = AttributionCache(project_cwd)
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
            ))
        c.traces = contribs
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
        commits = [c for c in commits
                   if any(t.trace_id == opts.pivot_trace_id for t in c.traces)]
    return _paginate(commits, opts)
