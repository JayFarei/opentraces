"""``opentraces setup`` secret-scanner commands — trufflehog / privacy-filter.

Extracted from the ``installers`` god module (cli/setup decomposition): the
``trufflehog`` deep-secret-detector and ``privacy-filter`` PII-NER setup
commands + their install-method picker / success renderer. Registered on the
shared ``setup_group``; imported by ``cli/__init__`` for the
decorator-registration side effect. One-way dep on ``installers`` (setup_group).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from .installers import setup_group


def _pick_install_method_interactive() -> str | None:
    """Ask the user which installer to use; return ``None`` if declined."""
    from ..security.trufflehog import available_installers

    available = available_installers()
    if not available:
        _cli.human_echo(
            "trufflehog is not installed and no supported installer was found "
            "(brew, go) on this machine."
        )
        _cli.human_echo(
            "Install it manually from https://github.com/trufflesecurity/trufflehog "
            "and re-run 'opentraces setup trufflehog --enable'."
        )
        return None

    _cli.human_echo("")
    _cli.human_echo("trufflehog is not installed. choose an install method:")
    for i, name in enumerate(available, 1):
        blurb = {"brew": "Homebrew (recommended on macOS)",
                 "go": "go install from source"}.get(name, name)
        _cli.human_echo(f"  {i}. {name}    [{blurb}]")
    _cli.human_echo(f"  {len(available) + 1}. skip    [leave trufflehog unconfigured]")
    _cli.human_echo("")

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
        ("opentraces doctor", "check security tool health"),
    ],
)
@click.option(
    "--enable",
    is_flag=True,
    help=(
        "Enable the optional TruffleHog detector; fails TRUFFLEHOG_MISSING "
        "if binary absent"
    ),
)
@click.option("--disable", is_flag=True,
              help="Disable the optional TruffleHog detector (binary stays installed)")
@click.option("--verify", is_flag=True, hidden=True,
              help="Legacy alias for --enable")
@click.option("--project", "scope_project", is_flag=True,
              help="Scope this change to the project's marker (default: global config).")
def setup_trufflehog_cmd(enable: bool, disable: bool, verify: bool, scope_project: bool = False) -> None:
    """Configure the optional deep secret detector via TruffleHog.

    TruffleHog runs only when explicitly enabled in config. Findings are
    redacted in place, recorded in trace metadata, and can block unsafe
    dataset publication.

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

    cfg = _cli.load_config()

    if disable:
        cfg.security.trufflehog.enabled = False
        _cli.save_config(cfg)
        _cli.human_echo("TruffleHog detector disabled. Binary was not uninstalled.")
        _cli.human_hint("Re-enable with: opentraces setup trufflehog --enable")
        _cli.emit_json({"status": "ok", "action": "disable",
                   "trufflehog_enabled": False})
        return

    enable_only = enable or verify

    if enable_only:
        version = find_trufflehog()
        if version is None:
            _cli.human_echo(
                "trufflehog binary not found on PATH. "
                "Install it first, then re-run 'opentraces setup trufflehog --enable'."
            )
            _cli.emit_json(_cli.error_response(
                "TRUFFLEHOG_MISSING", "setup",
                "trufflehog binary not found",
                "Install trufflehog, then run --enable. "
                "Or run 'opentraces setup trufflehog' (no flags) for an interactive installer.",
            ))
            sys.exit(3)
        cfg.security.trufflehog.enabled = True
        _cli.save_config(cfg)
        _render_trufflehog_success(version, already_present=True)
        _cli.emit_json({"status": "ok", "action": "enable",
                   "trufflehog_version": version, "trufflehog_enabled": True})
        return

    # Interactive path.
    version = find_trufflehog()
    if version is not None:
        cfg.security.trufflehog.enabled = True
        _cli.save_config(cfg)
        _render_trufflehog_success(version, already_present=True)
        _cli.emit_json({"status": "ok", "action": "enable",
                   "trufflehog_version": version, "trufflehog_enabled": True,
                   "install_method": "already-installed"})
        return

    chosen = _pick_install_method_interactive()
    if chosen is None:
        _cli.human_echo("")
        _cli.human_echo("trufflehog left unconfigured.")
        _cli.emit_json({"status": "ok", "action": "declined",
                   "trufflehog_enabled": False})
        return

    ok, method = install_binary(method=chosen)
    if not ok:
        _cli.human_echo(
            f"\nCould not install trufflehog via {chosen}.\n"
            "Install it manually from https://github.com/trufflesecurity/trufflehog\n"
            "and re-run 'opentraces setup trufflehog --enable'."
        )
        _cli.emit_json(_cli.error_response(
            "TRUFFLEHOG_INSTALL_FAILED", "setup",
            f"install via {chosen} failed",
            "Install manually, then run --enable.",
        ))
        sys.exit(4)

    version = find_trufflehog()
    if version is None:
        _cli.human_echo(
            f"trufflehog installed via {method} but not yet on PATH. "
            "Re-run 'opentraces setup trufflehog --enable' once PATH is updated."
        )
        _cli.emit_json(_cli.error_response(
            "TRUFFLEHOG_PATH_MISS", "setup",
            f"installed via {method} but not on PATH",
            "Source your shell config or add GOPATH/bin, then --enable.",
        ))
        sys.exit(4)

    _cli.human_echo(f"Installed trufflehog via {method}: {version}")
    cfg.security.trufflehog.enabled = True
    _cli.save_config(cfg)
    _render_trufflehog_success(version, already_present=False, method=method)
    _cli.emit_json({"status": "ok", "action": "install",
               "trufflehog_version": version, "trufflehog_enabled": True,
               "install_method": method})


def _render_trufflehog_success(version: str, *, already_present: bool,
                               method: str | None = None) -> None:
    """Shared success banner with a clear what-this-means + disable hint."""

    _cli.human_echo("")
    _cli.print_banner(tagline=_cli._ok(f"trufflehog ready ({version})"))
    if already_present:
        _cli.human_echo(f"  {_cli._dim('(binary was already installed)')}")
    elif method:
        _cli.human_echo(f"  {_cli._dim(f'installed via {method}')}")
    _cli.human_echo("")
    _cli.human_echo(f"  {_cli._bold('From now on:')} dataset publication gates can use TruffleHog.")
    _cli.human_echo(f"  {_cli._dim('Findings are redacted in place and require review before publication.')}")
    _cli.human_echo("")
    _cli.human_echo(f"  {_cli._dim('disable:')}        opentraces setup trufflehog --disable")
    _cli.human_echo(f"  {_cli._dim('re-enable:')}      opentraces setup trufflehog --enable")
    _cli.human_echo(f"  {_cli._dim('publish gate:')}    opentraces dataset publish <name> --check-only")
    _cli.human_echo(f"  {_cli._dim('health check:')}   opentraces doctor")


# ---------------------------------------------------------------------------
# Review-LLM setup (opt-in third-party LLM review for dataset egress)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Step 12 — ot setup upgrade (absorbs the flat ot upgrade).
# Both surfaces survive during the transition; Step 15 drops the flat one.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# `ot setup privacy-filter` — opt-in HuggingFace BERT-NER PII detector.
# ---------------------------------------------------------------------------


@setup_group.command("privacy-filter")
@click.option("--enable/--disable", "enable", default=True,
              help="Turn the privacy-filter PII detector on or off.")
@click.option(
    "--install-deps", is_flag=True,
    help="Pip-install transformers + torch into the active environment.",
)
@click.option(
    "--model", default="openai/privacy-filter", show_default=True,
    help="HuggingFace model identifier.",
)
@click.option(
    "--score-threshold", type=float, default=0.7, show_default=True,
    help="Minimum confidence score for emitting a finding.",
)
def setup_privacy_filter_cmd(
    enable: bool,
    install_deps: bool,
    model: str,
    score_threshold: float,
) -> None:
    """Configure the ``openai/privacy-filter`` PII detector.

    The detector is opt-in: the ``transformers`` and ``torch`` packages
    aren't part of the default ``opentraces`` install. Pass ``--install-deps``
    to pip-install them into the active environment; otherwise the user
    is responsible for ensuring they're available before the next
    ``opentraces`` invocation. Either way this command flips
    ``cfg.security.privacy_filter.enabled`` to match ``--enable/--disable``.
    """
    cfg = _cli.load_config()
    cfg.security.privacy_filter.enabled = enable
    cfg.security.privacy_filter.model_name = model
    cfg.security.privacy_filter.score_threshold = score_threshold
    _cli.save_config(cfg)

    if install_deps and enable:
        import subprocess
        import sys

        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "transformers", "torch"],
                check=True,
            )
            click.echo("Installed transformers + torch.")
        except subprocess.CalledProcessError as exc:
            click.echo(f"pip install failed: {exc}", err=True)
            click.echo(
                "privacy-filter is enabled in config but transformers/torch "
                "are not installed. Install them manually before next run.",
                err=True,
            )
        # Probe model availability (downloads on first use otherwise).
        try:
            from ..security.privacy_filter import PrivacyFilterModel

            ok = PrivacyFilterModel(model_name=model).is_available()
            if ok:
                click.echo(f"Model {model!r} is reachable.")
            else:
                click.echo(
                    f"Model {model!r} could not be loaded — first inference call "
                    "will retry (and may download).",
                    err=True,
                )
        except Exception as exc:  # noqa: BLE001
            click.echo(f"Model probe skipped: {exc}", err=True)

    state = "enabled" if enable else "disabled"
    _cli.human_echo(f"privacy-filter: {state} ({model}, threshold={score_threshold:.2f})")
    _cli.emit_json({
        "status": "ok",
        "action": "setup-privacy-filter",
        "enabled": enable,
        "model": model,
        "score_threshold": score_threshold,
    })
