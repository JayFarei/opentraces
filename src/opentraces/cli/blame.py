"""``ot trail blame commit <sha>`` — per-commit attribution lookup.

Post-043 layout:

    * c:2508ec1  2026-04-09  feat(...): add service catalog...
      73% attributed . 4586 lines . 5 traces . 11 files

      diamond t:b73af9c8   teenage-milestone-e2e   3149 lines . 69%  claude-opus-4-6
        + Added POST, Params to regenerate-token route
        ...

Colour via :mod:`opentraces.clients.text.colors` (16-ANSI only).
Entity-cache ingestion is shape-tolerant — see
:mod:`opentraces.core.entity_join`.

Modes:
    default   — summary + per-trace bullet breakdown
    --lines   — git-blame-style per-line output
    --entities — one bullet per entity under each trace
    --json    — additive JSON payload (entity_contributions added)

`c:<sha>` / `t:<id>` prefixes are accepted on the SHA argument; output
always emits the prefixed forms.
"""

from __future__ import annotations

import json as _json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import project_dir_option
from ..clients.text.colors import (
    Role,
    coverage_role,
    detect_color,
    paint,
    render_handle,
)
from ..clients.text.graph_renderer import (
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_YELLOW,
    _color,
    strip_ansi,
)


# --------------------------------------------------------------------------- #
# Glyphs (blame-scoped)
# --------------------------------------------------------------------------- #

COMMIT_BULLET = "\u25CF"        # ●
TRACE_BULLET = "\u25C6"         # ◆
GLYPH_PRE_AUDIT = "\u00B7"      # ·
GLYPH_MISSING = "?"

# Spine — ties the commit dot to its trace bullets (mirrors ot graph).
SPINE_V = "\u2502"              # │
SPINE_T = "\u251C"              # ├
SPINE_L = "\u2570"              # ╰
SPINE_H = "\u2500"              # ─

# Max characters for the body of an entity-bullet line; longer bodies
# get truncated with a `[...]` suffix to stop one trace from dominating
# the report.
ENTITY_LINE_MAX = 100


def _spine(glyph: str, color: bool) -> str:
    """Paint a spine glyph with the trace-id palette (magenta)."""
    return paint(Role.TRACE_ID, glyph, use_color=color)


def _truncate_body(text: str, max_len: int = ENTITY_LINE_MAX) -> str:
    """Clip long entity-commentary bodies and append ``[...]``."""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 6].rstrip()
    # Avoid ending mid-comma-list.
    cut = cut.rstrip(",").rstrip()
    return f"{cut} [...]"


ENTITY_GLYPHS = {
    "added": ("+", Role.ADDED),
    "modified": ("~", Role.MODIFIED),
    "deleted": ("-", Role.DELETED),
    "renamed": ("\u21B7", Role.RENAMED),
}


def _strip_id_prefix(s: str) -> str:
    if s and s[:2].lower() in ("c:", "t:"):
        return s[2:]
    return s


# --------------------------------------------------------------------------- #
# Git helpers
# --------------------------------------------------------------------------- #

def _git_show_meta(cwd: Path, sha: str) -> tuple[str, str, str] | None:
    try:
        out = subprocess.check_output(
            ["git", "show", "-s", "--format=%H%x1f%s%x1f%cI", sha],
            cwd=cwd, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    parts = out.split("\x1f")
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2]


def _resolve_sha(cwd: Path, ref: str) -> str | None:
    ref = _strip_id_prefix(ref)
    try:
        return subprocess.check_output(
            ["git", "rev-parse", ref], cwd=cwd, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    # rev-parse failed (likely a 2-3 char prefix — git needs 4+ by default).
    # Fall back to a unique-prefix search over the attribution cache keys.
    if ref and len(ref) >= 2 and all(
        ch in "0123456789abcdefABCDEF" for ch in ref
    ):
        try:
            from ..core.cache import AttributionCache
            shas = AttributionCache(cwd).list_attributed_shas()
        except Exception:
            shas = []
        matches = [s for s in shas if s.lower().startswith(ref.lower())]
        if len(matches) == 1:
            return matches[0]
    return None


def _looks_like_file_line_target(arg: str) -> bool:
    if not arg or arg[:2].lower() in ("c:", "t:", "s:"):
        return False
    if ":" not in arg:
        return False
    _path, raw_line = arg.rsplit(":", 1)
    return bool(_path) and raw_line.isdigit()


# --------------------------------------------------------------------------- #
# Cache inflation
# --------------------------------------------------------------------------- #

def _inflate_file_lines(cache: Any, finfo: dict) -> list[dict]:
    if not isinstance(finfo, dict):
        return []
    lines = finfo.get("lines")
    if isinstance(lines, list):
        return lines
    blob = finfo.get("blob_sha256")
    if blob:
        raw = cache.read_blob(blob)
        if raw is not None:
            try:
                parsed = _json.loads(raw.decode("utf-8"))
                if isinstance(parsed, list):
                    return parsed
            except (ValueError, UnicodeDecodeError):
                return []
    return []


def _coverage_pct(data: dict) -> tuple[int, int, float]:
    cov = data.get("coverage") or {}
    attributed = int(cov.get("attributed") or 0)
    total = int(cov.get("total") or 0)
    ratio = float(cov.get("ratio") or (attributed / total if total else 0.0))
    return attributed, total, ratio


# --------------------------------------------------------------------------- #
# Line status (for --lines)
# --------------------------------------------------------------------------- #

def _line_glyph_and_tid(line: dict) -> tuple[str, str]:
    """Return (tid_display, ansi_code) for one attribution line row."""
    cons = line.get("consistency") or "attributed"
    tid = line.get("trace_id") or ""
    if cons == "attributed" and tid:
        return (tid[:8], ANSI_GREEN)
    if cons == "pre-audit":
        return (GLYPH_PRE_AUDIT, ANSI_DIM)
    if cons == "missing_from_audit":
        return (GLYPH_MISSING, ANSI_YELLOW)
    return (GLYPH_PRE_AUDIT, ANSI_DIM)


# --------------------------------------------------------------------------- #
# Default + entity rendering
# --------------------------------------------------------------------------- #

def _render_header(meta: tuple[str, str, str], data: dict, *,
                   files: dict, traces: list[dict],
                   color: bool, project_cwd: Path | None = None) -> list[str]:
    full_sha, subject, ts = meta
    attributed, total, ratio = _coverage_pct(data)
    date_part = (ts or "").split("T", 1)[0]

    # Diff-scoped denominator (plan 047): explain the commit's change, not
    # the whole-file blame. Falls back to whole-file when diff_line_count
    # can't compute (merge commits, detached shas, binary-only diffs).
    diff_total = 0
    if project_cwd is not None:
        try:
            from ..enrichment.git.blame import diff_line_count
            diff_total = diff_line_count(project_cwd, full_sha)
        except Exception:
            diff_total = 0

    if diff_total > 0:
        diff_attr = min(attributed, diff_total)
        headline_pct = int(round((diff_attr / diff_total) * 100))
        headline_num, headline_den = diff_attr, diff_total
        headline_label = "of diff"
    else:
        headline_pct = int(round(ratio * 100))
        headline_num, headline_den = attributed, total
        headline_label = "attributed"

    # c: prefix rendered dim via render_handle (prefix is never colored).
    commit_id = render_handle("c", full_sha, use_color=color)
    subj = paint(Role.COMMIT_SUBJECT, subject, use_color=color)
    date_dim = paint(Role.DIM, date_part, use_color=color)

    line1 = (
        f"{paint(Role.COMMIT_ID, COMMIT_BULLET, use_color=color)} "
        f"Commit: {commit_id}  {date_dim}  {subj}"
    )
    cov_pct = paint(
        coverage_role(float(headline_pct)), f"{headline_pct}%", use_color=color,
    )
    n_traces = len(traces)
    n_files = len(files)
    lead = f"{_spine(SPINE_V, color)} " if traces else "  "
    line2 = (
        f"{lead}Coverage: {cov_pct} {headline_label} "
        f"({headline_num}/{headline_den} lines)  "
        f"{n_traces} traces  {n_files} files"
    )
    out = [line1, line2]
    # Secondary dim line: whole-file blame, when it differs from the
    # headline. Gives operators diagnostic signal (churn-prone files)
    # without crowding the lead number.
    if diff_total > 0 and total > 0 and (attributed != headline_num or total != headline_den):
        file_pct = int(round(ratio * 100))
        lead2 = f"{_spine(SPINE_V, color)} " if traces else "  "
        file_line = paint(
            Role.DIM,
            f"  file-wide: {file_pct}% ({attributed}/{total} lines)",
            use_color=color,
        )
        out.append(f"{lead2}{file_line}")
    return out


def _iter_trace_blocks(
    project_cwd: Path, sha: str, data: dict, color: bool,
    verbose_entities: bool, scope_file: str | None,
) -> list[str]:
    """Render the per-trace contribution blocks with a magenta spine
    connecting each trace to its parent commit.

    Layout:

        │                             ← spine continuation
        ├─◆ t:abcd1234  short-name    ← non-last trace header
        │   + Added foo, bar, [...]   ← entity bullet (spine continues)
        ╰─◆ t:ef567890  ...           ← last trace header (corner)
            - Removed baz             ← entity bullet (no spine, just indent)
    """
    from ..core.entity_join import join_entities_to_traces
    from ..core.trace_meta import resolve_trace_meta
    from ..core.trace_summary import (
        summarize_contribution,
        summarize_contribution_verbose,
    )

    _attr, total, _r = _coverage_pct(data)
    contribs = join_entities_to_traces(project_cwd, sha)
    by_tid = {c.trace_id: c for c in contribs}

    # Per-trace % is diff-scoped: we count the *new-file lines this commit
    # introduced* that are attributed to each trace, and divide by the
    # commit's diff size. This makes the per-trace % read in the same unit
    # as the "Coverage: N% of diff" headline. When ``diff_line_set`` is
    # empty (deletion-only / rename-only / mode-only / binary-only / merge
    # commits) there are no post-image lines to attribute, so the per-
    # trace view is rendered as "coverage unavailable" rather than fake
    # percentages against a whole-file denominator.
    diff_total = 0
    diff_set: dict[str, set[int]] = {}
    try:
        from ..enrichment.git.blame import diff_line_count, diff_line_set
        diff_total = diff_line_count(project_cwd, sha)
        diff_set = diff_line_set(project_cwd, sha)
    except Exception:
        diff_total = 0
        diff_set = {}

    # Cross-reference the cached per-line attribution against the diff's
    # new-file line-number set to derive per-trace diff-scoped counts.
    per_trace_diff_count: dict[str, int] = {}
    if diff_set:
        from ..core.cache import AttributionCache
        from ..core.entity_join import _inflate_lines
        cache = AttributionCache(project_cwd)
        files_data = data.get("files") or {}
        for path, line_numbers in diff_set.items():
            finfo = files_data.get(path)
            if not isinstance(finfo, dict):
                continue
            lines = _inflate_lines(cache, finfo)
            if not lines:
                continue
            # Build an n -> trace_id lookup once per file.
            by_n = {
                int(ln.get("n") or 0): ln.get("trace_id")
                for ln in lines
                if isinstance(ln, dict) and ln.get("n") is not None
            }
            for n in line_numbers:
                tid = by_n.get(n)
                if tid:
                    per_trace_diff_count[tid] = per_trace_diff_count.get(tid, 0) + 1

    # Build the inbox-membership set so each trace row can pick the right
    # handle prefix: ``t:`` for canonical (ingested) traces that resolve via
    # ``ot show``, ``s:`` for attribution-only upstream sessions that live
    # in the attribution cache but never landed in the opentraces inbox
    # (live/mid-conversation, below parse gate, pre-init, or discarded).
    canonical_ids: set[str] = set()
    try:
        from ..core.config import get_project_state_path
        from ..core.state import StateManager
        _state = StateManager(state_path=get_project_state_path(project_cwd))
        canonical_ids = set(_state._state.get("traces", {}).keys())  # noqa: SLF001
    except Exception:
        canonical_ids = set()

    traces = list(data.get("traces") or [])
    seen_tids = {t.get("trace_id") for t in traces if t.get("trace_id")}
    for c in contribs:
        if c.trace_id and c.trace_id not in seen_tids:
            traces.append({"trace_id": c.trace_id,
                           "line_count": c.line_count,
                           "files": [], "lifecycle": None})
            seen_tids.add(c.trace_id)

    # Filter out traces without an id before computing "last" position.
    traces = [t for t in traces if t.get("trace_id")]
    if not traces:
        return []

    out: list[str] = []
    # Blank spine row — visual break between commit header and first trace.
    out.append(_spine(SPINE_V, color))

    body_spine_v = _spine(SPINE_V, color)
    body_dash2 = _spine(SPINE_H * 2, color)

    for idx, tr in enumerate(traces):
        is_last = idx == len(traces) - 1
        tee = _spine(SPINE_L if is_last else SPINE_T, color)
        # Leading glyph for body lines under this trace:
        #   non-last: "│   " (spine continues beside the entity bullets)
        #   last:     "    " (no spine, plain indent)
        body_prefix = f"{body_spine_v}   " if not is_last else "    "

        tid = tr.get("trace_id") or ""
        lc_whole = int(tr.get("line_count") or 0)
        from ..core.trace_meta import short_trace_id as _short_tid
        short_id = _short_tid(tid)
        meta = resolve_trace_meta(project_cwd, tid)
        short_name = meta.short_name if meta and meta.short_name else short_id
        model = (meta.model if meta else None) or ""

        # ``t:`` for canonical (in-inbox) traces, ``s:`` for attribution-only
        # upstream sessions — see TraceContribution.canonical for the rule.
        handle_kind = "t" if tid in canonical_ids else "s"
        id_paint = render_handle(handle_kind, tid, use_color=color)
        bullet = paint(Role.TRACE_ID, TRACE_BULLET, use_color=color)
        name_paint = paint(Role.TRACE_NAME, short_name, use_color=color)
        model_paint = paint(Role.DIM, model, use_color=color)

        # Three display modes for the "lines · %" column, picked per commit:
        #
        # 1. diff-scoped (preferred): we know the commit's diff; show
        #    "<diff_lines> of <diff_total> diff lines · <pct>%" with a
        #    muted "(<whole-file>)" suffix for context. Sums cleanly.
        # 2. no-diff-signal fallback (diff_total == 0): deletion-only /
        #    rename-only / mode-only / binary-only / merge / initial-commit.
        #    Show "(coverage unavailable)" and dim the whole-file count as
        #    context rather than pretending a percentage exists.
        # 3. diff-available but this trace owns nothing in the diff: show
        #    "0 of <diff_total> diff lines · 0%" so the reader can see the
        #    trace was present on the touched files but didn't author any
        #    of the new-file lines.
        if diff_total > 0:
            lc_diff = per_trace_diff_count.get(tid, 0)
            pct = int(round((lc_diff / diff_total) * 100)) if diff_total else 0
            pct_paint = paint(
                coverage_role(float(pct)), f"{pct}%", use_color=color,
            )
            lines_text = f"{lc_diff} of {diff_total} diff lines . {pct_paint}"
            if lc_whole and lc_whole != lc_diff:
                lines_text += "  " + paint(
                    Role.DIM, f"({lc_whole} file-wide)", use_color=color,
                )
        else:
            unavailable = paint(
                Role.DIM, "(coverage unavailable)", use_color=color,
            )
            if lc_whole:
                lines_text = (
                    unavailable + "  "
                    + paint(Role.DIM, f"{lc_whole} file-wide", use_color=color)
                )
            else:
                lines_text = unavailable

        header = (
            f"{tee}{body_dash2} {bullet} {id_paint}   {name_paint}  "
            f"{lines_text}"
        )
        if model:
            header += f"   {model_paint}"
        out.append(header)

        contrib = by_tid.get(tid)
        if contrib is None:
            from ..core.entity_join import TraceContribution as _TC
            contrib = _TC(trace_id=tid, line_count=lc_whole,
                          line_ratio=(lc_whole / total) if total else 0.0)

        if verbose_entities:
            bullets = summarize_contribution_verbose(contrib)
        else:
            bullets = summarize_contribution(contrib)

        if verbose_entities:
            for b in bullets:
                head = b.split(" ", 1)
                if head:
                    g = head[0]
                    role = {
                        "+": Role.ADDED, "~": Role.MODIFIED,
                        "-": Role.DELETED, "\u21B7": Role.RENAMED,
                    }.get(g, Role.MODIFIED)
                    rest = _truncate_body(head[1] if len(head) > 1 else "")
                    out.append(
                        f"{body_prefix}{paint(role, g, use_color=color)} {rest}"
                    )
                else:
                    out.append(f"{body_prefix}{b}")
            for e in contrib.entities:
                g, role = ENTITY_GLYPHS.get(e.change_type, ("~", Role.MODIFIED))
                et = e.entity_type or ""
                if e.change_type == "renamed" and e.old_entity_name:
                    label = f"renamed {e.old_entity_name} \u2192 {e.entity_name}"
                elif e.change_type == "deleted":
                    label = f"{et} deleted {e.entity_name}".strip()
                else:
                    label = f"{et} {e.entity_name}".strip()
                out.append(
                    f"{body_prefix}  {paint(role, g, use_color=color)} "
                    f"{label:<28} {paint(Role.DIM, e.file_path, use_color=color)}"
                )
        else:
            for b in bullets:
                head = b.split(" ", 1)
                g = head[0] if head else "~"
                role = {
                    "+": Role.ADDED, "~": Role.MODIFIED,
                    "-": Role.DELETED, "\u21B7": Role.RENAMED,
                }.get(g, Role.MODIFIED)
                rest = _truncate_body(head[1] if len(head) > 1 else "")
                out.append(
                    f"{body_prefix}{paint(role, g, use_color=color)} {rest}"
                )

        # Blank spine row between traces (no spine after the last one).
        if not is_last:
            out.append(body_spine_v)

    return out


def _hook_linked_only_trace_ids(
    project_cwd: Path, sha: str, attribution_trace_ids: set[str],
) -> list[str]:
    """Return trace_ids linked via ``refs/notes/opentraces`` that are not
    already present in the attribution cache. Empty on any failure.

    Also filters out notes entries that overlap with attribution by
    session_id (legacy caches key on session_id; the real trace_id in
    notes may correspond to an attribution row keyed on session_id).
    """
    from ..enrichment.git import notes_store
    from ..core.config import get_project_traces_dir
    from ..core.inbox import load_trace_records

    try:
        tids = notes_store.trace_ids_for_commit(sha, project_cwd)
    except Exception:
        return []
    if not tids:
        return []

    # Expand attribution IDs with matching session_ids from staging so
    # we don't double-list a trace whose canonical trace_id is in notes
    # but whose session_id is what the attribution cache knows.
    attr_expanded: set[str] = set(attribution_trace_ids)
    try:
        staging = get_project_traces_dir(project_cwd)
        if staging.exists():
            for r in load_trace_records(staging):
                sid = getattr(r, "session_id", "") or ""
                if r.trace_id in attr_expanded:
                    if sid:
                        attr_expanded.add(sid)
                elif sid and sid in attr_expanded:
                    attr_expanded.add(r.trace_id)
    except Exception:
        pass

    return [tid for tid in tids if tid not in attr_expanded]


def _trail_projection_for(project_cwd: Path):
    try:
        from ..core.trails import build_trail_query_projection

        return build_trail_query_projection(project_cwd)
    except Exception:
        return None


def _trail_projection_for_commits(project_cwd: Path, commit_shas: set[str]):
    """Commit-scoped Trail Query projection (plan 120, #120).

    ``trail blame commit`` only ever reads ``anchors_for_commit_with_survival``
    for the resolved commit sha, so the projection can be built from the
    commit-scoped slice of the canonical event log instead of a whole-log walk.
    Falls back to the full builder if the scoped read raises, so the
    behaviour (and the None-on-error contract) is unchanged.
    """
    try:
        from ..core.trails import build_trail_query_projection_for_commits

        return build_trail_query_projection_for_commits(project_cwd, commit_shas)
    except Exception:
        return _trail_projection_for(project_cwd)


def _trail_trace_id_for_prefix(project_cwd: Path, prefix: str) -> str | None:
    projection = _trail_projection_for(project_cwd)
    if projection is None:
        return None
    return projection.resolve_trace_prefix(prefix)


def _trail_evidence_for_commit(
    project_cwd: Path,
    sha: str,
    scope_file: str | None = None,
) -> list[dict]:
    projection = _trail_projection_for_commits(project_cwd, {sha})
    if projection is None:
        return []
    rows = projection.anchors_for_commit_with_survival(sha)
    if scope_file:
        rows = [
            row
            for row in rows
            if row.get("file_path") == scope_file or row.get("path") == scope_file
        ]
    return rows


def _projection_disagreements(
    data: dict,
    hook_linked_ids: list[str],
    trail_rows: list[dict],
) -> tuple[list[dict], list[str]]:
    legacy_ids = {
        t.get("trace_id")
        for t in (data.get("traces") or [])
        if t.get("trace_id")
    }
    legacy_ids.update(hook_linked_ids)
    trail_ids = {
        row.get("trace_id")
        for row in trail_rows
        if row.get("trace_id")
    }
    disagreements: list[dict] = []
    limitations: list[str] = []
    if trail_rows and not legacy_ids:
        limitations.append("attribution_cache_missing_trail_events_used")
    if legacy_ids and trail_ids:
        missing_from_legacy = sorted(trail_ids - legacy_ids)
        missing_from_trails = sorted(legacy_ids - trail_ids)
        if missing_from_legacy:
            disagreements.append(
                {
                    "type": "trail_events_not_in_legacy_projection",
                    "trace_ids": missing_from_legacy,
                    "trace_patch_ids": sorted(
                        row.get("trace_patch_id")
                        for row in trail_rows
                        if row.get("trace_id") in missing_from_legacy
                        and row.get("trace_patch_id")
                    ),
                }
            )
        if missing_from_trails:
            disagreements.append(
                {
                    "type": "legacy_projection_not_in_trail_events",
                    "trace_ids": missing_from_trails,
                }
            )
    return disagreements, limitations


def _render_trail_evidence_section(
    trail_rows: list[dict],
    sha: str,
    color: bool,
) -> list[str]:
    if not trail_rows:
        return []
    rule = paint(Role.DIM, SPINE_H * 72, use_color=color)
    out = ["", rule, ""]
    out.append(
        "Trace Trails "
        + paint(Role.DIM, "(canonical event log evidence)", use_color=color)
    )
    out.append("")
    for row in trail_rows:
        trace = render_handle("t", row.get("trace_id") or "?", use_color=color)
        patch_id = row.get("trace_patch_id") or "?"
        anchor_id = row.get("git_anchor_id") or "?"
        path = row.get("file_path") or row.get("path") or "?"
        line_range = row.get("range") or row.get("affected_range") or {}
        start = line_range.get("start_line") or "?"
        evidence = row.get("evidence_tier") or "unknown"
        firmness = row.get("evidence_firmness") or row.get("firmness") or "unknown"
        out.append(f"  {trace}  {path}:{start}  {evidence} ({firmness})")
        out.append(f"    Trace Patch: {patch_id}")
        out.append(f"    Git Anchor:  {anchor_id}")
    out.append("")
    out.append(f"  explain: otd trail explain --commit {sha}")
    return out


def _render_hook_linked_section(
    project_cwd: Path, trace_ids: list[str], color: bool,
) -> list[str]:
    """Append a "Hook-linked traces (no per-line data)" section listing
    notes-only trace_ids with best-effort short-name + model.
    """
    from ..core.trace_meta import resolve_trace_meta

    if not trace_ids:
        return []
    rule = paint(Role.DIM, SPINE_H * 72, use_color=color)
    out = ["", rule, ""]
    out.append(
        "Hook-linked traces "
        + paint(
            Role.DIM, "(tool-emit evidence; no per-line attribution)",
            use_color=color,
        )
    )
    out.append("")
    for tid in trace_ids:
        meta = resolve_trace_meta(project_cwd, tid)
        name = (meta.short_name if meta and meta.short_name else "")
        model = (meta.model if meta else None) or ""
        handle = render_handle("t", tid, use_color=color)
        name_p = paint(Role.TRACE_NAME, name, use_color=color) if name else ""
        model_p = paint(Role.DIM, model, use_color=color) if model else ""
        row = f"  {handle}"
        if name:
            row += f"   {name_p}"
        if model:
            row += f"   {model_p}"
        out.append(row)
    return out


def _render_default(meta: tuple[str, str, str], data: dict,
                    project_cwd: Path, scope_file: str | None,
                    color: bool, *, show_entities: bool = False,
                    trail_rows: list[dict] | None = None) -> str:
    files = data.get("files") or {}
    traces = data.get("traces") or []
    lines: list[str] = []
    lines.extend(_render_header(meta, data, files=files, traces=traces,
                                color=color, project_cwd=project_cwd))
    lines.extend(_iter_trace_blocks(
        project_cwd, meta[0], data, color,
        verbose_entities=show_entities, scope_file=scope_file,
    ))

    hook_linked = _hook_linked_only_trace_ids(
        project_cwd, meta[0],
        {t.get("trace_id") for t in traces if t.get("trace_id")},
    )
    if hook_linked:
        lines.extend(_render_hook_linked_section(
            project_cwd, hook_linked, color,
        ))
    if trail_rows is None:
        trail_rows = _trail_evidence_for_commit(project_cwd, meta[0], scope_file)
    if trail_rows:
        lines.extend(_render_trail_evidence_section(trail_rows, meta[0], color))

    # Files block — simple, aligned, separated from the trace tree by a
    # dim horizontal rule so the two sections read as distinct groups.
    if files:
        rows: list[tuple[str, int, int, int, int]] = []
        for path in sorted(files):
            if scope_file and path != scope_file:
                continue
            finfo = files[path] or {}
            total_f = int(finfo.get("total") or 0)
            attr = pre = miss = 0
            for ln in finfo.get("lines") or []:
                c = (ln or {}).get("consistency") or "attributed"
                if c == "attributed":
                    attr += 1
                elif c == "pre-audit":
                    pre += 1
                elif c == "missing_from_audit":
                    miss += 1
            rows.append((path, total_f, attr, pre, miss))
        if rows:
            path_w = max(len(r[0]) for r in rows)
            total_w = max(len(str(r[1])) for r in rows)
            rule = paint(Role.DIM, SPINE_H * 72, use_color=color)
            lines.append("")
            lines.append(rule)
            lines.append("")
            lines.append("Files:")
            lines.append("")
            for path, total_f, attr, pre, miss in rows:
                parts = [f"{attr} attributed"]
                if pre:
                    parts.append(f"{pre} pre-audit")
                if miss:
                    parts.append(f"{miss} missing")
                path_cell = paint(Role.DIM, path.ljust(path_w), use_color=color)
                total_cell = str(total_f).rjust(total_w)
                breakdown = paint(
                    Role.DIM, f"({', '.join(parts)})", use_color=color,
                )
                lines.append(
                    f"  {path_cell}  {total_cell} line(s)  {breakdown}"
                )
    return "\n".join(lines) + "\n"


def _render_lines(data: dict, cache: Any, scope_file: str | None,
                  color: bool) -> str:
    out: list[str] = []
    files = data.get("files") or {}
    for path in sorted(files):
        if scope_file and path != scope_file:
            continue
        out.append(f"{paint(Role.DIM, path, use_color=color)}:")
        finfo = files[path] or {}
        inflated = _inflate_file_lines(cache, finfo)
        for ln in sorted(inflated, key=lambda r: int(r.get("n") or 0)):
            n = int(ln.get("n") or 0)
            tid_disp, code = _line_glyph_and_tid(ln)
            tid_col = _color(tid_disp, code, color)
            pad = max(0, 8 - len(strip_ansi(tid_col).rstrip()))
            tid_cell = tid_col + (" " * pad)
            out.append(f"  {n:>4} \u2502 {tid_cell} \u2502")
    if not out:
        return "(no files)\n"
    return "\n".join(out) + "\n"


def _load_entity_cache_raw(cache: Any, sha: str) -> dict | None:
    p = cache.entity_path(sha)
    if not p.is_file():
        return None
    try:
        return _json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _build_json_payload(meta: tuple[str, str, str], data: dict,
                        entity_data_raw: dict | None, cache: Any,
                        scope_file: str | None,
                        include_entities: bool,
                        project_cwd: Path) -> dict:
    """Build JSON payload. Backward-compatible: keys ``commit``, ``coverage``,
    ``traces``, ``files`` stay stable. Add ``entity_contributions`` when
    entity data is present (never drops existing keys)."""
    full_sha, subject, ts = meta
    attributed, total, ratio = _coverage_pct(data)
    files_out: dict = {}
    for path, finfo in (data.get("files") or {}).items():
        if scope_file and path != scope_file:
            continue
        finfo = finfo or {}
        inflated = _inflate_file_lines(cache, finfo)
        files_out[path] = {
            "total": int(finfo.get("total") or 0),
            "lines": inflated,
        }
    hook_linked_ids = _hook_linked_only_trace_ids(
        project_cwd, full_sha,
        {t.get("trace_id") for t in (data.get("traces") or [])
         if t.get("trace_id")},
    )
    payload: dict = {
        "commit": {"sha": full_sha, "subject": subject, "timestamp": ts},
        "coverage": {"attributed": attributed, "total": total, "ratio": ratio},
        "traces": data.get("traces") or [],
        "files": files_out,
        "hookLinked": [
            {"trace_id": tid, "id": tid[:8]} for tid in hook_linked_ids
        ],
    }
    trail_rows = _trail_evidence_for_commit(project_cwd, full_sha, scope_file)
    disagreements, projection_limitations = _projection_disagreements(
        data,
        hook_linked_ids,
        trail_rows,
    )
    payload["trailEvidence"] = trail_rows
    payload["projection_disagreements"] = disagreements
    payload["projection_limitations"] = projection_limitations
    # Legacy "entities" key — keep for back-compat.
    if include_entities:
        if entity_data_raw is None:
            payload["entities"] = []
        else:
            ents = (entity_data_raw.get("entities") or
                    entity_data_raw.get("changes") or [])
            if scope_file:
                ents = [e for e in ents if (
                    e.get("file") == scope_file or
                    e.get("filePath") == scope_file
                )]
            payload["entities"] = ents

    # New: per-trace entity_contributions.
    from ..core.entity_join import join_entities_to_traces
    from ..core.trace_meta import resolve_trace_meta, short_trace_id
    contribs = join_entities_to_traces(project_cwd, full_sha)
    ec_out: list[dict] = []
    for c in contribs:
        meta_obj = resolve_trace_meta(project_cwd, c.trace_id)
        ec_out.append({
            "trace_id": c.trace_id,
            "short_name": (meta_obj.short_name if meta_obj else None) or short_trace_id(c.trace_id),
            "line_count": c.line_count,
            "entities": [
                {
                    "change_type": e.change_type,
                    "entity_type": e.entity_type,
                    "entity_name": e.entity_name,
                    "file_path": e.file_path,
                    "old_entity_name": e.old_entity_name,
                } for e in c.entities
            ],
            "chunks": [
                {"start": s, "end": e, "change_type": ct}
                for s, e, ct in c.chunks
            ],
        })
    payload["entity_contributions"] = ec_out
    return payload


# --------------------------------------------------------------------------- #
# First-run prompt
# --------------------------------------------------------------------------- #

def _stdin_isatty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _print_empty_cache_guidance() -> None:
    click.echo(
        "Attribution cache is empty for this commit. "
        "Run `ot backfill` or wait for the watcher.",
        err=True,
    )


def _maybe_prompt_first_run(project_dir: Path, sha: str) -> bool:
    from ..core import backfill as _backfill
    from ..core.config import (
        get_first_run_backfill_decision,
        set_first_run_backfill_decision,
    )

    decision = get_first_run_backfill_decision(project_dir)
    if decision == "never":
        _print_empty_cache_guidance()
        return False
    if not _stdin_isatty():
        _print_empty_cache_guidance()
        return False
    try:
        answer = click.prompt(
            "Attribution cache empty for HEAD. Run backfill now? [Y/n/never]",
            default="Y",
            show_default=False,
        )
    except click.Abort:
        _print_empty_cache_guidance()
        return False
    a = (answer or "").strip().lower()
    if a in ("n", "no"):
        set_first_run_backfill_decision(project_dir, "declined")
        _print_empty_cache_guidance()
        return False
    if a == "never":
        set_first_run_backfill_decision(project_dir, "never")
        _print_empty_cache_guidance()
        return False
    set_first_run_backfill_decision(project_dir, "Y")
    try:
        _backfill.run_full(project_dir)
    except Exception as e:  # pragma: no cover
        click.echo(f"(backfill failed: {e})", err=True)
        return False
    from ..core.cache import AttributionCache
    return AttributionCache(project_dir).has_attribution(sha)


# --------------------------------------------------------------------------- #
# Trace-mode (inverse blame) rendering
# --------------------------------------------------------------------------- #

def _render_trace_blame(
    project_cwd: Path, trace_id: str, *,
    color: bool,
    include_overlapping: bool = False,
) -> str:
    """Inverse blame: commits a trace contributed to.

    Mirrors the commit-centric layout: one header line (``◆ t:… short-name
    model``), a coverage line summarising the result, then per-commit rows
    with a magenta spine tying them to the trace header. Attribution-cache
    rows show real line counts + percentages; git_links rows show a tier
    badge (``tool-emitted`` / ``divergent``).
    """
    from ..core import inverse_blame as _ib

    result = _ib.compute(project_cwd, trace_id,
                         include_overlapping=include_overlapping)

    lines: list[str] = []
    t_bullet = paint(Role.TRACE_ID, TRACE_BULLET, use_color=color)
    t_handle = render_handle("t", result.trace_id, use_color=color)
    name_paint = paint(Role.TRACE_NAME, result.short_name or result.short_id,
                       use_color=color)
    model_paint = paint(Role.DIM, result.model, use_color=color) if result.model else ""

    header = f"{t_bullet} Trace: {t_handle}  {name_paint}"
    if result.model:
        header += f"   {model_paint}"
    lines.append(header)

    n_att = len(result.attribution_commits)
    n_links = len(result.git_link_commits)
    n_trails = len(result.trail_event_commits)
    total = len(result.commits)
    summary_parts = [f"{total} commit{'' if total == 1 else 's'}"]
    if n_att:
        pct_covered = int(round(100 * sum(c.pct for c in result.attribution_commits) /
                                (n_att * 100))) if n_att else 0
        summary_parts.append(
            f"{n_att} with line-level attribution"
            + (f" ({result.total_lines} lines)" if result.total_lines else "")
        )
        # avoid confusing averages; users read per-row pcts below
        _ = pct_covered
    if n_links:
        summary_parts.append(f"{n_links} hook-linked")
    if n_trails:
        summary_parts.append(f"{n_trails} trail-anchored")

    spine_v = _spine(SPINE_V, color)
    lines.append(f"{spine_v} {' · '.join(summary_parts)}")

    if total == 0:
        lines.append(f"{spine_v}")
        lines.append("  (no commits attributed to this trace yet)")
        return "\n".join(lines) + "\n"

    lines.append(spine_v)  # blank spine row before list

    body_dash2 = _spine(SPINE_H * 2, color)
    for idx, commit in enumerate(result.commits):
        is_last = idx == total - 1
        tee = _spine(SPINE_L if is_last else SPINE_T, color)
        c_bullet = paint(Role.COMMIT_ID, COMMIT_BULLET, use_color=color)
        c_handle = render_handle("c", commit.sha, use_color=color)
        subj = paint(Role.COMMIT_SUBJECT, commit.subject or "(no subject)",
                     use_color=color)

        row = f"{tee}{body_dash2} {c_bullet} {c_handle}  {subj}"

        if commit.source == "attribution":
            if commit.total_commit_lines > 0:
                pct_role = coverage_role(float(commit.pct))
                pct_paint = paint(pct_role, f"{commit.pct}%", use_color=color)
                lines_text = (
                    f"{commit.lines_in_commit} of {commit.total_commit_lines} "
                    f"diff lines . {pct_paint}"
                )
            else:
                lines_text = (
                    f"{commit.lines_in_commit} attributed . "
                    + paint(Role.DIM, "0 diff lines", use_color=color)
                )
            row += f"  {lines_text}"
        elif commit.source == "trail_events":
            tier_text = commit.tier or "trail-events"
            tier_paint = paint(Role.ADDED, tier_text, use_color=color)
            row += f"  {tier_paint}"
            if commit.lines_in_commit:
                row += "  " + paint(
                    Role.DIM,
                    f"{commit.lines_in_commit} anchored line"
                    f"{'' if commit.lines_in_commit == 1 else 's'}",
                    use_color=color,
                )
        else:
            # git_links tier badge — no reliable per-line count.
            tier_text = {
                "tool_emitted": "tool-emitted",
                "tool_emitted_with_divergence": "divergent",
                "overlapping": "overlapping",
            }.get(commit.tier or "", commit.tier or "")
            tier_role = {
                "tool_emitted": Role.ADDED,
                "tool_emitted_with_divergence": Role.MODIFIED,
                "overlapping": Role.DIM,
            }.get(commit.tier or "", Role.DIM)
            tier_paint = paint(tier_role, tier_text, use_color=color)
            row += f"  {tier_paint}"
            if commit.total_commit_lines:
                row += "  " + paint(
                    Role.DIM,
                    f"({commit.total_commit_lines} diff lines)",
                    use_color=color,
                )
        lines.append(row)

    if result.files:
        rule = paint(Role.DIM, SPINE_H * 72, use_color=color)
        lines.append("")
        lines.append(rule)
        lines.append("")
        lines.append("Files touched by this trace:")
        lines.append("")
        pw = max(len(p) for p, _ in result.files)
        for path, n in result.files[:20]:
            lines.append(
                f"  {paint(Role.DIM, path.ljust(pw), use_color=color)}  "
                f"{n} line{'' if n == 1 else 's'}"
            )
        if len(result.files) > 20:
            lines.append(f"  ... +{len(result.files) - 20} more")

    return "\n".join(lines) + "\n"


def _build_trace_json_payload(
    project_cwd: Path, trace_id: str, *, include_overlapping: bool,
) -> dict:
    """JSON shape for ``ot blame t:<id> --json``. Matches the web payload
    keys to keep one contract across surfaces."""
    from ..core import inverse_blame as _ib

    result = _ib.compute(project_cwd, trace_id,
                         include_overlapping=include_overlapping)
    return {
        "trace": {
            "id": result.short_id,
            "trace_id": result.trace_id,
            "name": result.short_name,
            "lines": result.total_lines,
            "model": result.model,
        },
        "commits": [
            {
                "id": c.short_sha,
                "sha": c.sha,
                "msg": c.subject,
                "linesInCommit": c.lines_in_commit,
                "totalCommitLines": c.total_commit_lines,
                "pct": (f"{c.pct}%" if c.source == "attribution" else "-"),
                "source": c.source,
                "tier": c.tier,
            }
            for c in result.commits
        ],
        "files": [{"path": p, "lines": n} for p, n in result.files],
    }


def _build_file_line_json_payload(project_cwd: Path, target: str) -> dict:
    path, raw_line = target.rsplit(":", 1)
    line_no = int(raw_line)
    projection = _trail_projection_for(project_cwd)
    limitations: list[str] = []
    rows: list[dict] = []
    if projection is None:
        limitations.append("trail_query_unavailable")
    else:
        limitations.extend(projection.limitations)
        for row in projection.patches_touching_file(path):
            line_range = row.get("range") or row.get("affected_range") or {}
            start = line_range.get("start_line")
            end = line_range.get("end_line")
            if start is None or end is None:
                continue
            if int(start) <= line_no <= int(end):
                rows.append(projection.with_current_survival(row))
    rows.sort(
        key=lambda row: row.get("source_refs", {}).get("git_anchor_event_sequence", 0),
        reverse=True,
    )
    relation = "anchored_in_git" if rows else "unknown"
    if not rows and "no_git_anchor_for_line" not in limitations:
        limitations.append("no_git_anchor_for_line")
    return {
        "target": target,
        "relation": relation,
        "trailEvidence": rows,
        "trace_patch": rows[0] if rows else None,
        "git_anchor": rows[0] if rows else None,
        "limitations": limitations,
    }


# --------------------------------------------------------------------------- #
# Argument → mode resolution
# --------------------------------------------------------------------------- #

def _resolve_attribution_cache_id(cwd: Path, prefix: str) -> str | None:
    """Resolve ``prefix`` against the attribution cache's own ``trace_id``
    field, which historically stores session_ids for v1/legacy entries.

    Returns the full cache key on a unique match, raises
    :class:`AmbiguousPrefixError` on multiple, ``None`` on no match.
    """
    from ..core.cache import AttributionCache
    from ..core.trace_meta import AmbiguousPrefixError

    p = (prefix or "").strip().lower()
    if not p:
        return None
    cache = AttributionCache(cwd)
    matches: set[str] = set()
    for sha in cache.list_attributed_shas():
        data = cache.read_attribution(sha)
        if not data:
            continue
        for tr in (data.get("traces") or []):
            tid = (tr.get("trace_id") or "").lower()
            if tid.startswith(p):
                matches.add(tr["trace_id"])
    if len(matches) == 1:
        return next(iter(matches))
    if len(matches) > 1:
        raise AmbiguousPrefixError(prefix, sorted(matches))
    return None


def _resolve_session_id_in_staging(cwd: Path, prefix: str) -> str | None:
    """Find a staged trace by ``session_id`` prefix.

    Multiple staged trace records often share one session_id (forks /
    supersedes of the same Claude Code session). When a prefix matches
    a single cohesive session — i.e. one unique full ``session_id`` —
    we return the **session_id itself** so callers can ask ``compute()``
    for the merged view across all forks. When the prefix points at a
    specific canonical trace (session_id is unique to one record), we
    return that trace_id instead.

    Raises :class:`AmbiguousPrefixError` only when the prefix matches
    two or more *distinct* session_ids (that's genuine ambiguity).
    """
    from ..core.config import get_project_traces_dir
    from ..core.trace_meta import AmbiguousPrefixError

    p = (prefix or "").strip().lower()
    if not p:
        return None
    staging = get_project_traces_dir(cwd)
    if not staging.exists():
        return None
    # Group by full session_id so forks of one session coalesce.
    sid_to_tids: dict[str, set[str]] = {}
    for path in staging.glob("*.jsonl"):
        try:
            with path.open() as f:
                rec = _json.loads(f.readline())
        except (OSError, ValueError):
            continue
        sid = str(rec.get("session_id") or "")
        tid = str(rec.get("trace_id") or "")
        if sid.lower().startswith(p) and tid:
            sid_to_tids.setdefault(sid, set()).add(tid)
    if not sid_to_tids:
        return None
    if len(sid_to_tids) > 1:
        raise AmbiguousPrefixError(prefix, sorted(sid_to_tids))
    # One session, possibly with multiple forks.
    (sid, tids), = sid_to_tids.items()
    if len(tids) == 1:
        return next(iter(tids))
    # Return the session_id — compute() will fan out to all forks.
    return sid


def _resolve_blame_target(cwd: Path, arg: str) -> tuple[str, str] | tuple[None, str]:
    """Resolve ``arg`` to ``(mode, id)`` where mode is ``"commit"`` or
    ``"trace"``. On failure returns ``(None, reason)``.

    Resolution order:

    * ``c:<sha>`` → commit-mode, require ``git rev-parse``.
    * ``t:<id>`` → canonical trace-mode via ``resolve_trace_id_prefix``
      (matches staged records' ``trace_id`` field).
    * ``s:<id>`` → session/upstream trace-mode. Matches EITHER a staged
      record's ``session_id`` (returning the canonical trace_id) OR an
      attribution-cache-only entry whose key starts with the prefix.
      ``compute()`` handles both forms uniformly downstream.
    * Bare hyphenated UUID → try trace, then session/cache, then commit.
    * Bare hex → try commit, then trace, then session/cache.

    Ambiguity at any stage short-circuits with a typed error.
    """
    from ..core.trace_meta import AmbiguousPrefixError, resolve_trace_id_prefix

    if not arg:
        return (None, "empty argument")
    low = arg.strip()
    head = low[:2].lower()

    if head == "c:":
        sha = _resolve_sha(cwd, low)
        if sha:
            return ("commit", sha)
        return (None, f"Unknown commit: {arg}")

    if head == "t:":
        try:
            tid = resolve_trace_id_prefix(cwd, low)
        except AmbiguousPrefixError as e:
            return (None, str(e))
        if not tid:
            tid = _trail_trace_id_for_prefix(cwd, low)
        if tid:
            return ("trace", tid)
        return (None, f"Unknown trace: {arg}")

    if head == "s:":
        bare = low[2:]
        # Try staging session_id match first (preferred — returns canonical
        # trace_id). Fall back to attribution-cache prefix match for
        # upstream sessions that never landed in the inbox.
        try:
            tid = _resolve_session_id_in_staging(cwd, bare)
            if tid:
                return ("trace", tid)
            cache_id = _resolve_attribution_cache_id(cwd, bare)
            if cache_id:
                return ("trace", cache_id)
        except AmbiguousPrefixError as e:
            return (None, str(e))
        return (None, f"Unknown session: {arg}")

    if _looks_like_file_line_target(low):
        return ("file_line", low)

    def _try_trace_like() -> tuple[str, str] | None:
        """Try trace_id → session_id → attribution cache, in that order."""
        try:
            tid = resolve_trace_id_prefix(cwd, low)
            if tid:
                return ("trace", tid)
            trail_tid = _trail_trace_id_for_prefix(cwd, low)
            if trail_tid:
                return ("trace", trail_tid)
            sid_tid = _resolve_session_id_in_staging(cwd, low)
            if sid_tid:
                return ("trace", sid_tid)
            cache_id = _resolve_attribution_cache_id(cwd, low)
            if cache_id:
                return ("trace", cache_id)
        except AmbiguousPrefixError as e:
            return ("ambiguous", str(e))
        return None

    if "-" in low:
        found = _try_trace_like()
        if found and found[0] == "ambiguous":
            return (None, found[1])
        if found:
            return found
        sha = _resolve_sha(cwd, low)
        if sha:
            return ("commit", sha)
        return (None, f"Unknown id: {arg}")

    # Bare hex — commit first (backward-compatible), then trace-like.
    sha = _resolve_sha(cwd, low)
    if sha:
        return ("commit", sha)
    found = _try_trace_like()
    if found and found[0] == "ambiguous":
        return (None, found[1])
    if found:
        return found
    return (None, f"Unknown id: {arg}")


# --------------------------------------------------------------------------- #
# Click command
# --------------------------------------------------------------------------- #

@click.command(
    "commit",
    cls=OpentracesCommand,
    context_settings={"ignore_unknown_options": False},
    examples=[
        "opentraces trail blame commit abc1234",
        "opentraces trail blame commit c:abc1234 src/main.py",
        "opentraces trail blame commit t:4dccb032",
        "opentraces trail blame commit src/app.py:42 --json",
        "opentraces trail blame commit abc1234 --lines",
        "opentraces trail blame commit abc1234 --json",
    ],
    see_also=[
        ("opentraces trail blame pr", "blame a branch as a PR body."),
        ("opentraces trail graph", "render commit + trace history."),
        ("opentraces trace get", "view the full trace for an id."),
    ],
    option_groups=[
        ("Scope", ["show_lines", "show_entities", "project_dir",
                   "include_overlapping"]),
        ("Output", ["as_json", "no_color"]),
    ],
)
@click.argument("sha", required=True)
@click.argument("path", required=False, default=None)
@click.option("--lines", "show_lines", is_flag=True,
              help="Per-line output (git-blame-style). Commit-mode only.")
@click.option("--entities", "show_entities", is_flag=True,
              help="Expand entity changes under each trace. Commit-mode only.")
@click.option("--include-overlapping", "include_overlapping", is_flag=True,
              help="Trace-mode: include commits where files overlap without "
                   "direct tool-emit evidence (off by default — coincidence, "
                   "not contribution).")
@click.option("--json", "as_json", is_flag=True,
              help="Emit structured JSON instead of text.")
@click.option("--no-color", "no_color", is_flag=True,
              help="Disable ANSI colors.")
@project_dir_option
def blame_cmd(sha: str, path: str | None, show_lines: bool, show_entities: bool,
              include_overlapping: bool, as_json: bool, no_color: bool,
              project_dir: Path | None) -> None:
    """Attribution between traces and commits.

    Two modes, one argument:

    * Commit-mode (``c:<sha>`` or bare sha): which traces contributed to
      this commit. Uses the attribution cache for per-line detail.
    * Trace-mode (``t:<trace-id>`` or a hyphenated UUID): which commits
      carry this trace's output. Merges attribution-cache data
      (fine-grained) with the trace's ``git_links`` (hook-linked).

    ``--lines`` / ``--entities`` apply to commit-mode only.
    """
    cwd = Path(project_dir or Path.cwd()).resolve()
    color = detect_color(no_color, stream=sys.stdout) if not no_color else False

    mode, resolved = _resolve_blame_target(cwd, sha)
    if mode is None:
        click.echo(resolved, err=True)
        sys.exit(2)

    if mode == "trace":
        if show_lines or show_entities or path:
            click.echo(
                "--lines, --entities, and PATH are commit-mode only.",
                err=True,
            )
            sys.exit(2)
        if as_json:
            payload = _build_trace_json_payload(
                cwd, resolved, include_overlapping=include_overlapping,
            )
            click.echo(_json.dumps(payload, indent=2))
            return
        click.echo(
            _render_trace_blame(
                cwd, resolved, color=color,
                include_overlapping=include_overlapping,
            ),
            nl=False,
        )
        return

    if mode == "file_line":
        if path or show_lines or show_entities:
            click.echo(
                "--lines, --entities, and PATH are commit-mode only.",
                err=True,
            )
            sys.exit(2)
        payload = _build_file_line_json_payload(cwd, resolved)
        if as_json:
            click.echo(_json.dumps(payload, indent=2))
            return
        click.echo(f"Line {payload['target']}")
        if payload.get("trailEvidence"):
            row = payload["trailEvidence"][0]
            click.echo(
                f"  {row.get('trace_id')} {row.get('trace_patch_id')} "
                f"{row.get('evidence_tier')}"
            )
        else:
            click.echo("  relation: unknown")
        return

    full_sha = resolved  # commit-mode
    meta = _git_show_meta(cwd, full_sha)
    if not meta:
        click.echo(f"Unable to read commit metadata for {sha}", err=True)
        sys.exit(2)

    from ..core.cache import AttributionCache
    from ..enrichment.git import notes_store
    cache = AttributionCache(cwd)

    # If attribution is absent BUT the commit carries hook-linked notes,
    # render the notes-only view rather than prompting for backfill —
    # backfill produces attribution-cache rows from file blame, which
    # can't exist for hook-linked-only commits by construction.
    notes_tids = notes_store.trace_ids_for_commit(full_sha, cwd)
    trail_rows = _trail_evidence_for_commit(cwd, full_sha, path)

    if not cache.has_attribution(full_sha) and not notes_tids and not trail_rows:
        ran = _maybe_prompt_first_run(cwd, full_sha)
        if not ran or not cache.has_attribution(full_sha):
            sys.exit(1)

    data = cache.read_attribution(full_sha) or {}
    entity_data_raw = _load_entity_cache_raw(cache, full_sha) if show_entities else None

    if as_json:
        payload = _build_json_payload(
            meta, data, entity_data_raw, cache, path, show_entities, cwd,
        )
        click.echo(_json.dumps(payload, indent=2))
        return

    if show_lines:
        click.echo(_render_lines(data, cache, path, color), nl=False)
        return

    if show_entities and entity_data_raw is None:
        click.echo(
            _render_default(meta, data, cwd, path, color,
                            show_entities=False,
                            trail_rows=trail_rows),
            nl=False,
        )
        click.echo("")
        click.echo("(entity cache not available; run `ot setup entity-parser`)")
        return

    click.echo(
        _render_default(meta, data, cwd, path, color,
                        show_entities=show_entities,
                        trail_rows=trail_rows),
        nl=False,
    )


# --------------------------------------------------------------------------- #
# Group: ``trail blame``
# --------------------------------------------------------------------------- #
#
# Per-commit attribution and PR-shaped projections of trace lineage live here.
# Subcommands:
#   commit <sha>  — point-scope blame (the original ``trail blame <sha>``).
#   pr render|create|update — branch-scope blame projected as a GitHub PR body.
#
# More destinations (Slack, dashboards, CI checks) attach as additional
# subcommands of this group; they all consume the same blame-shaped data
# from the workflow runtime, just rendered for a different place and time.


@click.group("blame", cls=OpentracesGroup)
def blame_group() -> None:
    """Per-commit attribution and PR-shaped projections of trace lineage."""


# Attach the original commit-scope command under ``trail blame commit``.
blame_group.add_command(blame_cmd, name="commit")


__all__ = ["blame_cmd", "blame_group"]
