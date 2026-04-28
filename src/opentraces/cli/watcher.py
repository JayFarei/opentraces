"""``ot watcher`` — manage the background attribution watcher.

Subcommands:
    status      Show installed / running / interval.
    start       Install (if needed) + start the service.
    stop        Stop the service but leave the unit installed.
    restart     stop + start.
    uninstall   Remove the unit files.
    tick        Run a single watcher tick now across enlisted projects.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..watcher import daemon as _daemon
from ..watcher import installer as _installer


@click.group("watcher")
def watcher_group() -> None:
    """Manage the background attribution watcher."""


@watcher_group.command("status")
@click.option("--json", "json_out", is_flag=True,
              help="Emit machine-readable JSON.")
def _status(json_out: bool) -> None:
    """Show watcher install + running state."""
    try:
        st = _installer.status()
    except RuntimeError as e:
        click.echo(f"error: {e}", err=True)
        raise SystemExit(2)
    payload = {
        "platform": st.platform,
        "installed": st.installed,
        "running": st.running,
        "interval_seconds": st.interval_seconds,
        "unit_path": str(st.unit_path) if st.unit_path else None,
    }
    if json_out:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(f"platform:  {st.platform}")
    click.echo(f"installed: {st.installed}")
    click.echo(f"running:   {st.running}")
    click.echo(f"interval:  {st.interval_seconds}s"
               if st.interval_seconds else "interval:  -")
    if st.unit_path:
        click.echo(f"unit:      {st.unit_path}")


@watcher_group.command("start")
@click.option("--interval", type=int, default=300, show_default=True)
@click.option("--no-install", is_flag=True,
              help="Assume unit is already installed; just start it.")
def _start(interval: int, no_install: bool) -> None:
    """Install (if needed) and start the watcher service."""
    if not no_install:
        path = _installer.install(interval=interval)
        click.echo(f"installed: {path}")
    _installer.start()
    click.echo("started.")


@watcher_group.command("stop")
def _stop() -> None:
    """Stop the watcher service (unit remains installed)."""
    _installer.stop()
    click.echo("stopped.")


@watcher_group.command("restart")
def _restart() -> None:
    """Stop then start the watcher service."""
    _installer.stop()
    _installer.start()
    click.echo("restarted.")


@watcher_group.command("uninstall")
def _uninstall() -> None:
    """Remove the watcher unit files."""
    _installer.uninstall()
    click.echo("uninstalled.")


@watcher_group.command("tick")
@click.option("--project", "project_dir", type=click.Path(
                  exists=True, file_okay=False, dir_okay=True, path_type=Path),
              default=None, help="Project directory (default: all enlisted).")
@click.option("--json", "json_out", is_flag=True)
def _tick(project_dir: Path | None, json_out: bool) -> None:
    """Run one tick now and print reports (diagnostic)."""
    targets: list[Path]
    if project_dir is not None:
        targets = [Path(project_dir).resolve()]
    else:
        targets = _daemon.discover_enlisted_projects()
        if not targets:
            click.echo("(no enlisted projects)")
            return
    reports = [_daemon.run_once(p) for p in targets]
    if json_out:
        click.echo(json.dumps([
            {
                "project_cwd": str(r.project_cwd),
                "duration_ms": round(r.duration_ms, 2),
                "new_commits": r.new_commits,
                "jsonl_activity": r.jsonl_activity,
                "backfill_invoked": r.backfill_invoked,
                "commits_processed": r.commits_processed,
                "coverage_ratio": r.coverage_ratio,
                "fs_observations": r.fs_observations,
                "fs_reconciled": r.fs_reconciled,
                "fs_patches_created": r.fs_patches_created,
                "fs_patches_upgraded": r.fs_patches_upgraded,
                "trail_maturation_searches": r.trail_maturation_searches,
                "trail_maturation_anchors": r.trail_maturation_anchors,
                "error": r.error,
            } for r in reports
        ], indent=2))
        return
    for r in reports:
        status_bit = "ok" if not r.error else f"ERR {r.error}"
        click.echo(
            f"{r.project_cwd}: {status_bit} "
            f"new_commits={r.new_commits} jsonl={r.jsonl_activity} "
            f"backfilled={r.commits_processed} "
            f"fs_obs={r.fs_observations} "
            f"anchors={r.trail_maturation_anchors} "
            f"duration={r.duration_ms:.1f}ms"
        )
