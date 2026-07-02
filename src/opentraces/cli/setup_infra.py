"""``opentraces setup`` infra commands — upgrade / uninstall / entity-parser.

Extracted from the ``installers`` god module (cli/setup decomposition): the
CLI/integration ``upgrade``, the symmetric ``uninstall``, and the entity-parser
installer. Registered on the shared ``setup_group``; imported by ``cli/__init__``
for the decorator-registration side effect. One-way dep on ``installers``
(setup_group); installers does not import back.
"""
from __future__ import annotations

import copy as _copy
import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from . import main
from .installers import setup_group


@setup_group.command("upgrade")
@click.option(
    "--skill-only",
    is_flag=True,
    default=False,
    help="Only update the skill file and hook, skip CLI upgrade",
)
@click.option(
    "--integrations-only",
    is_flag=True,
    default=False,
    help="Only re-render installed integration glue, skip CLI upgrade",
)
@click.pass_context
def setup_upgrade(ctx: click.Context, skill_only: bool, integrations_only: bool) -> None:
    """Upgrade the CLI, re-render installed integration glue, and refresh the project skill file."""
    # Lazy import to avoid circular imports at module load time.
    from . import _upgrade_impl
    _upgrade_impl(skill_only, integrations_only=integrations_only)


@setup_group.command(
    "uninstall",
    examples=[
        "opentraces setup uninstall --dry-run",
        "opentraces setup uninstall",
        "opentraces setup uninstall --purge --yes",
        "opentraces setup uninstall --project . --purge",
    ],
    see_also=[
        ("opentraces setup", "re-install integrations"),
        ("opentraces doctor", "verify nothing remains wired"),
    ],
)
@click.option("--integrations-only", "integrations_only", is_flag=True, default=False,
              help="Reverse install-time patches + daemons; PRESERVE all captured data (default).")
@click.option("--purge", "purge", is_flag=True, default=False,
              help="Also DELETE captured data + git refs (unrecoverable). Requires confirmation.")
@click.option("--project", "project", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="Scope per-repo reversal to one repository (default: all registered).")
@click.option("--prune-unflushed", "prune_unflushed", is_flag=True, default=False,
              help="Also delete un-flushed raw bodies + OTel staging (default tier; destructive).")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Resolve and print the plan; change nothing. Recommended first run.")
@click.option("--yes", "-y", "assume_yes", is_flag=True, default=False,
              help="Skip the --purge confirmation (required for --purge in non-interactive use).")
def setup_uninstall(integrations_only: bool, purge: bool, project: Path | None,
                    prune_unflushed: bool, dry_run: bool, assume_yes: bool) -> None:
    """Reverse the opentraces install — the symmetric inverse of ``setup``.

    \b
    Default (``--integrations-only``) reverses every install-time patch and
    daemon (hooks, OTLP env + receiver, watcher, skill, completions, per-repo
    git hooks, security flags) and PRESERVES every captured trace, dataset,
    bucket, and Git ref. After it, no opentraces process runs and no shared
    file references opentraces, but your data survives.

    ``--purge`` additionally deletes the captured corpus (bucket, datasets,
    projects, staging) and the ``refs/opentraces/*`` + ``refs/notes/opentraces``
    Git refs. This is UNRECOVERABLE — the canonical Trail event log and its
    only local replay source (the bucket) both die. It requires a typed
    confirmation (or ``--yes``).

    The opentraces package itself is never self-uninstalled; the correct
    manual command is printed.
    """
    if integrations_only and purge:
        _cli.human_echo("Choose either --integrations-only or --purge, not both.")
        _cli.emit_json(_cli.error_response(
            "INVALID_UNINSTALL_MODE", "setup-uninstall",
            "--integrations-only and --purge are mutually exclusive",
        ))
        sys.exit(2)

    from ..core import uninstall as _uninstall

    tier = "purge" if purge else "integrations"

    # --purge confirmation (a typed confirmation; --yes / --dry-run bypass it).
    if purge and not dry_run and not assume_yes:
        from ..core.config import load_config as _load_config
        config = _load_config()
        repos = _uninstall._target_repos(config, project)
        summary = _uninstall.summarize_purge_targets(config, repos)
        if not sys.stdout.isatty():
            _cli.emit_json(_cli.error_response(
                "PURGE_NEEDS_CONFIRMATION", "setup-uninstall",
                "--purge is destructive; pass --yes for non-interactive use",
            ))
            sys.exit(2)
        click.echo(_cli._warn("This --purge is UNRECOVERABLE."))
        if project is not None:
            # --project scopes purge to one repo: only its refs + marker die;
            # the cross-repo captured corpus is preserved (see core.uninstall).
            click.echo(f"  scope:               {project} (one repository)")
            click.echo(f"  repos w/ git refs:   {summary['repos_with_refs']}")
            click.echo("  Deletes that repo's refs/opentraces/* + refs/notes/opentraces and its")
            click.echo("  .opentraces.json marker. Global captured data (bucket, datasets,")
            click.echo("  projects, staging) is PRESERVED — run without --project to purge it.")
        else:
            mb = summary["bucket_bytes"] / (1024 * 1024)
            click.echo(f"  bucket:              {mb:.1f} MiB")
            click.echo(f"  datasets:            {summary['dataset_count']}")
            click.echo(f"  registered projects: {summary['registered_projects']}")
            click.echo(f"  repos w/ git refs:   {summary['repos_with_refs']}")
            click.echo("  Deletes the canonical Trail event log (refs/opentraces/local/events/v1)")
            click.echo("  AND its only local replay source (the bucket). There is no undo.")
        if summary["remote_bucket_enabled"]:
            click.echo(_cli._warn(
                f"  NOTE: remote bucket at {summary['remote_bucket_url'] or '<configured>'} "
                "will SURVIVE (local-only teardown)."))
        click.echo("  refs/notes/opentraces may include collaborators' annotations — also deleted.")
        typed = click.prompt('Type "purge" to confirm', default="", show_default=False)
        if typed.strip().lower() != "purge":
            click.echo("Aborted.")
            sys.exit(1)

    envelope = _uninstall.run_uninstall(
        tier=tier,
        project=project,
        prune_unflushed=prune_unflushed,
        dry_run=dry_run,
    )

    # Human surface.
    verb = "Would remove" if dry_run else "Removed"
    if envelope["removed_names"]:
        _cli.human_echo(f"{verb}: {', '.join(envelope['removed_names'])}")
    if envelope["skipped_names"]:
        _cli.human_echo(f"Skipped (not installed): {', '.join(envelope['skipped_names'])}")
    if envelope["error_names"]:
        _cli.human_echo(f"{_cli._err('Errors')}: {', '.join(envelope['error_names'])}")
    if tier == "purge" and envelope["refs_purged"]:
        _cli.human_echo(f"Purged {len(envelope['refs_purged'])} git ref(s).")
    elif tier == "integrations" and envelope["refs_preserved"]:
        _cli.human_echo(f"Preserved {len(envelope['refs_preserved'])} git ref(s) and all captured data.")
    _cli.human_echo(f"To remove the package itself: {envelope['package_uninstall_command']}")
    if dry_run:
        _cli.human_echo("(dry-run — nothing was changed.)")

    _cli.emit_json(envelope)
    sys.exit(0 if envelope["ok"] else 5)


def _hidden_copy(cmd: click.Command) -> click.Command:
    """Shallow-copy a Click command for a second mount point with its own
    visibility.

    Click's ``hidden`` flag lives on the ``Command`` object itself, so one
    shared object cannot be visible at one mount and hidden at another.
    Giving the setup-namespace path its own hidden copy (while the root
    peer mount stays visible) reuses the exact same callback and params —
    the implementation is defined once; only the thin Command wrapper is
    duplicated.
    """
    dup = _copy.copy(cmd)
    dup.hidden = True
    return dup


# ---------------------------------------------------------------------------
# Root peer verbs (issue #160). `upgrade` and `uninstall` are the
# update/out members of the in / update / out triad, siblings of `setup`
# rather than subcommands of it — per the ADR-0006 taxonomy, `setup` NEVER
# removes. Mount the SAME command objects at root (visible, so they get a
# COMMAND_SECTIONS row) and demote the `setup`-namespace originals to hidden
# copies: `setup upgrade` / `setup uninstall` stay fully callable (hidden !=
# removed), just off `setup --help`.
# ---------------------------------------------------------------------------
main.add_command(setup_upgrade, name="upgrade")
main.add_command(setup_uninstall, name="uninstall")
setup_group.add_command(_hidden_copy(setup_upgrade), name="upgrade")
setup_group.add_command(_hidden_copy(setup_uninstall), name="uninstall")


# ---------------------------------------------------------------------------
# Plan-043 phase 2 — `ot setup entity-parser`.
#
# Isolated block: no shared helpers with the rest of this file so phase 3
# can grow its own install steps without a merge headache.
# ---------------------------------------------------------------------------

@setup_group.command(
    "entity-parser",
    hidden=True,
    examples=[
        "opentraces setup entity-parser",
        "opentraces setup entity-parser --force",
    ],
    see_also=[
        ("opentraces doctor", "verify entity-parser install"),
        ("opentraces backfill", "populate attribution + entity caches"),
    ],
)
@click.option(
    "--force", is_flag=True,
    help="Re-download even if the expected version is already installed.",
)
def setup_entity_parser(force: bool) -> None:
    """Download and verify the `ot-entities` binary.

    The entity parser is a separate binary (distributed via the opentraces
    release channel) that turns a commit diff into a structured entity
    change list: added/modified/renamed/deleted functions, classes, etc.
    It powers the richer side of `opentraces trail blame` and the per-commit
    entity cache under ~/.opentraces/projects/<slug>/entities/.

    Honours $OPENTRACES_ENTITY_BIN for airgapped installs: set it to a
    pre-placed binary and this command will verify instead of downloading.
    """
    from ..enrichment.entities import installer as _inst
    from ..enrichment.entities.version import ENTITY_BINARY_VERSION

    def _progress_printer():
        total = {"n": 0}

        def _cb(chunk_size: int) -> None:
            total["n"] += chunk_size
            mb = total["n"] / (1024 * 1024)
            click.echo(f"\r  downloading… {mb:6.2f} MiB", nl=False)

        return _cb, total

    cb, total = _progress_printer()
    try:
        result = _inst.install(force=force, progress=cb)
    except _inst.InstallError as e:
        if total["n"]:
            click.echo("")
        _cli.human_echo(f"{_cli._err('error')}: {e}")
        _cli.emit_json({"status": "error", "action": "setup-entity-parser",
                   "message": str(e)})
        sys.exit(5)

    if total["n"]:
        click.echo("")
    # Report the version the binary ACTUALLY reports (result.version), not the
    # pinned manifest constant — so a stale/bogus env-override surfaces honestly.
    up_to_date = result.version == ENTITY_BINARY_VERSION
    _cli.human_echo(
        f"Entity parser installed at {result.path} "
        f"(version {result.version}, {result.source})"
    )
    if not up_to_date:
        _cli.human_echo(
            f"{_cli._warn('warning')}: expected version {ENTITY_BINARY_VERSION} "
            f"but the binary reports {result.version!r}. Entity enrichment may "
            f"be degraded; run 'opentraces setup entity-parser --force' to refresh."
        )
    _cli.emit_json({
        "status": "ok",
        "action": "setup-entity-parser",
        "binary_path": str(result.path),
        "version": result.version,
        "expected_version": ENTITY_BINARY_VERSION,
        "up_to_date": up_to_date,
        "platform": result.platform,
        "source": result.source,
    })
