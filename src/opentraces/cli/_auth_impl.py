"""Auth / login implementation helpers.

Extracted from cli/__init__.py (behavior-preserving split).
All symbols are re-exported from opentraces.cli for backward-compat.

All symbols that tests monkeypatch via "opentraces.cli.<name>" (load_config,
_auth_identity, emit_json, error_response, _masked_input, _is_interactive_terminal,
_remote_probe, _remote_create, _classify_hf_repo_error) are accessed via
function-local lazy imports from opentraces.cli so monkeypatching targets remain
correct without any test changes.
"""

from __future__ import annotations

import sys

import click

HF_OAUTH_CLIENT_ID = "dc6cdff4-4835-462b-84fa-6aa3328a26f9"
HF_OAUTH_SCOPES = "openid profile write-repos manage-repos"
HF_DEVICE_CODE_URL = "https://huggingface.co/oauth/device"
HF_TOKEN_URL = "https://huggingface.co/oauth/token"
HF_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _login_impl(token: bool, device_timeout: int | None = None) -> None:
    """Log in to HuggingFace Hub (like gh auth login)."""
    import opentraces.cli as _cli
    from ..core.config import save_credentials, clear_credentials, CREDENTIALS_PATH

    config = _cli.load_config()

    # If user explicitly wants to re-auth (--token), skip the "already logged in" check
    if config.hf_token and not token:
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=config.hf_token)
            user_info = api.whoami()
            username = user_info.get("name", "unknown")
            click.echo(f"Already authenticated as {username}.")
            click.echo("Run 'opentraces auth login --token' to re-authenticate with a different token.")
            _cli.emit_json({
                "status": "ok",
                "authenticated": True,
                "username": username,
                "next_steps": ["Run 'opentraces init' to set up a project"],
                "next_command": "opentraces init",
            })
            return
        except Exception:
            click.echo("Token found but invalid. Re-authenticating...")
            clear_credentials()

    if token:
        if config.hf_token:
            clear_credentials()
        _login_with_token(save_credentials, CREDENTIALS_PATH)
    else:
        _login_with_device_code(
            save_credentials,
            CREDENTIALS_PATH,
            device_timeout=device_timeout,
        )


def _logout_impl() -> None:
    import opentraces.cli as _cli
    from ..core.config import clear_credentials

    # Did we have a token *anywhere* (CLI creds OR huggingface_hub cache)?
    was_authenticated = bool(_cli.load_config().hf_token)
    clear_credentials()

    if was_authenticated:
        click.echo("Logged out. Credentials removed.")
    else:
        click.echo("Not logged in.")

    _cli.emit_json({"status": "ok", "authenticated": False})


def _auth_status_impl() -> None:
    import opentraces.cli as _cli

    cfg = _cli.load_config()
    identity = _cli._auth_identity(cfg.hf_token)
    if identity is None:
        click.echo("Not authenticated.")
        _cli.emit_json({"status": "needs_action", "authenticated": False, "next_command": "opentraces auth login"})
        return

    username = identity.get("name", "unknown")
    click.echo(f"Authenticated as {username}.")
    _cli.emit_json({"status": "ok", "authenticated": True, "username": username})


def _auth_out_of_band_steps() -> list[str]:
    return [
        "Complete browser auth in a normal terminal with: opentraces auth login",
        "For headless shells, run: opentraces auth login --token",
        "Or export HF_TOKEN=hf_... in the environment and rerun: opentraces --json auth whoami",
    ]


def _login_with_device_code(
    save_credentials,
    credentials_path,
    *,
    device_timeout: int | None = None,
) -> None:
    """OAuth device code flow. User authorizes in browser with a short code."""
    import opentraces.cli as _cli
    import time as _time
    import logging

    try:
        import requests
    except ImportError:
        click.echo("'requests' package required for device login. Falling back to token paste.")
        click.echo("Install with: pip install requests")
        click.echo()
        _cli._login_with_token(save_credentials, credentials_path)
        return

    click.echo("Authenticating with HuggingFace Hub...\n")

    # Step 1: Request device code
    try:
        resp = requests.post(HF_DEVICE_CODE_URL, data={
            "client_id": HF_OAUTH_CLIENT_ID,
            "scope": HF_OAUTH_SCOPES,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        click.echo(f"Failed to start device login: {e}")
        click.echo("Falling back to token paste.\n")
        _cli._login_with_token(save_credentials, credentials_path)
        return

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data.get("verification_uri", "https://huggingface.co/device")
    interval = data.get("interval", 5)
    expires_in = data.get("expires_in", 900)

    # Step 2: Show code and try to open browser
    click.echo("  Open this URL in your browser:")
    click.echo(f"    {verification_uri}")
    click.echo()
    click.echo(f"  And enter code: {user_code}")
    click.echo()

    # Try to open browser automatically
    try:
        import webbrowser
        webbrowser.open(verification_uri)
    except Exception as e:
        logging.getLogger(__name__).debug("Could not open browser: %s", e)

    # Step 3: Poll for authorization
    click.echo("  Waiting for authorization...", nl=False)

    wait_seconds = (
        min(expires_in, device_timeout)
        if device_timeout is not None
        else expires_in
    )
    deadline = _time.time() + wait_seconds
    access_token = None

    while _time.time() < deadline:
        _time.sleep(interval)

        try:
            resp = requests.post(HF_TOKEN_URL, data={
                "grant_type": HF_DEVICE_GRANT_TYPE,
                "device_code": device_code,
                "client_id": HF_OAUTH_CLIENT_ID,
            }, timeout=15)

            token_data = resp.json()

            if "access_token" in token_data:
                access_token = token_data["access_token"]
                break
            elif token_data.get("error") == "authorization_pending":
                click.echo(".", nl=False)
                continue
            elif token_data.get("error") == "slow_down":
                interval = min(interval + 2, 15)
                click.echo(".", nl=False)
                continue
            elif token_data.get("error") == "expired_token":
                click.echo("\n  Code expired. Please try again.")
                sys.exit(3)
            else:
                error = token_data.get("error_description", token_data.get("error", "Unknown error"))
                click.echo(f"\n  Authorization failed: {error}")
                sys.exit(3)
        except requests.RequestException:
            click.echo(".", nl=False)
            continue

    if not access_token:
        click.echo("\n  Timed out waiting for authorization.")
        click.echo()
        click.echo("Complete HuggingFace auth outside this agent session:")
        for step in _auth_out_of_band_steps():
            click.echo(f"  - {step}")
        _cli.emit_json({
            "status": "needs_action",
            "authenticated": False,
            "error": {
                "code": "AUTH_TIMEOUT",
                "kind": "auth",
                "message": "Timed out waiting for HuggingFace device authorization.",
                "retryable": True,
            },
            "next_steps": _auth_out_of_band_steps(),
            "next_command": "opentraces auth login",
        })
        sys.exit(3)

    click.echo(" done\n")

    # Step 4: Validate and save
    _cli._validate_and_save(access_token, save_credentials, credentials_path)


def _login_with_token(save_credentials, credentials_path) -> None:
    """Manual token paste flow for CI/headless environments."""
    import opentraces.cli as _cli

    click.echo("Log in with a HuggingFace access token.")
    click.echo("Get your token at: https://huggingface.co/settings/tokens\n")
    token_input = _cli._masked_input("Token: ")

    if not token_input.startswith("hf_"):
        click.echo("Invalid token format (should start with hf_).")
        _cli.emit_json(_cli.error_response("INVALID_TOKEN", "auth", "Token must start with hf_"))
        sys.exit(3)

    _cli._validate_and_save(token_input, save_credentials, credentials_path)


def _validate_and_save(token_value: str, save_credentials, credentials_path) -> None:
    """Validate a token with HF API and save to credentials file."""
    import opentraces.cli as _cli

    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token_value)
        user_info = api.whoami()
        username = user_info.get("name", "unknown")
    except Exception as e:
        click.echo(f"Token validation failed: {e}")
        _cli.emit_json(_cli.error_response("TOKEN_INVALID", "auth", str(e)))
        sys.exit(3)

    save_credentials(token_value)
    click.echo(f"  Authenticated as {username}.")
    click.echo(f"  Token saved to {credentials_path}")
    click.echo("\n  You can now bind dataset remotes with 'opentraces dataset remote ...'.")

    _cli.emit_json({
        "status": "ok",
        "authenticated": True,
        "username": username,
        "credentials_path": str(credentials_path),
        "next_steps": ["Run 'opentraces init' to set up a project"],
        "next_command": "opentraces init",
    })


def _choose_remote_interactively(default_repo: str) -> tuple[str | None, str | None]:
    import asyncio

    return asyncio.run(_choose_remote_interactively_async(default_repo))


def _resolve_username_prefix(name: str, username: str) -> str:
    """If name has no '/', prefix with authenticated username."""
    if "/" not in name:
        return f"{username}/{name}"
    return name


async def _choose_remote_interactively_async(default_repo: str) -> tuple[str | None, str | None]:
    """Select, link, or create a dataset remote.

    Returns (repo_id, visibility) where visibility is "private" or "public".
    Returns (None, None) if the user skips.
    """
    import opentraces.cli as _cli

    _remote_probe = _cli._remote_probe
    _remote_create = _cli._remote_create
    _classify_hf_repo_error = _cli._classify_hf_repo_error
    _is_interactive_terminal = _cli._is_interactive_terminal

    cfg = _cli.load_config()
    identity = _cli._auth_identity(cfg.hf_token)
    if identity is None:
        return default_repo, "private"

    username = identity.get("name", "unknown")

    try:
        from ..publish.huggingface.upload import HFUploader

        uploader = HFUploader(token=cfg.hf_token, repo_id="placeholder")
        user_datasets = uploader.list_user_datasets(username)
    except Exception:
        user_datasets = []

    tagged = [d for d in user_datasets if d.get("tagged")]
    untagged = [d for d in user_datasets if not d.get("tagged")]

    default_name = default_repo.split("/")[-1] if "/" in default_repo else default_repo

    if _is_interactive_terminal():
        try:
            from pyclack.prompts import confirm, select, text
            from pyclack.core import Option

            options: list = []
            for ds in tagged:
                vis = "public ⚠" if not ds.get("private", True) else "private"
                options.append(Option(value=ds["id"], label=f"{ds['id']} ({vis})", hint="opentraces"))
            for ds in untagged:
                vis = "public ⚠" if not ds.get("private", True) else "private"
                options.append(Option(value=ds["id"], label=f"{ds['id']} ({vis})", hint="other"))
            options.append(Option(value="__link__", label="Enter repo name..."))
            options.append(Option(value="__later__", label="Skip for now"))

            choice = await select("Choose a dataset remote", options)

            if choice == "__later__":
                return None, None

            if choice == "__link__":
                typed = await text(
                    f"Repo name (e.g. {username}/{default_name} or owner/name)",
                    placeholder=f"{username}/{default_name}",
                    default_value=f"{username}/{default_name}",
                )
                repo_id = _resolve_username_prefix((typed or "").strip(), username)

                # Probe HF: exists -> attach; missing -> offer to create.
                try:
                    probed = _remote_probe(repo_id, cfg.hf_token)
                except Exception:
                    probed = None

                if probed is not None:
                    canonical = probed.get("id") or repo_id
                    vis = "private" if probed.get("private") else "public"
                    click.echo(f"  Connecting to existing {canonical} ({vis}).")
                    return canonical, vis

                should_create = await confirm(
                    f"{repo_id} doesn't exist yet. Create it?",
                    initial_value=True,
                    active="Create",
                    inactive="Cancel",
                )
                if not should_create:
                    return None, None

                visibility = await select(
                    "Visibility",
                    [
                        Option(value="private", label="Private", hint="only you can see this dataset"),
                        Option(value="public", label="Public", hint="visible to everyone"),
                    ],
                    initial_value="private",
                )

                try:
                    created = _remote_create(repo_id, visibility == "private", cfg.hf_token)
                    if created:
                        click.echo(f"  Created {repo_id} on HuggingFace.")
                    else:
                        click.echo(f"  {repo_id} already exists, connecting to it.")
                except Exception as e:
                    code, kind, message, hint = _classify_hf_repo_error(e, repo_id)
                    click.echo(f"  {message}")
                    if hint:
                        click.echo(f"    hint: {hint}")
                return repo_id, visibility

            # Existing repo selected (tagged or untagged): inherit visibility
            selected_ds = next((ds for ds in user_datasets if ds["id"] == choice), None)
            vis = "public" if selected_ds and not selected_ds.get("private", True) else "private"
            return choice, vis

        except ImportError:
            pass

    # Fallback: plain click prompts
    if user_datasets:
        click.echo("Your HuggingFace datasets:")
        for i, ds in enumerate(user_datasets, start=1):
            vis = "public ⚠" if not ds.get("private", True) else "private"
            badge = " [opentraces]" if ds.get("tagged") else ""
            click.echo(f"  {i}. {ds['id']} ({vis}){badge}")
        click.echo(f"  {len(user_datasets) + 1}. Enter repo name")
        click.echo(f"  {len(user_datasets) + 2}. Skip for now")
        choice_num = click.prompt("Choose", type=int, default=len(user_datasets) + 1)
        if choice_num <= len(user_datasets):
            selected_ds = user_datasets[choice_num - 1]
            vis = "public" if not selected_ds.get("private", True) else "private"
            return selected_ds["id"], vis
        if choice_num == len(user_datasets) + 2:
            return None, None

    # Manual-entry (or no datasets at all): probe HF, attach or create.
    typed = click.prompt(
        f"Repo name (e.g. {username}/{default_name} or owner/name)",
        default=f"{username}/{default_name}",
    )
    repo_id = _resolve_username_prefix((typed or "").strip(), username)
    try:
        probed = _remote_probe(repo_id, cfg.hf_token)
    except Exception:
        probed = None
    if probed is not None:
        canonical = probed.get("id") or repo_id
        vis = "private" if probed.get("private") else "public"
        click.echo(f"  Connecting to existing {canonical} ({vis}).")
        return canonical, vis
    if not click.confirm(f"{repo_id} doesn't exist yet. Create it?", default=True):
        return None, None
    visibility = click.prompt("Visibility", type=click.Choice(["private", "public"]), default="private")
    try:
        _remote_create(repo_id, visibility == "private", cfg.hf_token)
        click.echo(f"  Created {repo_id} on HuggingFace.")
    except Exception as e:
        code, kind, message, hint = _classify_hf_repo_error(e, repo_id)
        click.echo(f"  {message}")
        if hint:
            click.echo(f"    hint: {hint}")
    return repo_id, visibility
