"""``opentraces setup`` agent-harness install commands.

Extracted from the ``installers`` god module (cli/setup decomposition): the
per-agent capture-install commands — ``claude-code`` / ``codex-cli`` / ``pi`` /
``git`` / ``skill`` — registered on the shared ``setup_group`` via decorators.
Imported by ``cli/__init__`` for the registration side effect (one-way dep:
imports ``setup_group`` from ``installers``; ``installers`` does not import back).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from .installers import setup_group


@setup_group.command(
    "claude-code",
    examples=[
        "opentraces setup claude-code",
        "opentraces setup claude-code --dry-run",
        "opentraces setup claude-code --remove",
    ],
    see_also=[
        ("opentraces setup git", "install the post-commit hook"),
        ("opentraces doctor", "verify the installation"),
    ],
    option_groups=[
        ("Paths", ["hooks_dir", "settings_file"]),
        ("Action", ["dry_run", "remove"]),
    ],
)
@click.option(
    "--hooks-dir", default=None,
    help="Target directory for hook scripts (default: ~/.claude/hooks/)",
)
@click.option(
    "--settings-file", default=None,
    help="Claude Code settings file (default: ~/.claude/settings.json)",
)
@click.option("--dry-run", is_flag=True, help="Print the plan without making changes")
@click.option("--remove", is_flag=True, help="Uninstall hooks instead of installing")
def setup_claude_code(
    hooks_dir: str | None, settings_file: str | None, dry_run: bool, remove: bool,
) -> None:
    """Install the Claude Code session-capture hooks.

    Registers four hooks in ~/.claude/settings.json so every Claude Code
    session is enriched in place, ready for OpenTraces ingestion:

    \b
      PreToolUse   opens a firm tool-boundary window before a tool runs.
      PostToolUse  closes the tool window, records write metadata, and emits
                   patch evidence when the worktree changed.
      Stop         appends a git-state snapshot (branch, HEAD, dirty files)
                   to the session transcript when the agent stops.
      PostCompact  records explicit compaction events so collapsed context
                   is still attributable.

    OFF cost: hooks are the foundation of capture — they feed the trace,
    trail, and context-tree records alike. Turning them off does not stop
    capture: it continues via watcher backfill, but coarser (no live step
    boundaries) and attribution degrades.

    Use --dry-run to preview the changes, --remove to uninstall.
    """
    from ..capture.claude_code.install import (
        InstallError,
        install as install_hooks,
        plan_install,
        remove as remove_hooks,
    )

    hd = Path(hooks_dir) if hooks_dir else None
    sf = Path(settings_file) if settings_file else None

    if remove:
        result = remove_hooks(hd, sf)
        _cli.human_echo(
            f"opentraces hooks removed from {result.config_files[0] if result.config_files else sf}."
        )
        _cli.emit_json({
            "status": "ok",
            "action": "remove",
            "removed": result.removed,
        })
        return

    try:
        if dry_run:
            plan, ts = plan_install(hd, sf)
            plan_data = [
                {"event": p.event, "source": str(p.source), "dest": str(p.dest)}
                for p in plan
            ]
            _cli.human_echo("[dry-run] Would install hooks:")
            for pd in plan_data:
                _cli.human_echo(f"  {pd['event']}: {pd['source']} -> {pd['dest']}")
            _cli.human_echo(f"[dry-run] Would update: {ts}")
            _cli.emit_json({
                "status": "ok",
                "dry_run": True,
                "plan": plan_data,
                "settings_file": str(ts),
            })
            return

        result = install_hooks(hd, sf)
    except InstallError as e:
        _cli.emit_json(_cli.error_response(e.code, "install", e.message))
        sys.exit(5)

    for dest in result.installed.values():
        _cli.human_echo(f"Installed: {dest}")
    if result.added:
        _cli.human_echo(
            f"Registered hooks in {result.settings_file}: {', '.join(result.added)}"
        )
    else:
        _cli.human_echo(
            f"Hooks already registered in {result.settings_file}, no changes needed."
        )

    _cli.emit_json({
        "status": "ok",
        "installed": result.installed,
        "settings_file": str(result.settings_file),
        "hooks_added": result.added,
        "next_steps": [
            "Hooks are now active for all future Claude Code sessions.",
            "Use 'opentraces trace query' and dataset workflows after sessions.",
        ],
    })


@setup_group.command(
    "codex-cli",
    examples=[
        "opentraces setup codex-cli",
        "opentraces setup codex-cli --dry-run",
        "opentraces setup codex-cli --remove",
    ],
    see_also=[
        ("opentraces init --agent codex-cli", "connect a project to Codex capture"),
        ("opentraces setup git", "install the post-commit hook"),
        ("opentraces doctor", "verify the installation"),
    ],
    option_groups=[
        ("Paths", ["hooks_dir", "hooks_file"]),
        ("Action", ["dry_run", "remove"]),
    ],
)
@click.option(
    "--hooks-dir",
    default=None,
    help="Target directory for copied hook scripts (default: ~/.codex/hooks/opentraces/)",
)
@click.option(
    "--hooks-file",
    default=None,
    help="Codex hooks file (default: ~/.codex/hooks.json)",
)
@click.option("--dry-run", is_flag=True, help="Print the plan without making changes")
@click.option("--remove", is_flag=True, help="Uninstall hooks instead of installing")
def setup_codex_cli(
    hooks_dir: str | None,
    hooks_file: str | None,
    dry_run: bool,
    remove: bool,
) -> None:
    """Install Codex CLI session-capture hooks.

    Registers native Codex hook commands in ``~/.codex/hooks.json``. Hook
    scripts write project-local sidecar JSONL under ``.opentraces/codex-cli``
    and the Stop hook triggers bounded session ingestion through the Codex
    parser.

    OFF cost: hooks are the foundation of capture — they feed the trace,
    trail, and context-tree records alike. Turning them off does not stop
    capture: it continues via watcher backfill, but coarser (no live step
    boundaries) and attribution degrades.
    """
    from ..capture._base import HookInstallError
    from ..capture.codex_cli.install import (
        install as install_hooks,
        plan_install,
        remove as remove_hooks,
    )

    hd = Path(hooks_dir) if hooks_dir else None
    hf = Path(hooks_file) if hooks_file else None

    if remove:
        result = remove_hooks(hd, hf)
        target = result.config_files[0] if result.config_files else hf
        _cli.human_echo(f"opentraces Codex hooks removed from {target}.")
        _cli.emit_json({
            "status": "ok",
            "action": "remove",
            "removed": result.removed,
        })
        return

    try:
        if dry_run:
            plan, target = plan_install(hd, hf)
            plan_data = [
                {
                    "event": item.event,
                    "module": item.module,
                    "source": str(item.source),
                    "dest": str(item.dest),
                }
                for item in plan
            ]
            _cli.human_echo("[dry-run] Would install Codex hooks:")
            for item in plan_data:
                _cli.human_echo(
                    f"  {item['event']}: {item['source']} -> {item['dest']}"
                )
            _cli.human_echo(f"[dry-run] Would update: {target}")
            _cli.emit_json({
                "status": "ok",
                "dry_run": True,
                "plan": plan_data,
                "hooks_file": str(target),
            })
            return

        result = install_hooks(hd, hf)
    except HookInstallError as exc:
        _cli.emit_json(_cli.error_response(exc.code, "install", exc.message))
        sys.exit(5)

    for dest in result.installed.values():
        _cli.human_echo(f"Installed: {dest}")
    if result.added:
        _cli.human_echo(
            f"Registered Codex hooks in {result.hooks_file}: {', '.join(result.added)}"
        )
    else:
        _cli.human_echo(
            f"Codex hooks already registered in {result.hooks_file}, no changes needed."
        )

    _cli.emit_json({
        "status": "ok",
        "installed": result.installed,
        "hooks_file": str(result.hooks_file),
        "hooks_added": result.added,
        "next_steps": [
            "Hooks are now active for all future Codex CLI sessions.",
            "Use 'opentraces trace query' and dataset workflows after sessions.",
        ],
    })


@setup_group.command(
    "pi",
    examples=[
        "opentraces setup pi",
        "opentraces setup pi --dry-run --json",
        "opentraces setup pi --project --local",
        "opentraces setup pi --remove",
    ],
    see_also=[
        ("pi install npm:opentraces-pi", "primary Pi package install path"),
        ("opentraces init --agent pi", "enroll this project for Pi capture"),
        ("opentraces setup git", "install the post-commit hook"),
    ],
    option_groups=[
        ("Scope", ["project_scope", "settings_file", "local_package"]),
        ("Action", ["dry_run", "remove"]),
    ],
)
@click.option("--project", "project_scope", is_flag=True, help="Write/check project-local .pi/settings.json instead of global Pi settings")
@click.option("--settings-file", default=None, help="Explicit Pi settings.json path")
@click.option("--local", "local_package", is_flag=True, help="Use local packages/opentraces-pi path when present")
@click.option("--dry-run", is_flag=True, help="Report the setup plan without writing")
@click.option("--remove", is_flag=True, help="Remove the package entry instead of installing")
@click.option("--json", "json_flag", is_flag=True, help="Emit machine-readable JSON (accepted for Pi tool callers)")
def setup_pi(
    project_scope: bool,
    settings_file: str | None,
    local_package: bool,
    dry_run: bool,
    remove: bool,
    json_flag: bool,
) -> None:
    """Verify, install, repair, or remove the OpenTraces Pi package.

    This command manages the Pi package resource entry only. It does not
    silently install Python, start services, configure HuggingFace auth, or
    enable optional security tools. Use `/ot-setup` inside Pi or
    `opentraces init --agent pi` for project capture enrollment.

    OFF cost: hooks are the foundation of capture — they feed the trace,
    trail, and context-tree records alike. Turning them off does not stop
    capture: it continues via watcher backfill, but coarser (no live step
    boundaries) and attribution degrades.
    """
    if json_flag:
        _cli._json_mode = True  # noqa: SLF001 - command-local --json compatibility
    from ..capture._base import HookInstallError
    from ..capture.pi.install import (
        PiHookInstaller,
        plan_install,
        remove as remove_package,
        setup_plan_json,
        status as pi_status,
    )

    project_dir = Path.cwd()
    sf = Path(settings_file).expanduser() if settings_file else None
    try:
        if remove:
            result = remove_package(project=project_scope, settings_file=sf, cwd=project_dir)
            _cli.human_echo("opentraces Pi package entry removed." if result.removed else "opentraces Pi package entry was not installed.")
            _cli.emit_json({
                "status": "ok",
                "action": "remove",
                "removed": result.removed,
                "state": pi_status(project=project_scope, settings_file=sf, cwd=project_dir),
            })
            return

        if dry_run:
            plan, target = plan_install(
                project=project_scope,
                settings_file=sf,
                cwd=project_dir,
                local=local_package,
            )
            setup_plan = setup_plan_json(project_dir=project_dir, project=project_scope)
            setup_plan.update({
                "dry_run": True,
                "plan": plan,
                "settings_file": str(target),
                "writes": [],
            })
            _cli.human_echo("[dry-run] Would ensure opentraces-pi is present in Pi settings")
            _cli.human_echo(f"[dry-run] Would update: {target}")
            _cli.emit_json(setup_plan)
            return

        inst = PiHookInstaller(
            project=project_scope,
            settings_file=sf,
            cwd=project_dir,
            local=local_package,
        )
        result = inst.install()
    except HookInstallError as exc:
        _cli.emit_json(_cli.error_response(exc.code, "install", exc.message))
        sys.exit(5)

    if result.added:
        _cli.human_echo(f"Installed opentraces-pi package entry in {result.config_files[0] if result.config_files else 'Pi settings'}")
    else:
        _cli.human_echo("opentraces-pi package entry already present.")
    _cli.emit_json({
        "status": "ok",
        "installed": result.installed,
        "added": result.added,
        "config_files": [str(p) for p in result.config_files],
        "state": inst.status(),
        "next_steps": [
            "Run pi and invoke /ot-setup for guided local capture setup.",
            "Capture is on by default under global tracking; run 'opentraces config tracking-mode manual' or exclude a project to opt out.",
        ],
    })


@setup_group.command(
    "git",
    examples=[
        "opentraces setup git",
        "opentraces setup git --remove",
    ],
    see_also=[
        ("opentraces trail blame", "resolve a commit to its contributing traces"),
        ("opentraces setup claude-code", "install session capture hooks"),
    ],
)
@click.option("--remove", is_flag=True, help="Uninstall the hook instead of installing")
def setup_git(remove: bool) -> None:
    """Install the post-commit hook that correlates commits to traces.

    After install, every `git commit` attaches a note under
    refs/notes/opentraces linking the commit sha to the trace(s) whose
    Edit/Write tool calls produced its changes. This powers:

    \b
      opentraces trail blame commit <commit>
                                  resolve a commit back to contributing
                                  traces and the agent context behind them.

    Old commits cannot be backfilled, correlation starts from the first
    commit after install.

    OFF cost: git is a record ENRICHER, not a capture source — turning it
    off does not stop capture, but trace attribution and `trail blame`
    degrade (no commit-to-trace correlation for commits made while it's
    off). The watcher auto-reinstalls this hook every sweep while it runs,
    so an explicit `--remove` here is the honest way to keep it off.

    Use --remove to uninstall.
    """
    from ..capture.git import install as git_hook
    if remove:
        git_hook.remove(Path.cwd())
        _cli.human_echo("opentraces post-commit hook removed.")
        _cli.emit_json({"status": "ok", "action": "remove",
                   "state": git_hook.status(Path.cwd())})
        return
    ok = git_hook.install(Path.cwd())
    st = git_hook.status(Path.cwd())
    if ok and st["installed"]:
        _cli.human_echo("")
        _cli.print_banner(tagline=_cli._ok("git hook installed"))
        _cli.human_echo(f"  {_cli._dim('owned hook:')} {st['hook_dir']}/{git_hook.HOOK_FILENAME}")
    else:
        _cli.human_echo("install failed: not a git repo or insufficient permissions.")
    _cli.emit_json({"status": "ok" if ok else "error", "action": "install", "state": st})


@setup_group.command(
    "skill",
    examples=[
        "opentraces setup skill",
        "opentraces setup skill --remove",
        "opentraces setup skill --harness claude-code",
        "opentraces setup skill --harness codex-cli",
        "opentraces setup skill --harness pi",
    ],
    see_also=[
        ("opentraces setup upgrade", "refresh the skill after a CLI update"),
        ("opentraces doctor", "verify skill install + per-harness symlinks"),
    ],
)
@click.option("--remove", is_flag=True, help="Uninstall instead of installing")
@click.option(
    "--harness", "harnesses", multiple=True,
    help="Limit to specific agent harness (repeatable). Defaults to all supported.",
)
def setup_skill(remove: bool, harnesses: tuple[str, ...]) -> None:
    """Install the opentraces skill globally and link it into each agent harness.

    Canonical copy is written to ~/.agents/skills/opentraces/ (vendor-neutral
    staging dir). Each supported harness gets a symlink, e.g.
    ~/.claude/skills/opentraces -> ~/.agents/skills/opentraces or
    ~/.codex/skills/opentraces -> ~/.agents/skills/opentraces.

    Re-running is an idempotent refresh: canonical is wiped and repopulated
    from the packaged skill/, the version stamp is rewritten, and broken
    symlinks are repaired. Pre-existing non-symlink harness directories are
    moved aside to opentraces.bak.<timestamp> before the symlink is created.
    """
    from ..capture.skill.install import HARNESS_DIRS, SkillInstaller

    targets = list(harnesses) if harnesses else list(HARNESS_DIRS.keys())
    unknown = [h for h in targets if h not in HARNESS_DIRS]
    if unknown:
        _cli.human_echo(
            f"unknown harness(es): {', '.join(unknown)}. "
            f"supported: {', '.join(HARNESS_DIRS.keys())}"
        )
        _cli.emit_json(_cli.error_response("UNKNOWN_HARNESS", "setup",
                                 f"unknown harness: {unknown}"))
        raise click.exceptions.Exit(2)

    inst = SkillInstaller(harnesses=targets)
    if remove:
        result = inst.remove()
        _cli.human_echo("opentraces skill removed.")
        _cli.emit_json({"status": "ok", "action": "remove",
                   "removed": result.removed, "state": inst.status()})
        return

    result = inst.install()
    st = inst.status()
    if not result.ok:
        for note in result.notes:
            _cli.human_echo(f"  {_cli._err('error')}: {note}")
        _cli.emit_json({"status": "error", "action": "install",
                   "notes": result.notes, "state": st})
        raise click.exceptions.Exit(3)

    _cli.human_echo("")
    _cli.print_banner(tagline=_cli._ok("skill installed"))
    _cli.human_echo(f"  {_cli._dim('canonical:')} {st['canonical']} ({st['installed_version']})")
    for h, hs in st["harnesses"].items():
        if h not in targets:
            continue
        if hs.get("canonical"):
            _cli.human_echo(f"  {_cli._dim('linked:'):<14} {hs.get('symlink_path')}")
        elif hs.get("present"):
            _cli.human_echo(f"  {_cli._dim(h+':')} present but not canonical ({hs.get('kind')})")
        else:
            _cli.human_echo(f"  {_cli._dim(h+':')} not linked")
    for note in result.notes:
        _cli.human_echo(f"  {_cli._dim('note:')} {note}")
    _cli.emit_json({"status": "ok", "action": "install",
               "added": result.added, "notes": result.notes, "state": st})
