"""CLI installers/admin group: setup, blame, resume, doctor, llm-review."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main
from ..core.config import save_config  # noqa: F401
from ..core.trace_meta import short_trace_id


def load_config():
    return _cli.load_config()

logger = logging.getLogger("opentraces.cli.installers")


def emit_json(data):
    return _cli.emit_json(data)


def error_response(*a, **k):
    return _cli.error_response(*a, **k)


def human_echo(*a, **k):
    return _cli.human_echo(*a, **k)


def human_hint(*a, **k):
    return _cli.human_hint(*a, **k)


def print_banner(*a, **k):
    return _cli.print_banner(*a, **k)




@main.command("_run-post-commit-hook", hidden=True)
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False), default=".")
def run_post_commit_hook(repo_path: str) -> None:
    """Invoked by .git/hooks/opentraces-post-commit after each commit.

    Never raises to the shell: any failure exits 0.
    """
    from ..capture.git import post_commit

    post_commit.run_for_repo(Path(repo_path))


@main.group("setup", invoke_without_command=True)
@click.pass_context
def setup_group(ctx: click.Context) -> None:
    """Wire opentraces into your system.

    Each subcommand installs one integration:

    \b
      claude-code   PreToolUse/PostToolUse/Stop/PostCompact hooks that capture
                    Claude Code step boundaries and session transcripts.
      git           post-commit hook that correlates each commit to the
                    trace that produced it (via refs/notes/opentraces),
                    powering `opentraces blame` and git-linked uploads.
      trufflehog    Tier 1.5 secret scanner. Findings redact in place
                    and force review before push.
      llm-review    Tier 2 third-party LLM reviewer for staged traces,
                    used by `opentraces llm-review` and `push --llm-review`.

    Run bare ``opentraces setup`` for an interactive wizard that walks every
    integration, or call a subcommand to target one directly.
    """
    if ctx.invoked_subcommand is not None:
        return
    _run_setup_wizard()


def _wizard_confirm(prompt: str, *, default: bool, hint: str | None = None) -> bool:
    """Ask a yes/no question with pyclack when available (for consistent
    ◇ styling), else fall back to click.confirm.

    ``hint`` is shown in dim text under the prompt when using click — pyclack
    doesn't have a hint slot on confirm, so we fold it into the prompt.
    """
    full = prompt if not hint else f"{prompt} ({hint})"
    try:
        from pyclack.prompts import confirm as _pk_confirm
        import asyncio as _asyncio
        return bool(_asyncio.run(_pk_confirm(
            full,
            initial_value=default,
            active="Yes",
            inactive="No",
        )))
    except ImportError:
        return click.confirm(f"    {full}", default=default)


def _run_setup_wizard() -> None:
    """Walk every integration the user should know about, one prompt each.

    Order (mandatory with opt-out, then optional):
      1. claude-code / git / skill hooks  (hook installers, default yes)
      2. watcher                          (powers 'opentraces blame', default yes)
      3. entity-parser (sem)              (richer commit diffs, default yes)
      4. HuggingFace login                (log in now or skip)
      5. trufflehog                       (global config, default no)
      6. llm-review                       (global config, default no)
      7. closing panel — point at `opentraces init` + `opentraces doctor`
    """
    from ..capture import get_hook_installers
    from ..security.trufflehog import find_trufflehog, install_binary
    from ..watcher import installer as _winst
    from ..enrichment.entities import installer as _entinst
    from ..enrichment.entities import EntityRunner
    from ..enrichment.entities.runner import resolve_binary_path
    from opentraces import cli as _cli

    print_banner(tagline="setup wizard")
    human_echo("")

    # 1. Hook installers (claude-code, git, skill) — one prompt each,
    #    default yes.
    for name, cls in get_hook_installers().items():
        inst = cls()
        st = inst.status()
        installed = bool(st.get("installed"))
        label = _cli._ok("installed") if installed else _cli._dim("not installed")
        human_echo(f"  {_cli._bold(name):<28} {label}")
        if installed:
            continue
        if not _wizard_confirm(f"install {name}?", default=True):
            continue
        try:
            result = inst.install()
        except Exception as e:
            human_echo(f"    {_cli._err('failed')}: {e}")
            continue
        if result.ok:
            human_echo(f"    {_cli._ok('done')} ({', '.join(result.added) or 'already present'})")
        else:
            for note in result.notes:
                human_echo(f"    {_cli._err('skip')}: {note}")

    # 2. Watcher — default yes. Powers "opentraces blame" by running
    #    incremental backfill in the background after each commit.
    w_st = _winst.status()
    w_label = (
        _cli._ok("installed") if w_st.installed else _cli._dim("not installed")
    )
    human_echo(f"  {_cli._bold('watcher'):<28} {w_label}")
    if not w_st.installed:
        if _wizard_confirm(
            "install the attribution watcher?",
            default=True,
            hint="powers 'opentraces blame' on every new commit",
        ):
            try:
                path = _winst.install()
                human_echo(f"    {_cli._ok('installed')} {path}")
            except Exception as e:
                human_echo(f"    {_cli._err('failed')}: {e}")

    # 3. Entity parser (sem) — default yes, richer commit diffs.
    ent_runner = EntityRunner(binary_path=resolve_binary_path())
    ent_installed = ent_runner.available()
    ent_label = (
        _cli._ok("installed") if ent_installed else _cli._dim("not installed")
    )
    human_echo(f"  {_cli._bold('entity-parser'):<28} {ent_label}")
    if not ent_installed:
        if _wizard_confirm(
            "install the entity parser (sem)?",
            default=True,
            hint="entity-level diffs for richer commit attribution",
        ):
            try:
                _entinst.install()
                human_echo(f"    {_cli._ok('installed')}")
            except _entinst.InstallError as e:
                human_echo(f"    {_cli._err('failed')}: {e}")

    # 4. HuggingFace login.
    cfg = load_config()
    identity = _cli._auth_identity(cfg.hf_token) if cfg.hf_token else None
    if identity:
        human_echo(
            f"  {_cli._bold('huggingface'):<28} "
            f"{_cli._ok('authenticated')} "
            f"{_cli._dim('(' + str(identity.get('name') or '') + ')')}"
        )
    else:
        human_echo(f"  {_cli._bold('huggingface'):<28} {_cli._dim('not authenticated')}")
        if _wizard_confirm(
            "log into HuggingFace now?",
            default=True,
            hint="needed to push traces; skip and run 'opentraces auth login' later",
        ):
            try:
                from ..core.config import save_credentials, CREDENTIALS_PATH
                _cli._login_with_device_code(save_credentials, CREDENTIALS_PATH)
            except Exception as e:
                human_echo(f"    {_cli._err('failed')}: {e}")

    # 5. Optional: trufflehog. Default no — it's a global config
    #    change; users can override per-project via `opentraces init`.
    th_version = find_trufflehog()
    th_enabled = cfg.security.trufflehog.enabled
    th_label = (
        _cli._ok(f"enabled ({th_version})") if th_enabled and th_version
        else _cli._dim("disabled" if not th_enabled else "enabled but missing")
    )
    human_echo(f"  {_cli._bold('trufflehog'):<28} {th_label}")
    if not (th_enabled and th_version):
        if _wizard_confirm(
            "enable trufflehog (Tier 1.5) globally?",
            default=False,
            hint="redacts findings in place and forces review; global setting",
        ):
            if th_version is None:
                ok, method = install_binary()
                if ok:
                    human_echo(f"    installed via {method}")
                    th_version = find_trufflehog()
                else:
                    human_echo(f"    {_cli._err('install failed')} — see https://github.com/trufflesecurity/trufflehog")
            if th_version:
                cfg.security.trufflehog.enabled = True
                save_config(cfg)
                human_echo(f"    {_cli._ok('enabled')}")

    # 6. Optional: LLM review. Also a global config change.
    llm_enabled = getattr(cfg.security, "llm_review", None) and getattr(cfg.security.llm_review, "enabled", False)
    llm_label = _cli._ok("enabled") if llm_enabled else _cli._dim("disabled")
    human_echo(f"  {_cli._bold('llm-review'):<28} {llm_label}")
    if not llm_enabled:
        if _wizard_confirm(
            "enable Tier 2 LLM trace review globally?",
            default=False,
            hint="configure via 'opentraces setup llm-review'; global setting",
        ):
            human_hint(
                "    run 'opentraces setup llm-review' to pick provider/model."
            )

    human_echo("")
    human_echo(_cli._bold("Next steps"))
    human_echo(
        f"  • to track a project:  {_cli._bold('cd <project> && opentraces init')}"
    )
    human_echo(
        f"  • to inspect health:   {_cli._bold('opentraces doctor')}"
    )
    human_echo(
        f"  • security / review policy are per-project — set them at "
        f"{_cli._bold('opentraces init')} time."
    )


@main.command(
    "blame",
    examples=[
        "opentraces blame",
        "opentraces blame abc1234",
        "opentraces blame HEAD~3 --json",
    ],
    see_also=[
        ("opentraces resume", "re-open the trace's Claude session"),
        ("opentraces setup git", "install the post-commit hook"),
    ],
)
@click.argument("commit", default="HEAD")
@click.option("--json", "json_out", is_flag=True, help="Emit machine-readable JSON")
def blame_cmd(commit: str, json_out: bool) -> None:
    """Resolve a commit to the traces behind it.

    COMMIT is any git ref: a sha (short or full), branch name, or `HEAD~N`.
    Traces are linked via `refs/notes/opentraces` written by the post-commit
    hook (install with ``opentraces setup git``).
    """
    from ..core.config import get_project_traces_dir
    from ..core.inbox import load_trace_records
    from ..enrichment.git import blame as git_blame
    from opentraces import cli as _cli

    cwd = Path.cwd()
    full_sha, hits = git_blame.blame_commit(commit, cwd)

    if not hits:
        if json_out:
            emit_json({"commit": full_sha, "traces": []})
            return
        human_echo(f"no opentraces notes attached to {full_sha[:10]}")
        human_hint(
            "install the hook with 'opentraces setup git' and commit to "
            "start correlating; old commits can't be backfilled."
        )
        return

    staging = get_project_traces_dir(cwd)
    traces_by_id = {r.trace_id: r for r in load_trace_records(staging)}

    if json_out:
        emit_json({
            "commit": full_sha,
            "traces": [
                {
                    "trace_id": h.trace_id,
                    "session_id": getattr(traces_by_id.get(h.trace_id), "session_id", None),
                    "url": h.url,
                }
                for h in hits
            ],
        })
        return

    human_echo(
        f"commit {_cli._bold(full_sha[:10])} has {len(hits)} "
        f"trace{'s' if len(hits) != 1 else ''}:"
    )
    human_echo("")
    for h in hits:
        rec = traces_by_id.get(h.trace_id)
        session_id = getattr(rec, "session_id", None) if rec else None
        label = None
        if rec is not None:
            label, _src = _cli._describe_trace(rec)
            if label and len(label) > 70:
                label = label[:69] + "…"
        human_echo(f"  {_cli._dim('trace:  ')} {h.trace_id}")
        if label:
            human_echo(f"  {_cli._dim('task:   ')} {label}")
        if session_id:
            human_echo(
                f"  {_cli._dim('resume: ')} opentraces resume {h.trace_id}  "
                f"{_cli._dim(f'(claude session {session_id[:8]})')}"
            )
        if h.url:
            human_echo(f"  {_cli._dim('url:    ')} {h.url}")
        human_echo("")


@main.command("resume")
@click.argument("trace_id")
@click.option("--exec", "do_exec", is_flag=True, help="Exec the claude resume command instead of printing it.")
def resume_cmd(trace_id: str, do_exec: bool) -> None:
    """Resume the agent session that produced a trace.

    Looks up the trace's session_id and either prints the resume command
    (default) or execs it with --exec. Pairs naturally with `blame <sha>`
    to re-open the session behind a given commit.
    """
    from ..capture.claude_code.resume import ResumeError, resolve
    from ..core.config import get_project_traces_dir, project_is_opted_in
    from opentraces import cli as _cli

    if not project_is_opted_in(Path.cwd()):
        human_echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    staging = get_project_traces_dir(Path.cwd())
    try:
        target = resolve(trace_id, staging)
    except ResumeError as e:
        human_echo(e.message)
        sys.exit({"NO_MATCH": 3, "AMBIGUOUS": 3, "NO_SESSION": 4}.get(e.code, 3))

    if do_exec:
        import shutil as _sh
        if _sh.which("claude") is None:
            human_echo("'claude' not on PATH. Install Claude Code or run the command manually.")
            human_echo(f"  {target.command}")
            sys.exit(5)
        os.execvp(target.argv[0], target.argv)

    human_echo(f"{_cli._dim('session:')} {target.session_id}")
    human_echo(f"{_cli._dim('run:')}     {_cli._bold(target.command)}")
    human_echo(f"{_cli._dim('or:')}      opentraces resume {short_trace_id(trace_id)} --exec")
    emit_json({
        "trace_id": target.trace_id,
        "session_id": target.session_id,
        "resume_command": target.command,
    })


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

    Registers two hooks in ~/.claude/settings.json so every Claude Code
    session is enriched in place, ready for `opentraces push` to parse:

    \b
      Stop         appends a git-state snapshot (branch, HEAD, dirty files)
                   to the session transcript when the agent stops.
      PostCompact  records explicit compaction events so collapsed context
                   is still attributable.

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
        human_echo(
            f"opentraces hooks removed from {result.config_files[0] if result.config_files else sf}."
        )
        emit_json({
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
            human_echo("[dry-run] Would install hooks:")
            for pd in plan_data:
                human_echo(f"  {pd['event']}: {pd['source']} -> {pd['dest']}")
            human_echo(f"[dry-run] Would update: {ts}")
            emit_json({
                "status": "ok",
                "dry_run": True,
                "plan": plan_data,
                "settings_file": str(ts),
            })
            return

        result = install_hooks(hd, sf)
    except InstallError as e:
        emit_json(error_response(e.code, "install", e.message))
        sys.exit(5)

    for dest in result.installed.values():
        human_echo(f"Installed: {dest}")
    if result.added:
        human_echo(
            f"Registered hooks in {result.settings_file}: {', '.join(result.added)}"
        )
    else:
        human_echo(
            f"Hooks already registered in {result.settings_file}, no changes needed."
        )

    emit_json({
        "status": "ok",
        "installed": result.installed,
        "settings_file": str(result.settings_file),
        "hooks_added": result.added,
        "next_steps": [
            "Hooks are now active for all future Claude Code sessions.",
            "Re-run 'opentraces push' after sessions to include enriched data.",
        ],
    })


@setup_group.command(
    "git",
    examples=[
        "opentraces setup git",
        "opentraces setup git --remove",
    ],
    see_also=[
        ("opentraces blame", "resolve a commit to its contributing traces"),
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
      opentraces blame <commit>   resolve a commit back to contributing
                                  traces and the agent context behind them.
      opentraces push             uploads carry git-link metadata so
                                  consumers can trace a line to its session.

    Old commits cannot be backfilled, correlation starts from the first
    commit after install. Use --remove to uninstall.
    """
    from ..capture.git import install as git_hook
    if remove:
        git_hook.remove(Path.cwd())
        human_echo("opentraces post-commit hook removed.")
        emit_json({"status": "ok", "action": "remove",
                   "state": git_hook.status(Path.cwd())})
        return
    ok = git_hook.install(Path.cwd())
    st = git_hook.status(Path.cwd())
    if ok and st["installed"]:
        human_echo("")
        print_banner(tagline=_cli._ok("git hook installed"))
        human_echo(f"  {_cli._dim('owned hook:')} {st['hook_dir']}/{git_hook.HOOK_FILENAME}")
    else:
        human_echo("install failed: not a git repo or insufficient permissions.")
    emit_json({"status": "ok" if ok else "error", "action": "install", "state": st})


@setup_group.command(
    "skill",
    examples=[
        "opentraces setup skill",
        "opentraces setup skill --remove",
        "opentraces setup skill --harness claude-code",
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
    ~/.claude/skills/opentraces -> ~/.agents/skills/opentraces.

    Re-running is an idempotent refresh: canonical is wiped and repopulated
    from the packaged skill/, the version stamp is rewritten, and broken
    symlinks are repaired. Pre-existing non-symlink harness directories are
    moved aside to opentraces.bak.<timestamp> before the symlink is created.
    """
    from ..capture.skill.install import HARNESS_DIRS, SkillInstaller
    from opentraces import cli as _cli

    targets = list(harnesses) if harnesses else list(HARNESS_DIRS.keys())
    unknown = [h for h in targets if h not in HARNESS_DIRS]
    if unknown:
        human_echo(
            f"unknown harness(es): {', '.join(unknown)}. "
            f"supported: {', '.join(HARNESS_DIRS.keys())}"
        )
        emit_json(error_response("UNKNOWN_HARNESS", "setup",
                                 f"unknown harness: {unknown}"))
        raise click.exceptions.Exit(2)

    inst = SkillInstaller(harnesses=targets)
    if remove:
        result = inst.remove()
        human_echo("opentraces skill removed.")
        emit_json({"status": "ok", "action": "remove",
                   "removed": result.removed, "state": inst.status()})
        return

    result = inst.install()
    st = inst.status()
    if not result.ok:
        for note in result.notes:
            human_echo(f"  {_cli._err('error')}: {note}")
        emit_json({"status": "error", "action": "install",
                   "notes": result.notes, "state": st})
        raise click.exceptions.Exit(3)

    human_echo("")
    print_banner(tagline=_cli._ok("skill installed"))
    human_echo(f"  {_cli._dim('canonical:')} {st['canonical']} ({st['installed_version']})")
    for h, hs in st["harnesses"].items():
        if h not in targets:
            continue
        if hs.get("canonical"):
            human_echo(f"  {_cli._dim('linked:'):<14} ~/.{h.replace('-', '/')}/skills/opentraces")
        elif hs.get("present"):
            human_echo(f"  {_cli._dim(h+':')} present but not canonical ({hs.get('kind')})")
        else:
            human_echo(f"  {_cli._dim(h+':')} not linked")
    for note in result.notes:
        human_echo(f"  {_cli._dim('note:')} {note}")
    emit_json({"status": "ok", "action": "install",
               "added": result.added, "notes": result.notes, "state": st})


# ---------------------------------------------------------------------------
# Plan 032 Phase 1 — security-module CLI surfaces.
# ---------------------------------------------------------------------------


def _pick_install_method_interactive() -> str | None:
    """Ask the user which installer to use; return ``None`` if declined."""
    from ..security.trufflehog import available_installers

    available = available_installers()
    if not available:
        human_echo(
            "trufflehog is not installed and no supported installer was found "
            "(brew, go) on this machine."
        )
        human_echo(
            "Install it manually from https://github.com/trufflesecurity/trufflehog "
            "and re-run 'opentraces setup trufflehog --enable'."
        )
        return None

    human_echo("")
    human_echo("trufflehog is not installed. choose an install method:")
    for i, name in enumerate(available, 1):
        blurb = {"brew": "Homebrew (recommended on macOS)",
                 "go": "go install from source"}.get(name, name)
        human_echo(f"  {i}. {name}    [{blurb}]")
    human_echo(f"  {len(available) + 1}. skip    [leave trufflehog unconfigured]")
    human_echo("")

    default_idx = "1"
    raw = click.prompt("choose", default=default_idx, show_default=True)
    try:
        idx = int(raw)
    except ValueError:
        raise click.BadParameter(f"expected a number, got {raw!r}")
    if 1 <= idx <= len(available):
        return available[idx - 1]
    return None


@setup_group.command(
    "trufflehog",
    examples=[
        "opentraces setup trufflehog          # interactive install wizard",
        "opentraces setup trufflehog --enable # agent/CI: flip on",
        "opentraces setup trufflehog --disable",
    ],
    see_also=[
        ("opentraces doctor", "check Tier 1.5 health"),
    ],
)
@click.option("--enable", is_flag=True,
              help="Flip Tier 1.5 on; fails TRUFFLEHOG_MISSING if binary absent")
@click.option("--disable", is_flag=True,
              help="Flip Tier 1.5 off (binary stays installed)")
@click.option("--verify", is_flag=True, hidden=True,
              help="Legacy alias for --enable")
@click.option("--project", "scope_project", is_flag=True,
              help="Scope this change to the project's marker (default: global config).")
def setup_trufflehog_cmd(enable: bool, disable: bool, verify: bool, scope_project: bool = False) -> None:
    """Enable Tier 1.5 secret scanning via TruffleHog.

    Tier 1.5 runs the TruffleHog scanner on every staged and pushed trace.
    Findings are redacted in place, recorded in trace metadata, and force
    review before upload. Complements the always-on Tier 1a regex + 1b
    entropy scans.

    \b
    Flows:
        opentraces setup trufflehog             interactive wizard; if the
                                                binary is missing, offers to
                                                install it (brew/go).
        opentraces setup trufflehog --enable    agent/CI path: turn on only.
                                                Fails if binary not present.
        opentraces setup trufflehog --disable   turn off.
    """
    from ..security.trufflehog import find_trufflehog, install_binary

    cfg = load_config()

    if disable:
        cfg.security.trufflehog.enabled = False
        save_config(cfg)
        human_echo("TruffleHog tier disabled. Binary was not uninstalled.")
        human_hint("Re-enable with: opentraces setup trufflehog --enable")
        emit_json({"status": "ok", "action": "disable",
                   "trufflehog_enabled": False})
        return

    enable_only = enable or verify

    if enable_only:
        version = find_trufflehog()
        if version is None:
            human_echo(
                "trufflehog binary not found on PATH. "
                "Install it first, then re-run 'opentraces setup trufflehog --enable'."
            )
            emit_json(error_response(
                "TRUFFLEHOG_MISSING", "setup",
                "trufflehog binary not found",
                "Install trufflehog, then run --enable. "
                "Or run 'opentraces setup trufflehog' (no flags) for an interactive installer.",
            ))
            sys.exit(3)
        cfg.security.trufflehog.enabled = True
        save_config(cfg)
        _render_trufflehog_success(version, already_present=True)
        emit_json({"status": "ok", "action": "enable",
                   "trufflehog_version": version, "trufflehog_enabled": True})
        return

    # Interactive path.
    version = find_trufflehog()
    if version is not None:
        cfg.security.trufflehog.enabled = True
        save_config(cfg)
        _render_trufflehog_success(version, already_present=True)
        emit_json({"status": "ok", "action": "enable",
                   "trufflehog_version": version, "trufflehog_enabled": True,
                   "install_method": "already-installed"})
        return

    chosen = _pick_install_method_interactive()
    if chosen is None:
        human_echo("")
        human_echo("trufflehog left unconfigured.")
        emit_json({"status": "ok", "action": "declined",
                   "trufflehog_enabled": False})
        return

    ok, method = install_binary(method=chosen)
    if not ok:
        human_echo(
            f"\nCould not install trufflehog via {chosen}.\n"
            "Install it manually from https://github.com/trufflesecurity/trufflehog\n"
            "and re-run 'opentraces setup trufflehog --enable'."
        )
        emit_json(error_response(
            "TRUFFLEHOG_INSTALL_FAILED", "setup",
            f"install via {chosen} failed",
            "Install manually, then run --enable.",
        ))
        sys.exit(4)

    version = find_trufflehog()
    if version is None:
        human_echo(
            f"trufflehog installed via {method} but not yet on PATH. "
            "Re-run 'opentraces setup trufflehog --enable' once PATH is updated."
        )
        emit_json(error_response(
            "TRUFFLEHOG_PATH_MISS", "setup",
            f"installed via {method} but not on PATH",
            "Source your shell config or add GOPATH/bin, then --enable.",
        ))
        sys.exit(4)

    human_echo(f"Installed trufflehog via {method}: {version}")
    cfg.security.trufflehog.enabled = True
    save_config(cfg)
    _render_trufflehog_success(version, already_present=False, method=method)
    emit_json({"status": "ok", "action": "install",
               "trufflehog_version": version, "trufflehog_enabled": True,
               "install_method": method})


def _render_trufflehog_success(version: str, *, already_present: bool,
                               method: str | None = None) -> None:
    """Shared success banner with a clear what-this-means + disable hint."""
    from opentraces import cli as _cli

    human_echo("")
    print_banner(tagline=_cli._ok(f"trufflehog ready ({version})"))
    if already_present:
        human_echo(f"  {_cli._dim('(binary was already installed)')}")
    elif method:
        human_echo(f"  {_cli._dim(f'installed via {method}')}")
    human_echo("")
    human_echo(f"  {_cli._bold('From now on:')} every staged and pushed trace is scanned.")
    human_echo(f"  {_cli._dim('Findings are redacted in place and require review before push.')}")
    human_echo("")
    human_echo(f"  {_cli._dim('disable:')}        opentraces setup trufflehog --disable")
    human_echo(f"  {_cli._dim('re-enable:')}      opentraces setup trufflehog --enable")
    human_echo(f"  {_cli._dim('skip one push:')}  opentraces push --no-trufflehog")
    human_echo(f"  {_cli._dim('health check:')}   opentraces doctor")


# ---------------------------------------------------------------------------
# Review-LLM setup (opt-in Tier 2: third-party LLM review of staged traces)
# ---------------------------------------------------------------------------


# (name, base_url, api_key_env hint, suggested model, blurb)
_REVIEW_LLM_PRESETS: list[tuple[str, str, str, str, str]] = [
    ("ollama",     "http://localhost:11434/v1",       "",                "gemma3n:e4b",                   "local, no API key"),
    ("lm-studio",  "http://localhost:1234/v1",        "",                "",                              "local, no API key"),
    ("llama-cpp",  "http://localhost:8080/v1",        "",                "",                              "local, no API key (llama.cpp server)"),
    ("vllm",       "http://localhost:8000/v1",        "",                "",                              "local, no API key"),
    ("openai",     "https://api.openai.com/v1",       "OPENAI_API_KEY",  "gpt-4o-mini",                   "hosted"),
    ("groq",       "https://api.groq.com/openai/v1",  "GROQ_API_KEY",    "llama-3.3-70b-versatile",       "hosted"),
    ("openrouter", "https://openrouter.ai/api/v1",    "OPENROUTER_API_KEY", "anthropic/claude-3.5-haiku", "hosted"),
    ("together",   "https://api.together.xyz/v1",     "TOGETHER_API_KEY", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "hosted"),
    ("anthropic-direct", "",                          "ANTHROPIC_API_KEY", "claude-haiku-4-5-20251001",   "hosted (native SDK, not OpenAI-compat)"),
]

_LOCAL_PRESETS = {"ollama", "lm-studio", "llama-cpp", "vllm"}


def _probe_models(base_url: str, api_key_env: str,
                  timeout: float = 5.0) -> tuple[bool, list[str], str]:
    """Ping ``{base_url}/models`` and return (ok, names, message).

    Used both by the interactive flow (to list pickable models) and by
    ``--test``. Short default timeout so an unreachable endpoint doesn't
    stall the wizard.
    """
    from ..security.llm_provider import OpenAICompatProvider

    try:
        p = OpenAICompatProvider(
            model="_probe_only", base_url=base_url,
            api_key_env=api_key_env, timeout=timeout,
        )
        result = p.ping()
        names = result.get("models") or []
        return True, names, f"{len(names)} models at {base_url}"
    except Exception as exc:
        return False, [], str(exc)


def _maybe_ollama_pull(model: str) -> bool:
    """If ``ollama`` is on PATH, run ``ollama pull <model>`` interactively.

    Returns True on success, False on failure or if the binary is
    missing. Live-streams ollama's progress so the user sees it.
    """
    import subprocess

    if shutil.which("ollama") is None:
        return False
    try:
        human_echo(f"running: ollama pull {model}")
        result = subprocess.run(["ollama", "pull", model], check=False)
        return result.returncode == 0
    except Exception as exc:
        human_echo(f"ollama pull failed: {exc}")
        return False


def _pick_model_from_list(
    preset_name: str, base_url: str, api_key_env: str, suggested: str,
) -> str:
    """Probe the endpoint and let the user pick a model.

    - Local preset + reachable: numbered picker over installed models,
      plus a "custom" option.
    - Local preset (Ollama) + chosen model not installed: offer to pull.
    - Unreachable or non-local preset: free-form prompt with the
      suggested default.
    """
    from opentraces import cli as _cli

    is_local = preset_name in _LOCAL_PRESETS
    if is_local:
        ok, names, message = _probe_models(base_url, api_key_env)
        if ok and names:
            human_echo("")
            human_echo(f"{_cli._dim('models available:')}")
            for i, n in enumerate(names, 1):
                marker = _cli._ok(" ← suggested") if n == suggested else ""
                human_echo(f"  {i}. {n}{marker}")
            human_echo(f"  {len(names) + 1}. {_cli._bold('custom')} {_cli._dim('(type a tag; will pull if ollama)')}")
            human_echo("")
            default_idx = str(names.index(suggested) + 1) if suggested in names else "1"
            raw = click.prompt("choose a model", default=default_idx, show_default=True)
            try:
                idx = int(raw)
            except ValueError:
                # User typed a tag directly.
                return _maybe_pull_and_return(preset_name, raw)
            if 1 <= idx <= len(names):
                return names[idx - 1]
            if idx == len(names) + 1:
                tag = click.prompt("model tag", default=suggested or "", show_default=bool(suggested))
                return _maybe_pull_and_return(preset_name, tag)
            raise click.BadParameter(f"choice out of range: {idx}")

        # Unreachable local endpoint — warn clearly, fall through.
        if is_local:
            human_echo("")
            human_echo(_cli._err(f"couldn't reach {base_url}") + f": {message}")
            if preset_name == "ollama":
                human_echo(_cli._dim("is ollama running? try: ollama serve"))
            elif preset_name == "lm-studio":
                human_echo(_cli._dim("start LM Studio's local server from the app"))
            elif preset_name == "llama-cpp":
                human_echo(_cli._dim("start llama.cpp: llama-server -m <model.gguf> --port 8080"))
            elif preset_name == "vllm":
                human_echo(_cli._dim("start vllm: vllm serve <model>"))
            human_echo("")

    tag = click.prompt("model", default=suggested or "", show_default=bool(suggested))
    return _maybe_pull_and_return(preset_name, tag)


def _maybe_pull_and_return(preset_name: str, tag: str) -> str:
    """For Ollama, offer to pull the tag if it isn't installed yet."""
    from opentraces import cli as _cli

    if preset_name != "ollama" or not tag:
        return tag
    ok, names, _ = _probe_models("http://localhost:11434/v1", "")
    if ok and tag in names:
        return tag
    if not ok:
        return tag  # ollama isn't up; let save + test surface the error
    # Model not installed — offer pull.
    if not click.confirm(
        f"'{tag}' is not pulled. run 'ollama pull {tag}' now?",
        default=True,
    ):
        human_echo(_cli._dim("skipped; you can pull later with: ollama pull " + tag))
        return tag
    pulled = _maybe_ollama_pull(tag)
    if pulled:
        human_echo(_cli._ok(f"pulled {tag}"))
    else:
        human_echo(_cli._err(f"pull failed; saving '{tag}' anyway"))
    return tag


def _test_review_llm(api_format: str, base_url: str, model: str, api_key_env: str,
                     timeout: float) -> tuple[bool, str]:
    """Ping the configured LLM endpoint. Returns (ok, message)."""
    from ..security.llm_provider import (
        AnthropicProvider, OpenAICompatProvider,
    )
    try:
        if api_format == "anthropic":
            if not (api_key_env and os.environ.get(api_key_env)):
                return False, f"env var {api_key_env or 'ANTHROPIC_API_KEY'} is not set"
            # Just constructing + importing the SDK is the smoke test —
            # avoids spending a real Anthropic request on every doctor call.
            AnthropicProvider(model=model, api_key=os.environ.get(api_key_env))
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return False, "'anthropic' package not installed (pip install anthropic)"
            return True, f"anthropic SDK ready for {model}"

        p = OpenAICompatProvider(
            model=model, base_url=base_url, api_key_env=api_key_env, timeout=timeout,
        )
        result = p.ping()
        names = result.get("models") or []
        if names:
            present = model in names
            tail = f"{len(names)} models at {base_url}"
            if present:
                return True, f"{tail}; '{model}' is available"
            return True, f"{tail}; WARNING: '{model}' not in list"
        return True, f"reachable at {base_url}"
    except Exception as exc:
        return False, str(exc)


def _review_llm_config_from_cfg(cfg) -> dict:
    rc = cfg.security.llm_review
    return {
        "enabled": rc.enabled,
        "api_format": rc.api_format,
        "base_url": rc.base_url,
        "model": rc.model,
        "api_key_env": rc.api_key_env,
        "timeout": rc.timeout,
        "prompt_version": rc.prompt_version,
    }


def _setup_review_llm_interactive() -> tuple[str, str, str, str, float]:
    """Walk the user through preset selection. Returns config tuple."""
    from opentraces import cli as _cli

    human_echo("")
    print_banner(tagline="configure review LLM")
    human_echo("")
    human_echo("This is the third-party LLM used to independently review")
    human_echo("staged traces before you push. Runs locally or against a")
    human_echo("hosted API — this config is global, not per-project.")
    human_echo("")

    for i, (name, base_url, key_env, sample, blurb) in enumerate(_REVIEW_LLM_PRESETS, 1):
        tag = _cli._dim(f"[{blurb}]")
        human_echo(f"  {i}. {_cli._bold(name):<26} {tag}")
    human_echo(f"  {len(_REVIEW_LLM_PRESETS) + 1}. {_cli._bold('custom'):<26} {_cli._dim('[enter URL and model manually]')}")
    human_echo("")

    choice_str = click.prompt(
        "choose a preset",
        default="1",
        show_default=True,
    )
    try:
        choice = int(choice_str)
    except ValueError:
        raise click.BadParameter(f"expected a number, got {choice_str!r}")

    if 1 <= choice <= len(_REVIEW_LLM_PRESETS):
        name, base_url, api_key_env, sample, _blurb = _REVIEW_LLM_PRESETS[choice - 1]
        api_format = "anthropic" if name == "anthropic-direct" else "openai-compat"
    elif choice == len(_REVIEW_LLM_PRESETS) + 1:
        name = "custom"
        api_format = click.prompt(
            "api format", default="openai-compat", show_default=True,
            type=click.Choice(["openai-compat", "ollama", "anthropic", "fake"]),
        )
        base_url = click.prompt("base URL (empty for anthropic)", default="", show_default=False)
        api_key_env = click.prompt("API key env var name (empty for local)", default="", show_default=False)
        sample = ""
    else:
        raise click.BadParameter(f"choice out of range: {choice}")

    if api_format == "anthropic":
        model = click.prompt("model", default=sample or "claude-haiku-4-5-20251001",
                             show_default=True)
    else:
        model = _pick_model_from_list(name, base_url, api_key_env, sample)

    timeout_str = click.prompt("timeout seconds", default="120", show_default=True)
    try:
        timeout = float(timeout_str)
    except ValueError:
        timeout = 120.0

    return api_format, base_url, model, api_key_env, timeout


@setup_group.command("llm-review")
@click.option("--api-format", "api_format", default=None,
              type=click.Choice(["openai-compat", "ollama", "anthropic", "fake"], case_sensitive=False),
              help="Wire protocol the local client speaks: openai-compat "
                   "(default; vLLM/LM Studio/Ollama-via-/v1/OpenAI proper), "
                   "ollama (native /api/generate), anthropic, fake.")
@click.option("--base-url", default=None,
              help="Base URL including /v1 for openai-compat servers. "
                   "Ignored for anthropic.")
@click.option("--model", default=None, help="Model name/tag.")
@click.option("--api-key-env", default=None,
              help="Env var holding the API key. Empty for local servers.")
@click.option("--timeout", default=None, type=float, help="Request timeout (s).")
@click.option("--disable", is_flag=True, help="Turn llm-review off without changing other fields.")
@click.option("--enable", is_flag=True, help="Turn llm-review on using current config.")
@click.option("--test", "test_only", is_flag=True,
              help="Ping the endpoint; do not write config.")
@click.option("--print", "print_only", is_flag=True,
              help="Print effective config as JSON and exit.")
@click.option("--no-interactive", is_flag=True,
              help="Skip the preset picker even if no flags are given.")
@click.option("--project", "scope_project", is_flag=True,
              help="Scope this change to the project's marker (default: global config).")
def setup_review_llm_cmd(
    api_format: str | None, base_url: str | None, model: str | None,
    api_key_env: str | None, timeout: float | None,
    disable: bool, enable: bool, test_only: bool, print_only: bool,
    no_interactive: bool,
    scope_project: bool = False,
) -> None:
    """Configure the Tier 2 LLM reviewer for staged traces.

    Points opentraces at an OpenAI-compatible, Ollama, Anthropic, or
    fake backend that reads each staged trace and flags residual
    sensitive content the regex/entropy/TruffleHog tiers could miss
    (semantic PII, proprietary context, policy concerns). Used by
    `opentraces llm-review` and `opentraces push --llm-review`.

    Stored globally in ~/.opentraces/config.json under
    security.llm_review. One config per machine, projects inherit it.

    Interactive picker when run with no flags. Non-interactive for agents:

    \b
        opentraces setup llm-review --api-format openai-compat \\
            --base-url http://localhost:11434/v1 --model gemma3n:e4b
        opentraces setup llm-review --api-format openai-compat \\
            --base-url https://api.groq.com/openai/v1 \\
            --model llama-3.3-70b-versatile --api-key-env GROQ_API_KEY
        opentraces setup llm-review --api-format anthropic \\
            --model claude-haiku-4-5-20251001 --api-key-env ANTHROPIC_API_KEY
    """
    cfg = load_config()
    rc = cfg.security.llm_review

    if print_only:
        emit_json({"status": "ok", "llm_review": _review_llm_config_from_cfg(cfg)})
        return

    if disable:
        rc.enabled = False
        save_config(cfg)
        human_echo("llm-review disabled.")
        emit_json({"status": "ok", "action": "disable",
                   "llm_review": _review_llm_config_from_cfg(cfg)})
        return

    # Agent / non-interactive path: any flag provided => skip the wizard.
    any_flag = any(v is not None for v in (api_format, base_url, model, api_key_env, timeout))

    if not any_flag and not enable and not test_only and not no_interactive:
        api_format, base_url, model, api_key_env, timeout = _setup_review_llm_interactive()

    # Layer flag overrides on top of current config.
    eff_api_format = api_format or rc.api_format
    eff_base_url = base_url if base_url is not None else rc.base_url
    eff_model = model or rc.model
    eff_api_key_env = api_key_env if api_key_env is not None else rc.api_key_env
    eff_timeout = timeout if timeout is not None else rc.timeout

    if test_only:
        ok, message = _test_review_llm(
            eff_api_format, eff_base_url, eff_model, eff_api_key_env, eff_timeout,
        )
        human_echo(f"llm-review test: {'ok' if ok else 'failed'} — {message}")
        emit_json({
            "status": "ok" if ok else "error",
            "action": "test",
            "llm_review": {
                "api_format": eff_api_format, "base_url": eff_base_url,
                "model": eff_model, "api_key_env": eff_api_key_env,
            },
            "reachable": ok, "message": message,
        })
        if not ok:
            sys.exit(3)
        return

    rc.api_format = eff_api_format
    rc.base_url = eff_base_url
    rc.model = eff_model
    rc.api_key_env = eff_api_key_env
    rc.timeout = eff_timeout
    rc.enabled = True
    save_config(cfg)

    ok, message = _test_review_llm(
        rc.api_format, rc.base_url, rc.model, rc.api_key_env, rc.timeout,
    )
    human_echo("")
    from opentraces import cli as _cli
    tag = _cli._ok("llm-review configured") if ok else _cli._err("llm-review saved but unreachable")
    print_banner(tagline=tag)
    human_echo(f"  {_cli._dim('api format:')} {rc.api_format}")
    if rc.api_format != "anthropic":
        human_echo(f"  {_cli._dim('base url:  ')} {rc.base_url}")
    human_echo(f"  {_cli._dim('model:     ')} {rc.model}")
    if rc.api_key_env:
        present = "set" if os.environ.get(rc.api_key_env) else _cli._err("NOT SET")
        human_echo(f"  {_cli._dim('api key:   ')} ${rc.api_key_env} ({present})")
    human_echo(f"  {_cli._dim('reachable: ')} {message}")
    human_echo("")
    human_echo(f"  {_cli._bold('To run:')} opentraces llm-review  "
               f"{_cli._dim('(staged traces; out-of-band, not automatic)')}")
    human_echo(f"  {_cli._dim('gate push:')}     opentraces push --llm-review")
    human_echo(f"  {_cli._dim('disable:')}       opentraces setup llm-review --disable")
    human_echo(f"  {_cli._dim('health check:')}  opentraces doctor")

    emit_json({
        "status": "ok", "action": "install",
        "llm_review": _review_llm_config_from_cfg(cfg),
        "reachable": ok, "message": message,
    })


@setup_group.command("review-policy")
@click.option("--auto", "set_auto", is_flag=True,
              help="Skip human review for safe traces (auto-approve).")
@click.option("--review", "set_review", is_flag=True,
              help="Require human review on every trace.")
@click.option("--print", "print_only", is_flag=True,
              help="Print the current project review policy and exit.")
@click.option("--project", "scope_project", is_flag=True, default=True,
              help="Always scoped to project (this command's default; flag accepted for surface consistency).")
def setup_review_policy_cmd(
    set_auto: bool, set_review: bool, print_only: bool, scope_project: bool = True,
) -> None:
    """Set this project's review policy.

    Flips the ``review_policy`` field in ``.opentraces/config.json``:

    \b
        opentraces setup review-policy --review   require manual approval
        opentraces setup review-policy --auto     auto-approve safe traces into staged
        opentraces setup review-policy --print    show current value

    Equivalent to re-running ``opentraces init --review-policy ...`` but
    without touching the rest of the project config. Must be run from a
    directory that has already been initialized.
    """
    from ..core.config import (
        ProjectConfig,
        load_project_config,
        project_is_opted_in,
        save_project_config,
    )

    cwd = Path.cwd()
    if not project_is_opted_in(cwd):
        human_hint(
            "no .opentraces.json marker here — run 'opentraces init' first"
        )
        emit_json({"status": "error", "error": "not-initialized"})
        sys.exit(2)

    raw = load_project_config(cwd)
    current = raw.get("review_policy", "review")

    if print_only or (not set_auto and not set_review):
        human_echo(f"review policy: {_cli._bold(current)}")
        emit_json({"status": "ok", "review_policy": current})
        return

    if set_auto and set_review:
        human_hint("pass only one of --auto / --review")
        emit_json({"status": "error", "error": "conflicting-flags"})
        sys.exit(2)

    new_value = "auto" if set_auto else "review"
    if new_value == current:
        human_echo(f"review policy already {_cli._bold(current)}")
        emit_json({"status": "ok", "action": "noop", "review_policy": current})
        return

    raw["review_policy"] = new_value
    # Validate before writing so we never persist a bad config.
    ProjectConfig.model_validate(raw)
    save_project_config(cwd, raw)

    human_echo(
        f"review policy: {_cli._dim(current)} → {_cli._bold(new_value)}"
    )
    if new_value == "auto":
        human_echo(f"  {_cli._dim('safe traces will auto-approve into staged; push remains explicit')}")
    else:
        human_echo(f"  {_cli._dim('every trace needs manual approval before push')}")
    emit_json({
        "status": "ok", "action": "update",
        "review_policy": new_value, "previous": current,
    })


@main.command(
    "doctor",
    examples=[
        "opentraces doctor",
        "opentraces doctor --security",
    ],
    see_also=[
        ("opentraces setup", "install or configure a missing integration."),
        ("opentraces status", "project-level snapshot instead of pipeline."),
    ],
)
@click.option(
    "--security", "security_only", is_flag=True,
    help="Show only the security pipeline subview (versions + tiers).",
)
def doctor_cmd(security_only: bool) -> None:
    """Report security pipeline and integration health.

    Probes every configured integration (hooks, scanners, LLM review,
    post-processors) and reports versions, tier status, and any
    actionable failures. Exits non-zero if a required tier is broken.
    """
    from ..core import doctor

    cfg = load_config()
    report = doctor.report(cfg, Path.cwd())

    if security_only:
        _render_doctor_security(report)
        # Exit-code signal still needs the full tier data, but trim the
        # JSON payload so piping consumers don't get stuff they asked to
        # hide.
        trimmed = {
            "security_version": report["security_version"],
            "schema_version": report.get("schema_version"),
            "security": report["security"],
        }
        emit_json({"status": "ok", "doctor": trimmed})
    else:
        _render_doctor_human(report)
        emit_json({"status": "ok", "doctor": report})

    code = doctor.exit_code(report)
    if code:
        sys.exit(code)


# Marker glyphs. click.echo strips ANSI on non-TTY and respects NO_COLOR, so
# raw unicode + click.style is safe for both humans and pipes.
_MARK_OK = click.style("✓", fg="green", bold=True)
_MARK_WARN = click.style("⚠", fg="yellow", bold=True)
_MARK_ERR = click.style("✗", fg="red", bold=True)
_MARK_OFF = click.style("·", dim=True)


def _mark_for(kind: str) -> str:
    return {
        "ok": _MARK_OK,
        "warn": _MARK_WARN,
        "err": _MARK_ERR,
        "off": _MARK_OFF,
    }.get(kind, _MARK_OFF)


def _section(title: str) -> None:
    human_echo("")
    human_echo(_cli._bold(title))


def _row(mark_kind: str, label: str, value: str, *, detail: str | None = None) -> None:
    """Fixed-width label column so colons line up across sections."""
    mark = _mark_for(mark_kind)
    padded = f"{label:<16}"
    line = f"  {mark} {padded} {value}"
    if detail:
        line += f"  {_cli._dim(detail)}"
    human_echo(line)


_TIER_STATE_MARK = {
    "always-on": "ok",
    "enabled": "ok",
    "on-demand": "ok",
    "required": "ok",
    "disabled": "off",
    "not-required": "off",
    "not-initialized": "off",
    "missing": "err",
    "unreachable": "err",
}

_TIER_STATE_LABEL = {
    "always-on": "always on",
    "enabled": "enabled",
    "on-demand": "on demand",
    "required": "required",
    "disabled": "disabled",
    "not-required": "not required",
    "not-initialized": "no project",
    "missing": "missing binary",
    "unreachable": "unreachable",
}


def _tier_row(tier: dict) -> None:
    """Render one tier: name, state label, detail, and a toggle-hint line."""
    state = tier.get("state", "")
    mark = _mark_for(_TIER_STATE_MARK.get(state, "off"))
    name = tier.get("name", "?")
    value = _TIER_STATE_LABEL.get(state, state)
    detail = tier.get("detail")
    # Fixed name column so state values line up.
    head = f"{name:<22}"
    line = f"  {mark} {head} {value}"
    if detail:
        line += f"  {_cli._dim(detail)}"
    human_echo(line)

    # Setup info lines (e.g., LLM endpoint/env) render above the hints,
    # at the same indent, so `doctor` exposes the full configuration.
    pad = " " * (4 + 22 + 1)  # align under the state column
    for info in _tier_info_lines(tier):
        if info:
            human_echo(f"{pad}{_cli._dim(info)}")

    # Actionable hints, only where applicable.
    hints = _tier_toggle_hint(state, tier)
    if hints:
        if isinstance(hints, str):
            hints = [hints]
        for h in hints:
            if h:
                human_echo(f"{pad}{_cli._dim(h)}")


def _tier_info_lines(tier: dict) -> list[str]:
    """Return read-only setup detail lines for this tier.

    Currently only the LLM trace review tier exposes configuration worth
    surfacing (endpoint, api-key env var, probe result), so other tiers
    return an empty list.
    """
    if tier.get("name") != "LLM trace review":
        return []
    state = tier.get("state")
    # Only show setup details when the user has opted in; the disabled
    # row already carries an "enable:" hint and nothing else is configured.
    if state in ("disabled", None):
        return []
    lines: list[str] = []
    base_url = tier.get("base_url")
    api_format = tier.get("api_format")
    if base_url:
        parts = [f"endpoint: {base_url}"]
        if api_format:
            parts.append(f"api: {api_format}")
        lines.append(" — ".join(parts))
    api_key_env = tier.get("api_key_env")
    if api_key_env:
        import os as _os
        present = bool(_os.environ.get(api_key_env))
        lines.append(
            f"api key env: ${api_key_env} ({'set' if present else 'unset'})"
        )
    probe = tier.get("probe_status")
    # `probe_status` already folds model/backend into the main row for the
    # on-demand case; only surface it when it carries extra signal (model
    # count, unreachable reason, etc.).
    if probe and any(tok in probe for tok in ("models available", "UNREACHABLE", "not found", "not installed", "not set")):
        lines.append(f"probe: {probe}")
    return lines


def _tier_toggle_hint(state: str, tier: dict) -> str | None:
    """Return the command the user can run to flip this tier's state."""
    enable = tier.get("enable_cmd")
    disable = tier.get("disable_cmd")
    # Always-on tiers have no toggle — skip the hint line entirely to
    # keep the report compact.
    if state == "always-on":
        return None
    # Human review is policy-driven, not an on/off toggle.
    if "review_policy" in tier:
        other = "auto" if tier.get("review_policy") == "review" else "review"
        cmd = disable if other == "auto" else enable
        return f"switch to {other}: {cmd}" if cmd else None
    if state in ("disabled", "not-initialized"):
        return f"enable: {enable}" if enable else None
    if state == "enabled":
        return f"disable: {disable}" if disable else None
    if state == "on-demand":
        return [
            "run: opentraces llm-review",
            "gate push: opentraces push --llm-review",
            f"reconfigure: {enable}" if enable else "",
        ]
    if state == "missing":
        return f"fix: {enable} --enable"
    if state == "unreachable":
        return f"reconfigure: {enable}"
    return None


def _security_section(sec: dict) -> None:
    _section("Security pipeline")
    for tier in sec.get("tiers", []):
        _tier_row(tier)
    sensitivity = sec.get("classifier_sensitivity")
    if sensitivity:
        human_echo("")
        human_echo(f"  {_cli._dim(f'classifier sensitivity: {sensitivity}')}")


def _processors_section(specs: list[dict]) -> None:
    _section("Post-processors")
    if not specs:
        human_echo(f"  {_cli._dim('(none configured)')}")
        return
    for p in specs:
        status = p.get("status")
        kind = "ok" if status == "detected" else "err"
        detail = p.get("resolved_path") or p.get("command")
        _row(kind, p["name"], status or "?", detail=detail)


def _entity_parser_section(info: dict) -> None:
    """Render the entity-parser panel under `opentraces doctor`."""
    _section("Entity parser")
    if not info:
        _row("off", "ot-entities", "not installed",
             detail="run 'opentraces setup entity-parser'")
        return
    if info.get("installed"):
        version = info.get("version") or "installed"
        _row("ok", "ot-entities", version, detail=info.get("binary_path"))
    else:
        _row(
            "off", "ot-entities", "not installed",
            detail=info.get("advice") or "run 'opentraces setup entity-parser'",
        )
    if info.get("platform"):
        _row("ok", "  ↳ platform", info["platform"])


def _hooks_section(hooks: list[dict]) -> None:
    _section("Agent integrations")
    if not hooks:
        human_echo(f"  {_cli._dim('(no installers registered)')}")
        return
    for h in hooks:
        name = h.get("installer", "?")
        if name == "skill":
            _skill_row(h)
        elif name == "claude-code":
            _claude_code_row(h)
        elif name == "git":
            _git_row(h)
        else:
            kind = "ok" if h.get("installed") else "off"
            _row(kind, name, "installed" if h.get("installed") else "not installed")


def _post_commit_hook_section(info: dict) -> None:
    """Render post-commit hook runtime status, including Trail anchors."""
    _section("Post-commit hook")
    state = info.get("state") or "missing"
    kind = {
        "ok": "ok",
        "installed_never_ran": "warn",
        "installed_not_chained": "warn",
        "missing": "off",
    }.get(state, "warn")
    _row(kind, "status", state, detail=info.get("log_path"))
    if not info.get("installed"):
        return
    _row(
        "ok" if info.get("chained_in_post_commit") else "warn",
        "chained",
        "yes" if info.get("chained_in_post_commit") else "no",
    )
    last = info.get("last_run") or {}
    if not last:
        return
    sha = last.get("sha")
    if sha:
        _row("ok", "last commit", str(sha)[:12])
    _row("ok", "candidates", str(last.get("candidates") or 0))
    if last.get("notes_written"):
        _row("ok", "notes", "written")
    else:
        _row("off", "notes", "not written", detail=last.get("reason"))
    anchor_error = info.get("last_trail_anchor_error")
    if anchor_error:
        _row("err", "trail anchors", "error", detail=str(anchor_error))
    else:
        count = info.get("last_trail_anchors_created")
        _row("ok", "trail anchors", str(count if count is not None else 0))


def _skill_row(h: dict) -> None:
    installed = h.get("installed")
    iv = h.get("installed_version")
    pv = h.get("package_version")
    broken = h.get("broken_harnesses") or []
    canonical_path = h.get("canonical")
    if not installed:
        _row("off", "skill", "not installed", detail="run 'opentraces setup skill'")
    else:
        kind = "warn" if h.get("drift") or broken else "ok"
        value = iv or "installed"
        # Always surface the canonical path so users see the one global
        # copy their harness symlinks point at.
        detail = canonical_path
        if h.get("drift"):
            detail = f"drift: package is {pv}, run 'opentraces setup skill'"
        _row(kind, "skill", value, detail=detail)
    # Per-harness detail — shows the symlink location in the agent's
    # own skills namespace and what it resolves to, so the global-vs-
    # per-agent split is explicit.
    harnesses = h.get("harnesses") or {}
    for hname, st in harnesses.items():
        symlink_path = st.get("symlink_path") or ""
        if not st.get("present"):
            sub_kind, sub_val = "off", "not linked"
            sub_detail: str | None = symlink_path or None
        elif st.get("canonical"):
            sub_kind, sub_val = "ok", "linked"
            target = st.get("target") or ""
            sub_detail = f"{symlink_path} → {target}" if symlink_path else target
        else:
            sub_kind, sub_val = "warn", "non-canonical dir"
            sub_detail = f"{symlink_path} ({st.get('kind')})" if symlink_path else st.get("kind")
        _row(sub_kind, f"  ↳ {hname}", sub_val, detail=sub_detail)


def _claude_code_row(h: dict) -> None:
    if not h.get("installed"):
        _row("off", "claude-code", "not installed", detail="run 'opentraces setup claude-code'")
        return
    _row("ok", "claude-code", "installed")


def _git_row(h: dict) -> None:
    if not h.get("installed"):
        reason = h.get("reason") or "not installed"
        _row("off", "git", reason, detail="run 'opentraces setup git'")
        return
    _row("ok", "git", "post-commit hook active")


def _opted_in_section(info: dict) -> None:
    _section("Opted-in projects")
    count = info.get("count", 0)
    paths = info.get("paths") or []
    if not count:
        human_echo(f"  {_cli._dim('(none — run opentraces init in a project to opt in)')}")
        return
    human_echo(f"  {_cli._dim(f'{count} project(s) registered — list with: opentraces list --projects')}")
    # Show at most 3 to keep doctor compact; full list via the command.
    for p in paths[:3]:
        human_echo(f"    {_cli._dim(p)}")
    if len(paths) > 3:
        human_echo(f"    {_cli._dim(f'... and {len(paths) - 3} more')}")


def _versions_section(report: dict) -> None:
    _section("Versions")
    _row("ok", "security", report["security_version"])
    if report.get("schema_version"):
        _row("ok", "schema", report["schema_version"])


def _render_doctor_human(report: dict) -> None:
    _cli.print_banner(tagline="doctor")

    _versions_section(report)
    _security_section(report["security"])
    _opted_in_section(report.get("opted_in_projects") or {})

    _section("Authentication")
    hf = report.get("hf_auth")
    if hf == "ok":
        _row("ok", "huggingface", "authenticated")
    else:
        _row("err", "huggingface", "missing", detail="run 'hf auth login'")

    _entity_parser_section(report.get("entity_parser") or {})
    _attribution_section(report.get("attribution") or {})
    _watcher_section(report.get("watcher") or {})
    _hooks_section(report["hooks"])
    _post_commit_hook_section(report.get("post_commit_hook") or {})
    _trail_event_log_section(report.get("trail_event_log") or {})
    human_echo("")


def _attribution_section(info: dict) -> None:
    """Render attribution cache panel (plan 043 phase 7)."""
    _section("Attribution cache")
    health = info.get("health")
    if health == "no-project":
        _row("off", "status", "no project",
             detail="run 'opentraces init' in a project to enable attribution")
        return
    _row(
        {"ok": "ok", "empty": "warn", "stale": "warn"}.get(health, "warn"),
        "status",
        health or "?",
        detail=info.get("attribution_cache_dir"),
    )
    _row("ok", "cached commits", str(info.get("cached_commits") or 0))
    last = info.get("last_backfilled_commit")
    if last:
        _row("ok", "last backfill", last[:12],
             detail=info.get("last_backfill_at") or None)
    else:
        _row("off", "last backfill", "never",
             detail="run 'opentraces backfill --project .'")
    decision = info.get("first_run_backfill_decision")
    if decision:
        _row("ok", "first-run", str(decision))


def _watcher_section(info: dict) -> None:
    """Render watcher panel (plan 043 phase 7)."""
    _section("Watcher")
    health = info.get("health")
    plat = info.get("platform") or "?"
    if health == "unsupported-platform":
        _row("off", "platform", plat, detail="watcher unavailable on this platform")
        return
    _row("ok", "platform", plat)
    if not info.get("installed"):
        _row("off", "installed", "no",
             detail="run 'opentraces setup watcher'")
        return
    _row("ok", "installed", "yes", detail=info.get("unit_path"))
    running_kind = "ok" if info.get("running") else "warn"
    _row(running_kind, "running", "yes" if info.get("running") else "no")
    interval = info.get("interval_seconds")
    if interval:
        _row("ok", "interval", f"{interval}s")
    last = info.get("last_run_at")
    if last:
        _row("ok", "last tick", last)


def _trail_event_log_section(info: dict) -> None:
    """Render Trace Trails event-log integrity."""
    _section("Trace Trails")
    state = info.get("state") or "missing"
    ref = info.get("ref") or "refs/opentraces/local/events/v1"

    if state == "missing":
        _row("off", "event log", "missing", detail=ref)
        return

    kind = "ok" if state == "ok" else "err"
    detail = ref
    head = info.get("head")
    if head:
        detail = f"{ref} @ {head[:12]}"
    _row(kind, "event log", state, detail=detail)
    parents_ok = bool(info.get("batch_parents_linear"))
    hashes_ok = bool(info.get("content_hashes_valid"))
    chain_ok = bool(info.get("event_chain_valid"))
    _row("ok" if parents_ok else "err", "batch parents", "linear" if parents_ok else "invalid")
    _row("ok" if hashes_ok else "err", "content hashes", "valid" if hashes_ok else "invalid")
    _row("ok" if chain_ok else "err", "event chain", "valid" if chain_ok else "invalid")
    _row("ok", "batches", str(info.get("batch_count") or 0))
    _row("ok", "events", str(info.get("event_count") or 0))

    for error in (info.get("errors") or [])[:3]:
        _row("err", "  ↳ error", str(error))


def _render_doctor_security(report: dict) -> None:
    """Focused subview: versions + security pipeline only."""
    human_echo(_cli._bold("opentraces doctor — security"))
    _versions_section(report)
    _security_section(report["security"])
    human_echo("")


def _filter_by_scope(records: list[dict], scope: str, state) -> list[dict]:
    """Filter records by visible stage from the StateManager.

    ``scope`` values (display vocabulary):
      - ``all``: every record in the staging directory (default)
      - ``inbox``: pre-add traces still awaiting review
      - ``staged``: post-add traces ready to push (second line of defence
        before push, after human review)
    """
    from ..core.state import TraceStatus

    if scope == "all":
        return records
    target = {
        "inbox": TraceStatus.STAGED.value,
        "staged": TraceStatus.COMMITTED.value,
    }.get(scope)
    if target is None:
        return records
    out: list[dict] = []
    for rec in records:
        entry = state.get_trace(rec.get("trace_id", ""))
        status = None
        if entry is not None:
            status = (
                entry.get("status") if isinstance(entry, dict)
                else getattr(entry, "status", None)
            )
        if status == target:
            out.append(rec)
    return out


def _filter_by_trace_ids(records: list[dict],
                         trace_ids: tuple[str, ...]) -> list[dict]:
    """Select records matching any of ``trace_ids`` (full id or short prefix).

    Prefix matching mirrors ``resume`` / ``blame`` so users can pass
    ``--trace 8a3f1c`` without the full sha.
    """
    if not trace_ids:
        return records
    wanted = [t.strip() for t in trace_ids if t.strip()]
    out: list[dict] = []
    matched: set[str] = set()
    for rec in records:
        tid = rec.get("trace_id", "") or ""
        for prefix in wanted:
            if tid == prefix or tid.startswith(prefix):
                out.append(rec)
                matched.add(prefix)
                break
    unmatched = [p for p in wanted if p not in matched]
    if unmatched:
        human_hint(f"no matching trace for: {', '.join(unmatched)}")
    return out


def _persist_llm_verdicts(staging_dir: Path, outcome, state) -> None:
    """Write each verdict back into its trace's ``metadata.llm_review``
    so later gates (``push --llm-review``) and the TUI can see them.

    Verdicts that flag the trace (``shareable=no`` or
    ``missed_sensitive_data=yes``) also promote the trace to the
    BLOCKED state — the push flow skips BLOCKED traces entirely and
    the user sees the flag surfaced in the TUI rather than the trace
    silently failing the gate on every push attempt.
    """
    import json as _json

    for result in outcome.results:
        tid = result.get("trace_id")
        verdict = result.get("verdict") or {}
        if not tid or not verdict:
            continue
        jsonl = staging_dir / f"{tid}.jsonl"
        if not jsonl.exists():
            # Fallback: scan the dir for a file whose first-line
            # ``trace_id`` matches. Covers any non-canonical layout.
            for f in staging_dir.glob("*.jsonl"):
                try:
                    head = f.read_text().strip().splitlines()
                    if head and _json.loads(head[0]).get("trace_id") == tid:
                        jsonl = f
                        break
                except Exception:
                    continue
            else:
                continue
        try:
            raw = jsonl.read_text().strip().splitlines()
            if not raw:
                continue
            rec = _json.loads(raw[0])
            meta = rec.setdefault("metadata", {})
            # Merge so any unrelated metadata keys survive untouched.
            meta["llm_review"] = verdict
            jsonl.write_text(_json.dumps(rec) + "\n")
        except Exception as exc:
            human_hint(f"could not persist verdict for {tid}: {exc}")
            continue

        if verdict.get("shareable") == "no" or \
                verdict.get("missed_sensitive_data") == "yes":
            reason = verdict.get("summary") or "flagged by LLM review"
            try:
                state.block_trace(tid, f"llm-review: {reason}")
            except Exception as exc:
                human_hint(f"could not mark {tid} blocked: {exc}")


@main.command(
    "llm-review",
    examples=[
        "opentraces llm-review                      # every trace in staging",
        "opentraces llm-review --scope staged       # 2nd line of defence before push",
        "opentraces llm-review --scope inbox        # pre-add only",
        "opentraces llm-review --trace 8a3f1c       # one trace (short id ok)",
        "opentraces llm-review --dry-run            # estimate token usage only",
    ],
    see_also=[
        ("opentraces setup llm-review", "configure the LLM"),
        ("opentraces push --llm-review", "gate uploads on a clean verdict"),
    ],
    option_groups=[
        ("API overrides", ["api_format", "model", "base_url", "api_key_env"]),
        ("Selection", ["scope", "trace_ids", "limit"]),
        ("Run", ["dry_run", "force", "context_file"]),
    ],
)
@click.option("--api-format", "api_format", default=None,
              type=click.Choice(["openai-compat", "ollama", "anthropic", "fake"], case_sensitive=False),
              help="Override the wire-protocol family (openai-compat, ollama, anthropic, fake)")
@click.option("--model", default=None, help="Override model")
@click.option("--base-url", default=None,
              help="Override base URL for openai-compat servers")
@click.option("--api-key-env", default=None,
              help="Override the env var holding the API key")
@click.option("--scope",
              type=click.Choice(["all", "inbox", "staged"], case_sensitive=False),
              default="all",
              help="Which traces to review: 'all' (every trace in staging; default), "
                   "'inbox' (Inbox-stage only, pre-add), "
                   "'staged' (Staged-stage only, second line of defence before push).")
@click.option("--trace", "trace_ids", multiple=True,
              help="Target specific trace(s) by id (full or short prefix). "
                   "Repeatable. Overrides --scope when set.")
@click.option("--dry-run", is_flag=True,
              help="Estimate token usage without calling the provider")
@click.option("--limit", type=int, default=0,
              help="Cap the batch at N traces (0 = no cap). Applied after --scope / --trace filtering.")
@click.option("--force", is_flag=True,
              help="Re-review traces that already have a cached verdict")
@click.option("--context-file", "context_file", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Project README/AGENTS.md passed as context")
def review_llm_cmd(api_format: str | None, model: str | None, base_url: str | None,
                   api_key_env: str | None, scope: str,
                   trace_ids: tuple[str, ...], dry_run: bool, limit: int,
                   force: bool, context_file: str | None) -> None:
    """Run Tier 2 LLM semantic review.

    Uses the LLM configured by 'opentraces setup llm-review' unless you
    override via --api-format / --model / --base-url / --api-key-env.

    LLM can be slow if using local models. Narrow with --scope (pick
    inbox or staged only) or --trace (one or more specific trace ids),
    and cap with --limit. The typical "second line of defence" flow is
    'llm-review --scope staged' right before 'push --llm-review'.
    """
    from ..core.config import get_project_traces_dir, get_project_state_path
    from ..core.inbox import load_traces
    from ..core.review import estimate_llm_review, run_llm_review
    from ..core.state import StateManager

    cfg = load_config()
    rc = cfg.security.llm_review
    eff_api_format = api_format or rc.api_format
    eff_model = model or rc.model
    eff_base_url = base_url if base_url is not None else rc.base_url
    eff_api_key_env = api_key_env if api_key_env is not None else rc.api_key_env
    eff_timeout = rc.timeout

    if not rc.enabled and api_format is None and model is None:
        human_hint(
            "llm-review is not configured. Run 'opentraces setup llm-review' "
            "once, or pass --api-format/--model explicitly."
        )

    staging = get_project_traces_dir(Path.cwd())
    if not staging.exists():
        human_echo("No staging directory found. Run opentraces init first.")
        emit_json(error_response(
            "NO_STAGING", "review", "staging dir missing",
            "Run 'opentraces init'.",
        ))
        sys.exit(2)

    records: list[dict] = list(load_traces(staging))
    total_available = len(records)

    if trace_ids:
        records = _filter_by_trace_ids(records, trace_ids)
        filter_desc = f"--trace {','.join(trace_ids)}"
    else:
        state_mgr = StateManager(get_project_state_path(Path.cwd()))
        records = _filter_by_scope(records, scope, state_mgr)
        filter_desc = f"--scope {scope}"

    if limit > 0:
        records = records[:limit]

    if not records:
        human_echo(
            f"No traces match {filter_desc}"
            + (f" (limit {limit})" if limit else "")
            + f" — {total_available} trace(s) in staging."
        )
        payload: dict = {
            "status": "ok", "action": "llm-review",
            "scope": scope, "trace_ids": list(trace_ids),
            "matched": 0, "total_available": total_available,
        }
        if dry_run:
            payload.update({
                "dry_run": True, "sessions": 0, "chars": 0,
                "estimate": {"tokens": 0, "cost_usd": 0.0},
                "model": eff_model, "api_format": eff_api_format,
                "base_url": eff_base_url,
            })
        else:
            payload["results"] = []
        emit_json(payload)
        return

    from opentraces import cli as _cli
    human_echo(
        f"{_cli._dim(filter_desc + ':')} "
        f"{len(records)}/{total_available} trace(s) selected"
        + (f" (limit {limit})" if limit else "")
    )

    context = ""
    if context_file:
        try:
            context = Path(context_file).read_text()[:10_000]
        except OSError as exc:
            human_echo(f"Could not read context file: {exc}")
            sys.exit(2)

    if dry_run:
        est = estimate_llm_review(records, api_format=eff_api_format, model=eff_model)
        human_echo(
            f"Dry run: {est.sessions} sessions, ~{est.chars:,} chars, "
            f"~{est.tokens:,} tokens, ~${est.cost_usd:.4f}."
        )
        emit_json({
            "status": "ok",
            "action": "llm-review",
            "dry_run": True,
            "scope": scope,
            "trace_ids": list(trace_ids),
            "matched": len(records),
            "total_available": total_available,
            "sessions": est.sessions,
            "chars": est.chars,
            "estimate": {"tokens": est.tokens, "cost_usd": est.cost_usd},
            "model": eff_model,
            "api_format": eff_api_format,
            "base_url": eff_base_url,
        })
        return

    n = len(records)
    _counter = {"i": 0}

    def _progress(trace_id: str, status: str) -> None:
        _counter["i"] += 1
        human_echo(f"[{_counter['i']}/{n}] {trace_id}: {status}")

    outcome = run_llm_review(
        records,
        api_format=eff_api_format,
        model=eff_model,
        base_url=eff_base_url,
        api_key_env=eff_api_key_env,
        timeout=eff_timeout,
        prompt_version=rc.prompt_version,
        context=context,
        force=force,
        on_progress=_progress,
    )
    # Persist verdicts so downstream gates (``push --llm-review``) and
    # the TUI can see them. Without this the verdict only lives in the
    # JSON payload we emit below and is lost as soon as the command
    # exits. Bad verdicts also mark the trace BLOCKED in state so the
    # push flow skips them and the user sees them flagged.
    state_for_block = StateManager(get_project_state_path(Path.cwd()))
    _persist_llm_verdicts(staging, outcome, state_for_block)
    emit_json({
        "status": "ok",
        "action": "llm-review",
        "dry_run": False,
        "scope": scope,
        "trace_ids": list(trace_ids),
        "matched": len(records),
        "total_available": total_available,
        "api_format": eff_api_format,
        "model": eff_model,
        "base_url": eff_base_url,
        "results": outcome.results,
    })



# ---------------------------------------------------------------------------
# Step 12 — ot setup upgrade (absorbs the flat ot upgrade).
# Both surfaces survive during the transition; Step 15 drops the flat one.
# ---------------------------------------------------------------------------

@setup_group.command("upgrade")
@click.option(
    "--skill-only",
    is_flag=True,
    default=False,
    help="Only update the skill file and hook, skip CLI upgrade",
)
@click.pass_context
def setup_upgrade(ctx: click.Context, skill_only: bool) -> None:
    """Upgrade opentraces CLI and refresh the project skill file."""
    # Lazy import to avoid circular imports at module load time.
    from . import _upgrade_impl
    _upgrade_impl(skill_only)


# ---------------------------------------------------------------------------
# Plan-043 phase 2 — `ot setup entity-parser`.
#
# Isolated block: no shared helpers with the rest of this file so phase 3
# can grow its own install steps without a merge headache.
# ---------------------------------------------------------------------------

@setup_group.command(
    "entity-parser",
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
    It powers the richer side of `opentraces blame` and the per-commit
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
        human_echo(f"{_cli._err('error')}: {e}")
        emit_json({"status": "error", "action": "setup-entity-parser",
                   "message": str(e)})
        sys.exit(5)

    if total["n"]:
        click.echo("")
    human_echo(
        f"Entity parser installed at {result.path} "
        f"(version {ENTITY_BINARY_VERSION}, {result.source})"
    )
    emit_json({
        "status": "ok",
        "action": "setup-entity-parser",
        "binary_path": str(result.path),
        "version": ENTITY_BINARY_VERSION,
        "platform": result.platform,
        "source": result.source,
    })


# ---------------------------------------------------------------------------
# Plan-043 phase 3 — `ot setup watcher`.
#
# Isolated block (append-only, EOF) so phase 5/6 edits elsewhere in this
# file never collide with phase 3. Mirrors the entity-parser block style.
# ---------------------------------------------------------------------------

@setup_group.command("watcher")
@click.option("--interval", type=int, default=300, show_default=True,
              help="Polling interval in seconds.")
@click.option("--no-install", is_flag=True,
              help="Render the unit file but don't load it.")
@click.option("--uninstall", is_flag=True,
              help="Unload and remove the watcher unit file.")
def setup_watcher(interval: int, no_install: bool, uninstall: bool) -> None:
    """Install (or uninstall) the background attribution watcher.

    Writes a launchd plist (macOS) or systemd user unit + timer (Linux),
    plus a worker shim at ~/.opentraces/bin/ot-watcher. The service wakes
    up every `--interval` seconds, walks enlisted projects, and runs
    incremental backfill when new commits or new Claude Code JSONL
    activity appears.

    Run with ``--uninstall`` to tear it down.
    """
    from ..watcher import installer as _winst

    if uninstall:
        try:
            _winst.uninstall()
        except RuntimeError as e:
            human_echo(f"{_cli._err('error')}: {e}")
            emit_json({"status": "error", "action": "uninstall-watcher",
                       "message": str(e)})
            sys.exit(5)
        human_echo(f"{_cli._ok('uninstalled')} watcher")
        emit_json({"status": "ok", "action": "uninstall-watcher"})
        return

    try:
        path = _winst.install(interval=interval, dry_run=no_install)
    except RuntimeError as e:
        human_echo(f"{_cli._err('error')}: {e}")
        emit_json({"status": "error", "action": "setup-watcher",
                   "message": str(e)})
        sys.exit(5)

    human_echo(f"Watcher unit written: {path}")
    if no_install:
        human_hint("(dry run — not loaded)")
    emit_json({
        "status": "ok",
        "action": "setup-watcher",
        "unit_path": str(path),
        "interval_seconds": int(interval),
        "dry_run": bool(no_install),
    })
