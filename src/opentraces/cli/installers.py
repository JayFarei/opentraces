"""CLI installers/admin group: setup, doctor, and supporting setup actions."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from . import main
from ._options import dump_json as _dump_json
from ._security_flags import (
    BUCKET_SECURITY_POLICIES,
    RECOMMENDED_BUCKET_SECURITY_TOOLS,
    SECURITY_TOOL_NAMES,
    apply_bucket_security_policy,
    apply_security_tool_flag_changes,
    enabled_security_tool_names,
    security_tool_state_payload,
    set_security_tools_exact,
)

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


def _prompt_bucket_security_policy(cfg) -> list[str]:
    """Choose the bucket security policy before a private bucket can sync."""

    _cli.human_echo("")
    _cli.human_echo(_cli._bold("Bucket security policy"))
    choices = [
        ("recommended", "local redaction + business signals + path anonymizer + classifier"),
        ("basic", "regex + entropy only"),
        ("strict", "recommended plus TruffleHog and privacy-filter if configured"),
        ("off", "no automatic bucket security tools"),
        ("custom", "choose tools one by one"),
    ]
    for idx, (name, detail) in enumerate(choices, start=1):
        tools = BUCKET_SECURITY_POLICIES.get(name)
        suffix = f" ({', '.join(tools) if tools else 'no tools'})" if tools is not None else ""
        _cli.human_echo(f"  {idx}. {name}{suffix}")
        _cli.human_echo(f"     {_cli._dim(detail)}")

    raw = click.prompt("choose bucket security policy", default="1", show_default=True)
    try:
        idx = int(str(raw).strip())
    except ValueError:
        raise click.BadParameter(f"expected a number, got {raw!r}")
    if not (1 <= idx <= len(choices)):
        raise click.BadParameter(f"choose a number from 1 to {len(choices)}")

    policy = choices[idx - 1][0]
    if policy == "custom":
        # Default each prompt to the tool's CURRENT state so pressing Enter
        # preserves the existing setup; only suggest the recommended baseline
        # when nothing is enabled yet. This stops a user who picks "custom" to
        # ADD tools from silently disabling everything by declining prompts.
        current = set(enabled_security_tool_names(cfg))
        enabled: list[str] = []
        for tool_name in SECURITY_TOOL_NAMES:
            default = (
                tool_name in current
                if current
                else tool_name in RECOMMENDED_BUCKET_SECURITY_TOOLS
            )
            if _wizard_confirm(f"enable {tool_name}?", default=default):
                enabled.append(tool_name)
        changes = set_security_tools_exact(cfg, enabled)
    else:
        changes = apply_bucket_security_policy(cfg, policy)
    _cli.save_config(cfg)

    # TruffleHog rides in via the strict/custom paths. If the chosen policy
    # enables it and the binary is missing, run the existing install flow
    # inline right here — this is the wizard's single interactive security
    # step (issue #66), so there is no later standalone prompt to catch it.
    if "trufflehog" in enabled_security_tool_names(cfg):
        from ..security.trufflehog import find_trufflehog, install_binary

        if find_trufflehog() is None:
            _cli.human_echo("    trufflehog binary not found — installing now")
            ok, method = install_binary()
            if ok and find_trufflehog():
                _cli.human_echo(f"    installed via {method}")
            else:
                _cli.human_echo(
                    f"    {_cli._err('install failed')} — run "
                    "'opentraces setup trufflehog' or see "
                    "https://github.com/trufflesecurity/trufflehog"
                )

    if changes["disabled"]:
        _cli.human_echo(
            "    note: bucket security flags are global; turned OFF: "
            + ", ".join(changes["disabled"])
        )
    _cli.human_echo(
        "    security tools: "
        + (", ".join(enabled_security_tool_names(cfg)) or "none")
    )
    return changes["enabled"]


def _configure_bucket_local(cfg) -> dict:
    from ..core.config import BucketConfig, BucketRemoteConfig

    cfg.bucket = BucketConfig(
        storage="local",
        local_cache=True,
        remote=BucketRemoteConfig(enabled=False),
    )
    _cli.save_config(cfg)
    return cfg.bucket.model_dump(mode="json")


def _configure_bucket_remote(
    cfg,
    *,
    provider: str,
    repo: str | None,
    username: str | None,
    fake_root: Path | None,
    sync_policy: str,
) -> dict:
    from ..core.config import BucketConfig, BucketRemoteConfig
    from ..core.datasets import hf_url, normalize_hf_repo_id

    if provider == "fake":
        if fake_root is None:
            raise ValueError("--fake-root is required when --provider fake is used")
        remote_url = fake_root.expanduser().resolve().as_uri()
    else:
        repo_id = normalize_hf_repo_id(repo or "opentraces-bucket", username)
        remote_url = hf_url(repo_id)

    cfg.bucket = BucketConfig(
        storage="remote",
        local_cache=True,
        remote=BucketRemoteConfig(
            enabled=True,
            provider=provider,
            url=remote_url,
            visibility="private",
            sync_policy=sync_policy,
        ),
    )
    _cli.save_config(cfg)
    return cfg.bucket.model_dump(mode="json")


@setup_group.command(
    "bucket",
    examples=[
        "opentraces setup bucket",
        "opentraces setup bucket --local-only",
        "opentraces setup bucket --repo me/opentraces-bucket",
        "opentraces setup bucket --migrate",
    ],
    see_also=[
        ("opentraces bucket status", "inspect local bucket sync readiness"),
        ("opentraces auth login", "authenticate before using the default HF target"),
        ("opentraces dataset remote create", "attach a publication remote to a dataset"),
    ],
)
@click.option(
    "--remote/--local-only",
    "remote_enabled",
    default=True,
    show_default=True,
    help="Configure private remote bucket sync, or opt out to local-only.",
)
@click.option(
    "--provider",
    type=click.Choice(["huggingface", "fake"]),
    default="huggingface",
    show_default=True,
    help="Remote bucket provider.",
)
@click.option(
    "--repo",
    default=None,
    help=(
        "Private HuggingFace bucket repo id (S3-backed storage). "
        "Defaults to <authenticated-user>/opentraces-bucket."
    ),
)
@click.option(
    "--fake-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Local directory used by the fake bucket remote harness.",
)
@click.option(
    "--sync-policy",
    type=click.Choice(["daemon", "manual"]),
    default="daemon",
    show_default=True,
    help="How the private remote bucket should be kept current.",
)
@click.option("--push-now", is_flag=True, help="Upload the existing local bucket after setup.")
@click.option("--pull-now", is_flag=True, help="Restore the local bucket from the remote after setup.")
@click.option(
    "--enable-security-tool",
    "enable_security_tools",
    multiple=True,
    type=click.Choice(SECURITY_TOOL_NAMES),
    help="Enable a named security tool before configuring or syncing the bucket.",
)
@click.option(
    "--disable-security-tool",
    "disable_security_tools",
    multiple=True,
    type=click.Choice(SECURITY_TOOL_NAMES),
    help="Disable a named security tool before configuring or syncing the bucket.",
)
@click.option(
    "--no-security-prompt",
    is_flag=True,
    help="Skip the interactive recommended security-tool prompt.",
)
@click.option(
    "--migrate",
    "migrate",
    is_flag=True,
    help=(
        "Migrate an existing pre-plan-080 bucket layout to the v2 layout "
        "(write-new-and-swap, atomic). Skips other setup steps when set."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def setup_bucket_cmd(
    remote_enabled: bool,
    provider: str,
    repo: str | None,
    fake_root: Path | None,
    sync_policy: str,
    push_now: bool,
    pull_now: bool,
    enable_security_tools: tuple[str, ...],
    disable_security_tools: tuple[str, ...],
    no_security_prompt: bool,
    migrate: bool,
    as_json: bool,
) -> None:
    """Configure the private bucket sync target.

    By default this configures a private HuggingFace bucket remote with a local
    cache; use ``--local-only`` to opt out. Dataset remotes are attached later
    with ``opentraces dataset remote ...`` and are not created here.

    Pass ``--migrate`` to upgrade an existing pre-plan-080 bucket layout
    (Plan 079 ``bucket/contexts/v1/...`` or earlier
    ``bucket/events/trail/v1/...``) to the plan-080 v2 layout. The migration
    uses write-new-and-swap (writes to ``bucket.v2/``, verifies, then
    atomically swaps) per plan 080 §15(a).
    """

    if migrate:
        _handle_bucket_migrate(as_json=as_json)
        return

    cfg = _cli.load_config()
    security_changes: dict[str, list[str]] = {"enabled": [], "disabled": []}
    remote_sync: dict[str, object] | None = None
    from ..core.bucket_remote import BucketRemoteError

    try:
        security_changes = apply_security_tool_flag_changes(
            cfg,
            enable=enable_security_tools,
            disable=disable_security_tools,
        )
        if security_changes["enabled"] or security_changes["disabled"]:
            _cli.save_config(cfg)
        if push_now and pull_now:
            raise ValueError("--push-now and --pull-now are mutually exclusive")
        if not remote_enabled and (push_now or pull_now):
            raise ValueError("--push-now/--pull-now require remote bucket setup")
        if not remote_enabled:
            bucket = _configure_bucket_local(cfg)
        else:
            if fake_root is not None:
                provider = "fake"
            username = None
            if provider == "huggingface":
                identity = _cli._auth_identity(cfg.hf_token) if cfg.hf_token else None
                if identity is None:
                    raise ValueError(
                        "not authenticated; run 'opentraces auth login' before "
                        "setting up a private HuggingFace bucket"
                    )
                username = str(identity.get("name")) if identity.get("name") else None
            if (
                provider == "huggingface"
                and not no_security_prompt
                and not enable_security_tools
                and not disable_security_tools
            ):
                if not as_json and _cli._is_interactive_terminal():
                    security_changes["enabled"].extend(
                        _prompt_bucket_security_policy(cfg)
                    )
                elif not enabled_security_tool_names(cfg):
                    # Non-interactive / --json: never configure a remote-syncing
                    # private bucket with zero redaction. Apply the safe
                    # 'recommended' baseline the interactive default would pick.
                    default_changes = apply_bucket_security_policy(cfg, "recommended")
                    _cli.save_config(cfg)
                    security_changes["enabled"].extend(default_changes["enabled"])
                    security_changes["disabled"].extend(default_changes["disabled"])
            bucket = _configure_bucket_remote(
                cfg,
                provider=provider,
                repo=repo,
                username=username,
                fake_root=fake_root,
                sync_policy=sync_policy,
            )
            if push_now or pull_now:
                from ..core.bucket_remote import remote_pull, remote_push

                remote_sync = (
                    remote_push(force=True) if push_now else remote_pull(force=True)
                )
    except (BucketRemoteError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)

    payload = {
        "status": "ok",
        "bucket": bucket,
        "security_tools": security_tool_state_payload(cfg),
    }
    if security_changes["enabled"] or security_changes["disabled"]:
        payload["security_tool_changes"] = security_changes
    if remote_sync is not None:
        payload["remote_sync"] = remote_sync
    if as_json:
        click.echo(_dump_json(payload))
        return
    if bucket["storage"] == "remote":
        provider_label = "HuggingFace" if bucket["remote"]["provider"] == "huggingface" else bucket["remote"]["provider"]
        _cli.human_echo(f"Private {provider_label} bucket remote: {bucket['remote']['url']}")
        enabled_tools = enabled_security_tool_names(cfg)
        _cli.human_echo(
            "Security tools enabled: "
            + (", ".join(enabled_tools) if enabled_tools else "none")
        )
        _cli.human_echo("Dataset remotes remain explicit: opentraces dataset remote create ...")
        if remote_sync is not None:
            _cli.human_echo(f"Bucket remote sync: {remote_sync.get('state')}")
    else:
        _cli.human_echo("Private bucket: local-only")


def _detect_bucket_layout() -> str:
    """Detect the on-disk bucket layout version.

    Returns one of:
      * ``"v2"``        — plan-080 layout already in place
                          (``bucket/traces/v1/`` or
                          ``bucket/events/v1/batches/``).
      * ``"v1_plan79"`` — plan-079 layout (``bucket/contexts/v1/...``).
      * ``"v1_pre79"``  — earlier layout
                          (``bucket/events/trail/v1/...`` or
                          ``bucket/objects/traces/v1/``).
      * ``"empty"``     — no bucket present yet (nothing to migrate).
    """
    from ..core import paths as _paths

    root = _paths.bucket_dir()
    if not root.exists():
        return "empty"
    if (root / "traces" / "v1").exists() or (root / "events" / "v1" / "batches").exists():
        return "v2"
    if (root / "contexts" / "v1").exists():
        return "v1_plan79"
    if (root / "events" / "trail" / "v1").exists() or (root / "objects" / "traces" / "v1").exists():
        return "v1_pre79"
    return "empty"


def _handle_bucket_migrate(*, as_json: bool) -> None:
    """Migrate an existing bucket to the plan-080 v2 layout.

    Strategy (plan 080 §15(a) lean): write-new-and-swap.

    1. Detect the current on-disk layout.
    2. If already v2 or empty: report and exit ok.
    3. Otherwise, call the bucket_store migrator (B1 stub if not yet
       implemented) which writes the new layout to ``bucket.v2/``,
       verifies consistency, then atomically swaps it in.
    4. Emit a structured envelope with from/to layouts and counts.
    """
    from ..core import paths as _paths

    from_layout = _detect_bucket_layout()
    to_layout = "v2"

    bucket_root = _paths.bucket_dir()
    bucket_v2 = bucket_root.parent / (bucket_root.name + ".v2")

    payload: dict[str, object] = {
        "from_layout": from_layout,
        "to_layout": to_layout,
        "bucket_root": str(bucket_root),
        "bucket_v2_path": str(bucket_v2),
        "traces_migrated": 0,
        "blobs_migrated": 0,
        "status": "noop",
    }

    if from_layout in ("empty", "v2"):
        payload["status"] = "ok" if from_layout == "v2" else "noop"
        if as_json:
            click.echo(_dump_json({"status": "ok", "migrate": payload}))
            return
        if from_layout == "v2":
            _cli.human_echo("Bucket already on plan-080 v2 layout; nothing to migrate.")
        else:
            _cli.human_echo("No bucket present yet; nothing to migrate.")
        return

    # B1 owns the actual migration body in core/bucket_store.
    try:
        from ..core.bucket_store import migrate_bucket_to_v2  # type: ignore[attr-defined]
    except ImportError as exc:
        msg = (
            "Phase B Track B1 stub: migrate_bucket_to_v2 not yet implemented "
            f"({exc})"
        )
        payload["status"] = "stub_missing"
        payload["error"] = msg
        if as_json:
            click.echo(_dump_json({"status": "error", "migrate": payload}))
        else:
            click.echo(msg, err=True)
        sys.exit(3)

    try:
        result = migrate_bucket_to_v2(
            bucket_root=bucket_root,
            bucket_v2_path=bucket_v2,
            from_layout=from_layout,
        )
    except NotImplementedError as exc:
        msg = f"Phase B Track B1 stub: migrate_bucket_to_v2 not yet implemented ({exc})"
        payload["status"] = "stub_missing"
        payload["error"] = msg
        if as_json:
            click.echo(_dump_json({"status": "error", "migrate": payload}))
        else:
            click.echo(msg, err=True)
        sys.exit(3)
    except (OSError, ValueError) as exc:
        payload["status"] = "error"
        payload["error"] = str(exc)
        if as_json:
            click.echo(_dump_json({"status": "error", "migrate": payload}))
        else:
            click.echo(f"bucket migrate failed: {exc}", err=True)
        sys.exit(3)

    payload.update(
        {
            "traces_migrated": int(result.get("traces_migrated", 0) or 0),
            "blobs_migrated": int(result.get("blobs_migrated", 0) or 0),
            "status": str(result.get("status", "ok")),
        }
    )
    # Pass through any extra fields the migrator surfaced (e.g. verification report).
    for key, value in result.items():
        if key not in payload:
            payload[key] = value
    if as_json:
        click.echo(_dump_json({"status": "ok", "migrate": payload}))
        return
    _cli.human_echo(f"Bucket migrate: {from_layout} -> {to_layout}")
    _cli.human_echo(f"  traces_migrated: {payload['traces_migrated']}")
    _cli.human_echo(f"  blobs_migrated:  {payload['blobs_migrated']}")
    _cli.human_echo(f"  status:          {payload['status']}")


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
    commit after install. Use --remove to uninstall.
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


# ---------------------------------------------------------------------------
# Plan 032 Phase 1 — security-module CLI surfaces.
# ---------------------------------------------------------------------------


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
        _cli.human_echo(f"running: ollama pull {model}")
        result = subprocess.run(["ollama", "pull", model], check=False)
        return result.returncode == 0
    except Exception as exc:
        _cli.human_echo(f"ollama pull failed: {exc}")
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

    is_local = preset_name in _LOCAL_PRESETS
    if is_local:
        ok, names, message = _probe_models(base_url, api_key_env)
        if ok and names:
            _cli.human_echo("")
            _cli.human_echo(f"{_cli._dim('models available:')}")
            for i, n in enumerate(names, 1):
                marker = _cli._ok(" ← suggested") if n == suggested else ""
                _cli.human_echo(f"  {i}. {n}{marker}")
            _cli.human_echo(f"  {len(names) + 1}. {_cli._bold('custom')} {_cli._dim('(type a tag; will pull if ollama)')}")
            _cli.human_echo("")
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
            _cli.human_echo("")
            _cli.human_echo(_cli._err(f"couldn't reach {base_url}") + f": {message}")
            if preset_name == "ollama":
                _cli.human_echo(_cli._dim("is ollama running? try: ollama serve"))
            elif preset_name == "lm-studio":
                _cli.human_echo(_cli._dim("start LM Studio's local server from the app"))
            elif preset_name == "llama-cpp":
                _cli.human_echo(_cli._dim("start llama.cpp: llama-server -m <model.gguf> --port 8080"))
            elif preset_name == "vllm":
                _cli.human_echo(_cli._dim("start vllm: vllm serve <model>"))
            _cli.human_echo("")

    tag = click.prompt("model", default=suggested or "", show_default=bool(suggested))
    return _maybe_pull_and_return(preset_name, tag)


def _maybe_pull_and_return(preset_name: str, tag: str) -> str:
    """For Ollama, offer to pull the tag if it isn't installed yet."""

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
        _cli.human_echo(_cli._dim("skipped; you can pull later with: ollama pull " + tag))
        return tag
    pulled = _maybe_ollama_pull(tag)
    if pulled:
        _cli.human_echo(_cli._ok(f"pulled {tag}"))
    else:
        _cli.human_echo(_cli._err(f"pull failed; saving '{tag}' anyway"))
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

    _cli.human_echo("")
    _cli.print_banner(tagline="configure review LLM")
    _cli.human_echo("")
    _cli.human_echo("This is the third-party LLM used to independently review")
    _cli.human_echo("staged traces before you push. Runs locally or against a")
    _cli.human_echo("hosted API — this config is global, not per-project.")
    _cli.human_echo("")

    for i, (name, base_url, key_env, sample, blurb) in enumerate(_REVIEW_LLM_PRESETS, 1):
        tag = _cli._dim(f"[{blurb}]")
        _cli.human_echo(f"  {i}. {_cli._bold(name):<26} {tag}")
    _cli.human_echo(f"  {len(_REVIEW_LLM_PRESETS) + 1}. {_cli._bold('custom'):<26} {_cli._dim('[enter URL and model manually]')}")
    _cli.human_echo("")

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
    """Configure the optional LLM dataset-row reviewer for publication gates.

    Points opentraces at an OpenAI-compatible, Ollama, Anthropic, or
    fake backend that can review outgoing dataset rows and flag residual
    sensitive content explicit sanitize tools could miss (semantic PII,
    proprietary context, policy concerns).

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
    cfg = _cli.load_config()
    rc = cfg.security.llm_review

    if print_only:
        _cli.emit_json({"status": "ok", "llm_review": _review_llm_config_from_cfg(cfg)})
        return

    if disable:
        rc.enabled = False
        _cli.save_config(cfg)
        _cli.human_echo("llm-review disabled.")
        _cli.emit_json({"status": "ok", "action": "disable",
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
        _cli.human_echo(f"llm-review test: {'ok' if ok else 'failed'} — {message}")
        _cli.emit_json({
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
    _cli.save_config(cfg)

    ok, message = _test_review_llm(
        rc.api_format, rc.base_url, rc.model, rc.api_key_env, rc.timeout,
    )
    _cli.human_echo("")
    tag = _cli._ok("llm-review configured") if ok else _cli._err("llm-review saved but unreachable")
    _cli.print_banner(tagline=tag)
    _cli.human_echo(f"  {_cli._dim('api format:')} {rc.api_format}")
    if rc.api_format != "anthropic":
        _cli.human_echo(f"  {_cli._dim('base url:  ')} {rc.base_url}")
    _cli.human_echo(f"  {_cli._dim('model:     ')} {rc.model}")
    if rc.api_key_env:
        present = "set" if os.environ.get(rc.api_key_env) else _cli._err("NOT SET")
        _cli.human_echo(f"  {_cli._dim('api key:   ')} ${rc.api_key_env} ({present})")
    _cli.human_echo(f"  {_cli._dim('reachable: ')} {message}")
    _cli.human_echo("")
    _cli.human_echo(f"  {_cli._bold('To run:')} opentraces dataset publish <name> --check-only")
    _cli.human_echo(f"  {_cli._dim('scope:')}         dataset publication gates; upload remains explicit")
    _cli.human_echo(f"  {_cli._dim('disable:')}       opentraces setup llm-review --disable")
    _cli.human_echo(f"  {_cli._dim('health check:')}  opentraces doctor")

    _cli.emit_json({
        "status": "ok", "action": "install",
        "llm_review": _review_llm_config_from_cfg(cfg),
        "reachable": ok, "message": message,
    })


from . import doctor_cli  # noqa: F401


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
        _cli.human_hint(f"no matching trace for: {', '.join(unmatched)}")
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
            _cli.human_hint(f"could not persist verdict for {tid}: {exc}")
            continue

        if verdict.get("shareable") == "no" or \
                verdict.get("missed_sensitive_data") == "yes":
            reason = verdict.get("summary") or "flagged by LLM review"
            try:
                state.block_trace(tid, f"llm-review: {reason}")
            except Exception as exc:
                _cli.human_hint(f"could not mark {tid} blocked: {exc}")


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
        ("opentraces dataset publish <name> --check-only", "run publication gates without upload"),
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

    cfg = _cli.load_config()
    rc = cfg.security.llm_review
    eff_api_format = api_format or rc.api_format
    eff_model = model or rc.model
    eff_base_url = base_url if base_url is not None else rc.base_url
    eff_api_key_env = api_key_env if api_key_env is not None else rc.api_key_env
    eff_timeout = rc.timeout

    if not rc.enabled and api_format is None and model is None:
        _cli.human_hint(
            "llm-review is not configured. Run 'opentraces setup llm-review' "
            "once, or pass --api-format/--model explicitly."
        )

    staging = get_project_traces_dir(Path.cwd())
    if not staging.exists():
        _cli.human_echo("No staging directory found. Run opentraces init first.")
        _cli.emit_json(_cli.error_response(
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
        _cli.human_echo(
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
        _cli.emit_json(payload)
        return

    _cli.human_echo(
        f"{_cli._dim(filter_desc + ':')} "
        f"{len(records)}/{total_available} trace(s) selected"
        + (f" (limit {limit})" if limit else "")
    )

    context = ""
    if context_file:
        try:
            context = Path(context_file).read_text()[:10_000]
        except OSError as exc:
            _cli.human_echo(f"Could not read context file: {exc}")
            sys.exit(2)

    if dry_run:
        est = estimate_llm_review(records, api_format=eff_api_format, model=eff_model)
        _cli.human_echo(
            f"Dry run: {est.sessions} sessions, ~{est.chars:,} chars, "
            f"~{est.tokens:,} tokens, ~${est.cost_usd:.4f}."
        )
        _cli.emit_json({
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
        _cli.human_echo(f"[{_counter['i']}/{n}] {trace_id}: {status}")

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
    _cli.emit_json({
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
    _cli.human_echo(
        f"Entity parser installed at {result.path} "
        f"(version {ENTITY_BINARY_VERSION}, {result.source})"
    )
    _cli.emit_json({
        "status": "ok",
        "action": "setup-entity-parser",
        "binary_path": str(result.path),
        "version": ENTITY_BINARY_VERSION,
        "platform": result.platform,
        "source": result.source,
    })


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


# ---------------------------------------------------------------------------
# `ot setup watcher` — install + lifecycle for the background watcher.
#
# The watcher is a system-level service (launchd agent on macOS, systemd
# user timer on Linux). It only ever exists as one global install, so its
# entire surface — install/uninstall plus runtime control — lives under
# the global ``setup`` namespace rather than being split across `setup
# watcher` and a separate top-level `watcher` group.
# ---------------------------------------------------------------------------
