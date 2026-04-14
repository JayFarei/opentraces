"""Backfill orchestrator — walks commits, attributes each, writes cache.

Public API:
    run_incremental(project_cwd) -> BackfillReport
    run_full(project_cwd)        -> BackfillReport    # --rebuild
    run_dry_run(project_cwd)     -> BackfillReport    # computes, writes nothing

Algorithm:
  1. Load state.json. Read ``last_backfilled_commit``.
  2. ``git rev-parse HEAD`` -> head.
  3. Target commits:
       - incremental: ``git rev-list --first-parent <last>..HEAD``
       - full: ``git rev-list --first-parent HEAD`` capped at ``max_commits``
  4. ``attribution.build_audit_history(project_cwd)`` ONCE.
  5. For each commit: ``attribution.attribute_commit(..., sha)`` -> normalize
     into cache shape -> ``cache.write_attribution``.
  6. On success, advance ``last_backfilled_commit = head``.
  7. Aggregate coverage across processed commits for the report.

Merge commits: we walk first-parent only to avoid double-counting.
Pre-audit commits: recorded with their lines marked ``consistency="pre-audit"``.
Failures on individual commits are collected in ``report.errors`` and do NOT
abort the run — other commits keep going. Under strict callers should inspect
``report.errors``.
"""

from __future__ import annotations

import datetime
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..enrichment.git import attribution as _attr
from ..enrichment.git.attribution import (
    attribute_commit,
    build_audit_history,
    git,
    git_ok,
)
from .cache import AttributionCache, CACHE_VERSION
from .config import _project_slug_for, get_project_state_path
from .state import StateManager

# Safety cap for --rebuild; phase 7 will raise this.
DEFAULT_MAX_COMMITS = 500


@dataclass
class BackfillReport:
    commits_processed: int = 0
    commits_skipped: int = 0
    coverage_ratio: float = 0.0
    attributed_lines: int = 0
    total_lines: int = 0
    last_commit: str | None = None
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


# --- internals --------------------------------------------------------------

def _utcnow_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _rev_parse(project_cwd: Path, ref: str) -> str | None:
    try:
        return git("-C", str(project_cwd), "rev-parse", ref).strip()
    except RuntimeError:
        return None


def _rev_list(project_cwd: Path, *extra: str) -> list[str]:
    """``git rev-list --first-parent --reverse <extra>``; [] on failure."""
    try:
        out = git("-C", str(project_cwd), "rev-list",
                  "--first-parent", "--reverse", *extra)
    except RuntimeError:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _target_commits(project_cwd: Path, last: str | None, *,
                    full: bool, max_commits: int) -> list[str]:
    head = _rev_parse(project_cwd, "HEAD")
    if not head:
        return []
    if full or not last or not git_ok(
        "-C", str(project_cwd), "cat-file", "-e", last + "^{commit}"
    ):
        return _rev_list(project_cwd, f"--max-count={max_commits}", "HEAD")
    return _rev_list(project_cwd, f"{last}..HEAD")


def _normalize_attribution(spike_result: dict, *,
                           commit_sha: str,
                           project_slug: str) -> dict:
    """Convert the spike's per-file/per-trace shape into the cache shape.

    The spike emits:
        files[rel] = {status, total_lines, by_trace: {trace_id: {count, line_ranges, introducer, audit_commits}}}
        summary[trace_id] = {files_touched, total_lines}

    We emit:
        files[rel] = {lines: [{n, trace_id, consistency}], total}
        traces: [{trace_id, line_count, files}]
        coverage: {attributed, total, ratio}
    """
    files_in: dict = spike_result.get("files") or {}
    summary_in: dict = spike_result.get("summary") or {}

    files_out: dict[str, dict] = {}
    attributed_total = 0
    grand_total = 0

    for rel, info in files_in.items():
        status = info.get("status", "unknown")
        if status != "attributed":
            # missing_from_audit / blame_failed / etc — record status only.
            files_out[rel] = {
                "lines": [],
                "total": int(info.get("total_lines") or 0),
                "status": status,
            }
            continue

        total = int(info.get("total_lines") or 0)
        grand_total += total

        by_trace: dict = info.get("by_trace") or {}
        # Expand line_ranges back into per-line records.
        line_records: list[dict] = []
        for tid, entry in by_trace.items():
            consistency = (
                "pre-audit" if str(tid).startswith("pre-audit:") else "attributed"
            )
            trace_key = None if consistency == "pre-audit" else tid
            for ln in _expand_ranges(entry.get("line_ranges") or []):
                line_records.append({
                    "n": ln, "trace_id": trace_key, "consistency": consistency,
                })
                if consistency == "attributed":
                    attributed_total += 1
        line_records.sort(key=lambda r: r["n"])
        files_out[rel] = {"lines": line_records, "total": total,
                          "status": "attributed"}

    traces_out = [
        {"trace_id": tid,
         "line_count": int(v.get("total_lines") or 0),
         "files": list(v.get("files_touched") or [])}
        for tid, v in summary_in.items()
        if not str(tid).startswith("pre-audit:")
    ]

    ratio = (attributed_total / grand_total) if grand_total else 0.0
    return {
        "commit_sha": commit_sha,
        "project_slug": project_slug,
        "version": CACHE_VERSION,
        "generated_at": _utcnow_iso(),
        "traces": traces_out,
        "files": files_out,
        "coverage": {
            "attributed": attributed_total,
            "total": grand_total,
            "ratio": ratio,
        },
    }


def _expand_ranges(ranges: list[str]) -> list[int]:
    """``["1-3", "5", "7-8"]`` -> ``[1,2,3,5,7,8]``."""
    out: list[int] = []
    for r in ranges:
        s = str(r)
        if "-" in s:
            a, b = s.split("-", 1)
            try:
                ai = int(a)
                bi = int(b)
            except ValueError:
                continue
            out.extend(range(ai, bi + 1))
        else:
            try:
                out.append(int(s))
            except ValueError:
                continue
    return out


# --- public api -------------------------------------------------------------

def _run(project_cwd: Path, *, full: bool, dry_run: bool,
         verbose: bool, max_commits: int) -> BackfillReport:
    project_cwd = Path(project_cwd).resolve()
    state = StateManager(state_path=get_project_state_path(project_cwd))
    cache = AttributionCache(project_cwd)

    report = BackfillReport(dry_run=dry_run)

    if full and not dry_run:
        cache.clear()

    last = None if full else state.get_last_backfilled_commit()
    targets = _target_commits(project_cwd, last, full=full,
                              max_commits=max_commits)

    if not targets:
        # Nothing to do — still stamp a coverage of 1.0 if we're up to date.
        head = _rev_parse(project_cwd, "HEAD")
        report.last_commit = head
        return report

    # Build audit once for the entire batch (expensive, cached on refs/notes).
    try:
        build_audit_history(project_cwd, verbose=verbose)
    except Exception as e:  # noqa: BLE001
        # Not fatal — attribute_commit has a pre-audit fallback.
        report.errors.append(f"build_audit_history: {type(e).__name__}: {e}")

    project_slug = _project_slug_for(project_cwd)

    for sha in targets:
        try:
            spike = attribute_commit(project_cwd, sha, verbose=False)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"{sha[:8]}: {type(e).__name__}: {e}")
            report.commits_skipped += 1
            continue

        data = _normalize_attribution(spike, commit_sha=sha,
                                      project_slug=project_slug)
        if not dry_run:
            cache.write_attribution(sha, data)
        report.commits_processed += 1
        cov = data.get("coverage") or {}
        report.attributed_lines += int(cov.get("attributed") or 0)
        report.total_lines += int(cov.get("total") or 0)

    report.coverage_ratio = (
        report.attributed_lines / report.total_lines
        if report.total_lines else 0.0
    )
    head = _rev_parse(project_cwd, "HEAD")
    report.last_commit = head
    if not dry_run and head and report.commits_processed > 0:
        state.set_last_backfilled_commit(head)
    return report


def run_incremental(project_cwd: Path, *, verbose: bool = False,
                    max_commits: int = DEFAULT_MAX_COMMITS) -> BackfillReport:
    return _run(project_cwd, full=False, dry_run=False,
                verbose=verbose, max_commits=max_commits)


def run_full(project_cwd: Path, *, verbose: bool = False,
             max_commits: int = DEFAULT_MAX_COMMITS) -> BackfillReport:
    return _run(project_cwd, full=True, dry_run=False,
                verbose=verbose, max_commits=max_commits)


def run_dry_run(project_cwd: Path, *, verbose: bool = False,
                max_commits: int = DEFAULT_MAX_COMMITS) -> BackfillReport:
    return _run(project_cwd, full=True, dry_run=True,
                verbose=verbose, max_commits=max_commits)
