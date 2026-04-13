"""CLI installers/admin group: setup, blame, resume, doctor, review-llm."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main
from ..core.config import save_config  # noqa: F401


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
    """Wire opentraces into your agent, git, and security stack.

    Each subcommand installs one integration:

    \b
      claude-code   Stop/PostCompact hooks that capture every Claude Code
                    session transcript so `opentraces push` can parse it.
      git           post-commit hook that correlates each commit to the
                    trace that produced it (via refs/notes/opentraces),
                    powering `opentraces blame` and git-linked uploads.
      trufflehog    Tier 1.5 secret scanner, any finding blocks upload
                    until resolved.
      review-llm    Tier 2 third-party LLM reviewer for staged traces,
                    used by `opentraces review-llm` and `push --llm-review`.

    Run bare ``opentraces setup`` for an interactive wizard that walks every
    integration, or call a subcommand to target one directly.
    """
    if ctx.invoked_subcommand is not None:
        return
    _run_setup_wizard()


def _run_setup_wizard() -> None:
    """Iterate HOOK_INSTALLERS + trufflehog and ask the user about each."""
    from ..capture import get_hook_installers
    from ..security.trufflehog import find_trufflehog, install_binary
    from opentraces import cli as _cli

    print_banner(tagline="setup wizard")
    human_echo("")

    # Hook installers — one prompt each, driven by HOOK_INSTALLERS registry.
    for name, cls in get_hook_installers().items():
        inst = cls()
        st = inst.status()
        installed = bool(st.get("installed"))
        label = _cli._ok("installed") if installed else _cli._dim("not installed")
        human_echo(f"  {_cli._bold(name):<28} {label}")
        if installed:
            continue
        if not click.confirm(f"    install {name}?", default=True):
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

    # Optional dependency: trufflehog.
    cfg = load_config()
    th_version = find_trufflehog()
    th_enabled = cfg.security.trufflehog.enabled
    label = (
        _cli._ok(f"enabled ({th_version})") if th_enabled and th_version
        else _cli._dim("disabled" if not th_enabled else "enabled but missing")
    )
    human_echo(f"  {_cli._bold('trufflehog'):<28} {label}")
    if not (th_enabled and th_version):
        if click.confirm("    install and enable trufflehog (Tier 1.5)?", default=False):
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

    human_echo("")
    human_echo(f"run {_cli._bold('opentraces doctor')} to verify.")


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
    """Resolve a commit to the opentraces trace(s) behind it.

    COMMIT is any git ref — a sha (short or full), branch name, or `HEAD~N`.
    Traces are linked via `refs/notes/opentraces` written by the post-commit
    hook (install with ``opentraces setup git``).
    """
    from ..core.config import get_project_staging_dir
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

    staging = get_project_staging_dir(cwd)
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
    """Resume the Claude Code session that produced a trace.

    Looks up the trace's session_id and either prints the resume command
    (default) or execs it with --exec. Pairs naturally with `blame <sha>`
    to re-open the session behind a given commit.
    """
    from ..capture.claude_code.resume import ResumeError, resolve
    from ..core.config import get_project_staging_dir
    from opentraces import cli as _cli

    staging = get_project_staging_dir(Path.cwd())
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
    human_echo(f"{_cli._dim('or:')}      opentraces resume {trace_id[:8]} --exec")
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
        ("opentraces upgrade", "refresh the skill after a CLI update"),
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
            "and re-run 'opentraces setup trufflehog --verify'."
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
def setup_trufflehog_cmd(enable: bool, disable: bool, verify: bool) -> None:
    """Enable Tier 1.5 secret scanning via TruffleHog.

    Tier 1.5 runs the TruffleHog verified-secret scanner on every staged
    and pushed trace. Any finding moves the trace to BLOCKED locally and
    stops the upload, traces with verified secrets never leave the
    machine. Complements the always-on Tier 1a regex + 1b entropy scans.

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
    human_echo(f"  {_cli._dim('Any TruffleHog finding blocks upload until resolved.')}")
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


def _test_review_llm(provider: str, base_url: str, model: str, api_key_env: str,
                     timeout: float) -> tuple[bool, str]:
    """Ping the configured LLM endpoint. Returns (ok, message)."""
    from ..security.llm_provider import (
        AnthropicProvider, OpenAICompatProvider,
    )
    try:
        if provider == "anthropic":
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
    rc = cfg.security.review_llm
    return {
        "enabled": rc.enabled,
        "provider": rc.provider,
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
        provider = "anthropic" if name == "anthropic-direct" else "openai"
    elif choice == len(_REVIEW_LLM_PRESETS) + 1:
        name = "custom"
        provider = click.prompt("provider", default="openai", show_default=True)
        base_url = click.prompt("base URL (empty for anthropic)", default="", show_default=False)
        api_key_env = click.prompt("API key env var name (empty for local)", default="", show_default=False)
        sample = ""
    else:
        raise click.BadParameter(f"choice out of range: {choice}")

    if provider == "anthropic":
        model = click.prompt("model", default=sample or "claude-haiku-4-5-20251001",
                             show_default=True)
    else:
        model = _pick_model_from_list(name, base_url, api_key_env, sample)

    timeout_str = click.prompt("timeout seconds", default="120", show_default=True)
    try:
        timeout = float(timeout_str)
    except ValueError:
        timeout = 120.0

    return provider, base_url, model, api_key_env, timeout


@setup_group.command("review-llm")
@click.option("--provider", default=None,
              help="Provider kind: openai (default, OpenAI-compat servers), "
                   "ollama (native /api/generate), anthropic, fake.")
@click.option("--base-url", default=None,
              help="Base URL including /v1 for OpenAI-compat servers. "
                   "Ignored for anthropic.")
@click.option("--model", default=None, help="Model name/tag.")
@click.option("--api-key-env", default=None,
              help="Env var holding the API key. Empty for local servers.")
@click.option("--timeout", default=None, type=float, help="Request timeout (s).")
@click.option("--disable", is_flag=True, help="Turn review-llm off without changing other fields.")
@click.option("--enable", is_flag=True, help="Turn review-llm on using current config.")
@click.option("--test", "test_only", is_flag=True,
              help="Ping the endpoint; do not write config.")
@click.option("--print", "print_only", is_flag=True,
              help="Print effective config as JSON and exit.")
@click.option("--no-interactive", is_flag=True,
              help="Skip the preset picker even if no flags are given.")
def setup_review_llm_cmd(
    provider: str | None, base_url: str | None, model: str | None,
    api_key_env: str | None, timeout: float | None,
    disable: bool, enable: bool, test_only: bool, print_only: bool,
    no_interactive: bool,
) -> None:
    """Configure the Tier 2 LLM reviewer for staged traces.

    Points opentraces at an OpenAI-compatible, Ollama, Anthropic, or
    fake backend that reads each staged trace and flags residual
    sensitive content the regex/entropy/TruffleHog tiers could miss
    (semantic PII, proprietary context, policy concerns). Used by
    `opentraces review-llm` and `opentraces push --llm-review`.

    Stored globally in ~/.opentraces/config.json under
    security.review_llm. One config per machine, projects inherit it.

    Interactive picker when run with no flags. Non-interactive for agents:

    \b
        opentraces setup review-llm --provider openai \\
            --base-url http://localhost:11434/v1 --model gemma3n:e4b
        opentraces setup review-llm --provider openai \\
            --base-url https://api.groq.com/openai/v1 \\
            --model llama-3.3-70b-versatile --api-key-env GROQ_API_KEY
        opentraces setup review-llm --provider anthropic \\
            --model claude-haiku-4-5-20251001 --api-key-env ANTHROPIC_API_KEY
    """
    cfg = load_config()
    rc = cfg.security.review_llm

    if print_only:
        emit_json({"status": "ok", "review_llm": _review_llm_config_from_cfg(cfg)})
        return

    if disable:
        rc.enabled = False
        save_config(cfg)
        human_echo("review-llm disabled.")
        emit_json({"status": "ok", "action": "disable",
                   "review_llm": _review_llm_config_from_cfg(cfg)})
        return

    # Agent / non-interactive path: any flag provided => skip the wizard.
    any_flag = any(v is not None for v in (provider, base_url, model, api_key_env, timeout))

    if not any_flag and not enable and not test_only and not no_interactive:
        provider, base_url, model, api_key_env, timeout = _setup_review_llm_interactive()

    # Layer flag overrides on top of current config.
    eff_provider = provider or rc.provider
    eff_base_url = base_url if base_url is not None else rc.base_url
    eff_model = model or rc.model
    eff_api_key_env = api_key_env if api_key_env is not None else rc.api_key_env
    eff_timeout = timeout if timeout is not None else rc.timeout

    if test_only:
        ok, message = _test_review_llm(
            eff_provider, eff_base_url, eff_model, eff_api_key_env, eff_timeout,
        )
        human_echo(f"review-llm test: {'ok' if ok else 'failed'} — {message}")
        emit_json({
            "status": "ok" if ok else "error",
            "action": "test",
            "review_llm": {
                "provider": eff_provider, "base_url": eff_base_url,
                "model": eff_model, "api_key_env": eff_api_key_env,
            },
            "reachable": ok, "message": message,
        })
        if not ok:
            sys.exit(3)
        return

    rc.provider = eff_provider
    rc.base_url = eff_base_url
    rc.model = eff_model
    rc.api_key_env = eff_api_key_env
    rc.timeout = eff_timeout
    rc.enabled = True
    save_config(cfg)

    ok, message = _test_review_llm(
        rc.provider, rc.base_url, rc.model, rc.api_key_env, rc.timeout,
    )
    human_echo("")
    from opentraces import cli as _cli
    tag = _cli._ok("review-llm configured") if ok else _cli._err("review-llm saved but unreachable")
    print_banner(tagline=tag)
    human_echo(f"  {_cli._dim('provider:  ')} {rc.provider}")
    if rc.provider != "anthropic":
        human_echo(f"  {_cli._dim('base url:  ')} {rc.base_url}")
    human_echo(f"  {_cli._dim('model:     ')} {rc.model}")
    if rc.api_key_env:
        present = "set" if os.environ.get(rc.api_key_env) else _cli._err("NOT SET")
        human_echo(f"  {_cli._dim('api key:   ')} ${rc.api_key_env} ({present})")
    human_echo(f"  {_cli._dim('reachable: ')} {message}")
    human_echo("")
    human_echo(f"  {_cli._bold('To run:')} opentraces review-llm  "
               f"{_cli._dim('(staged traces; out-of-band, not automatic)')}")
    human_echo(f"  {_cli._dim('gate push:')}     opentraces push --llm-review")
    human_echo(f"  {_cli._dim('disable:')}       opentraces setup review-llm --disable")
    human_echo(f"  {_cli._dim('health check:')}  opentraces doctor")

    emit_json({
        "status": "ok", "action": "install",
        "review_llm": _review_llm_config_from_cfg(cfg),
        "reachable": ok, "message": message,
    })


@main.command("doctor")
def doctor_cmd() -> None:
    """Report security pipeline and integration health."""
    from ..core import doctor

    cfg = load_config()
    report = doctor.report(cfg, Path.cwd())
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


def _trufflehog_row(th: dict) -> None:
    if not th.get("enabled"):
        _row("off", "trufflehog", "disabled", detail="opt in via 'opentraces setup trufflehog'")
        return
    ver = th.get("binary_version")
    if ver is None:
        _row("err", "trufflehog", "missing binary", detail="run 'opentraces setup trufflehog --verify'")
    else:
        _row("ok", "trufflehog", ver)


def _review_llm_row(rl: dict) -> None:
    if not rl.get("enabled"):
        _row("off", "review-llm", "disabled", detail="opt in via 'opentraces setup review-llm'")
        return
    backend = rl.get("backend") or rl.get("provider") or "?"
    model = rl.get("model") or "?"
    reachable = rl.get("reachable")
    if reachable is False:
        _row("err", "review-llm", f"{backend} / {model}", detail=rl.get("status", "unreachable"))
        return
    # Try to pull the trailing "— N models available" off the status string.
    note = None
    status = rl.get("status") or ""
    if "—" in status:
        note = status.rsplit("—", 1)[1].strip()
    _row("ok", "review-llm", f"{backend} / {model}", detail=note)


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


def _skill_row(h: dict) -> None:
    installed = h.get("installed")
    iv = h.get("installed_version")
    pv = h.get("package_version")
    broken = h.get("broken_harnesses") or []
    if not installed:
        _row("off", "skill", "not installed", detail="run 'opentraces setup skill'")
    else:
        kind = "warn" if h.get("drift") or broken else "ok"
        value = iv or "installed"
        detail = None
        if h.get("drift"):
            detail = f"drift: package is {pv}, run 'opentraces setup skill'"
        _row(kind, "skill", value, detail=detail)
    # Per-harness detail — this is the "which agents are we in" view.
    harnesses = h.get("harnesses") or {}
    for hname, st in harnesses.items():
        if not st.get("present"):
            sub_kind, sub_val, sub_detail = "off", "not linked", None
        elif st.get("canonical"):
            sub_kind, sub_val, sub_detail = "ok", "linked", st.get("target")
        else:
            sub_kind, sub_val, sub_detail = "warn", "non-canonical dir", st.get("kind")
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


def _render_doctor_human(report: dict) -> None:
    human_echo(_cli._bold("opentraces doctor"))

    _section("Versions")
    _row("ok", "security", report["security_version"])
    if report.get("schema_version"):
        _row("ok", "schema", report["schema_version"])

    _section("Security pipeline")
    _trufflehog_row(report["trufflehog"])
    _review_llm_row(report["review_llm"])

    _section("Authentication")
    hf = report.get("hf_auth")
    if hf == "ok":
        _row("ok", "huggingface", "authenticated")
    else:
        _row("err", "huggingface", "missing", detail="run 'huggingface-cli login'")

    _processors_section(report["post_processors"])
    _hooks_section(report["hooks"])
    human_echo("")


def _filter_by_scope(records: list[dict], scope: str, state) -> list[dict]:
    """Filter records by TraceStatus from the StateManager.

    ``scope`` values:
      - ``all``: every record in the staging directory (default)
      - ``staged``: STAGED status only (pre-commit)
      - ``committed``: COMMITTED status only (second line of defence
        before push, after human review)
    """
    from ..core.state import TraceStatus

    if scope == "all":
        return records
    target = {
        "staged": TraceStatus.STAGED.value,
        "committed": TraceStatus.COMMITTED.value,
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


@main.command(
    "review-llm",
    examples=[
        "opentraces review-llm                      # every trace in staging",
        "opentraces review-llm --scope committed    # 2nd line of defence before push",
        "opentraces review-llm --scope staged       # pre-commit only",
        "opentraces review-llm --trace 8a3f1c       # one trace (short id ok)",
        "opentraces review-llm --dry-run            # estimate token usage only",
    ],
    see_also=[
        ("opentraces setup review-llm", "configure the LLM provider"),
        ("opentraces push --llm-review", "gate uploads on a clean verdict"),
    ],
    option_groups=[
        ("Provider overrides", ["provider", "model", "base_url", "api_key_env"]),
        ("Selection", ["scope", "trace_ids", "limit"]),
        ("Run", ["dry_run", "force", "context_file"]),
    ],
)
@click.option("--provider", default=None,
              help="Override provider (openai, ollama, anthropic, fake)")
@click.option("--model", default=None, help="Override model")
@click.option("--base-url", default=None,
              help="Override base URL for OpenAI-compat providers")
@click.option("--api-key-env", default=None,
              help="Override the env var holding the API key")
@click.option("--scope",
              type=click.Choice(["all", "staged", "committed"], case_sensitive=False),
              default="all",
              help="Which traces to review: 'all' (every trace in staging; default), "
                   "'staged' (STAGED status only, pre-commit), "
                   "'committed' (COMMITTED status only — second line of defence before push)")
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
def review_llm_cmd(provider: str | None, model: str | None, base_url: str | None,
                   api_key_env: str | None, scope: str,
                   trace_ids: tuple[str, ...], dry_run: bool, limit: int,
                   force: bool, context_file: str | None) -> None:
    """Run Tier 2 LLM semantic review over staged or committed traces.

    Uses the LLM configured by 'opentraces setup review-llm' unless you
    override via --provider / --model / --base-url / --api-key-env.

    LLM can be slow if using local models. Narrow with --scope (pick
    staged or committed only) or --trace (one or more specific trace
    ids), and cap with --limit. The typical "second line of defence"
    flow is 'review-llm --scope committed' right before 'push --llm-review'.
    """
    from ..core.config import get_project_staging_dir, get_project_state_path
    from ..core.inbox import load_traces
    from ..core.review import estimate_llm_review, run_llm_review
    from ..core.state import StateManager

    cfg = load_config()
    rc = cfg.security.review_llm
    eff_provider = provider or rc.provider
    eff_model = model or rc.model
    eff_base_url = base_url if base_url is not None else rc.base_url
    eff_api_key_env = api_key_env if api_key_env is not None else rc.api_key_env
    eff_timeout = rc.timeout

    if not rc.enabled and provider is None and model is None:
        human_hint(
            "review-llm is not configured. Run 'opentraces setup review-llm' "
            "once, or pass --provider/--model explicitly."
        )

    staging = get_project_staging_dir(Path.cwd())
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
            "status": "ok", "action": "review-llm",
            "scope": scope, "trace_ids": list(trace_ids),
            "matched": 0, "total_available": total_available,
        }
        if dry_run:
            payload.update({
                "dry_run": True, "sessions": 0, "chars": 0,
                "estimate": {"tokens": 0, "cost_usd": 0.0},
                "model": eff_model, "provider": eff_provider,
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
        est = estimate_llm_review(records, provider=eff_provider, model=eff_model)
        human_echo(
            f"Dry run: {est.sessions} sessions, ~{est.chars:,} chars, "
            f"~{est.tokens:,} tokens, ~${est.cost_usd:.4f}."
        )
        emit_json({
            "status": "ok",
            "action": "review-llm",
            "dry_run": True,
            "scope": scope,
            "trace_ids": list(trace_ids),
            "matched": len(records),
            "total_available": total_available,
            "sessions": est.sessions,
            "chars": est.chars,
            "estimate": {"tokens": est.tokens, "cost_usd": est.cost_usd},
            "model": eff_model,
            "provider": eff_provider,
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
        provider=eff_provider,
        model=eff_model,
        base_url=eff_base_url,
        api_key_env=eff_api_key_env,
        timeout=eff_timeout,
        prompt_version=rc.prompt_version,
        context=context,
        force=force,
        on_progress=_progress,
    )
    emit_json({
        "status": "ok",
        "action": "review-llm",
        "dry_run": False,
        "scope": scope,
        "trace_ids": list(trace_ids),
        "matched": len(records),
        "total_available": total_available,
        "provider": eff_provider,
        "model": eff_model,
        "base_url": eff_base_url,
        "results": outcome.results,
    })

