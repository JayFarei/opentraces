"""``opentraces setup watcher`` — background attribution watcher control.

Extracted from the ``installers`` god module (cli/setup decomposition). Defines
the ``setup watcher`` sub-group + its 8 commands (install / uninstall / start /
stop / restart / status / sweep / tick) on the shared ``setup_group``. Imported
by ``cli/__init__`` so the ``@setup_group.group``/``@...command`` decorators
register the commands at CLI load. One-way dep: this module imports
``setup_group`` from ``installers``; ``installers`` does not import it back.
"""
from __future__ import annotations

import json as _setup_watcher_json
import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from .installers import setup_group


@setup_group.group("watcher")
def setup_watcher_group() -> None:
    """Install and control the background attribution watcher.

    The watcher is a launchd agent (macOS) or systemd user timer (Linux)
    that wakes every ``--interval`` seconds, walks enlisted projects, and
    runs incremental backfill when new commits or Claude Code JSONL
    activity appears. It powers ``opentraces trail blame`` and the lazy
    Trace Trails maturation pipeline.

    OFF cost: the watcher is an always-on TRI-record engine (trace ingest +
    trail maturation + ctx-to-bucket) and it also auto-reinstalls the git
    post-commit hook every sweep. Stopping it means no maturation and no
    backstop — captured data still lands via hooks, but nothing matures it
    or catches what the live hooks missed.

    Subcommands:

    \b
      install     Render and load the unit + shim.
      uninstall   Unload and remove the unit + shim.
      start       Load the already-installed unit.
      stop        Unload but keep the unit installed.
      restart     stop + start.
      status      Show install / running state.
      tick        Run one diagnostic tick now.
    """


@setup_watcher_group.command("install")
@click.option("--interval", type=int, default=300, show_default=True,
              help="Polling interval in seconds.")
@click.option("--no-install", is_flag=True,
              help="Render the unit file but don't load it.")
def setup_watcher_install(interval: int, no_install: bool) -> None:
    """Install the background attribution watcher.

    Writes a launchd plist (macOS) or systemd user unit + timer (Linux),
    plus a worker shim at ~/.opentraces/bin/ot-watcher. The service wakes
    up every ``--interval`` seconds, walks enlisted projects, and runs
    incremental backfill when new commits or new Claude Code JSONL
    activity appears.
    """
    from ..watcher import installer as _winst

    try:
        path = _winst.install(interval=interval, dry_run=no_install)
    except RuntimeError as e:
        _cli.human_echo(f"{_cli._err('error')}: {e}")
        _cli.emit_json({"status": "error", "action": "setup-watcher",
                   "message": str(e)})
        sys.exit(5)

    _cli.human_echo(f"Watcher unit written: {path}")
    if no_install:
        _cli.human_hint("(dry run — not loaded)")
    _cli.emit_json({
        "status": "ok",
        "action": "setup-watcher",
        "unit_path": str(path),
        "interval_seconds": int(interval),
        "dry_run": bool(no_install),
    })


@setup_watcher_group.command("uninstall")
def setup_watcher_uninstall() -> None:
    """Unload and remove the watcher unit file."""
    from ..watcher import installer as _winst

    try:
        _winst.uninstall()
    except RuntimeError as e:
        _cli.human_echo(f"{_cli._err('error')}: {e}")
        _cli.emit_json({"status": "error", "action": "uninstall-watcher",
                   "message": str(e)})
        sys.exit(5)
    _cli.human_echo(f"{_cli._ok('uninstalled')} watcher")
    _cli.emit_json({"status": "ok", "action": "uninstall-watcher"})


@setup_watcher_group.command("status")
@click.option("--json", "json_out", is_flag=True,
              help="Emit machine-readable JSON.")
def setup_watcher_status(json_out: bool) -> None:
    """Show watcher install + running state."""
    from ..watcher import installer as _winst

    try:
        st = _winst.status()
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
        click.echo(_setup_watcher_json.dumps(payload, indent=2))
        return
    click.echo(f"platform:  {st.platform}")
    click.echo(f"installed: {st.installed}")
    click.echo(f"running:   {st.running}")
    click.echo(f"interval:  {st.interval_seconds}s"
               if st.interval_seconds else "interval:  -")
    if st.unit_path:
        click.echo(f"unit:      {st.unit_path}")


@setup_watcher_group.command("start")
@click.option("--interval", type=int, default=300, show_default=True,
              help="Polling interval in seconds.")
@click.option("--no-install", is_flag=True,
              help="Assume unit is already installed; just load it.")
def setup_watcher_start(interval: int, no_install: bool) -> None:
    """Install (if needed) and start the watcher service."""
    from ..watcher import installer as _winst

    if not no_install:
        path = _winst.install(interval=interval)
        click.echo(f"installed: {path}")
    _winst.start()
    click.echo("started.")


@setup_watcher_group.command("stop")
def setup_watcher_stop() -> None:
    """Stop the watcher service (unit remains installed)."""
    from ..watcher import installer as _winst

    _winst.stop()
    _cli.human_echo("stopped.")
    _cli.emit_json({"status": "ok", "action": "stop-watcher"})


@setup_watcher_group.command("restart")
def setup_watcher_restart() -> None:
    """Stop then start the watcher service."""
    from ..watcher import installer as _winst

    _winst.stop()
    _winst.start()
    click.echo("restarted.")


@setup_watcher_group.command("sweep", hidden=True)
@click.option("--in-process", "in_process", is_flag=True,
              help="Tick projects in this process instead of budgeted "
                   "child processes (tests / debugging).")
@click.option("--json", "json_out", is_flag=True, help="Emit machine-readable JSON.")
def setup_watcher_sweep(in_process: bool, json_out: bool) -> None:
    """Run one bounded sweep over all enlisted projects, then exit.

    The production watcher entrypoint (#65): the launchd/systemd unit
    re-runs the shim on its interval, the shim execs this verb, and each
    project ticks in a child process with an RSS + wall-clock budget. A
    process that exits after each sweep cannot accumulate memory across
    sweeps; a pathological project is killed at its budget and the sweep
    continues.
    """
    from ..watcher import daemon as _daemon

    summary = _daemon.run_sweep(in_process=in_process)
    if json_out:
        click.echo(_setup_watcher_json.dumps(summary))
        return
    click.echo(
        f"sweep: {summary['projects']} projects, {summary['ok']} ok, "
        f"{summary['rss_killed']} rss-killed, "
        f"{summary['peak_rss_exceeded']} peak-rss-exceeded, "
        f"{summary['timeout_killed']} timeout-killed, "
        f"{summary['errors']} errors"
        + (f", {summary['pruned']} enlistments pruned" if "pruned" in summary else "")
    )


@setup_watcher_group.command("tick", hidden=True)
@click.option("--project", "project_dir", type=click.Path(
                  exists=True, file_okay=False, dir_okay=True, path_type=Path),
              default=None, help="Project directory (default: all enlisted).")
@click.option("--json", "json_out", is_flag=True, help="Emit machine-readable JSON.")
def setup_watcher_tick(project_dir: Path | None, json_out: bool) -> None:
    """Run one tick now and print reports (diagnostic)."""
    from ..watcher import daemon as _daemon

    targets: list[Path]
    if project_dir is not None:
        targets = [Path(project_dir).resolve()]
    else:
        targets = _daemon.discover_enlisted_projects()
        if not targets:
            if json_out:
                # Type-stable with the non-empty path below: consumers
                # (otbox journeys, integration harnesses) index the report
                # array as `0.<field>`, so the empty world is `[]`, not an
                # object envelope.
                click.echo(_setup_watcher_json.dumps([]))
            else:
                click.echo("(no enlisted projects)")
            return
    reports = [_daemon.run_once(p) for p in targets]
    if json_out:
        click.echo(_setup_watcher_json.dumps([
            {
                "project_cwd": str(r.project_cwd),
                "duration_ms": round(r.duration_ms, 2),
                "new_commits": r.new_commits,
                "jsonl_activity": r.jsonl_activity,
                "backfill_invoked": r.backfill_invoked,
                "commits_processed": r.commits_processed,
                "coverage_ratio": r.coverage_ratio,
                "sessions_created": r.sessions_created,
                "sessions_refreshed": r.sessions_refreshed,
                "sessions_new_generations": r.sessions_new_generations,
                "sessions_noops": r.sessions_noops,
                "sessions_errored": r.sessions_errored,
                "fs_observations": r.fs_observations,
                "fs_reconciled": r.fs_reconciled,
                "fs_patches_created": r.fs_patches_created,
                "fs_patches_upgraded": r.fs_patches_upgraded,
                "trail_maturation_searches": r.trail_maturation_searches,
                "trail_maturation_anchors": r.trail_maturation_anchors,
                "bucket_sync_state": r.bucket_sync_state,
                "bucket_sync_digest": r.bucket_sync_digest,
                "bucket_sync_error": r.bucket_sync_error,
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
            f"bucket_sync={r.bucket_sync_state or 'n/a'} "
            f"duration={r.duration_ms:.1f}ms"
        )
