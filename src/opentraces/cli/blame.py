"""``ot blame <sha>`` — per-commit attribution lookup.

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

from ..clients.text.colors import (
    RESET,
    Role,
    coverage_role,
    detect_color,
    paint,
    render_handle,
)
from ..clients.text.graph_renderer import (
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RESET,
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
        lc = int(tr.get("line_count") or 0)
        pct = int(round((lc / total) * 100)) if total else 0
        short_id = tid[:8]
        meta = resolve_trace_meta(project_cwd, tid)
        short_name = meta.short_name if meta and meta.short_name else short_id
        model = (meta.model if meta else None) or ""

        # Dim prefix on t: via render_handle.
        id_paint = render_handle("t", tid, use_color=color)
        bullet = paint(Role.TRACE_ID, TRACE_BULLET, use_color=color)
        name_paint = paint(Role.TRACE_NAME, short_name, use_color=color)
        pct_paint = paint(
            coverage_role(float(pct)), f"{pct}%", use_color=color,
        )
        model_paint = paint(Role.DIM, model, use_color=color)

        header = (
            f"{tee}{body_dash2} {bullet} {id_paint}   {name_paint}  "
            f"{lc} lines . {pct_paint}"
        )
        if model:
            header += f"   {model_paint}"
        out.append(header)

        contrib = by_tid.get(tid)
        if contrib is None:
            from ..core.entity_join import TraceContribution as _TC
            contrib = _TC(trace_id=tid, line_count=lc,
                          line_ratio=(lc / total) if total else 0.0)

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


def _render_default(meta: tuple[str, str, str], data: dict,
                    project_cwd: Path, scope_file: str | None,
                    color: bool, *, show_entities: bool = False) -> str:
    files = data.get("files") or {}
    traces = data.get("traces") or []
    lines: list[str] = []
    lines.extend(_render_header(meta, data, files=files, traces=traces,
                                color=color, project_cwd=project_cwd))
    lines.extend(_iter_trace_blocks(
        project_cwd, meta[0], data, color,
        verbose_entities=show_entities, scope_file=scope_file,
    ))

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
    payload: dict = {
        "commit": {"sha": full_sha, "subject": subject, "timestamp": ts},
        "coverage": {"attributed": attributed, "total": total, "ratio": ratio},
        "traces": data.get("traces") or [],
        "files": files_out,
    }
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
    from ..core.trace_meta import resolve_trace_meta
    contribs = join_entities_to_traces(project_cwd, full_sha)
    ec_out: list[dict] = []
    for c in contribs:
        meta_obj = resolve_trace_meta(project_cwd, c.trace_id)
        ec_out.append({
            "trace_id": c.trace_id,
            "short_name": (meta_obj.short_name if meta_obj else None) or c.trace_id[:8],
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
# Click command
# --------------------------------------------------------------------------- #

@click.command("blame", context_settings={"ignore_unknown_options": False})
@click.argument("sha", required=True)
@click.argument("path", required=False, default=None)
@click.option("--lines", "show_lines", is_flag=True,
              help="Per-line output (git-blame-style).")
@click.option("--entities", "show_entities", is_flag=True,
              help="Expand entity changes under each trace.")
@click.option("--json", "as_json", is_flag=True,
              help="Emit structured JSON instead of text.")
@click.option("--no-color", "no_color", is_flag=True,
              help="Disable ANSI colors.")
@click.option("--project", "project_dir", type=click.Path(
                  exists=True, file_okay=False, dir_okay=True, path_type=Path),
              default=None, help="Project directory (default: CWD).")
def blame_cmd(sha: str, path: str | None, show_lines: bool, show_entities: bool,
              as_json: bool, no_color: bool, project_dir: Path | None) -> None:
    """Show per-commit attribution for SHA.

    Accepts a bare SHA or the ``c:<sha>`` prefixed form. Use ``-- <path>``
    to scope output to one file.
    """
    cwd = Path(project_dir or Path.cwd()).resolve()
    color = detect_color(no_color, stream=sys.stdout) if not no_color else False
    # Retain compat with existing tests: --no-color forces plain ASCII;
    # otherwise we honour TTY detection + NO_COLOR env.

    full_sha = _resolve_sha(cwd, sha)
    if not full_sha:
        click.echo(f"Unknown commit: {sha}", err=True)
        sys.exit(2)

    meta = _git_show_meta(cwd, full_sha)
    if not meta:
        click.echo(f"Unable to read commit metadata for {sha}", err=True)
        sys.exit(2)

    from ..core.cache import AttributionCache
    cache = AttributionCache(cwd)

    if not cache.has_attribution(full_sha):
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
        # Explicit message expected by test_entities_missing_cache_message.
        click.echo(
            _render_default(meta, data, cwd, path, color,
                            show_entities=False),
            nl=False,
        )
        click.echo("")
        click.echo("(entity cache not available; run `ot setup entity-parser`)")
        return

    click.echo(
        _render_default(meta, data, cwd, path, color,
                        show_entities=show_entities),
        nl=False,
    )
