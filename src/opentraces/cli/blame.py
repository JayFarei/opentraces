"""``ot blame <sha>`` — per-commit attribution lookup.

Three modes sharing a single Click command:

- **default summary** — one-screen overview (commit, coverage, traces, files).
- ``--lines`` — git-blame-style per-line output with trace ids + status glyphs.
- ``--entities`` — entity changes grouped by trace (requires entity cache).

A trailing ``-- <path>`` restricts output to one file. ``--json`` emits
structured output for programmatic consumers.

First-run behavior: if the attribution cache is empty for the requested
commit and stdin is a TTY (and the user hasn't previously declined with
``never``), we prompt ``[Y/n/never]`` and run ``core.backfill.run_full`` on
Y. Non-TTY or ``never`` always exits 1 with guidance.
"""

from __future__ import annotations

import json as _json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from ..clients.text.graph_renderer import (
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RESET,
    ANSI_YELLOW,
    _color,
    strip_ansi,
)


# --------------------------------------------------------------------------- #
# Line-status glyphs (stable user-facing spec)
# --------------------------------------------------------------------------- #

GLYPH_PRE_AUDIT = "\u00B7"       # ·
GLYPH_MISSING = "?"


def _line_glyph_and_tid(line: dict) -> tuple[str, str, str]:
    """Return (tid_display, glyph, ansi_code) for one attribution line row."""
    cons = line.get("consistency") or "attributed"
    tid = line.get("trace_id") or ""
    if cons == "attributed" and tid:
        return (tid[:8], "", ANSI_GREEN)
    if cons == "pre-audit":
        return (GLYPH_PRE_AUDIT, "", ANSI_DIM)
    if cons == "missing_from_audit":
        return (GLYPH_MISSING, "", ANSI_YELLOW)
    return (GLYPH_PRE_AUDIT, "", ANSI_DIM)


# --------------------------------------------------------------------------- #
# Entity change-type glyphs
# --------------------------------------------------------------------------- #

ENTITY_GLYPHS = {
    "added": "+",
    "modified": "~",
    "renamed": "\u2192",  # →
    "deleted": "-",
}


# --------------------------------------------------------------------------- #
# Git helpers (local — blame is independent of the graph renderer's git log)
# --------------------------------------------------------------------------- #

def _git_show_meta(cwd: Path, sha: str) -> tuple[str, str, str] | None:
    """Return (full_sha, subject, iso_timestamp) or None if sha unknown."""
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
    try:
        return subprocess.check_output(
            ["git", "rev-parse", ref], cwd=cwd, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


# --------------------------------------------------------------------------- #
# Cache inflation (blob-spill aware)
# --------------------------------------------------------------------------- #

def _inflate_file_lines(cache: Any, finfo: dict) -> list[dict]:
    """Return the ``lines`` list, reading from the blob store if spilled."""
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


# --------------------------------------------------------------------------- #
# Coverage helpers
# --------------------------------------------------------------------------- #

def _coverage_pct(data: dict) -> tuple[int, int, float]:
    cov = data.get("coverage") or {}
    attributed = int(cov.get("attributed") or 0)
    total = int(cov.get("total") or 0)
    ratio = float(cov.get("ratio") or (attributed / total if total else 0.0))
    return attributed, total, ratio


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

def _render_default(meta: tuple[str, str, str], data: dict,
                    scope_file: str | None, color: bool) -> str:
    full_sha, subject, ts = meta
    attributed, total, ratio = _coverage_pct(data)
    lines: list[str] = []
    lines.append(f"Commit: {full_sha[:10]} {subject}")
    lines.append(f"Date:   {ts}")
    pct = int(round(ratio * 100))
    lines.append(f"Coverage: {pct}% attributed ({attributed}/{total} lines)")
    lines.append("")
    traces = data.get("traces") or []
    if traces:
        lines.append("Contributing traces:")
        for t in traces:
            tid = t.get("trace_id") or "?"
            lc = int(t.get("line_count", 0))
            files = t.get("files") or []
            lifecycle = t.get("lifecycle") or "provisional"
            tid_disp = _color(tid[:8], ANSI_GREEN, color)
            lines.append(
                f"  \u2022 {tid_disp}  {lc} line(s)  {len(files)} file(s)  "
                f"<{lifecycle}>  resume: ot trace resume {tid[:8]}"
            )
        lines.append("")

    files = data.get("files") or {}
    if files:
        lines.append("Files:")
        for path in sorted(files):
            if scope_file and path != scope_file:
                continue
            finfo = files[path] or {}
            total_f = int(finfo.get("total") or 0)
            # Count attributed vs pre-audit in inflated lines.
            attr = pre = miss = 0
            from ..core.cache import AttributionCache  # defer import
            # Use the already-open cache via data closure — caller doesn't pass
            # it in; rely on simple counting here.
            for ln in finfo.get("lines") or []:
                c = (ln or {}).get("consistency") or "attributed"
                if c == "attributed":
                    attr += 1
                elif c == "pre-audit":
                    pre += 1
                elif c == "missing_from_audit":
                    miss += 1
            parts = [f"{attr} attributed"]
            if pre:
                parts.append(f"{pre} pre-audit")
            if miss:
                parts.append(f"{miss} missing")
            lines.append(
                f"  {path}  {total_f} line(s)  ({', '.join(parts)})"
            )
    return "\n".join(lines) + "\n"


def _render_lines(data: dict, cache: Any, scope_file: str | None,
                  color: bool) -> str:
    out: list[str] = []
    files = data.get("files") or {}
    for path in sorted(files):
        if scope_file and path != scope_file:
            continue
        out.append(f"{path}:")
        finfo = files[path] or {}
        inflated = _inflate_file_lines(cache, finfo)
        # Sort by n for stable rendering.
        for ln in sorted(inflated, key=lambda r: int(r.get("n") or 0)):
            n = int(ln.get("n") or 0)
            tid_disp, _glyph, code = _line_glyph_and_tid(ln)
            tid_col = _color(tid_disp, code, color)
            # Pad tid to 8 chars for stable columns.
            pad = max(0, 8 - len(strip_ansi(tid_col).rstrip()))
            tid_cell = tid_col + (" " * pad)
            out.append(f"  {n:>4} \u2502 {tid_cell} \u2502")
    if not out:
        return "(no files)\n"
    return "\n".join(out) + "\n"


def _load_entity_cache(cache: Any, sha: str) -> dict | None:
    p = cache.entity_path(sha)
    if not p.is_file():
        return None
    try:
        return _json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _render_entities(entity_data: dict | None, scope_file: str | None,
                     color: bool) -> str:
    if entity_data is None:
        return "(entity cache not available; run `ot setup entity-parser`)\n"
    entities = entity_data.get("entities") or []
    if scope_file:
        entities = [e for e in entities if (e.get("file") == scope_file)]
    # Group by trace_id. Entity records may or may not carry trace_id; if
    # absent, bucket under "(untraced)".
    by_trace: dict[str, list[dict]] = {}
    for e in entities:
        tid = e.get("trace_id") or "(untraced)"
        by_trace.setdefault(tid, []).append(e)
    out: list[str] = []
    for tid in sorted(by_trace):
        entries = by_trace[tid]
        tid_short = tid[:8] if tid != "(untraced)" else tid
        tid_disp = _color(tid_short, ANSI_GREEN, color)
        out.append(f"{tid_disp} {{{len(entries)} entities}}")
        for e in entries:
            ct = e.get("change_type") or "modified"
            glyph = ENTITY_GLYPHS.get(ct, "~")
            kind = e.get("entity_kind") or ""
            name = e.get("entity_name") or ""
            old = e.get("old_entity_name")
            file_ = e.get("file") or ""
            if ct == "renamed" and old:
                label = f"renamed {old} \u2192 {name}"
            elif ct == "deleted":
                label = f"{kind} deleted {name}".strip()
            elif ct == "added":
                label = f"{kind} {name}".strip()
            else:
                label = f"{kind} {name}".strip()
            out.append(f"  {glyph} {label:<28} {file_}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _build_json_payload(meta: tuple[str, str, str], data: dict,
                        entity_data: dict | None, cache: Any,
                        scope_file: str | None,
                        include_entities: bool) -> dict:
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
    if include_entities:
        if entity_data is None:
            payload["entities"] = []
        else:
            ents = entity_data.get("entities") or []
            if scope_file:
                ents = [e for e in ents if e.get("file") == scope_file]
            payload["entities"] = ents
    return payload


# --------------------------------------------------------------------------- #
# First-run prompt
# --------------------------------------------------------------------------- #

def _stdin_isatty() -> bool:
    """Module-indirected isatty check so tests can monkeypatch it."""
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
    """Return True if the backfill ran and cache is now populated for sha."""
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
    # Y path — run the full backfill.
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
              help="Show entity changes grouped by trace.")
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

    Use ``-- <path>`` to scope to one file.
    """
    cwd = Path(project_dir or Path.cwd()).resolve()
    color = not no_color

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
        # Empty-cache flow.
        ran = _maybe_prompt_first_run(cwd, full_sha)
        if not ran or not cache.has_attribution(full_sha):
            sys.exit(1)

    data = cache.read_attribution(full_sha) or {}
    entity_data = _load_entity_cache(cache, full_sha) if show_entities else None

    if as_json:
        payload = _build_json_payload(
            meta, data, entity_data, cache, path, show_entities
        )
        click.echo(_json.dumps(payload, indent=2))
        return

    if show_lines:
        click.echo(_render_lines(data, cache, path, color), nl=False)
        return

    if show_entities:
        # Summary first, then entities block.
        click.echo(_render_default(meta, data, path, color), nl=False)
        click.echo("")
        click.echo(_render_entities(entity_data, path, color), nl=False)
        return

    click.echo(_render_default(meta, data, path, color), nl=False)
