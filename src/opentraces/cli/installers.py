"""CLI installers/admin group: setup, doctor, and supporting setup actions."""
from __future__ import annotations

import logging
from pathlib import Path

import click

import opentraces.cli as _cli
from . import main

logger = logging.getLogger("opentraces.cli.installers")


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

    Each subcommand installs (or controls) one integration:

    \b
      claude-code   PreToolUse/PostToolUse/Stop/PostCompact hooks that capture
                    Claude Code step boundaries and session transcripts.
      codex-cli     Native Codex CLI hook commands that write opentraces
                    sidecars and trigger ingestion on Stop.
      pi            Pi package install/repair/status for opentraces-pi.
      git           post-commit hook that correlates each commit to the
                    trace that produced it (via refs/notes/opentraces),
                    powering `opentraces trail blame`.
      watcher       background attribution daemon (launchd/systemd) that
                    walks enlisted projects and matures Trace Trails.
                    Has its own subcommands: install/start/stop/status/tick.
      auth          HuggingFace login for private bucket sync and dataset remotes.
      bucket        Configure the private bucket as remote-by-default or local-only.
      trufflehog    optional deep secret detector. Findings redact in place
                    only when the tool is explicitly enabled.
      privacy-filter
                    optional local/HF NER PII detector (transformers + torch).
      llm-review    optional dataset-row reviewer used by publication gates,
                    separate from per-record sanitize tools.
      uninstall     the symmetric inverse of ``setup``: reverse every
                    install-time patch + daemon (``--integrations-only``,
                    preserves captured data) or also delete captured data +
                    git refs (``--purge``).

    Run bare ``opentraces setup`` for an interactive wizard that walks every
    integration, or call a subcommand to target one directly.
    """
    if ctx.invoked_subcommand is not None:
        return
    _run_setup_wizard()


def _wizard_confirm(prompt: str, *, default: bool, hint: str | None = None) -> bool:
    """Ask a yes/no question in the setup wizard.

    Rendered as a clack-style ``◇`` prompt but printed strictly sequentially —
    no in-place cursor redraw. ``hint`` gets its own dim line under the prompt
    so long hints never have to be folded into (and wrap) the question.

    Why not pyclack here: ``pyclack.prompts.confirm`` saves an *absolute*
    cursor position (``\\033[s``) and, on submit, restores it and rewrites the
    frame in place. Once the wizard fills the screen and scrolls — which it
    reliably does on a short or narrow terminal — that saved row is stale, so
    the submitted frame clears and overwrites the wrong lines, mangling the
    neighbouring status output. Sequential printing has nothing to go stale, so
    it stays correct at any terminal size.
    """
    symbol = click.style("◇", fg="cyan", bold=True)
    bar = _cli._dim("│")
    _cli.human_echo(f"{symbol}  {prompt}")
    if hint:
        _cli.human_echo(f"{bar}  {_cli._dim(hint)}")
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = click.prompt(
            f"{bar}  {suffix}",
            default="y" if default else "n",
            show_default=False,
            prompt_suffix=" ",
        ).strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        _cli.human_echo(f"{bar}  please answer y or n")


def _run_setup_wizard() -> None:
    """Walk every integration the user should know about, one prompt each.

    Order (mandatory with opt-out, then optional):
      1. agent / git / skill hooks        (hook installers, default yes)
      2. watcher                          (powers 'opentraces trail blame', default yes)
      3. entity-parser (sem)              (richer commit diffs, default yes)
      4. HuggingFace login                (log in now or skip)
      5. private bucket sync              (remote by default when authenticated;
                                           the bucket security policy chosen here
                                           is the single interactive security step)
      6. security tool status             (trufflehog / llm-review, read-only)
      7. closing panel — point at `opentraces init` + `opentraces doctor`
    """
    from ..capture import get_hook_installers
    from ..security.trufflehog import find_trufflehog
    from ..watcher import installer as _winst
    from ..enrichment.entities import installer as _entinst
    from ..enrichment.entities import EntityRunner
    from ..enrichment.entities.runner import resolve_binary_path
    # Bucket configurators moved to cli/setup_bucket in the decomposition; lazy
    # import here (rather than module-top) so installers <-> setup_bucket stays
    # acyclic — setup_bucket imports _wizard_confirm/setup_group from installers.
    from .setup_bucket import (
        _configure_bucket_local,
        _configure_bucket_remote,
        _prompt_bucket_security_policy,
    )

    _cli.print_banner(tagline="setup wizard")
    _cli.human_echo("")

    # 0. Tracking mode — the headline choice (plan 081). Global (default)
    #    auto-enrolls every agent including Pi; opt out via manual mode or a
    #    per-project `excluded` marker. Persisted to global config.
    cfg = _cli.load_config()
    current_mode = cfg.capture.tracking_mode
    _cli.human_echo(f"  {_cli._bold('tracking mode'):<28} {_cli._ok(current_mode)}")
    track_global = _wizard_confirm(
        "track every project automatically?",
        default=(current_mode == "global"),
        hint="auto-enrolls Claude/Codex/Pi; opt out per project anytime",
    )
    new_mode = "global" if track_global else "manual"
    if new_mode != current_mode:
        cfg.capture.tracking_mode = new_mode
        _cli.save_config(cfg)
    _cli.human_echo(f"    {_cli._ok(new_mode)}")

    # 1. Hook installers (agents, git, skill) — one prompt each, default yes.
    for name, cls in get_hook_installers().items():
        inst = cls()
        st = inst.status()
        installed = bool(st.get("installed"))
        label = _cli._ok("installed") if installed else _cli._dim("not installed")
        _cli.human_echo(f"  {_cli._bold(name):<28} {label}")
        if installed:
            continue
        if not _wizard_confirm(f"install {name}?", default=True):
            continue
        try:
            result = inst.install()
        except Exception as e:
            _cli.human_echo(f"    {_cli._err('failed')}: {e}")
            continue
        if result.ok:
            _cli.human_echo(f"    {_cli._ok('done')} ({', '.join(result.added) or 'already present'})")
        else:
            for note in result.notes:
                _cli.human_echo(f"    {_cli._err('skip')}: {note}")

    # 2. Watcher — default yes. Powers "opentraces trail blame" by running
    #    incremental backfill in the background after each commit.
    w_st = _winst.status()
    w_label = (
        _cli._ok("installed") if w_st.installed else _cli._dim("not installed")
    )
    _cli.human_echo(f"  {_cli._bold('watcher'):<28} {w_label}")
    if not w_st.installed:
        if _wizard_confirm(
            "install the attribution watcher?",
            default=True,
            hint="powers 'opentraces trail blame' on every new commit",
        ):
            try:
                path = _winst.install()
                _cli.human_echo(f"    {_cli._ok('installed')} {path}")
            except Exception as e:
                _cli.human_echo(f"    {_cli._err('failed')}: {e}")

    # 3. Entity parser (sem) — default yes, richer commit diffs.
    ent_runner = EntityRunner(binary_path=resolve_binary_path())
    ent_installed = ent_runner.available()
    ent_label = (
        _cli._ok("installed") if ent_installed else _cli._dim("not installed")
    )
    _cli.human_echo(f"  {_cli._bold('entity-parser'):<28} {ent_label}")
    if not ent_installed:
        if _wizard_confirm(
            "install the entity parser (sem)?",
            default=True,
            hint="entity-level diffs for richer commit attribution",
        ):
            try:
                _entinst.install()
                _cli.human_echo(f"    {_cli._ok('installed')}")
            except _entinst.InstallError as e:
                _cli.human_echo(f"    {_cli._err('failed')}: {e}")

    # 4. HuggingFace login.
    cfg = _cli.load_config()
    identity = _cli._auth_identity(cfg.hf_token) if cfg.hf_token else None
    if identity:
        _cli.human_echo(
            f"  {_cli._bold('huggingface'):<28} "
            f"{_cli._ok('authenticated')} "
            f"{_cli._dim('(' + str(identity.get('name') or '') + ')')}"
        )
    else:
        _cli.human_echo(f"  {_cli._bold('huggingface'):<28} {_cli._dim('not authenticated')}")
        if _wizard_confirm(
            "log into HuggingFace now?",
            default=True,
            hint="needed for dataset remotes; skip and run 'opentraces setup auth' later",
        ):
            try:
                from ..core.config import save_credentials, CREDENTIALS_PATH
                _cli._login_with_device_code(save_credentials, CREDENTIALS_PATH)
            except Exception as e:
                _cli.human_echo(f"    {_cli._err('failed')}: {e}")

    cfg = _cli.load_config()
    identity = _cli._auth_identity(cfg.hf_token) if cfg.hf_token else None
    bucket_remote = cfg.bucket.remote
    bucket_configured = cfg.bucket.storage == "remote" and bucket_remote.enabled
    bucket_label = (
        _cli._ok(f"remote ({bucket_remote.url})")
        if bucket_configured and bucket_remote.url
        else _cli._ok("remote")
        if bucket_configured
        else _cli._dim("local only")
    )
    _cli.human_echo(f"  {_cli._bold('private bucket'):<28} {bucket_label}")
    if not bucket_configured:
        if identity:
            username = str(identity.get("name") or "")
            if _wizard_confirm(
                "sync the private bucket remotely?",
                default=True,
                hint="private HuggingFace bucket; local cache remains available",
            ):
                if _cli._is_interactive_terminal():
                    _prompt_bucket_security_policy(cfg)
                configured = _configure_bucket_remote(
                    cfg,
                    provider="huggingface",
                    repo=None,
                    username=username,
                    fake_root=None,
                    sync_policy="daemon",
                )
                _cli.human_echo(f"    {_cli._ok('configured')} {configured['remote']['url']}")
            else:
                _configure_bucket_local(cfg)
                _cli.human_echo(f"    {_cli._dim('local-only')}")
        else:
            _cli.human_hint(
                "    private bucket sync will stay local until HuggingFace auth is configured."
            )

    # 6. Security tool status (read-only). The bucket security policy above is
    #    the wizard's single interactive security choice (issue #66); these
    #    lines just surface the current global state. Enable via
    #    `opentraces setup trufflehog` / `opentraces setup llm-review`.
    th_version = find_trufflehog()
    th_enabled = cfg.security.trufflehog.enabled
    th_label = (
        _cli._ok(f"enabled ({th_version})") if th_enabled and th_version
        else _cli._dim("disabled" if not th_enabled else "enabled but missing")
    )
    _cli.human_echo(f"  {_cli._bold('trufflehog'):<28} {th_label}")

    llm_enabled = getattr(cfg.security, "llm_review", None) and getattr(cfg.security.llm_review, "enabled", False)
    llm_label = _cli._ok("enabled") if llm_enabled else _cli._dim("disabled")
    _cli.human_echo(f"  {_cli._bold('llm-review'):<28} {llm_label}")

    _cli.human_echo("")
    _cli.human_echo(_cli._bold("Next steps"))
    if new_mode == "global":
        _cli.human_echo(
            "  • tracking mode is global: Claude/Codex/Pi projects are auto-enrolled "
            "(private + review-required); opt out via manual mode or a per-project marker."
        )
        _cli.human_echo(
            f"  • to opt one project out:  {_cli._bold('opentraces remove')}"
        )
    else:
        _cli.human_echo(
            f"  • to track a project:  {_cli._bold('cd <project> && opentraces init')}"
        )
    _cli.human_echo(
        f"  • to inspect health:   {_cli._bold('opentraces doctor')}"
    )
    _cli.human_echo(
        f"  • optional secret scanning:   {_cli._bold('opentraces setup trufflehog')}"
    )
    _cli.human_echo(
        f"  • optional LLM row review:    {_cli._bold('opentraces setup llm-review')}"
    )
    _cli.human_echo("  • dataset review policy lives in the dataset manifest and review commands.")


# ---------------------------------------------------------------------------
# Plan 032 Phase 1 — security-module CLI surfaces.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# `ot setup watcher` — install + lifecycle for the background watcher.
#
# The watcher is a system-level service (launchd agent on macOS, systemd
# user timer on Linux). It only ever exists as one global install, so its
# entire surface — install/uninstall plus runtime control — lives under
# the global ``setup`` namespace rather than being split across `setup
# watcher` and a separate top-level `watcher` group.
# ---------------------------------------------------------------------------
