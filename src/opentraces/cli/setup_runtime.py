"""``opentraces setup runtime`` — runtime selection / reconcile (issue #99).

Extracted from the ``installers`` god module (cli/setup decomposition). Defines
the ``setup runtime`` sub-group + its commands (status / use / use-dev /
remove-duplicates) on the shared ``setup_group``; imported by ``cli/__init__``
so the decorators register the commands at CLI load. One-way dep: imports
``setup_group`` from ``installers``; ``installers`` does not import it back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from .installers import setup_group


@setup_group.group("runtime", hidden=True)
def setup_runtime_group() -> None:
    """Select / reconcile the active opentraces runtime (issue #99).

    On a machine where opentraces is installed several ways at once
    (pipx + Homebrew + an editable checkout), the integration glue can each
    execute opentraces from a DIFFERENT install root. `doctor` DETECTS that
    split; this group is the TREATMENT.

    Subcommands:

    \b
      status              Show discovered installs + per-runner runtime.
      use <kind>          Re-render the glue to a chosen installed runtime
                          (pipx | homebrew | source). Integrations-only.
      use-dev             Point the glue at the editable checkout (dev mode).
      remove-duplicates   PRINT the data-safe package-removal commands.

    Runtime selection is integrations-only and DATA-SAFE: it never installs or
    removes a package, never deletes captured data, and never touches a Git
    ref. Every verb is `--dry-run`-able and `--json`-reportable.
    """


def _runtime_json(json_flag: bool) -> None:
    if json_flag:
        _cli._json_mode = True


@setup_runtime_group.command("status")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Emit JSON.")
@click.option("--probe-runtimes", "probe", is_flag=True, default=False,
              help="Opt-in: execute each runner to verify its real module file.")
def setup_runtime_status(json_flag: bool, probe: bool) -> None:
    """Show the discovered installs and which runtime each integration uses."""
    _runtime_json(json_flag)
    from ..core import runtime_select as _rs

    prov = _rs.discover_runtimes(Path.cwd(), probe=probe)
    envelope = {
        "status": "ok",
        "action": "runtime-status",
        "state": prov.get("state"),
        "severity": prov.get("severity"),
        "current": prov.get("current"),
        "discovered_installs": prov.get("discovered_installs"),
        "integration_runners": prov.get("integration_runners"),
        "dev_runtime_active": prov.get("dev_runtime_active", False),
        "advice": prov.get("advice"),
        "probed": prov.get("probed"),
        "remediation": {
            "select": "opentraces setup runtime use <pipx|homebrew|source>",
            "remove_duplicates": "opentraces setup runtime remove-duplicates --keep <kind>",
            "note": (
                "Selection is integrations-only and data-safe: it re-renders "
                "glue, prints (never runs) package-removal commands, and never "
                "touches captured data, datasets, staging, or git refs."
            ),
        },
    }
    if prov.get("state") == "mixed_runtimes":
        _cli.human_echo("Integrations execute opentraces from more than one install root.")
        for inst in prov.get("discovered_installs") or []:
            _cli.human_echo(
                f"  - {inst.get('source_kind')}: {inst.get('interpreter')}"
            )
        _cli.human_echo("Converge with: opentraces setup runtime use <pipx|homebrew|source>")
    else:
        _cli.human_echo("All integrations resolve to a single runtime.")
    _cli.emit_json(envelope)


@setup_runtime_group.command("use")
@click.argument("kind", type=click.Choice(["pipx", "homebrew", "brew", "source"]))
@click.option("--integrations-only", is_flag=True, default=True,
              help="Only re-render integration glue (the only supported scope).")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Resolve and print the plan; change nothing.")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Emit JSON.")
@click.option("--probe-runtimes", "probe", is_flag=True, default=False,
              help="Opt-in: verify each runner's real module file before selecting.")
def setup_runtime_use(kind: str, integrations_only: bool, dry_run: bool,
                      json_flag: bool, probe: bool) -> None:
    """Re-render the integration glue to a chosen installed runtime.

    KIND is the install to converge on: pipx, homebrew (alias: brew), or
    source. Re-renders ONLY the integration runners doctor discovered; it never
    installs a missing runtime (selecting one with no discovered install exits
    2 with guidance).
    """
    _runtime_json(json_flag)
    from ..core import runtime_select as _rs

    envelope = _rs.apply_runtime(
        Path.cwd(), kind=kind, dry_run=dry_run, probe=probe
    )
    if envelope.get("status") == "error":
        msg = (envelope.get("error") or {}).get("message") or "could not resolve runtime"
        _cli.human_echo(f"{_cli._err('error')}: {msg}")
        _cli.emit_json(envelope)
        sys.exit(2)

    verb = "Would re-render" if dry_run else "Re-rendered"
    _cli.human_echo(
        f"{verb} {', '.join(envelope.get('runners_targeted') or []) or 'nothing'} "
        f"→ {envelope.get('resolved_interpreter')}"
    )
    if dry_run:
        _cli.human_echo("(dry-run — nothing was changed.)")
    _cli.emit_json(envelope)
    sys.exit(0 if envelope.get("status") == "ok" else 5)


@setup_runtime_group.command("use-dev")
@click.option("--project", "project", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="Editable checkout to point the glue at (default: cwd).")
@click.option("--integrations-only", is_flag=True, default=True,
              help="Only re-render integration glue (the only supported scope).")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Resolve and print the plan; change nothing.")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Emit JSON.")
def setup_runtime_use_dev(project: Path | None, integrations_only: bool,
                          dry_run: bool, json_flag: bool) -> None:
    """Point the integration glue at the editable checkout (dev mode).

    Re-renders the glue to the checkout's interpreter AND records the choice in
    a project-local marker so `doctor` reports it as deliberate dev mode, not
    drift. Switch back with `setup runtime use <pipx|homebrew>`.
    """
    _runtime_json(json_flag)
    from ..core import runtime_select as _rs

    cwd = Path.cwd()
    envelope = _rs.apply_runtime(
        cwd, dev=True, project=project or cwd, dry_run=dry_run
    )
    if envelope.get("status") == "error":
        msg = (envelope.get("error") or {}).get("message") or "could not resolve dev runtime"
        _cli.human_echo(f"{_cli._err('error')}: {msg}")
        _cli.emit_json(envelope)
        sys.exit(2)

    verb = "Would re-render" if dry_run else "Re-rendered"
    _cli.human_echo(
        f"{verb} {', '.join(envelope.get('runners_targeted') or []) or 'nothing'} "
        f"→ {envelope.get('resolved_interpreter')} (dev runtime)"
    )
    if dry_run:
        _cli.human_echo("(dry-run — nothing was changed.)")
    _cli.emit_json(envelope)
    sys.exit(0 if envelope.get("status") == "ok" else 5)


@setup_runtime_group.command("remove-duplicates")
@click.option("--keep", "keep",
              type=click.Choice(["pipx", "homebrew", "brew", "source"]), default=None,
              help="Runtime to KEEP (pipx|homebrew|source); default keeps the current.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="No effect (this verb already only prints); accepted for symmetry.")
@click.option("--json", "json_flag", is_flag=True, default=False, help="Emit JSON.")
@click.option("--probe-runtimes", "probe", is_flag=True, default=False,
              help="Opt-in: verify each runner's real module file first.")
def setup_runtime_remove_duplicates(keep: str | None, dry_run: bool,
                                    json_flag: bool, probe: bool) -> None:
    """PRINT the data-safe package-removal commands for duplicate installs.

    Never executes anything — copy/paste the printed commands yourself. Never
    touches captured data, datasets, staging, or git refs.
    """
    _runtime_json(json_flag)
    from ..core import runtime_select as _rs

    envelope = _rs.remove_duplicates(Path.cwd(), keep=keep, probe=probe)
    dups = envelope.get("duplicates") or []
    if not dups:
        _cli.human_echo("No duplicate installs to remove.")
    else:
        _cli.human_echo(f"Keeping: {envelope.get('keep') or 'current'}")
        _cli.human_echo("Run these yourself to remove the duplicates (nothing was executed):")
        for dup in dups:
            _cli.human_echo(f"  {dup['command']}")
            if dup.get("note"):
                _cli.human_echo(f"      # {dup['note']}")
    _cli.emit_json(envelope)
