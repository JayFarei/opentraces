"""``opentraces git-backfill`` — retro-correlate inbox traces to past commits.

The post-commit hook only sees traces whose Stop hook already landed
within a 2-hour window. Anything older — prior sessions, commits made
after a long pause, or traces staged while the hook was broken — stays
uncorrelated forever. This verb walks recent first-parent history,
re-runs the same correlator the hook uses, writes `refs/notes/opentraces`,
and persists `git_links` back onto each trace's JSONL file.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from ..capture.git import post_commit as _pc


@click.command("git-backfill")
@click.option("--project", "project_dir", type=click.Path(
                  exists=True, file_okay=False, dir_okay=True, path_type=Path),
              default=None, help="Project directory (default: CWD).")
@click.option("--max-commits", type=int, default=500, show_default=True,
              help="Cap on first-parent commits to walk.")
@click.option("--window-hours", type=float, default=24.0, show_default=True,
              help="Match a trace to a commit if timestamp_end is within "
                   "this many hours of the commit's date (either side).")
@click.option("--json", "as_json", is_flag=True,
              help="Emit a JSON payload instead of the human summary.")
def git_backfill_cmd(project_dir: Path | None, max_commits: int,
                     window_hours: float, as_json: bool) -> None:
    """Retroactively correlate inbox traces to past commits.

    Useful after installing the post-commit hook for the first time, or
    after a period where the hook failed to run. Safe to re-run: notes
    dedupe on append and in-memory git_links dedupe before rewrite.
    """
    cwd = Path(project_dir or Path.cwd()).resolve()
    report = _pc.backfill(cwd, max_commits=max_commits, window_hours=window_hours)

    payload = {
        "commits_scanned": report.commits_scanned,
        "commits_correlated": report.commits_correlated,
        "commits_skipped": report.commits_skipped,
        "traces_linked": report.traces_linked,
        "traces_persisted": report.traces_persisted,
        "tiers": report.tiers,
        "errors": report.errors,
    }

    if as_json:
        click.echo(_json.dumps(payload, indent=2))
        return

    click.echo(
        f"Scanned {report.commits_scanned} commit(s), "
        f"correlated {report.commits_correlated}, "
        f"skipped {report.commits_skipped}."
    )
    click.echo(
        f"Linked {report.traces_linked} trace-commit pair(s); "
        f"{report.traces_persisted} trace file(s) rewritten."
    )
    if report.tiers:
        tier_bits = ", ".join(
            f"{k}: {v}" for k, v in sorted(report.tiers.items())
        )
        click.echo(f"  tiers: {tier_bits}")
    if report.errors:
        click.echo(f"  {len(report.errors)} error(s):")
        for e in report.errors[:5]:
            click.echo(f"    - {e}")
