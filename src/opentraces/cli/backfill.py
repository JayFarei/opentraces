"""Hidden ``ot backfill`` verb — runs the attribution cache orchestrator.

Wired into the root group in ``cli/__init__.py``. Hidden from top-level
``--help`` but ``ot backfill --help`` works. Human output by default,
``--json`` for machine consumption.

Usage:
    ot backfill                 # incremental from state bookmark
    ot backfill --dry-run       # compute coverage, write nothing
    ot backfill --rebuild       # clear cache and walk from HEAD back
    ot backfill --project <dir> # run outside the current CWD
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from ..core import backfill as _backfill


@click.command("backfill", hidden=True)
@click.option("--dry-run", "dry_run", is_flag=True,
              help="Compute coverage without writing cache files.")
@click.option("--rebuild", "rebuild", is_flag=True,
              help="Clear the cache and re-attribute from HEAD.")
@click.option("--since", "since_ref", default=None,
              help="Start from this ref instead of the state bookmark. "
                   "Reserved; currently forces --rebuild from HEAD.")
@click.option("--project", "project_dir", type=click.Path(
                  exists=True, file_okay=False, dir_okay=True, path_type=Path),
              default=None, help="Project directory (default: CWD).")
@click.option("--max-commits", type=int, default=_backfill.DEFAULT_MAX_COMMITS,
              show_default=True,
              help="Cap on commits to walk when rebuilding.")
@click.option("--json", "as_json", is_flag=True,
              help="Emit a JSON payload instead of the human summary.")
@click.option("--verbose", "-v", is_flag=True,
              help="Forward verbose logging to the audit builder.")
def backfill_cmd(dry_run: bool, rebuild: bool, since_ref: str | None,
                 project_dir: Path | None, max_commits: int,
                 as_json: bool, verbose: bool) -> None:
    """Backfill per-commit attribution into the local cache."""
    cwd = Path(project_dir or Path.cwd()).resolve()
    if dry_run:
        report = _backfill.run_dry_run(cwd, verbose=verbose,
                                       max_commits=max_commits)
    elif rebuild or since_ref:
        report = _backfill.run_full(cwd, verbose=verbose,
                                    max_commits=max_commits)
    else:
        report = _backfill.run_incremental(cwd, verbose=verbose,
                                           max_commits=max_commits)

    payload = {
        "commits_processed": report.commits_processed,
        "commits_skipped": report.commits_skipped,
        "coverage_ratio": round(report.coverage_ratio, 4),
        "attributed_lines": report.attributed_lines,
        "total_lines": report.total_lines,
        "last_commit": report.last_commit,
        "errors": list(report.errors),
        "dry_run": report.dry_run,
    }

    if as_json:
        click.echo(_json.dumps(payload, indent=2))
        return

    head = (report.last_commit or "")[:10]
    pct = report.coverage_ratio * 100
    prefix = "Would backfill" if report.dry_run else "Backfilled"
    click.echo(
        f"{prefix} {report.commits_processed} commit(s), "
        f"coverage {pct:.1f}% "
        f"({report.attributed_lines}/{report.total_lines} lines), "
        f"last commit {head or '(none)'}"
    )
    if report.commits_skipped:
        click.echo(f"  skipped: {report.commits_skipped}")
    if report.errors:
        click.echo(f"  errors: {len(report.errors)}")
        for e in report.errors[:5]:
            click.echo(f"    - {e}")
