"""``opentraces setup bucket`` — private-bucket sync configuration.

Extracted from the ``installers`` god module (cli/setup decomposition): the
``setup bucket`` command + its local/remote configurators, security-policy
prompt, layout detection and v2 migration helper. Registered on the shared
``setup_group``; imported by ``cli/__init__`` for the decorator-registration
side effect. One-way dep on ``installers`` (setup_group + the shared
``_wizard_confirm``); installers does not import back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from ._options import dump_json as _dump_json
from ._security_flags import (
    BUCKET_SECURITY_POLICIES,
    RECOMMENDED_BUCKET_SECURITY_TOOLS,
    apply_bucket_security_policy,
    security_tool_state_payload,
)
from ..security.config import (
    SECURITY_TOOL_NAMES,
    apply_security_tool_flag_changes,
    enabled_security_tool_names,
    set_security_tools_exact,
)
from .installers import setup_group, _wizard_confirm


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
    hidden=True,
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
