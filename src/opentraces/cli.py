"""CLI entry point for opentraces.

Every command emits structured JSON with next_steps and next_command fields.
Designed to be driven by Claude Code via bundled SKILL.md.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys

import click

logger = logging.getLogger(__name__)

from pathlib import Path

from . import __version__
from .config import auth_identity, load_config, load_project_config, save_config
from .workflow import (
    DEFAULT_AGENT,
    DEFAULT_PUSH_POLICY,
    DEFAULT_REMOTE_NAME,
    DEFAULT_REVIEW_POLICY,
    SUPPORTED_AGENTS,
    normalize_agents,
    normalize_push_policy,
    normalize_review_policy,
    resolve_visible_stage,
    stage_label,
)

SENTINEL = "---OPENTRACES_JSON---"

# Global JSON mode flag, set by --json on the root group.
_json_mode = False


# -- Grouped help formatting --------------------------------------------------

COMMAND_SECTIONS = [
    ("Getting Started", ["login", "init", "status"]),
    ("Import", ["import-hf"]),
    ("Review & Publish", ["session", "commit", "push", "log"]),
    ("Inspect", ["stats", "web", "tui"]),
    ("Settings", ["auth", "config", "remote", "whoami", "logout", "remove", "upgrade"]),
    ("Integrations", ["hooks"]),
]


class GroupedGroup(click.Group):
    """Click group that renders commands in named sections."""

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        placed = set()
        for section_name, cmd_names in COMMAND_SECTIONS:
            rows: list[tuple[str, str]] = []
            for name in cmd_names:
                cmd = self.commands.get(name)
                if cmd is None or cmd.hidden:
                    continue
                help_text = cmd.get_short_help_str(limit=formatter.width)
                rows.append((name, help_text))
                placed.add(name)
            if rows:
                with formatter.section(section_name):
                    formatter.write_dl(rows)

        # Anything not in a section goes under "Other"
        other: list[tuple[str, str]] = []
        for name in self.list_commands(ctx):
            if name in placed:
                continue
            cmd = self.commands.get(name)
            if cmd is None or cmd.hidden:
                continue
            help_text = cmd.get_short_help_str(limit=formatter.width)
            other.append((name, help_text))
        if other:
            with formatter.section("Other"):
                formatter.write_dl(other)


def emit_json(data: dict) -> None:
    """Emit structured JSON after the sentinel for agent-native parsing."""
    click.echo(f"\n{SENTINEL}")
    click.echo(json.dumps(data, indent=2))


def human_echo(message: str = "", **kwargs) -> None:
    """Echo human-readable text, suppressed in --json mode."""
    if not _json_mode:
        click.echo(message, **kwargs)


def human_hint(hint: str | None) -> None:
    """Echo a Hint: line to human output when a hint is available."""
    if hint and not _json_mode:
        click.echo(f"Hint: {hint}")


def error_response(code: str, kind: str, message: str, hint: str | None = None, retryable: bool = False) -> dict[str, object]:
    return {
        "status": "error",
        "error": {
            "code": code,
            "kind": kind,
            "message": message,
            "hint": hint,
            "retryable": retryable,
        },
    }


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _masked_input(prompt: str = "Token: ") -> str:
    """Read input showing * for each character typed."""
    import tty
    import termios

    if not sys.stdin.isatty():
        return input(prompt)

    sys.stderr.write(prompt)
    sys.stderr.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    chars = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch in ("\x7f", "\x08"):  # backspace
                if chars:
                    chars.pop()
                    sys.stderr.write("\b \b")
                    sys.stderr.flush()
            elif ch == "\x03":  # ctrl-c
                raise KeyboardInterrupt
            else:
                chars.append(ch)
                sys.stderr.write("*")
                sys.stderr.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stderr.write("\n")
    return "".join(chars)



_auth_identity = auth_identity


def _default_repo(identity: dict | None) -> str:
    if identity is not None:
        return f"{identity.get('name', 'me')}/{DEFAULT_REMOTE_NAME}"
    return DEFAULT_REMOTE_NAME


def _launch_tui_ui(fullscreen: bool = False) -> None:
    from .config import get_project_staging_dir
    from .clients.tui import OpenTracesApp

    project_staging = get_project_staging_dir(Path.cwd())
    app = OpenTracesApp(staging_dir=project_staging, fullscreen=fullscreen)
    app.run()


def _launch_web_ui(port: int = 5050, open_browser: bool = False) -> None:
    from .config import get_project_staging_dir, get_project_state_path
    from .clients.web_server import create_app

    project_staging = get_project_staging_dir(Path.cwd())
    project_state = get_project_state_path(Path.cwd())
    # Installed wheel: <site-packages>/opentraces/static/viewer
    pkg_path = Path(__file__).parent / "static" / "viewer"
    if pkg_path.exists():
        viewer_dist = pkg_path
    else:
        # Editable install / source tree: web/viewer/dist at repo root
        viewer_dist = Path(__file__).parent.parent.parent / "web" / "viewer" / "dist"
        if not viewer_dist.exists():
            viewer_dist = None

    app = create_app(
        str(project_staging),
        state_path=str(project_state),
        viewer_dist=str(viewer_dist) if viewer_dist else None,
    )
    url = f"http://localhost:{port}"
    click.echo(f"Starting opentraces web inbox at {url}")
    click.echo("Press Ctrl+C to stop.")
    if open_browser:
        _schedule_browser_open(url)
    app.run(host="127.0.0.1", port=port, debug=False)


def _parse_agent_selection(agent_text: str) -> list[str]:
    return normalize_agents([part.strip() for part in agent_text.split(",") if part.strip()])


def _prompt_agents_with_click(default_agents: list[str] | None = None) -> list[str]:
    default_value = ",".join(default_agents or list(SUPPORTED_AGENTS[:1]))
    click.echo("Supported agents:")
    for agent in SUPPORTED_AGENTS:
        click.echo(f"  - {agent}")
    while True:
        agent_text = click.prompt(
            "Agents (comma-separated)",
            default=default_value,
        )
        selected_agents = _parse_agent_selection(agent_text)
        if selected_agents:
            return selected_agents
        click.echo("Select at least one supported agent.")


def _agent_placeholder() -> str:
    return ",".join(SUPPORTED_AGENTS[:2]) or DEFAULT_AGENT


def _schedule_browser_open(url: str) -> None:
    try:
        import threading
        import webbrowser

        timer = threading.Timer(0.6, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    except Exception as e:
        logger.debug("Could not schedule browser open: %s", e)


@click.group(cls=GroupedGroup, invoke_without_command=True)
@click.version_option(version=__version__)
@click.option("--json", "json_mode", is_flag=True, help="Emit only machine-readable JSON output")
@click.pass_context
def main(ctx: click.Context, json_mode: bool) -> None:
    """opentraces - crowdsource agent traces to HuggingFace Hub."""
    global _json_mode
    _json_mode = json_mode
    ctx.ensure_object(dict)
    ctx.obj["json_mode"] = json_mode

    if ctx.invoked_subcommand is not None:
        return

    if os.environ.get("OPENTRACES_NO_TUI") or not _is_interactive_terminal():
        click.echo(ctx.get_help())
        return

    try:
        _launch_tui_ui()
    except ImportError:
        click.echo("TUI dependencies are not installed.")
        click.echo("Install with: pip install opentraces[tui]")
        click.echo("Or run: opentraces web")


HF_OAUTH_CLIENT_ID = "dc6cdff4-4835-462b-84fa-6aa3328a26f9"
HF_OAUTH_SCOPES = "openid profile write-repos manage-repos"
HF_DEVICE_CODE_URL = "https://huggingface.co/oauth/device"
HF_TOKEN_URL = "https://huggingface.co/oauth/token"
HF_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _login_impl(token: bool) -> None:
    """Log in to HuggingFace Hub (like gh auth login)."""
    from .config import save_credentials, clear_credentials, CREDENTIALS_PATH

    config = load_config()

    # If user explicitly wants to re-auth (--token), skip the "already logged in" check
    if config.hf_token and not token:
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=config.hf_token)
            user_info = api.whoami()
            username = user_info.get("name", "unknown")
            click.echo(f"Already authenticated as {username}.")
            click.echo("Run 'opentraces login --token' to re-authenticate with a different token.")
            emit_json({
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
        _login_with_device_code(save_credentials, CREDENTIALS_PATH)


def _logout_impl() -> None:
    from .config import clear_credentials, CREDENTIALS_PATH

    if CREDENTIALS_PATH.exists():
        clear_credentials()
        click.echo("Logged out. Credentials removed.")
    else:
        click.echo("Not logged in (no stored credentials).")

    emit_json({"status": "ok", "authenticated": False})


def _auth_status_impl() -> None:
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token)
    if identity is None:
        click.echo("Not authenticated.")
        emit_json({"status": "needs_action", "authenticated": False, "next_command": "opentraces login"})
        return

    username = identity.get("name", "unknown")
    click.echo(f"Authenticated as {username}.")
    emit_json({"status": "ok", "authenticated": True, "username": username})


@main.command()
@click.option("--token", is_flag=True, help="Paste a personal access token (required for pushing traces)")
def login(token: bool) -> None:
    """Log in to HuggingFace Hub."""
    _login_impl(token)


def _login_with_device_code(save_credentials, credentials_path) -> None:
    """OAuth device code flow. User authorizes in browser with a short code."""
    import time as _time

    try:
        import requests
    except ImportError:
        click.echo("'requests' package required for device login. Falling back to token paste.")
        click.echo("Install with: pip install requests")
        click.echo()
        _login_with_token(save_credentials, credentials_path)
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
        _login_with_token(save_credentials, credentials_path)
        return

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data.get("verification_uri", "https://huggingface.co/device")
    interval = data.get("interval", 5)
    expires_in = data.get("expires_in", 900)

    # Step 2: Show code and try to open browser
    click.echo(f"  Open this URL in your browser:")
    click.echo(f"    {verification_uri}")
    click.echo()
    click.echo(f"  And enter code: {user_code}")
    click.echo()

    # Try to open browser automatically
    try:
        import webbrowser
        webbrowser.open(verification_uri)
    except Exception as e:
        logger.debug("Could not open browser: %s", e)

    # Step 3: Poll for authorization
    click.echo("  Waiting for authorization...", nl=False)

    deadline = _time.time() + expires_in
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
        sys.exit(3)

    click.echo(" done\n")

    # Step 4: Validate and save
    _validate_and_save(access_token, save_credentials, credentials_path)


def _login_with_token(save_credentials, credentials_path) -> None:
    """Manual token paste flow for CI/headless environments."""
    click.echo("Log in with a HuggingFace access token.")
    click.echo("Get your token at: https://huggingface.co/settings/tokens\n")
    token_input = _masked_input("Token: ")

    if not token_input.startswith("hf_"):
        click.echo("Invalid token format (should start with hf_).")
        emit_json(error_response("INVALID_TOKEN", "auth", "Token must start with hf_"))
        sys.exit(3)

    _validate_and_save(token_input, save_credentials, credentials_path)


def _validate_and_save(token_value: str, save_credentials, credentials_path) -> None:
    """Validate a token with HF API and save to credentials file."""
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token_value)
        user_info = api.whoami()
        username = user_info.get("name", "unknown")
    except Exception as e:
        click.echo(f"Token validation failed: {e}")
        emit_json(error_response("TOKEN_INVALID", "auth", str(e)))
        sys.exit(3)

    save_credentials(token_value)
    click.echo(f"  Authenticated as {username}.")
    click.echo(f"  Token saved to {credentials_path}")
    click.echo(f"\n  You can now push traces with 'opentraces push'.")

    emit_json({
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
    """Select an existing dataset remote or create a new one.

    Returns (repo_id, visibility) where visibility is "private" or "public".
    Returns (None, None) if the user skips.
    """
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token)
    if identity is None:
        return default_repo, "private"

    username = identity.get("name", "unknown")

    try:
        from .upload.hf_hub import HFUploader

        uploader = HFUploader(token=cfg.hf_token, repo_id="placeholder")
        existing = uploader.list_opentraces_datasets(username)
    except Exception:
        existing = []

    if _is_interactive_terminal():
        try:
            from pyclack.prompts import select, text
            from pyclack.core import Option

            # Step 1: show existing repos + create new + skip
            options = []
            for ds in existing:
                vis = "public \u26A0" if not ds.get("private", True) else "private"
                options.append(Option(value=ds["id"], label=f"{ds['id']} ({vis})"))
            options.append(Option(value="__new__", label=f"Create new dataset"))
            options.append(Option(value="__later__", label="Skip for now"))

            choice = await select("Choose a dataset remote", options)

            if choice == "__later__":
                return None, None

            if choice == "__new__":
                # Step 2a: visibility (only for new repos)
                visibility = await select(
                    "Visibility",
                    [
                        Option(value="private", label="Private", hint="only you can see this dataset"),
                        Option(value="public", label="Public", hint="visible to everyone"),
                    ],
                    initial_value="private",
                )

                # Step 2b: name (just the repo part, username is auto-prefixed)
                default_name = default_repo.split("/")[-1] if "/" in default_repo else default_repo
                repo_name = await text(
                    f"Dataset name ({username}/...)",
                    placeholder=default_name,
                    default_value=default_name,
                )
                repo_id = _resolve_username_prefix(repo_name, username)
                return repo_id, visibility

            # Existing repo selected: inherit visibility
            selected_ds = next((ds for ds in existing if ds["id"] == choice), None)
            vis = "public" if selected_ds and not selected_ds.get("private", True) else "private"
            return choice, vis

        except ImportError:
            pass

    # Fallback: plain click prompts
    if existing:
        click.echo("Existing opentraces datasets:")
        for i, ds in enumerate(existing, start=1):
            vis = "public \u26A0" if not ds.get("private", True) else "private"
            click.echo(f"  {i}. {ds['id']} ({vis})")
        click.echo(f"  {len(existing) + 1}. Create new")
        click.echo(f"  {len(existing) + 2}. Skip for now")
        choice_num = click.prompt("Choose", type=int, default=len(existing) + 1)
        if choice_num <= len(existing):
            selected_ds = existing[choice_num - 1]
            vis = "public" if not selected_ds.get("private", True) else "private"
            return selected_ds["id"], vis
        if choice_num == len(existing) + 2:
            return None, None

    # New repo flow
    visibility = click.prompt("Visibility", type=click.Choice(["private", "public"]), default="private")
    default_name = default_repo.split("/")[-1] if "/" in default_repo else default_repo
    repo_name = click.prompt(f"Dataset name ({username}/...)", default=default_name)
    repo_id = _resolve_username_prefix(repo_name, username)
    return repo_id, visibility


def _current_project_session_dir(project_dir: Path, cfg=None) -> Path | None:
    """Return the Claude Code session directory for the current repo, if present."""
    from .config import get_projects_path

    if cfg is None:
        cfg = load_config()
    projects_path = get_projects_path(cfg)
    slug = project_dir.resolve().as_posix().replace("/", "-")
    session_dir = projects_path / slug
    return session_dir if session_dir.exists() else None


def _capture_sessions_into_project(session_dir: Path, project_dir: Path, cfg=None) -> tuple[int, int]:
    """Import existing session files into the project's local inbox."""
    from .config import load_project_config, get_project_staging_dir, get_project_state_path
    from .parsers.claude_code import ClaudeCodeParser
    from .pipeline import process_trace
    from .state import StateManager, TraceStatus, ProcessedFile

    if cfg is None:
        cfg = load_config()

    proj_config = load_project_config(project_dir)
    review_policy = normalize_review_policy(proj_config.get("review_policy"))

    staging = get_project_staging_dir(project_dir)
    staging.mkdir(parents=True, exist_ok=True)

    parser = ClaudeCodeParser()

    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    parsed_count = 0
    error_count = 0

    for session_file in sorted(session_dir.glob("*.jsonl")):
        should_process, offset = state.should_reprocess(str(session_file))
        if not should_process:
            continue

        try:
            record = parser.parse_session(session_file, byte_offset=offset)
            if record is None:
                continue

            result = process_trace(record, project_dir, cfg)
            staging_file = staging / f"{result.record.trace_id}.jsonl"
            staging_file.write_text(result.record.to_jsonl_line() + "\n")

            if review_policy == "auto" and not result.needs_review:
                # Auto mode: commit directly for push
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.COMMITTED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
                task_desc = ""
                if result.record.task:
                    task_desc = (result.record.task.description or "")[:80] if hasattr(result.record.task, "description") else ""
                state.create_commit_group(
                    [result.record.trace_id],
                    task_desc or result.record.trace_id[:12],
                )
            else:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.STAGED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )

            stat = session_file.stat()
            state.mark_file_processed(ProcessedFile(
                file_path=str(session_file),
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                last_byte_offset=stat.st_size,
            ))
            parsed_count += 1
        except Exception as e:
            error_count += 1
            click.echo(f"  Error: {session_file.name}: {e}", err=True)

    return parsed_count, error_count


@main.command()
def logout() -> None:
    """Log out from HuggingFace Hub."""
    _logout_impl()


@main.group(invoke_without_command=True)
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Manage HuggingFace authentication."""
    if ctx.invoked_subcommand is None:
        _auth_status_impl()


@auth.command("login")
@click.option("--token", is_flag=True, help="Paste a personal access token (required for pushing traces)")
def auth_login(token: bool) -> None:
    _login_impl(token)


@auth.command("logout")
def auth_logout() -> None:
    _logout_impl()


@auth.command("status")
def auth_status() -> None:
    _auth_status_impl()


@main.command()
def whoami() -> None:
    """Show the active HuggingFace identity."""
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token)
    if identity is None:
        click.echo("Not authenticated.")
        emit_json(error_response("NOT_AUTHENTICATED", "auth", "No active HuggingFace identity"))
        sys.exit(3)

    username = identity.get("name", "unknown")
    click.echo(username)
    emit_json({"status": "ok", "username": username})


@main.group()
def config() -> None:
    """Manage opentraces configuration."""
    pass


@config.command("show")
def config_show() -> None:
    """Display current configuration (redact_strings masked)."""
    cfg = load_config()
    data = cfg.model_dump()
    # Mask redact strings
    if data.get("custom_redact_strings"):
        data["custom_redact_strings"] = ["***" for _ in data["custom_redact_strings"]]
    # Never show token
    if data.get("hf_token"):
        data["hf_token"] = "***"
    click.echo(json.dumps(data, indent=2))


@config.command("set")
@click.option("--project", type=str, help="Project path for per-project config")
@click.option("--exclude", type=str, help="Project path to exclude (appends)")
@click.option("--redact", type=str, help="Custom redaction string (appends)")
@click.option("--pricing-file", type=str, help="Path to custom pricing table")
@click.option("--classifier-sensitivity", type=click.Choice(["low", "medium", "high"]))
def config_set(
    project: str | None,
    exclude: str | None,
    redact: str | None,
    pricing_file: str | None,
    classifier_sensitivity: str | None,
) -> None:
    """Set configuration values. Append-only for --exclude and --redact."""
    cfg = load_config()

    if exclude:
        if exclude not in cfg.excluded_projects:
            cfg.excluded_projects.append(exclude)
        click.echo(f"Excluded project: {exclude}")

    if redact:
        if redact not in cfg.custom_redact_strings:
            cfg.custom_redact_strings.append(redact)
        click.echo(f"Added redaction string")

    if pricing_file:
        cfg.pricing_file = pricing_file
        click.echo(f"Set pricing file: {pricing_file}")

    if classifier_sensitivity:
        cfg.classifier_sensitivity = classifier_sensitivity
        click.echo(f"Set classifier sensitivity: {classifier_sensitivity}")

    save_config(cfg)
    emit_json({
        "status": "ok",
        "next_steps": ["Run 'opentraces discover' to find sessions"],
        "next_command": "opentraces discover",
    })


@main.command()
@click.option("--agent", "agents", multiple=True, type=click.Choice(list(SUPPORTED_AGENTS)), help="Agent runtime to connect")
@click.option("--review-policy", type=click.Choice(["review", "auto"]), default=None, help="Whether safe sessions require review")
@click.option("--push-policy", type=click.Choice(["manual", "auto-push"]), default=None, hidden=True, help="Legacy: derived from review policy")
@click.option(
    "--import-existing/--start-fresh",
    "import_existing",
    default=None,
    help="Whether to import existing Claude Code sessions for this repo during init",
)
@click.option("--mode", type=click.Choice(["auto", "review"]), default=None, hidden=True, help="Legacy alias for --review-policy")
@click.option("--remote", type=str, default=None, help="HF dataset repo (owner/name)")
@click.option("--private/--public", "is_private", default=None, help="Dataset visibility (default: private)")
@click.option("--no-hook", is_flag=True, help="Skip Claude Code hook installation")
def init(
    agents: tuple[str, ...],
    review_policy: str | None,
    push_policy: str | None,
    import_existing: bool | None,
    mode: str | None,
    remote: str | None,
    is_private: bool | None,
    no_hook: bool,
) -> None:
    """Initialize opentraces in the current project directory.

    Sets up the repo-local inbox, agent hooks, policies, and optional remote.
    """
    from .config import load_project_config, save_project_config

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    staging_dir = ot_dir / "staging"
    config_json = ot_dir / "config.json"
    config_yml = ot_dir / "config.yml"

    # Check if already initialized
    if config_json.exists() or config_yml.exists():
        proj_config = load_project_config(project_dir)
        current_remote = proj_config.get("remote", "not set")
        click.echo(
            "Already initialized "
            f"(mode: {proj_config.get('review_policy', 'review')}, remote: {current_remote})"
        )
        click.echo("Run 'opentraces status' to inspect this inbox.")
        emit_json(
            {
                "status": "ok",
                "message": "Already initialized",
                "review_policy": proj_config["review_policy"],
                "push_policy": proj_config["push_policy"],
                "agents": proj_config["agents"],
            }
        )
        return

    # Legacy --mode mapping
    if review_policy is None and mode is not None:
        review_policy = "auto" if mode == "auto" else "review"
    review_policy = normalize_review_policy(review_policy)
    # Push policy is derived from review policy: auto → auto-push, review → manual
    if push_policy is None:
        push_policy = "auto-push" if review_policy == "auto" else "manual"
    push_policy = normalize_push_policy(push_policy)
    selected_agents = normalize_agents(list(agents))

    # Resolve visibility from --private/--public flags
    if is_private is True:
        visibility = "private"
    elif is_private is False:
        visibility = "public"
    else:
        visibility = "private"  # default, may be overridden by interactive selector

    if _is_interactive_terminal() and (not agents or review_policy == DEFAULT_REVIEW_POLICY and remote is None):
        try:
            from pyclack.prompts import confirm, select, text
            from pyclack.core import Option
            import asyncio

            async def _interactive_setup() -> tuple[list[str], str, str | None, str]:
                if len(SUPPORTED_AGENTS) == 1:
                    chosen_agents = list(SUPPORTED_AGENTS)
                    click.echo(f"Supported agent detected: {chosen_agents[0]}")
                else:
                    click.echo("Supported agents:")
                    for agent in SUPPORTED_AGENTS:
                        click.echo(f"  - {agent}")

                    def _validate_agents(value: str) -> str | None:
                        if _parse_agent_selection(value):
                            return None
                        return "Select at least one supported agent"

                    chosen_agents_text = await text(
                        "Which agents should opentraces connect in this project?",
                        placeholder=_agent_placeholder(),
                        default_value=list(SUPPORTED_AGENTS)[0],
                        validate=_validate_agents,
                    )
                    chosen_agents = _parse_agent_selection(chosen_agents_text)
                chosen_review = await select(
                    "Which review policy should this inbox use?",
                    [
                        Option(value="review", label="Review every session", hint="Sessions land in Inbox for you to review"),
                        Option(value="auto", label="Fully automatic", hint="Capture, sanitize, commit, and push automatically"),
                    ],
                    initial_value=review_policy,
                )

                chosen_remote = remote
                chosen_visibility = "private"
                # Remote setup: login if needed, then select
                cfg = load_config()
                if not cfg.hf_token:
                    should_login = await confirm(
                        "Log into HuggingFace now?",
                        initial_value=True,
                        active="Login",
                        inactive="Skip",
                    )
                    if should_login:
                        from .config import save_credentials, CREDENTIALS_PATH

                        _login_with_device_code(save_credentials, CREDENTIALS_PATH)
                identity = _auth_identity(load_config().hf_token)
                if identity:
                    chosen_remote, chosen_visibility = await _choose_remote_interactively_async(_default_repo(identity))
                return normalize_agents(chosen_agents), normalize_review_policy(chosen_review), chosen_remote, chosen_visibility or "private"

            selected_agents, review_policy, remote, visibility = asyncio.run(_interactive_setup())
        except ImportError:
            visibility = "private"
            if not agents:
                selected_agents = list(SUPPORTED_AGENTS) if len(SUPPORTED_AGENTS) == 1 else _prompt_agents_with_click()
            if review_policy == DEFAULT_REVIEW_POLICY:
                review_policy = click.prompt(
                    "Review policy",
                    type=click.Choice(["review", "auto"]),
                    default=DEFAULT_REVIEW_POLICY,
                )
            if remote is None:
                identity = _auth_identity(load_config().hf_token)
                if identity:
                    remote, visibility = _choose_remote_interactively(_default_repo(identity))
                    visibility = visibility or "private"

    ot_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # visibility may be set by interactive selector or --private/--public flags
    if not isinstance(visibility, str) or visibility not in ("private", "public"):
        visibility = "private"

    proj_config: dict = {
        "mode": "auto" if review_policy == "auto" else "review",
        "review_policy": review_policy,
        "push_policy": push_policy,
        "agents": selected_agents,
        "visibility": visibility,
    }
    if remote:
        proj_config["remote"] = remote
    save_project_config(project_dir, proj_config)

    gitignore_path = project_dir / ".gitignore"
    gitignore_line = ".opentraces/staging/"
    if gitignore_path.exists():
        existing_gi = gitignore_path.read_text()
        if gitignore_line not in existing_gi.splitlines():
            with open(gitignore_path, "a") as f:
                if not existing_gi.endswith("\n"):
                    f.write("\n")
                f.write(f"{gitignore_line}\n")
    if gitignore_path.exists():
        existing_gi = gitignore_path.read_text()
        if ".opentraces/config.json" not in existing_gi.splitlines():
            with open(gitignore_path, "a") as f:
                f.write(".opentraces/config.json\n")

    hook_installed = False
    if not no_hook:
        hook_installed = _install_capture_hook(project_dir, selected_agents)

    skill_installed = _install_skill(project_dir, selected_agents)

    cfg = load_config()
    existing_session_dir = _current_project_session_dir(project_dir, cfg=cfg)
    existing_session_files = sorted(existing_session_dir.glob("*.jsonl")) if existing_session_dir else []
    existing_session_count = len(existing_session_files)
    imported_existing = 0
    import_errors = 0
    if existing_session_count and import_existing is None and _is_interactive_terminal():
        try:
            from pyclack.prompts import confirm
            import asyncio

            import_existing = asyncio.run(
                confirm(
                    f"Import {existing_session_count} existing Claude Code session(s) for this repo now?",
                    initial_value=True,
                    active="Import now",
                    inactive="Start fresh",
                )
            )
        except ImportError:
            import_existing = click.confirm(
                f"Import {existing_session_count} existing Claude Code session(s) for this repo now?",
                default=True,
            )

    if existing_session_count and import_existing:
        imported_existing, import_errors = _capture_sessions_into_project(existing_session_dir, project_dir, cfg=cfg)

    click.echo(f"\nInitialized opentraces ({review_policy} policy) in {ot_dir}")
    if remote:
        click.echo(f"  Remote:  {remote}")
    else:
        click.echo(f"  Remote:  not set (will be configured on first push)")
    click.echo(f"  Config:  {config_json}")
    click.echo(f"  Staging: {staging_dir}")
    if hook_installed:
        click.echo(f"  Hook:    .claude/settings.json (SessionEnd)")
    if skill_installed:
        click.echo(f"  Skill:   .agents/skills/opentraces/SKILL.md")
    click.echo(f"  Agents:  {', '.join(selected_agents)}")
    click.echo(f"  Policy:  {review_policy}")
    click.echo(f"  Push:    {push_policy}")
    if existing_session_count:
        click.echo(f"  Existing Claude sessions: {existing_session_count}")
        if imported_existing or import_errors:
            click.echo(f"  Imported existing: {imported_existing} ({import_errors} errors)")
        else:
            click.echo("  Existing sessions were left untouched; new sessions will capture automatically.")
    click.echo("\nRecommended flow:")
    if existing_session_count and imported_existing:
        click.echo("  1. Review the imported inbox with 'opentraces web' or 'opentraces tui'")
    elif existing_session_count:
        click.echo("  1. Decide whether to import past sessions or just start from now on")
        click.echo(f"     Session dir: {existing_session_dir}")
    else:
        click.echo("  1. Start a connected agent session; capture is automatic from now on")
    click.echo("  2. Review and commit inbox traces with 'opentraces commit --all'")
    click.echo("  3. Publish committed traces with 'opentraces push'")

    emit_json({
        "status": "ok",
        "mode": proj_config["mode"],
        "review_policy": review_policy,
        "push_policy": push_policy,
        "remote": remote,
        "agents": selected_agents,
        "hook_installed": hook_installed,
        "skill_installed": skill_installed,
        "existing_session_count": existing_session_count,
        "import_existing": import_existing,
        "imported_existing": imported_existing,
        "import_errors": import_errors,
        "config_path": str(config_json),
        "staging_path": str(staging_dir),
        "next_steps": [
            "Review imported traces with opentraces web" if imported_existing else (
                "Import past sessions or start a connected agent session; future traces will be captured automatically"
                if existing_session_count
                else "Start a connected agent session, traces will be captured automatically"
            ),
        ],
        "next_command": "opentraces web" if imported_existing else "opentraces",
    })


@main.command()
def remove() -> None:
    """Remove opentraces from the current project."""
    import shutil

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"

    removed_hook = _remove_capture_hook(project_dir)
    removed_local = False
    if ot_dir.exists():
        shutil.rmtree(ot_dir)
        removed_local = True

    if removed_local:
        click.echo(f"Removed local inbox: {ot_dir}")
    else:
        click.echo("No local .opentraces directory found.")

    if removed_hook:
        click.echo("Removed Claude Code SessionEnd hook.")

    click.echo("Remote datasets were not changed.")
    emit_json({
        "status": "ok",
        "removed_local": removed_local,
        "removed_hook": removed_hook,
        "remote_changed": False,
        "next_steps": ["Run 'opentraces init' to set this project up again"],
        "next_command": "opentraces init",
    })


def _detect_install_method() -> str:
    """Detect how opentraces was installed: pipx, brew, editable, or pip."""
    import shutil

    pkg_path = Path(__file__).resolve()
    pkg_str = str(pkg_path)

    # Editable / source install: not in site-packages
    if "site-packages" not in pkg_str:
        return "source"

    # Check if installed via brew (macOS Cellar, homebrew, or Linux linuxbrew)
    if "/Cellar/" in pkg_str or "/homebrew/" in pkg_str.lower() or "/linuxbrew/" in pkg_str.lower():
        return "brew"

    # Check if pipx manages this package
    if shutil.which("pipx"):
        pipx_home = os.environ.get("PIPX_HOME", str(Path.home() / ".local" / "pipx"))
        if pipx_home in pkg_str:
            return "pipx"

    # Default: regular pip
    return "pip"


def _run_upgrade_subprocess(cmd: list[str], method: str, timeout: int = 120) -> bool:
    """Run an upgrade subprocess with error handling. Returns True on success."""
    import subprocess

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        human_echo(f"{method} binary not found on PATH.")
        emit_json(error_response("UPGRADE_FAILED", "upgrade", f"{method} not found"))
        sys.exit(4)
    except subprocess.TimeoutExpired:
        human_echo(f"{method} upgrade timed out after {timeout}s.")
        emit_json(error_response("UPGRADE_FAILED", "upgrade", f"{method} timed out"))
        sys.exit(4)

    if result.returncode == 0:
        output = result.stdout.strip()
        human_echo(output if output else "CLI upgraded.")
        return True

    combined = (result.stderr + result.stdout).lower()
    # "already at latest" is not an error
    if "already" in combined and ("latest" in combined or "installed" in combined or "up-to-date" in combined):
        human_echo("Already on the latest version.")
        return True

    human_echo(f"{method} upgrade failed: {result.stderr.strip()}")
    emit_json(error_response("UPGRADE_FAILED", "upgrade", result.stderr.strip()))
    sys.exit(4)


@main.command()
@click.option("--skill-only", is_flag=True, default=False, help="Only update the skill file and hook, skip CLI upgrade")
def upgrade(skill_only: bool) -> None:
    """Upgrade opentraces CLI and refresh the project skill file."""
    current_version = __version__

    if not skill_only:
        method = _detect_install_method()
        human_echo(f"Current version: {current_version}")
        human_echo(f"Install method:  {method}")

        if method == "source":
            human_echo("Source install detected. Pull the latest and run: pip install -e .")
            human_echo("Skipping CLI upgrade, updating skill and hook only.")
        elif method == "brew":
            human_echo("Upgrading via brew...")
            _run_upgrade_subprocess(["brew", "upgrade", "opentraces"], "brew")
        elif method == "pipx":
            human_echo("Upgrading via pipx...")
            _run_upgrade_subprocess(["pipx", "upgrade", "opentraces"], "pipx")
        else:
            human_echo("Upgrading via pip...")
            _run_upgrade_subprocess(
                [sys.executable, "-m", "pip", "install", "--upgrade", "opentraces"], "pip"
            )

    # Refresh skill and hook in current project
    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"

    if not ot_dir.exists():
        if skill_only:
            human_echo("Not an opentraces project. Run 'opentraces init' first.")
            sys.exit(3)
        human_echo("No project found in current directory. Skill refresh skipped.")
        emit_json({
            "status": "ok",
            "cli_upgraded": not skill_only,
            "skill_refreshed": False,
            "next_steps": ["Run 'opentraces init' in your project to set up"],
            "next_command": "opentraces init",
        })
        return

    proj_config = load_project_config(project_dir)
    agents = proj_config.get("agents") or ["claude-code"]

    skill_refreshed = _install_skill(project_dir, agents)
    if not skill_refreshed:
        human_echo("Warning: could not find skill source to install.")

    hook_refreshed = _install_capture_hook(project_dir, agents) if not proj_config.get("no_hook") else False

    human_echo("Project updated." if (skill_refreshed or hook_refreshed) else "Project skill and hook unchanged.")

    emit_json({
        "status": "ok",
        "cli_upgraded": not skill_only,
        "skill_refreshed": skill_refreshed,
        "hook_refreshed": hook_refreshed,
        "next_steps": ["Run 'opentraces context' to check project state"],
        "next_command": "opentraces context",
    })


def _install_capture_hook(project_dir: Path, agents: list[str]) -> bool:
    """Install supported agent hooks for auto-parsing."""
    if "claude-code" not in agents:
        return False

    claude_dir = project_dir / ".claude"
    settings_path = claude_dir / "settings.json"

    hook_entry = {
        "type": "command",
        "command": "opentraces _capture --session-dir \"$CLAUDE_SESSION_DIR\" --project-dir .",
        "timeout": 60,
    }

    try:
        claude_dir.mkdir(parents=True, exist_ok=True)

        settings = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
            except Exception:
                settings = {}

        hooks = settings.setdefault("hooks", {})
        session_end = hooks.setdefault("SessionEnd", [])

        # Check if hook already installed
        for group in session_end:
            for h in group.get("hooks", []):
                if "opentraces" in h.get("command", ""):
                    human_echo("  Hook already installed")
                    return True

        # Add the hook
        session_end.append({"hooks": [hook_entry]})
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        human_echo("  Installed Claude Code SessionEnd hook")
        return True
    except Exception as e:
        human_echo(f"  Could not install hook: {e}")
        human_echo("  Add manually to .claude/settings.json")
        return False


def _remove_capture_hook(project_dir: Path) -> bool:
    """Remove the OpenTraces Claude Code hook if present."""
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return False

    try:
        settings = json.loads(settings_path.read_text())
    except Exception:
        return False

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False

    session_end = hooks.get("SessionEnd")
    if not isinstance(session_end, list):
        return False

    changed = False
    filtered_groups = []
    for group in session_end:
        if not isinstance(group, dict):
            filtered_groups.append(group)
            continue
        group_hooks = group.get("hooks", [])
        if not isinstance(group_hooks, list):
            filtered_groups.append(group)
            continue

        kept_hooks = []
        for hook in group_hooks:
            command = hook.get("command", "") if isinstance(hook, dict) else ""
            if "opentraces _capture" in command:
                changed = True
                continue
            kept_hooks.append(hook)

        if kept_hooks:
            updated_group = dict(group)
            updated_group["hooks"] = kept_hooks
            filtered_groups.append(updated_group)
        elif "hooks" not in group or len(group) > 1:
            updated_group = {k: v for k, v in group.items() if k != "hooks"}
            if updated_group:
                filtered_groups.append(updated_group)

    if not changed:
        return False

    if filtered_groups:
        hooks["SessionEnd"] = filtered_groups
    else:
        hooks.pop("SessionEnd", None)

    if not hooks:
        settings.pop("hooks", None)

    import os as _os
    tmp_path = settings_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(settings, indent=2) + "\n")
    _os.replace(str(tmp_path), str(settings_path))
    return True


# Agent directory mapping: agent name -> skill directory relative to project root
AGENT_SKILL_DIRS = {
    "claude-code": ".claude/skills",
}


def _resolve_skill_source() -> Path | None:
    """Find SKILL.md from installed package or source tree."""
    # Installed package (wheel with force-include)
    pkg_path = Path(__file__).parent / "skill" / "SKILL.md"
    if pkg_path.exists():
        return pkg_path
    # Editable install / source tree: skill/ at repo root
    repo_path = Path(__file__).parent.parent.parent / "skill" / "SKILL.md"
    if repo_path.exists():
        return repo_path
    return None


def _install_skill(project_dir: Path, agents: list[str]) -> bool:
    """Install the opentraces skill into .agents/ and symlink per selected agent."""
    skill_source = _resolve_skill_source()
    if not skill_source:
        return False

    try:
        # 1. Copy into .agents/skills/opentraces/
        agents_skill_dir = project_dir / ".agents" / "skills" / "opentraces"
        agents_skill_dir.mkdir(parents=True, exist_ok=True)
        target = agents_skill_dir / "SKILL.md"
        shutil.copy2(str(skill_source), str(target))

        # 2. Symlink into each selected agent's skill directory
        for agent in agents:
            agent_skills_path = AGENT_SKILL_DIRS.get(agent)
            if not agent_skills_path:
                continue
            agent_skill_dir = project_dir / agent_skills_path / "opentraces"
            agent_skill_dir.mkdir(parents=True, exist_ok=True)
            symlink = agent_skill_dir / "SKILL.md"
            if symlink.exists() or symlink.is_symlink():
                symlink.unlink()
            symlink.symlink_to(os.path.relpath(str(target), str(symlink.parent)))
            human_echo(f"  Linked skill: {agent_skills_path}/opentraces/SKILL.md")

        human_echo(f"  Installed skill: .agents/skills/opentraces/SKILL.md")
        return True
    except Exception as e:
        human_echo(f"  Could not install skill: {e}")
        return False


@main.command("_capture", hidden=True)
@click.option("--session-dir", required=True, type=click.Path(exists=True), help="Path to Claude Code session dir")
@click.option("--project-dir", required=True, type=click.Path(exists=True), help="Path to project root")
def capture(session_dir: str, project_dir: str) -> None:
    """Capture a Claude Code session (hidden, for automation)."""
    session_path = Path(session_dir)
    proj_path = Path(project_dir)

    # Find JSONL files in session dir
    session_files = list(session_path.glob("*.jsonl"))
    if not session_files:
        click.echo("No session files found.", err=True)
        return

    parsed_count, error_count = _capture_sessions_into_project(session_path, proj_path)
    click.echo(f"Captured {parsed_count} sessions ({error_count} errors)", err=True)


@main.command("_assess-remote", hidden=True)
@click.option("--repo", required=True, help="HF dataset repo ID (e.g. user/my-traces)")
@click.option("--judge/--no-judge", default=False, help="Enable LLM judge")
@click.option("--judge-model", default="haiku", type=click.Choice(["haiku", "sonnet", "opus"]))
@click.option("--limit", type=int, default=0, help="Max traces (0=all)")
@click.option("--rewrite-readme/--no-rewrite-readme", default=True,
              help="Rewrite README from scratch rather than updating the auto-managed section only")
def assess_remote(repo: str, judge: bool, judge_model: str, limit: int, rewrite_readme: bool) -> None:
    """Force quality assessment on a remote HF dataset via hf-mount (hidden, for automation).

    Uses hf-mount for lazy shard streaming (no full download). Writes quality.json
    sidecar and, when --rewrite-readme, regenerates the full README from scratch
    rather than patching only the auto-managed stats section.

    Requires hf-mount: curl -fsSL https://raw.githubusercontent.com/huggingface/hf-mount/main/install.sh | sh
    """
    import glob
    import io
    import shutil
    import subprocess
    from datetime import datetime

    from .quality.engine import assess_batch, generate_report
    from .quality.gates import check_gate
    from .quality.summary import build_summary
    from .upload.hf_hub import HFUploader
    from .upload.dataset_card import generate_dataset_card
    from .config import load_config
    from opentraces_schema import TraceRecord

    if not shutil.which("hf-mount"):
        click.echo("Error: hf-mount is not installed.", err=True)
        click.echo("Install: curl -fsSL https://raw.githubusercontent.com/huggingface/hf-mount/main/install.sh | sh", err=True)
        raise SystemExit(1)

    config = load_config()
    token = config.hf_token

    slug = repo.replace("/", "-")
    mount_path = f"/tmp/opentraces-eval-{slug}"
    Path(mount_path).mkdir(parents=True, exist_ok=True)

    click.echo(f"Mounting {repo} at {mount_path}...")
    try:
        result = subprocess.run(
            ["hf-mount", "start", "repo", f"datasets/{repo}", mount_path],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        click.echo("Error: hf-mount timed out after 60s.", err=True)
        raise SystemExit(1)
    if result.returncode != 0:
        click.echo(f"Error: hf-mount failed: {result.stderr.strip()}", err=True)
        raise SystemExit(1)

    try:
        shard_files = sorted(glob.glob(f"{mount_path}/data/traces_*.jsonl"))
        if not shard_files:
            click.echo(f"No shards found in {mount_path}/data/", err=True)
            raise SystemExit(1)

        click.echo(f"Found {len(shard_files)} shard(s), loading traces...")
        traces: list[TraceRecord] = []
        for shard_path in shard_files:
            with open(shard_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        traces.append(TraceRecord.model_validate_json(line))
                    except Exception:
                        continue

        if limit > 0:
            traces = traces[:limit]

        if not traces:
            click.echo("No valid traces found.", err=True)
            raise SystemExit(1)

        click.echo(f"Assessing {len(traces)} traces...")
        batch = assess_batch(traces, enable_judge=judge, judge_model=judge_model)
        gate = check_gate(batch)
        mode = "hybrid" if judge else "deterministic"
        summary = build_summary(batch, gate, mode=mode, judge_model=judge_model if judge else None)

        click.echo(f"\nDataset: {repo}")
        for name, ps in summary.persona_scores.items():
            status_label = "PASS" if ps.average >= 80 else ("WARN" if ps.average >= 60 else "FAIL")
            click.echo(f"  {name}: {ps.average:.1f}%  [{status_label}]")
        click.echo(f"\nOverall utility: {summary.overall_utility:.1f}% | Gate: {'PASS' if summary.gate_passed else 'FAIL'}")

        if token:
            click.echo("\nUploading results...")
            uploader = HFUploader(token=token, repo_id=repo)
            summary_dict = summary.to_dict()

            if uploader.upload_quality_json(summary_dict):
                click.echo("  quality.json uploaded")

            if rewrite_readme:
                # Full rewrite: generate a fresh card ignoring existing content
                new_card = generate_dataset_card(
                    repo_id=repo, traces=traces, existing_card=None,
                    quality_summary=summary_dict,
                )
                commit_msg = "chore: full README rewrite with quality scores"
            else:
                # Patch only the auto-managed section
                try:
                    existing_path = uploader.api.hf_hub_download(
                        repo_id=repo, filename="README.md", repo_type="dataset",
                    )
                    existing_card = Path(existing_path).read_text()
                except Exception:
                    existing_card = None
                new_card = generate_dataset_card(
                    repo_id=repo, traces=traces, existing_card=existing_card,
                    quality_summary=summary_dict,
                )
                commit_msg = "chore: update quality scores"

            uploader.api.upload_file(
                path_or_fileobj=io.BytesIO(new_card.encode("utf-8")),
                path_in_repo="README.md",
                repo_id=repo, repo_type="dataset",
                commit_message=commit_msg,
            )
            click.echo(f"  README.md {'rewritten' if rewrite_readme else 'updated'}")
        else:
            click.echo("\nNo HF token — scores computed but not uploaded. Run 'huggingface-cli login'.")

        # Local report
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_dir = Path(".opentraces/reports")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"assess-remote-{slug}-{ts}.md"
        report_path.write_text(generate_report(batch))
        click.echo(f"\nLocal report: {report_path}")

        emit_json({
            "status": "ok",
            "command": "_assess-remote",
            "repo_id": repo,
            "traces_assessed": len(traces),
            "readme_rewritten": rewrite_readme,
            "report_path": str(report_path),
            "quality_summary": summary.to_dict(),
        })

    finally:
        click.echo(f"Unmounting {mount_path}...")
        subprocess.run(["hf-mount", "stop", mount_path], capture_output=True, timeout=30)


@main.command()
def status() -> None:
    """Show status of the current opentraces project."""
    import time as _time
    from .config import load_project_config, get_project_staging_dir, get_project_state_path
    from .state import StateManager

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"

    if not ot_dir.exists():
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    proj_config = load_project_config(project_dir)
    remote = proj_config.get("remote", None)
    project_name = project_dir.name

    click.echo(f"{project_name} inbox")
    click.echo(f"mode:    {proj_config.get('review_policy', 'review')}")
    click.echo(f"agents:  {', '.join(proj_config['agents'])}")
    visibility = proj_config.get("visibility", "private")
    if remote:
        click.echo(f"remote:  {remote} ({visibility})")
    else:
        click.echo("remote:  not set")
    click.echo()

    staging_dir = get_project_staging_dir(project_dir)
    staged_files = list(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []

    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)
    counts = {stage: 0 for stage in ("inbox", "committed", "pushed", "rejected")}

    if not staged_files:
        click.echo("0 sessions in inbox")
    else:
        click.echo(f"{len(staged_files)} session files tracked\n")

        from opentraces_schema import TraceRecord
        now = _time.time()
        for i, sf in enumerate(sorted(staged_files)):
            is_last = (i == len(staged_files) - 1)
            prefix = "└── " if is_last else "├── "
            try:
                data = sf.read_text().strip()
                record = TraceRecord.model_validate_json(data)
                entry = state.get_trace(record.trace_id)
                visible_stage = resolve_visible_stage(entry.status if entry else None)
                counts[visible_stage] += 1
                # Relative timestamp
                if record.timestamp_end:
                    ts = record.timestamp_end.timestamp()
                    diff_seconds = now - ts
                    if diff_seconds < 3600:
                        rel_time = f"{int(diff_seconds / 60)}m ago"
                    elif diff_seconds < 86400:
                        rel_time = f"{int(diff_seconds / 3600)}h ago"
                    elif diff_seconds < 172800:
                        rel_time = "yesterday"
                    else:
                        rel_time = f"{int(diff_seconds / 86400)}d ago"
                else:
                    rel_time = "unknown"

                task_desc = (record.task.description or "untitled")[:40]
                n_steps = len(record.steps)
                n_tools = sum(len(s.tool_calls) for s in record.steps)
                n_flags = record.security.flags_reviewed or 0
                click.echo(
                    f"{prefix}{stage_label(visible_stage):<10} {rel_time:<12} "
                    f"\"{task_desc}\"  {n_steps} steps  {n_tools} tools  {n_flags} flags"
                )
            except Exception:
                click.echo(f"{prefix}{sf.name}")

    click.echo(
        "\n"
        f"inbox {counts['inbox']}  "
        f"committed {counts['committed']}  "
        f"pushed {counts['pushed']}  "
        f"rejected {counts['rejected']}"
    )

    emit_json({
        "status": "ok",
        "project": project_name,
        "review_policy": proj_config["review_policy"],
        "push_policy": proj_config["push_policy"],
        "agents": proj_config["agents"],
        "remote": remote,
        "counts": counts,
    })


# ---------------------------------------------------------------------------
# session subgroup: CRUD for trace review actions
# ---------------------------------------------------------------------------

@main.group()
def session() -> None:
    """Manage individual trace sessions (list, show, commit, reject, reset, redact, discard)."""
    pass


def _load_project_state():
    """Shared helper: load project-local StateManager and staging dir."""
    from .config import get_project_staging_dir, get_project_state_path
    from .state import StateManager

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    if not ot_dir.exists():
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)
    staging_dir = get_project_staging_dir(project_dir)
    return state, staging_dir


def _load_trace_record(staging_dir: Path, trace_id: str):
    """Load a TraceRecord from staging by trace_id."""
    from opentraces_schema import TraceRecord

    staging_file = staging_dir / f"{trace_id}.jsonl"
    if not staging_file.exists():
        return None, staging_file
    data = staging_file.read_text().strip()
    if not data:
        return None, staging_file
    record = TraceRecord.model_validate_json(data.splitlines()[0])
    return record, staging_file


@session.command("list")
@click.option("--stage", type=click.Choice(["inbox", "committed", "pushed", "rejected"]), default=None, help="Filter by stage")
@click.option("--model", type=str, default=None, help="Filter by model name (substring)")
@click.option("--agent", type=str, default=None, help="Filter by agent name")
@click.option("--limit", type=int, default=50, help="Max sessions to return")
def session_list(stage: str | None, model: str | None, agent: str | None, limit: int) -> None:
    """List trace sessions with optional filters."""
    import time as _time
    from opentraces_schema import TraceRecord

    state, staging_dir = _load_project_state()
    staged_files = sorted(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []

    sessions = []
    now = _time.time()
    for sf in staged_files:
        try:
            data = sf.read_text().strip()
            record = TraceRecord.model_validate_json(data.splitlines()[0])
            entry = state.get_trace(record.trace_id)
            visible_stage = resolve_visible_stage(entry.status if entry else None)

            # Apply filters
            if stage and visible_stage != stage:
                continue
            if agent and record.agent.name != agent:
                continue
            if model and (not record.agent.model or model.lower() not in record.agent.model.lower()):
                continue

            # Relative timestamp
            rel_time = "unknown"
            ts_iso = None
            if record.timestamp_end:
                try:
                    from datetime import datetime
                    ts_str = str(record.timestamp_end)
                    ts_iso = ts_str
                    # Parse ISO string (may be str or datetime)
                    if hasattr(record.timestamp_end, 'timestamp'):
                        ts_epoch = record.timestamp_end.timestamp()
                    else:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        ts_epoch = dt.timestamp()
                    diff_seconds = now - ts_epoch
                    if diff_seconds < 3600:
                        rel_time = f"{int(diff_seconds / 60)}m ago"
                    elif diff_seconds < 86400:
                        rel_time = f"{int(diff_seconds / 3600)}h ago"
                    else:
                        rel_time = f"{int(diff_seconds / 86400)}d ago"
                except (ValueError, TypeError, AttributeError) as e:
                    logger.debug("Could not compute relative time: %s", e)

            sessions.append({
                "trace_id": record.trace_id,
                "task": (record.task.description or "untitled")[:60],
                "agent": record.agent.name,
                "model": record.agent.model or "unknown",
                "stage": visible_stage,
                "step_count": len(record.steps),
                "tool_count": sum(len(s.tool_calls) for s in record.steps),
                "flag_count": record.security.flags_reviewed or 0,
                "timestamp": ts_iso,
                "relative_time": rel_time,
            })

            if len(sessions) >= limit:
                break
        except Exception:
            continue

    for s in sessions:
        human_echo(
            f"{s['stage']:<10} {s['relative_time']:<10} {s['trace_id'][:8]}  "
            f"\"{s['task']}\"  {s['step_count']} steps  {s['flag_count']} flags"
        )

    human_echo(f"\n{len(sessions)} sessions")

    emit_json({
        "status": "ok",
        "sessions": sessions,
        "total": len(sessions),
    })


@session.command("show")
@click.argument("trace_id")
@click.option("--verbose", is_flag=True, default=False, help="Show full step content (default: truncated to 500 chars)")
def session_show(trace_id: str, verbose: bool) -> None:
    """Show full detail for a trace session."""
    state, staging_dir = _load_project_state()
    record, staging_file = _load_trace_record(staging_dir, trace_id)

    if record is None:
        click.echo(f"Trace not found: {trace_id}")
        emit_json(error_response("NOT_FOUND", "session", f"No staging file for {trace_id}"))
        sys.exit(6)

    entry = state.get_trace(trace_id)
    visible_stage = resolve_visible_stage(entry.status if entry else None)

    # Emit the full record as JSON (never truncated)
    record_dict = json.loads(record.model_dump_json())
    record_dict["_stage"] = visible_stage

    human_echo(f"Trace: {trace_id}")
    human_echo(f"Stage: {visible_stage}")
    human_echo(f"Task:  {record.task.description or 'untitled'}")
    human_echo(f"Agent: {record.agent.name} ({record.agent.model or 'unknown'})")
    human_echo(f"Steps: {len(record.steps)}")
    if record.metrics:
        human_echo(f"Cost:  ${record.metrics.estimated_cost_usd:.4f}" if record.metrics.estimated_cost_usd else "")

    _STEP_TRUNCATE = 500
    for i, step in enumerate(record.steps):
        content = step.content or ""
        if not verbose and len(content) > _STEP_TRUNCATE:
            content = content[:_STEP_TRUNCATE] + f"\n[... {len(step.content) - _STEP_TRUNCATE} chars truncated, use --verbose to see full content]"
        human_echo(f"\n--- Step {i} ---")
        human_echo(content)

    emit_json({
        "status": "ok",
        "trace": record_dict,
    })


def _session_commit_impl(trace_id: str) -> None:
    """Commit a single session for push."""
    from .state import TraceStatus

    state, staging_dir = _load_project_state()
    entry = state.get_trace(trace_id)
    if entry is None:
        click.echo(f"Trace not found: {trace_id}")
        emit_json(error_response("NOT_FOUND", "session", f"No trace entry for {trace_id}"))
        sys.exit(6)

    # Build a commit message from the trace task description
    message = trace_id[:12]
    try:
        if entry.file_path:
            from opentraces_schema import TraceRecord
            record = TraceRecord.model_validate_json(Path(entry.file_path).read_text().strip())
            task_desc = (record.task or {}).get("description", "") if isinstance(record.task, dict) else (getattr(record.task, "description", "") if record.task else "")
            if task_desc:
                message = task_desc[:80]
    except Exception:
        pass

    commit_id = state.create_commit_group([trace_id], message)
    human_echo(f"Committed: {trace_id[:8]} (commit {commit_id})")

    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "commit_id": commit_id,
        "stage": "committed",
        "next_steps": ["Run 'opentraces push' to upload"],
        "next_command": "opentraces push",
    })


@session.command("commit")
@click.argument("trace_id")
def session_commit(trace_id: str) -> None:
    """Commit a session for push."""
    _session_commit_impl(trace_id)


@session.command("approve", hidden=True)
@click.argument("trace_id")
def session_approve(trace_id: str) -> None:
    """Backward-compatible alias for session commit."""
    _session_commit_impl(trace_id)


@session.command("reject")
@click.argument("trace_id")
def session_reject(trace_id: str) -> None:
    """Reject a session (kept local only, not pushed)."""
    from .state import TraceStatus

    state, staging_dir = _load_project_state()
    entry = state.get_trace(trace_id)
    if entry is None:
        click.echo(f"Trace not found: {trace_id}")
        emit_json(error_response("NOT_FOUND", "session", f"No trace entry for {trace_id}"))
        sys.exit(6)

    state.set_trace_status(trace_id, TraceStatus.REJECTED)
    human_echo(f"Rejected: {trace_id[:8]}")

    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "stage": "rejected",
    })


@session.command("reset")
@click.argument("trace_id")
def session_reset(trace_id: str) -> None:
    """Reset a session back to Inbox (undo commit or reject)."""
    from .state import TraceStatus

    state, staging_dir = _load_project_state()
    entry = state.get_trace(trace_id)
    if entry is None:
        click.echo(f"Trace not found: {trace_id}")
        emit_json(error_response("NOT_FOUND", "session", f"No trace entry for {trace_id}"))
        sys.exit(6)

    # Only allow reset from APPROVED, REJECTED, or COMMITTED (not UPLOADED)
    resettable = {TraceStatus.APPROVED, TraceStatus.REJECTED, TraceStatus.COMMITTED, TraceStatus.STAGED}
    current = TraceStatus(entry.status) if isinstance(entry.status, str) else entry.status
    if current not in resettable:
        click.echo(f"Cannot reset from {current.value} stage.")
        emit_json(error_response("INVALID_STATE", "session", f"Cannot reset from {current.value}"))
        sys.exit(2)

    state.set_trace_status(trace_id, TraceStatus.STAGED)
    human_echo(f"Reset to inbox: {trace_id[:8]}")

    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "stage": "inbox",
    })


@session.command("redact")
@click.argument("trace_id")
@click.option("--step", "step_index", required=True, type=int, help="Step index to redact")
def session_redact(trace_id: str, step_index: int) -> None:
    """Redact a step's content from a staged trace."""
    import re as _re
    import os
    import tempfile

    if not _re.match(r'^[a-f0-9-]+$', trace_id):
        click.echo("Invalid trace ID format.")
        sys.exit(2)

    state, staging_dir = _load_project_state()
    staging_file = staging_dir / f"{trace_id}.jsonl"
    if not staging_file.exists():
        click.echo(f"Staging file not found for {trace_id}")
        emit_json(error_response("NOT_FOUND", "session", f"No staging file for {trace_id}"))
        sys.exit(6)

    text = staging_file.read_text().strip()
    if not text:
        click.echo("Staging file is empty.")
        sys.exit(5)

    trace_data = json.loads(text.splitlines()[0])
    steps = trace_data.get("steps", [])
    if step_index < 0 or step_index >= len(steps):
        click.echo(f"Step index {step_index} out of range (0-{len(steps) - 1}).")
        emit_json(error_response("OUT_OF_RANGE", "session", f"Step {step_index} out of range"))
        sys.exit(2)

    from .inbox import redact_step
    redact_step(steps[step_index])

    # Atomic write
    new_line = json.dumps(trace_data, ensure_ascii=False)
    fd = tempfile.NamedTemporaryFile(
        mode="w", dir=str(staging_dir), suffix=".jsonl.tmp", delete=False,
    )
    try:
        fd.write(new_line + "\n")
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
        os.replace(fd.name, str(staging_file))
    except BaseException:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise

    human_echo(f"Redacted step {step_index} in {trace_id[:8]}")

    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "step_index": step_index,
        "redacted": True,
    })


@session.command("discard")
@click.argument("trace_id")
@click.option("--yes", "confirmed", is_flag=True, help="Skip confirmation")
def session_discard(trace_id: str, confirmed: bool) -> None:
    """Permanently delete a staged trace."""
    import re as _re

    if not _re.match(r'^[a-f0-9-]+$', trace_id):
        click.echo("Invalid trace ID format.")
        sys.exit(2)

    state, staging_dir = _load_project_state()
    staging_file = staging_dir / f"{trace_id}.jsonl"

    if not staging_file.exists() and state.get_trace(trace_id) is None:
        click.echo(f"Trace not found: {trace_id}")
        emit_json(error_response("NOT_FOUND", "session", f"No trace for {trace_id}"))
        sys.exit(6)

    if not confirmed and _is_interactive_terminal():
        if not click.confirm(f"Permanently delete {trace_id[:8]}?"):
            click.echo("Cancelled.")
            return

    # Delete staging file
    if staging_file.exists():
        staging_file.unlink()

    # Remove from state
    entry = state.get_trace(trace_id)
    if entry is not None:
        # Remove trace from state dict directly and save
        state._state["traces"].pop(trace_id, None)
        state.save()

    human_echo(f"Discarded: {trace_id[:8]}")

    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "discarded": True,
    })


@main.group(invoke_without_command=True)
@click.pass_context
def remote(ctx) -> None:
    """Manage the HF dataset remote."""
    if ctx.invoked_subcommand is None:
        project_dir = Path.cwd()
        proj_config = load_project_config(project_dir)
        remote_name = proj_config.get("remote")
        visibility = proj_config.get("visibility", "private")
        if not remote_name:
            click.echo("No remote configured. Run 'opentraces remote set' to configure.")
            emit_json({"status": "ok", "remote": None})
            return
        click.echo(f"{remote_name} ({visibility})")
        emit_json({"status": "ok", "remote": remote_name, "visibility": visibility})


@remote.command("set")
@click.argument("name", required=False, default=None)
@click.option("--private/--public", "is_private", default=None, help="Dataset visibility")
def remote_set(name: str | None, is_private: bool | None) -> None:
    """Set the dataset remote. Interactive if no arguments given."""
    from .config import load_project_config, save_project_config

    project_dir = Path.cwd()
    proj_config = load_project_config(project_dir)

    if name is not None:
        # Non-interactive: resolve owner prefix if needed
        cfg = load_config()
        identity = _auth_identity(cfg.hf_token)
        username = identity.get("name", "unknown") if identity else "unknown"
        repo_id = _resolve_username_prefix(name, username)

        proj_config["remote"] = repo_id
        if is_private is not None:
            proj_config["visibility"] = "private" if is_private else "public"
        save_project_config(project_dir, proj_config)

        vis = proj_config.get("visibility", "private")
        click.echo(f"Remote set to {repo_id} ({vis})")
        emit_json({"status": "ok", "remote": repo_id, "visibility": vis})
        return

    # Interactive: use shared selector
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token)
    if identity is None:
        click.echo("Not authenticated. Run 'opentraces login' first.")
        sys.exit(3)

    repo_id, visibility = _choose_remote_interactively(_default_repo(identity))
    if repo_id is None:
        click.echo("Remote unchanged.")
        return

    proj_config["remote"] = repo_id
    proj_config["visibility"] = visibility or "private"
    save_project_config(project_dir, proj_config)
    click.echo(f"Remote set to {repo_id} ({visibility})")
    emit_json({"status": "ok", "remote": repo_id, "visibility": visibility})


@remote.command("use", hidden=True)
@click.argument("repo", required=False, default=None)
@click.pass_context
def remote_use(ctx: click.Context, repo: str | None) -> None:
    """Backward-compatible alias for remote set."""
    ctx.invoke(remote_set, name=repo)


@remote.command("remove")
def remote_remove() -> None:
    """Remove the configured remote."""
    from .config import load_project_config, save_project_config

    project_dir = Path.cwd()
    proj_config = load_project_config(project_dir)

    if "remote" not in proj_config:
        click.echo("No remote configured.")
        return

    del proj_config["remote"]
    save_project_config(project_dir, proj_config)
    click.echo("Remote removed.")
    emit_json({"status": "ok", "remote": None})


# ---------------------------------------------------------------------------
# stats and context: aggregate views for agent consumption
# ---------------------------------------------------------------------------

@main.command()
def stats() -> None:
    """Show aggregate statistics for the current project inbox."""
    from .config import get_project_staging_dir, get_project_state_path
    from .state import StateManager
    from opentraces_schema import TraceRecord

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    if not ot_dir.exists():
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    staging_dir = get_project_staging_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    staged_files = sorted(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []

    counts = {stage: 0 for stage in ("inbox", "committed", "pushed", "rejected")}
    models: dict[str, int] = {}
    agents: dict[str, int] = {}
    total_steps = 0
    total_tokens = 0
    total_cost = 0.0
    timestamps = []

    for sf in staged_files:
        try:
            data = sf.read_text().strip()
            record = TraceRecord.model_validate_json(data.splitlines()[0])
            entry = state.get_trace(record.trace_id)
            visible_stage = resolve_visible_stage(entry.status if entry else None)
            counts[visible_stage] += 1

            model_name = record.agent.model or "unknown"
            models[model_name] = models.get(model_name, 0) + 1
            agents[record.agent.name] = agents.get(record.agent.name, 0) + 1

            total_steps += len(record.steps)
            if record.metrics:
                total_tokens += (record.metrics.total_input_tokens or 0) + (record.metrics.total_output_tokens or 0)
                total_cost += record.metrics.estimated_cost_usd or 0.0

            if record.timestamp_start:
                timestamps.append(str(record.timestamp_start) if isinstance(record.timestamp_start, str) else record.timestamp_start.isoformat())
            if record.timestamp_end:
                timestamps.append(str(record.timestamp_end) if isinstance(record.timestamp_end, str) else record.timestamp_end.isoformat())
        except Exception:
            continue

    result = {
        "status": "ok",
        "total_traces": len(staged_files),
        "counts": counts,
        "models": models,
        "agents": agents,
        "total_steps": total_steps,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "date_range": {
            "earliest": min(timestamps) if timestamps else None,
            "latest": max(timestamps) if timestamps else None,
        },
    }

    human_echo(f"Traces: {len(staged_files)}")
    human_echo(f"Steps:  {total_steps}")
    human_echo(f"Tokens: {total_tokens}")
    human_echo(f"Cost:   ${total_cost:.4f}")
    for stage, count in counts.items():
        if count > 0:
            human_echo(f"  {stage}: {count}")
    if models:
        human_echo("Models:")
        for m, c in sorted(models.items(), key=lambda x: -x[1]):
            human_echo(f"  {m}: {c}")

    emit_json(result)


@main.command()
def context() -> None:
    """Show full project context for agent consumption."""
    from .config import get_project_staging_dir, get_project_state_path
    from .state import StateManager
    from opentraces_schema import SCHEMA_VERSION

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    if not ot_dir.exists():
        click.echo("Not an opentraces project.")
        human_hint("Run: opentraces init")
        emit_json(error_response("NOT_INITIALIZED", "project", "No .opentraces directory", "Run: opentraces init"))
        sys.exit(3)

    proj_config = load_project_config(project_dir)
    staging_dir = get_project_staging_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    staged_files = list(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []
    counts = {stage: 0 for stage in ("inbox", "committed", "pushed", "rejected")}
    for sf in staged_files:
        try:
            data = sf.read_text().strip()
            from opentraces_schema import TraceRecord
            record = TraceRecord.model_validate_json(data.splitlines()[0])
            entry = state.get_trace(record.trace_id)
            visible_stage = resolve_visible_stage(entry.status if entry else None)
            counts[visible_stage] += 1
        except Exception:
            counts["inbox"] += 1

    # Auth status
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token)
    authenticated = identity is not None
    username = identity.get("name", "unknown") if identity else None

    # Suggest next action
    if not authenticated:
        suggested_next = "opentraces login"
    elif counts["inbox"] > 0:
        suggested_next = "opentraces session list --stage inbox"
    elif counts["committed"] > 0:
        suggested_next = "opentraces push"
    else:
        suggested_next = "opentraces status"

    result = {
        "status": "ok",
        "project": project_dir.name,
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "config": {
            "review_policy": proj_config.get("review_policy", "review"),
            "push_policy": proj_config.get("push_policy", "manual"),
            "agents": proj_config.get("agents", ["claude-code"]),
            "remote": proj_config.get("remote"),
            "visibility": proj_config.get("visibility", "private"),
        },
        "auth": {
            "authenticated": authenticated,
            "username": username,
        },
        "counts": counts,
        "total_traces": sum(counts.values()),
        "suggested_next": suggested_next,
    }

    human_echo(f"Project:  {project_dir.name}")
    human_echo(f"Remote:   {proj_config.get('remote', 'not set')}")
    human_echo(f"Auth:     {'yes (' + username + ')' if authenticated else 'no'}")
    human_echo(f"Inbox:    {counts['inbox']}  Committed: {counts['committed']}  Pushed: {counts['pushed']}")
    human_echo(f"Next:     {suggested_next}")

    emit_json(result)


@main.command()
def log() -> None:
    """List uploaded traces grouped by date."""
    from .state import StateManager, TraceStatus
    from datetime import datetime

    state = StateManager()
    uploaded = state.get_traces_by_status(TraceStatus.UPLOADED)

    if not uploaded:
        click.echo("No traces have been pushed yet.")
        return

    # Group by date
    by_date: dict[str, int] = {}
    for entry in uploaded:
        if entry.uploaded_at:
            try:
                dt = datetime.fromisoformat(entry.uploaded_at)
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                date_str = "unknown"
        else:
            date_str = datetime.fromtimestamp(entry.created_at).strftime("%Y-%m-%d")
        by_date[date_str] = by_date.get(date_str, 0) + 1

    for date_str in sorted(by_date.keys(), reverse=True):
        count = by_date[date_str]
        click.echo(f"{date_str}  pushed {count} sessions")


@main.command(hidden=True)
def discover() -> None:
    """List available agent sessions across projects."""
    from .config import get_projects_path

    cfg = load_config()
    projects_path = get_projects_path(cfg)

    if not projects_path.exists():
        click.echo(f"No sessions found. Directory does not exist: {projects_path}")
        human_hint("Run Claude Code at least once to generate session logs, or use 'opentraces config set --projects-path' to specify a custom location")
        emit_json(error_response(
            code="NO_SESSIONS_FOUND",
            kind="not_found",
            message=f"{projects_path} not found",
            hint="Run Claude Code at least once to generate session logs, or use 'opentraces config set --projects-path' to specify a custom location",
        ))
        sys.exit(6)

    sessions = []
    for project_dir in sorted(projects_path.iterdir()):
        if not project_dir.is_dir():
            continue
        session_files = list(project_dir.glob("*.jsonl"))
        if session_files:
            sessions.append({
                "project": project_dir.name,
                "path": str(project_dir),
                "session_files": len(session_files),
            })

    if not sessions:
        click.echo("No session files found.")
        human_hint("Run Claude Code to generate session logs")
        emit_json(error_response(
            code="NO_SESSIONS_FOUND",
            kind="not_found",
            message="No .jsonl session files found",
            hint="Run Claude Code to generate session logs",
        ))
        sys.exit(6)

    click.echo(f"Found {len(sessions)} projects with sessions:\n")
    for s in sessions:
        click.echo(f"  {s['project']}: {s['session_files']} session file(s)")

    emit_json({
        "status": "ok",
        "sessions": sessions,
        "total_projects": len(sessions),
        "next_steps": ["Run 'opentraces parse' to parse sessions into enriched JSONL"],
        "next_command": "opentraces parse",
    })


@main.command(hidden=True)
@click.option("--auto", is_flag=True, help="Auto-approve (skip review)")
@click.option("--limit", type=int, default=0, help="Max sessions to parse (0=all)")
def parse(auto: bool, limit: int) -> None:
    """Parse agent sessions into enriched JSONL traces."""
    from .config import get_projects_path, is_project_excluded
    from .parsers.claude_code import ClaudeCodeParser
    from .pipeline import process_trace
    from .state import StateManager, TraceStatus, ProcessedFile, STAGING_DIR

    cfg = load_config()
    projects_path = get_projects_path(cfg)
    parser = ClaudeCodeParser()
    state = StateManager()

    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    parsed_count = 0
    skipped_count = 0
    error_count = 0

    click.echo(f"Scanning sessions in {projects_path}...")

    for session_path in parser.discover_sessions(projects_path):
        if limit > 0 and parsed_count >= limit:
            break

        # Check incremental processing
        should_process, offset = state.should_reprocess(str(session_path))
        if not should_process:
            skipped_count += 1
            continue

        try:
            record = parser.parse_session(session_path, byte_offset=offset)
            if record is None:
                skipped_count += 1
                continue

            # Check project exclusion
            project_dir = session_path.parent
            if is_project_excluded(cfg, str(project_dir)):
                skipped_count += 1
                continue

            result = process_trace(record, project_dir, cfg)

            # Stage the trace
            jsonl_line = result.record.to_jsonl_line()
            staging_file = STAGING_DIR / f"{result.record.trace_id}.jsonl"
            staging_file.write_text(jsonl_line + "\n")

            state.set_trace_status(
                result.record.trace_id,
                TraceStatus.APPROVED if auto else TraceStatus.STAGED,
                session_id=result.record.session_id,
                file_path=str(staging_file),
            )

            # Track processed file
            stat = session_path.stat()
            state.mark_file_processed(ProcessedFile(
                file_path=str(session_path),
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                last_byte_offset=stat.st_size,
            ))

            parsed_count += 1
            click.echo(f"  Parsed: {session_path.name} ({len(result.record.steps)} steps, {sum(len(s.tool_calls) for s in result.record.steps)} tool calls)")

        except Exception as e:
            error_count += 1
            click.echo(f"  Error: {session_path.name}: {e}", err=True)

    click.echo(f"\nDone: {parsed_count} parsed, {skipped_count} skipped, {error_count} errors")
    emit_json({
        "status": "ok",
        "parsed": parsed_count,
        "skipped": skipped_count,
        "errors": error_count,
        "next_steps": [
            "Run 'opentraces review' to review staged traces" if not auto else "Run 'opentraces push' to upload",
        ],
        "next_command": "opentraces review" if not auto else "opentraces push",
    })


@main.command("import-hf")
@click.argument("dataset_id")
@click.option("--parser", "parser_name", required=True, help="Format parser name (e.g. hermes)")
@click.option("--subset", default=None, help="Dataset subset/config name")
@click.option("--split", default="train", help="Dataset split")
@click.option("--limit", type=int, default=0, help="Max rows to import (0=all)")
@click.option("--auto", is_flag=True, help="Auto-commit imported traces (skip review)")
@click.option("--dry-run", is_flag=True, help="Parse and report without writing to staging")
def import_hf(
    dataset_id: str,
    parser_name: str,
    subset: str | None,
    split: str,
    limit: int,
    auto: bool,
    dry_run: bool,
) -> None:
    """Import traces from a HuggingFace dataset."""
    from .config import get_project_staging_dir, get_project_state_path
    from .parsers import get_importers
    from .pipeline import process_imported_trace
    from .state import StateManager, TraceStatus

    # 1. Project guard
    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    if not ot_dir.exists():
        human_echo("Not an opentraces project. Run 'opentraces init' first.")
        emit_json(error_response(
            "NOT_INITIALIZED", "setup", "Not an opentraces project",
            hint="Run 'opentraces init' first",
        ))
        sys.exit(3)

    # 2. Resolve parser
    importers = get_importers()
    if parser_name not in importers:
        available = ", ".join(sorted(importers.keys())) or "(none)"
        human_echo(f"Unknown parser: {parser_name}. Available: {available}")
        emit_json(error_response(
            "UNKNOWN_PARSER", "config",
            f"Unknown parser: {parser_name}",
            hint=f"Available parsers: {available}",
        ))
        sys.exit(2)
    parser = importers[parser_name]()

    # 3. Import datasets library
    try:
        import datasets as ds_lib
        from huggingface_hub import HfApi
    except ImportError:
        human_echo("Missing dependencies. Run: pip install 'opentraces[import]'")
        emit_json(error_response(
            "MISSING_DEPS", "setup",
            "datasets library not installed",
            hint="pip install 'opentraces[import]'",
        ))
        sys.exit(2)

    cfg = load_config()

    # 4. Fetch dataset revision SHA for provenance
    human_echo(f"Fetching dataset info for {dataset_id}...")
    try:
        api = HfApi()
        info = api.dataset_info(dataset_id)
        revision = info.sha or "unknown"
    except Exception as e:
        human_echo(f"Failed to fetch dataset info: {e}")
        emit_json(error_response(
            "HF_API_ERROR", "network",
            f"Failed to fetch dataset info: {e}",
            hint="Check the dataset ID and your network connection",
            retryable=True,
        ))
        sys.exit(1)

    # 5. Load dataset (try datasets library first, fall back to raw JSONL download)
    dataset = None
    human_echo(f"Loading dataset {dataset_id} (subset={subset}, split={split})...")
    try:
        dataset = ds_lib.load_dataset(dataset_id, subset, split=split)
    except Exception as ds_err:
        human_echo(f"  datasets library failed ({type(ds_err).__name__}), trying raw JSONL download...")
        # Fall back to direct file download for datasets with heterogeneous schemas
        try:
            from huggingface_hub import hf_hub_download
            # Guess the file path from subset name
            file_candidates = [
                f"data/{subset}.jsonl" if subset else "data/train.jsonl",
                f"{subset}.jsonl" if subset else "train.jsonl",
                f"data/{split}.jsonl",
            ]
            jsonl_path = None
            for candidate in file_candidates:
                try:
                    jsonl_path = hf_hub_download(dataset_id, candidate, repo_type="dataset")
                    break
                except Exception:
                    continue
            if jsonl_path is None:
                raise RuntimeError(f"Could not find JSONL file for subset={subset}")
            # Load as list of dicts
            rows = []
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            dataset = rows
            human_echo(f"  Loaded {len(rows)} rows from raw JSONL")
        except Exception as fallback_err:
            human_echo(f"Failed to load dataset: {ds_err} (fallback: {fallback_err})")
            emit_json(error_response(
                "DATASET_LOAD_ERROR", "network",
                f"Failed to load dataset: {ds_err}",
                hint="Check dataset ID, subset, and split names",
                retryable=True,
            ))
            sys.exit(1)

    # 6. Build source_info for provenance
    source_info = {
        "dataset_id": dataset_id,
        "revision": revision,
        "subset": subset or "default",
        "split": split,
    }

    # 7. Setup staging
    staging_dir = get_project_staging_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    # 8. Process rows
    parsed_count = 0
    skipped_count = 0
    error_count = 0
    total_redactions = 0
    total_rows = len(dataset)
    rows_to_process = min(total_rows, limit) if limit > 0 else total_rows

    human_echo(f"Processing {rows_to_process} of {total_rows} rows...")

    for i, row in enumerate(dataset):
        if limit > 0 and parsed_count >= limit:
            break

        # Parse row
        try:
            record = parser.map_record(row, i, source_info)
        except Exception as e:
            error_count += 1
            logger.warning("Parse error at row %d: %s", i, e)
            continue

        if record is None:
            skipped_count += 1
            continue

        # Abort if failure rate > 10% (after 10+ rows the parser actually attempted).
        # Skipped rows (e.g. no 'conversations' key) are excluded from the denominator
        # since they represent valid parser decisions, not parser failures.
        total_attempted = parsed_count + error_count
        if total_attempted >= 10 and error_count / total_attempted > 0.10:
            human_echo(
                f"Aborting: error rate {error_count}/{total_attempted} "
                f"({error_count / total_attempted:.0%}) exceeds 10% threshold"
            )
            emit_json(error_response(
                "HIGH_ERROR_RATE", "data",
                f"Error rate {error_count}/{total_attempted} exceeds 10%",
                hint="Check that the dataset matches the parser format",
            ))
            sys.exit(1)

        # Enrich + security scan
        try:
            result = process_imported_trace(record, cfg)
        except Exception as e:
            error_count += 1
            logger.warning("Pipeline error at row %d: %s", i, e)
            continue

        total_redactions += result.redaction_count

        if not dry_run:
            # Write staging file
            staging_file = staging_dir / f"{result.record.trace_id}.jsonl"
            staging_file.write_text(result.record.to_jsonl_line() + "\n")

            # FIX-6: --auto uses COMMITTED (matching _capture_sessions_into_project)
            if auto and not result.needs_review:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.COMMITTED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
                task_desc = record.task.description or record.session_id
                state.create_commit_group(
                    [result.record.trace_id],
                    task_desc[:80] if task_desc else result.record.trace_id[:12],
                )
            else:
                state.set_trace_status(
                    result.record.trace_id,
                    TraceStatus.STAGED,
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )

        parsed_count += 1

    # 9. Summary
    mode = "dry-run" if dry_run else ("auto-committed" if auto else "staged")
    human_echo(
        f"\nDone: {parsed_count} {mode}, {skipped_count} skipped, "
        f"{error_count} errors, {total_redactions} redactions"
    )
    emit_json({
        "status": "ok",
        "dataset": dataset_id,
        "parsed": parsed_count,
        "skipped": skipped_count,
        "errors": error_count,
        "redactions": total_redactions,
        "dry_run": dry_run,
        "next_steps": (
            ["Review with 'opentraces status'"]
            if not auto else ["Push with 'opentraces push'"]
        ) if not dry_run else ["Re-run without --dry-run to stage traces"],
        "next_command": (
            "opentraces status" if not auto else "opentraces push"
        ) if not dry_run else f"opentraces import-hf {dataset_id} --parser {parser_name}",
    })


@main.command()
@click.option("--port", type=int, default=5050, help="Port for the local web inbox")
@click.option("--no-open", is_flag=True, help="Do not open a browser automatically")
def web(port: int, no_open: bool) -> None:
    """Open the browser inbox UI."""
    try:
        _launch_web_ui(port=port, open_browser=_is_interactive_terminal() and not no_open)
    except ImportError:
        click.echo("Flask not installed. Run: pip install opentraces[web]")
        sys.exit(2)


@main.command()
@click.option("--fullscreen", is_flag=True, help="Open directly into fullscreen inspect mode")
def tui(fullscreen: bool) -> None:
    """Open the terminal inbox UI."""
    try:
        _launch_tui_ui(fullscreen=fullscreen)
    except ImportError:
        click.echo("Textual not installed. Run: pip install opentraces[tui]")
        sys.exit(2)


@main.command(hidden=True)
@click.option("--web", is_flag=True, help="Launch local web inbox interface")
@click.option("--port", type=int, default=5050, help="Port for web review server")
@click.option("--tui", is_flag=True, help="Launch TUI inbox interface")
def review(web: bool, port: int, tui: bool) -> None:
    """Backward-compatible alias for the inbox UI."""
    if web:
        _launch_web_ui(port=port)
        return
    if tui or _is_interactive_terminal():
        _launch_tui_ui()
        return

    click.echo("Use 'opentraces tui' for the terminal inbox or 'opentraces web' for the browser UI.")


def _assess_dataset(repo_id: str, judge: bool = False, judge_model: str = "haiku", limit: int = 0) -> None:
    """Assess a full HF dataset and update its quality card.

    Downloads all shards via huggingface_hub (cached locally after first fetch).
    Does not require hf-mount.
    """
    import io
    from datetime import datetime

    from .quality.engine import assess_batch, generate_report
    from .quality.gates import check_gate
    from .quality.summary import build_summary
    from .upload.hf_hub import HFUploader
    from .upload.dataset_card import generate_dataset_card
    from .config import load_config

    config = load_config()
    token = config.hf_token

    uploader = HFUploader(token=token or "", repo_id=repo_id)

    click.echo(f"Fetching traces from {repo_id}...")
    traces = uploader.fetch_all_remote_traces()

    if not traces:
        click.echo("No valid traces found in dataset.")
        emit_json(error_response("NO_TRACES", "assess", "No valid traces in dataset"))
        return

    if limit > 0:
        traces = traces[:limit]

    click.echo(f"Assessing {len(traces)} traces...")
    batch = assess_batch(traces, enable_judge=judge, judge_model=judge_model)
    gate = check_gate(batch)
    mode = "hybrid" if judge else "deterministic"
    summary = build_summary(batch, gate, mode=mode, judge_model=judge_model if judge else None)

    # Display results
    click.echo(f"\nDataset: {repo_id}")
    click.echo(f"Traces assessed: {len(traces)}")
    for name, ps in summary.persona_scores.items():
        status = "PASS" if ps.average >= 80 else ("WARN" if ps.average >= 60 else "FAIL")
        click.echo(f"  {name}: {ps.average:.1f}%  [{status}]  min={ps.min:.1f}% max={ps.max:.1f}%")
    click.echo(f"\nOverall utility: {summary.overall_utility:.1f}%")
    click.echo(f"Gate: {'PASS' if summary.gate_passed else 'FAIL'}")

    # Upload quality.json and update README
    if not token:
        click.echo("\nWarning: No HF token — scores calculated but dataset card not updated.")
        click.echo("Run 'huggingface-cli login' or set HF_TOKEN to enable card updates.")
    else:
        click.echo("\nUpdating dataset card...")
        summary_dict = summary.to_dict()

        if uploader.upload_quality_json(summary_dict):
            click.echo("  quality.json uploaded")
        else:
            click.echo("  Warning: failed to upload quality.json")

        new_card = generate_dataset_card(
            repo_id=repo_id, traces=traces,
            existing_card=None,
            quality_summary=summary_dict,
        )
        try:
            uploader.api.upload_file(
                path_or_fileobj=io.BytesIO(new_card.encode("utf-8")),
                path_in_repo="README.md",
                repo_id=repo_id, repo_type="dataset",
                commit_message="chore: update quality scores",
            )
            click.echo("  README.md updated")
        except Exception as e:
            click.echo(f"  Warning: could not update README.md: {e}")

    # Write local report
    slug = repo_id.replace("/", "-")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = generate_report(batch)
    report_dir = Path(".opentraces/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"assess-dataset-{slug}-{ts}.md"
    report_path.write_text(report)
    click.echo(f"\nLocal report: {report_path}")

    emit_json({
        "status": "ok",
        "command": "assess",
        "mode": "dataset",
        "repo_id": repo_id,
        "traces_assessed": len(traces),
        "report_path": str(report_path),
        "quality_summary": summary.to_dict(),
    })


@main.command()
@click.option("--judge/--no-judge", default=False, help="Enable LLM judge for qualitative scoring")
@click.option("--judge-model", default="haiku", type=click.Choice(["haiku", "sonnet", "opus"]),
              help="Model for LLM judge")
@click.option("--limit", type=int, default=0, help="Max traces to assess (0=all)")
@click.option("--compare-remote", is_flag=True, help="Compare local scores against remote dataset quality.json")
@click.option("--all-staged", is_flag=True, help="Assess all staged traces (default: COMMITTED only)")
@click.option("--dataset", "dataset_repo", type=str, default=None,
              help="Assess a remote HF dataset (e.g. user/my-traces). Downloads shards, updates README and quality.json.")
def assess(judge: bool, judge_model: str, limit: int, compare_remote: bool, all_staged: bool, dataset_repo: str | None) -> None:
    """Run quality assessment on committed traces or a full HF dataset.

    By default, assesses only COMMITTED traces (matching the push population).
    Use --all-staged to assess everything in staging.
    Use --compare-remote to fetch the remote dataset's quality.json and show score deltas.
    Use --dataset user/repo to assess a full HF dataset and update its card.
    """
    from opentraces_schema import TraceRecord
    from .quality.engine import assess_batch, generate_report
    from .quality.gates import check_gate
    from .quality.summary import build_summary, QualitySummary

    # Full dataset assessment via huggingface_hub (no hf-mount required)
    if dataset_repo:
        _assess_dataset(dataset_repo, judge=judge, judge_model=judge_model, limit=limit)
        return

    traces = []

    if all_staged:
        # Legacy behavior: read all staging files
        staging = Path(".opentraces/staging")
        if not staging.exists():
            click.echo("No staged traces found. Run 'opentraces parse' first.")
            emit_json(error_response("NO_TRACES", "assessment", "No staged traces", hint="Run opentraces parse first"))
            return
        for f in sorted(staging.glob("*.jsonl")):
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    traces.append(TraceRecord.model_validate_json(line))
                except Exception:
                    continue
    else:
        # Default: read only COMMITTED traces (matches push population)
        from .state import StateManager
        from .config import get_project_state_path

        project_dir = Path.cwd()
        state_path = get_project_state_path(project_dir)
        state = StateManager(state_path=state_path if state_path.parent.exists() else None)
        committed = state.get_committed_traces()

        if not committed:
            # Fall back to all staged if nothing committed
            staging = Path(".opentraces/staging")
            if staging.exists():
                for f in sorted(staging.glob("*.jsonl")):
                    for line in f.read_text().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            traces.append(TraceRecord.model_validate_json(line))
                        except Exception:
                            continue
                if traces:
                    click.echo("No committed traces found, assessing all staged traces instead.")
        else:
            staging = Path(".opentraces/staging")
            for trace_id, info in committed.items():
                trace_file = staging / f"{trace_id}.jsonl"
                if trace_file.exists():
                    for line in trace_file.read_text().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            traces.append(TraceRecord.model_validate_json(line))
                        except Exception:
                            continue

    if limit > 0:
        traces = traces[:limit]

    if not traces:
        click.echo("No valid traces found.")
        emit_json(error_response("NO_TRACES", "assessment", "No valid traces"))
        return

    click.echo(f"Assessing {len(traces)} traces...")
    if judge:
        click.echo(f"LLM judge enabled (model: {judge_model})")

    batch = assess_batch(traces, enable_judge=judge, judge_model=judge_model)
    gate = check_gate(batch)
    mode = "hybrid" if judge else "deterministic"
    summary = build_summary(batch, gate, mode=mode, judge_model=judge_model if judge else None)

    report = generate_report(batch)

    # Write markdown report
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = Path(".opentraces/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"assess-{ts}.md"
    report_path.write_text(report)

    # Display results with gate status
    click.echo(f"\nReport written to {report_path}")
    click.echo(f"Traces assessed: {len(traces)}")
    click.echo(f"Scoring mode: {summary.scoring_mode}")
    click.echo(f"Scorer version: {summary.scorer_version}")
    click.echo("")

    # Per-persona scores with gate pass/fail
    from .quality.gates import DEFAULT_THRESHOLDS
    threshold_map = {t.persona: t for t in DEFAULT_THRESHOLDS}
    for name, ps in summary.persona_scores.items():
        threshold = threshold_map.get(name)
        gate_str = ""
        if threshold:
            passed = ps.average >= threshold.min_average
            gate_str = f"  {'PASS' if passed else 'FAIL'} (gate: {threshold.min_average}%)"
        click.echo(f"  {name}: avg={ps.average:.1f}% min={ps.min:.1f}% max={ps.max:.1f}%{gate_str}")

    click.echo(f"\nOverall utility: {summary.overall_utility:.1f}%")
    click.echo(f"Gate: {'PASS' if summary.gate_passed else 'FAIL'}")
    if not summary.gate_passed:
        for failure in summary.gate_failures:
            click.echo(f"  - {failure}")

    # Compare with remote if requested
    if compare_remote:
        click.echo("\nFetching remote quality scores...")
        try:
            from .config import load_config, get_project_state_path
            config = load_config()
            project_dir = Path.cwd()
            project_name = project_dir.name
            project_config = config.get_project(project_name)
            repo_id = project_config.remote if project_config and project_config.remote else None

            if not repo_id:
                click.echo("No remote repo configured. Use 'opentraces init' to set one.")
            else:
                from huggingface_hub import HfApi
                api = HfApi()
                try:
                    content = api.hf_hub_download(repo_id=repo_id, filename="quality.json", repo_type="dataset")
                    with open(content) as f:
                        remote_data = json.load(f)
                    remote_summary = QualitySummary.from_dict(remote_data)

                    # Warn if scoring modes differ
                    if remote_summary.scoring_mode != summary.scoring_mode:
                        click.echo(f"  Warning: remote scored with '{remote_summary.scoring_mode}', local with '{summary.scoring_mode}'")

                    click.echo(f"\nRemote scores (assessed {remote_summary.assessed_at}):")
                    click.echo(f"  Traces: {remote_summary.trace_count}")
                    for name, remote_ps in remote_summary.persona_scores.items():
                        local_ps = summary.persona_scores.get(name)
                        if local_ps:
                            delta = local_ps.average - remote_ps.average
                            arrow = "+" if delta > 0 else ""
                            click.echo(f"  {name}: remote={remote_ps.average:.1f}% local={local_ps.average:.1f}% ({arrow}{delta:.1f}%)")
                        else:
                            click.echo(f"  {name}: remote={remote_ps.average:.1f}% (no local score)")
                except Exception as e:
                    click.echo(f"  Could not fetch remote quality.json: {e}")
        except Exception as e:
            click.echo(f"  Error comparing with remote: {e}")

    emit_json({
        "status": "ok",
        "command": "assess",
        "traces_assessed": len(traces),
        "report_path": str(report_path),
        "persona_averages": batch.persona_averages,
        "judge_enabled": judge,
        "gate_passed": summary.gate_passed,
        "quality_summary": summary.to_dict(),
        "next_steps": ["Review the report", "Run opentraces push to upload"],
        "next_command": "opentraces push",
    })


@main.command("commit")
@click.option("-m", "--message", type=str, default=None, help="Commit message")
@click.option("--all", "commit_all", is_flag=True, help="Commit all inbox traces")
def commit_traces(message: str | None, commit_all: bool) -> None:
    """Commit inbox traces for push."""
    from .state import StateManager, TraceStatus
    from .config import get_project_state_path
    from opentraces_schema import TraceRecord

    project_dir = Path.cwd()
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    inbox = state.get_traces_by_status(TraceStatus.STAGED)
    if not inbox:
        click.echo("No inbox traces to commit. Run 'opentraces' or 'opentraces web' to review sessions.")
        emit_json({"status": "ok", "committed": 0, "hint": "Open the inbox to review traces"})
        return

    if commit_all:
        trace_ids = [entry.trace_id for entry in inbox]
    else:
        click.echo(f"{len(inbox)} inbox traces:\n")
        for i, entry in enumerate(inbox):
            desc = "(no description)"
            if entry.file_path:
                try:
                    data = Path(entry.file_path).read_text().strip()
                    record = TraceRecord.model_validate_json(data)
                    desc = (record.task.description or "untitled")[:50]
                except (OSError, ValueError) as e:
                    logger.debug("Could not load trace %s: %s", entry.trace_id, e)
            click.echo(f"  {i+1}. {entry.trace_id[:8]}  {desc}")

        click.echo()
        if click.confirm(f"Commit all {len(inbox)} traces?", default=True):
            trace_ids = [entry.trace_id for entry in inbox]
        else:
            click.echo("Cancelled.")
            return

    # Auto-generate message if not provided
    if message is None:
        descriptions = []
        for entry in inbox:
            if entry.file_path:
                try:
                    data = Path(entry.file_path).read_text().strip()
                    record = TraceRecord.model_validate_json(data)
                    if record.task.description:
                        descriptions.append(record.task.description[:60])
                except (OSError, ValueError) as e:
                    logger.debug("Could not read trace for message: %s", e)
        if descriptions:
            message = "; ".join(descriptions[:3])
            if len(descriptions) > 3:
                message += f" (+{len(descriptions) - 3} more)"
        else:
            message = f"Commit {len(trace_ids)} traces"

    commit_id = state.create_commit_group(trace_ids, message)

    click.echo(f"\nCommitted {len(trace_ids)} traces (commit {commit_id})")
    click.echo(f"  Message: {message}")
    click.echo(f"\nRun 'opentraces push' to upload to HuggingFace Hub.")

    emit_json({
        "status": "ok",
        "commit_id": commit_id,
        "committed": len(trace_ids),
        "message": message,
        "next_steps": ["Run 'opentraces push' to upload committed traces"],
        "next_command": "opentraces push",
    })


def _resolve_repo_id(username: str, repo_flag: str | None = None) -> str:
    """Resolve the HF dataset repo_id using priority chain.

    Priority:
      1. --repo flag (highest)
      2. .opentraces/config.json 'remote' field
      3. Default: {username}/opentraces
    """
    if repo_flag:
        return repo_flag

    from .config import load_project_config
    proj_config = load_project_config(Path.cwd())
    config_remote = proj_config.get("remote")
    if config_remote:
        return config_remote

    return f"{username}/opentraces"


@main.command()
@click.option("--private", is_flag=True, help="Force private visibility (overrides config)")
@click.option("--public", is_flag=True, help="Force public visibility (overrides config)")
@click.option("--publish", is_flag=True, help="Change an existing private dataset to public (no upload)")
@click.option("--gated", is_flag=True, help="Enable gated access (auto-approve) on the dataset")
@click.option("--repo", default=None, help="HF dataset repo (default: username/opentraces)")
@click.option("--assess", "run_assess", is_flag=True, help="Run quality assessment after upload and include scores in dataset card")
def push(private: bool, public: bool, publish: bool, gated: bool, repo: str | None, run_assess: bool) -> None:
    """Upload committed traces to HuggingFace Hub."""
    from .config import load_project_config, save_project_config
    from .state import StateManager, TraceStatus, StagingLock
    from .upload.hf_hub import HFUploader
    from .upload.dataset_card import generate_dataset_card
    from opentraces_schema import TraceRecord

    cfg = load_config()
    if private and public:
        click.echo("Cannot use both --private and --public.")
        sys.exit(2)

    if not cfg.hf_token:
        click.echo("Not authenticated.")
        human_hint("Run: opentraces login")
        emit_json(error_response("NOT_AUTHENTICATED", "auth", "No HF token", "Run: opentraces login"))
        sys.exit(3)

    # Get username from HF (needed for all paths)
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=cfg.hf_token)
        user_info = api.whoami()
        username = user_info.get("name", "unknown")
    except Exception as e:
        click.echo(f"Could not get HF username: {e}")
        sys.exit(4)

    # Resolve repo_id: --repo flag > config remote > interactive selector > default
    repo_id = _resolve_repo_id(username, repo)

    # If no remote was configured, run the shared interactive selector
    proj_config = load_project_config(Path.cwd())
    if not repo and not proj_config.get("remote"):
        click.echo("No remote configured.")
        identity = {"name": username}
        selected_repo, selected_vis = _choose_remote_interactively(f"{username}/{DEFAULT_REMOTE_NAME}")
        if selected_repo:
            repo_id = selected_repo
            proj_config["remote"] = repo_id
            proj_config["visibility"] = selected_vis or "private"
            save_project_config(Path.cwd(), proj_config)
            click.echo(f"Remote set to: {repo_id} ({proj_config['visibility']})\n")
        else:
            click.echo("No remote selected. Cannot push.")
            sys.exit(3)

    # Handle --publish: just change visibility, no upload
    if publish:
        try:
            uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
            uploader.publish_dataset()

            # Save visibility to project config
            try:
                proj_config = load_project_config(Path.cwd())
                proj_config["remote"] = repo_id
                proj_config["visibility"] = "public"
                ot_dir = Path.cwd() / ".opentraces"
                if ot_dir.exists():
                    save_project_config(Path.cwd(), proj_config)
            except OSError as e:
                logger.debug("Could not save visibility config: %s", e)

            click.echo(f"Dataset is now public: https://huggingface.co/datasets/{repo_id}")
            emit_json({
                "status": "ok",
                "repo_url": f"https://huggingface.co/datasets/{repo_id}",
                "visibility": "public",
            })
        except Exception as e:
            click.echo(f"Failed to publish dataset: {e}")
            sys.exit(4)
        return

    # Use project-local state if available, fall back to global
    from .config import get_project_state_path
    proj_state_path = get_project_state_path(Path.cwd())
    state = StateManager(
        state_path=proj_state_path if proj_state_path.parent.exists() else None
    )
    # Get committed traces
    traces_to_upload = state.get_traces_by_status(TraceStatus.COMMITTED)

    if not traces_to_upload:
        # If --gated was passed standalone, apply it even without uploading
        if gated:
            try:
                uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
                uploader.set_gated()
                click.echo(f"Gated access enabled on {repo_id}")
            except Exception as e:
                click.echo(f"Failed to set gated access: {e}")
                sys.exit(4)
            return

        click.echo("No traces ready for upload.")
        emit_json({"status": "ok", "uploaded": 0, "message": "No committed traces ready to upload"})
        return

    # Load trace records from staging files, track which ones loaded successfully
    records = []
    loaded_trace_ids = set()
    for entry in traces_to_upload:
        if entry.file_path:
            staging_file = Path(entry.file_path)
            if staging_file.exists():
                try:
                    data = staging_file.read_text().strip()
                    record = TraceRecord.model_validate_json(data)
                    records.append(record)
                    loaded_trace_ids.add(entry.trace_id)
                except Exception as e:
                    click.echo(f"  Error loading {entry.trace_id}: {e}", err=True)

    if not records:
        click.echo("No valid traces to upload.")
        return

    # Determine visibility: --public/--private flags > project config > global config
    if public:
        is_private = False
    elif private:
        is_private = True
    else:
        proj_vis = proj_config.get("visibility")
        is_private = (proj_vis or cfg.dataset_visibility) == "private"

    visibility_label = "private" if is_private else "public"

    try:
        with StagingLock():
            uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
            try:
                uploader.ensure_repo_exists(private=is_private)
            except Exception as e:
                msg = str(e)
                if "403" in msg or "Forbidden" in msg:
                    click.echo(
                        "Permission denied. Your token does not have write access.\n"
                        "OAuth device tokens have limited permissions on HuggingFace.\n"
                        "Re-authenticate with a personal access token:\n"
                        "  opentraces login --token\n"
                        "Get a token with 'write' scope at https://huggingface.co/settings/tokens"
                    )
                    human_hint("Run: opentraces login --token")
                    emit_json(error_response("PERMISSION_DENIED", "auth", "Token lacks write permissions", "Run: opentraces login --token"))
                    sys.exit(3)
                raise

            # Dedup: skip traces whose content_hash already exists on the remote
            remote_hashes = uploader.fetch_remote_content_hashes()
            if remote_hashes:
                before_count = len(records)
                # Only pair records with the entries that actually loaded successfully.
                # traces_to_upload may include entries whose files failed to read above,
                # so zip(records, traces_to_upload) would silently misalign the pairs.
                loaded_entries = [e for e in traces_to_upload if e.trace_id in loaded_trace_ids]
                duplicate_trace_ids: set[str] = set()
                new_records = []
                for record, entry in zip(records, loaded_entries):
                    if record.compute_content_hash() in remote_hashes:
                        duplicate_trace_ids.add(entry.trace_id)
                    else:
                        new_records.append(record)

                if duplicate_trace_ids:
                    # Mark duplicates as uploaded (they exist on the remote)
                    for entry in traces_to_upload:
                        if entry.trace_id in duplicate_trace_ids:
                            state.set_trace_status(entry.trace_id, TraceStatus.UPLOADED)
                    click.echo(f"Skipped {len(duplicate_trace_ids)} duplicate trace(s) already on remote.")

                if not new_records:
                    click.echo("All traces already exist on remote. Nothing to upload.")
                    emit_json({"status": "ok", "uploaded": 0, "skipped_duplicates": len(duplicate_trace_ids)})
                    return

                records = new_records

            click.echo(f"Uploading {len(records)} traces to {repo_id}...")
            result = uploader.upload_traces(records)

            # Generate and upload dataset card from the full remote dataset
            if result.success:
                try:
                    existing_card = None
                    try:
                        from huggingface_hub import HfApi as _HfApi
                        _api = _HfApi(token=cfg.hf_token)
                        existing_card = _api.hf_hub_download(repo_id, "README.md", repo_type="dataset")
                        existing_card = Path(existing_card).read_text()
                    except Exception as e:
                        logger.debug("Could not fetch existing dataset card: %s", e)
                    # Aggregate stats from ALL remote shards (not just this batch)
                    # so the card reflects the full dataset after each push.
                    all_remote_traces = uploader.fetch_all_remote_traces()
                    card_traces = all_remote_traces if all_remote_traces else records

                    quality_summary = None
                    if run_assess:
                        try:
                            from .quality.engine import assess_batch
                            from .quality.gates import check_gate
                            from .quality.summary import build_summary
                            click.echo(f"  Assessing {len(card_traces)} traces...")
                            batch = assess_batch(card_traces)
                            gate = check_gate(batch)
                            summary = build_summary(batch, gate, mode="deterministic")
                            quality_summary = summary.to_dict()
                            if uploader.upload_quality_json(quality_summary):
                                click.echo(f"  Overall utility: {summary.overall_utility:.1f}% | Gate: {'PASS' if summary.gate_passed else 'FAIL'}")
                            else:
                                click.echo("  Warning: quality.json upload failed -- quality scores excluded from README", err=True)
                                quality_summary = None
                        except Exception as e:
                            click.echo(f"  Warning: quality assessment failed: {e}", err=True)

                    card = generate_dataset_card(repo_id, card_traces, existing_card, quality_summary=quality_summary)
                    import io as _io
                    uploader.api.upload_file(
                        path_or_fileobj=_io.BytesIO(card.encode("utf-8")),
                        path_in_repo="README.md",
                        repo_id=repo_id,
                        repo_type="dataset",
                    )
                except Exception as e:
                    click.echo(f"  Warning: dataset card update failed: {e}", err=True)

            if result.success:
                # Apply gated access if requested
                if gated:
                    try:
                        uploader.set_gated()
                    except Exception as e:
                        click.echo(f"  Warning: failed to set gated access: {e}", err=True)

                # Only mark traces that were actually loaded and uploaded
                for entry in traces_to_upload:
                    if entry.trace_id in loaded_trace_ids:
                        state.set_trace_status(entry.trace_id, TraceStatus.UPLOADED)

                # Print visibility-aware success message
                if is_private:
                    click.echo(f"Pushed {result.trace_count} sessions (private) -- only you can see this dataset")
                    click.echo("  Run 'opentraces push --publish' when ready to share")
                else:
                    click.echo(f"Pushed {result.trace_count} sessions (public) -- visible to everyone")

                # Save remote URL and visibility to project config
                try:
                    proj_config = load_project_config(Path.cwd())
                    proj_config["remote"] = repo_id
                    proj_config["visibility"] = visibility_label
                    ot_dir = Path.cwd() / ".opentraces"
                    if ot_dir.exists():
                        save_project_config(Path.cwd(), proj_config)
                except OSError as e:
                    logger.debug("Could not save post-upload config: %s", e)

                emit_json({
                    "status": "ok",
                    "uploaded": result.trace_count,
                    "shard": result.shard_name,
                    "repo_url": result.repo_url,
                    "visibility": visibility_label,
                    "next_steps": [f"View at https://huggingface.co/datasets/{repo_id}"],
                })
            else:
                for entry in traces_to_upload:
                    state.set_trace_status(entry.trace_id, TraceStatus.FAILED, error=result.error)
                click.echo(f"Upload failed: {result.error}")
                emit_json(error_response("UPLOAD_FAILED", "network", str(result.error), retryable=True))
                sys.exit(4)

    except RuntimeError as e:
        click.echo(f"Error: {e}")
        sys.exit(7)




@main.command(hidden=True)
@click.option("--format", "output_format", required=True, type=click.Choice(["atif"]))
def export(output_format: str) -> None:
    """Export traces to other formats."""
    click.echo(f"Exporting to {output_format}...")
    emit_json({
        "status": "ok",
        "message": f"Export to {output_format} will be implemented later",
    })


@main.command(hidden=True)
def migrate() -> None:
    """Check schema version and run migrations if needed."""
    from opentraces_schema import SCHEMA_VERSION

    cfg = load_config()
    click.echo(f"Config version: {cfg.config_version}")
    click.echo(f"Schema version: {SCHEMA_VERSION}")
    emit_json({
        "status": "ok",
        "config_version": cfg.config_version,
        "schema_version": SCHEMA_VERSION,
    })


@main.command(hidden=True)
@click.option("--json", "as_json", is_flag=True, default=True)
def capabilities(as_json: bool) -> None:
    """Show machine-discoverable feature list."""
    from opentraces_schema import SCHEMA_VERSION

    caps = {
        "name": "opentraces",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "agents": ["claude-code"],
        "modes": ["auto", "review"],
        "export_formats": ["atif"],
        "features": [
            "passive_capture",
            "session_end_hook",
            "recursive_subagent_loading",
            "full_snippet_extraction",
            "attribution_blocks",
            "classifier",
            "web_review",
            "sharded_upload",
            "commit_groups",
        ],
        "env_vars": {
            "HF_TOKEN": "HuggingFace access token (highest priority over saved credentials)",
            "OPENTRACES_NO_TUI": "Set to any value to suppress TUI launch on bare invocation",
        },
    }
    click.echo(json.dumps(caps, indent=2))


@main.command(hidden=True)
def introspect() -> None:
    """Show full API schema for machine discovery."""
    from opentraces_schema import TraceRecord, SCHEMA_VERSION

    schema = {
        "name": "opentraces",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "trace_record_schema": TraceRecord.model_json_schema(),
        "commands": {
            "init": {"description": "One-stop setup for the repo inbox", "options": ["--agent", "--review-policy", "--remote", "--private", "--public", "--no-hook"]},
            "login": {"description": "Authenticate with HuggingFace Hub"},
            "auth": {"description": "Manage HuggingFace authentication", "subcommands": ["login", "logout", "status"]},
            "whoami": {"description": "Show the active HuggingFace identity"},
            "web": {"description": "Open the browser inbox", "options": ["--port"]},
            "tui": {"description": "Open the terminal inbox"},
            "commit": {"description": "Commit inbox traces for push", "options": ["-m", "--all"]},
            "push": {"description": "Upload committed traces to HuggingFace Hub", "options": ["--private", "--public"]},
            "session": {"description": "Manage individual trace sessions", "subcommands": ["list", "show", "commit", "reject", "reset", "redact", "discard"]},
            "remote": {"description": "Manage dataset remote", "subcommands": ["current", "list", "use", "remove"]},
            "status": {"description": "Show repo inbox status"},
            "stats": {"description": "Aggregate statistics (traces, tokens, cost, models)"},
            "context": {"description": "Full project context for agent consumption"},
            "capabilities": {"description": "Machine-discoverable feature list"},
            "introspect": {"description": "Full API schema (this command)"},
        },
        "exit_codes": {
            "0": "OK",
            "2": "Usage error (bad flags or conflicting options)",
            "3": "Auth/config error (not authenticated, not initialized)",
            "4": "Network error",
            "5": "Data corrupt",
            "6": "Not found (trace, project, or resource)",
            "7": "Lock/busy (another process is pushing)",
        },
    }
    click.echo(json.dumps(schema, indent=2))


# ---------------------------------------------------------------------------
# hooks command group
# ---------------------------------------------------------------------------

@main.group()
def hooks() -> None:
    """Manage Claude Code hooks for richer session capture."""


@hooks.command("install")
@click.option(
    "--hooks-dir",
    default=None,
    help="Target directory for hook scripts (default: ~/.claude/hooks/)",
)
@click.option(
    "--settings-file",
    default=None,
    help="Claude Code settings file to update (default: ~/.claude/settings.json)",
)
@click.option("--dry-run", is_flag=True, help="Print what would be done without making changes.")
def hooks_install(hooks_dir: str | None, settings_file: str | None, dry_run: bool) -> None:
    """Install opentraces hooks into ~/.claude/hooks/ and register them in settings.json.

    The Stop hook appends a git-state snapshot to each session transcript.
    The PostCompact hook records explicit compaction events.
    Both are picked up automatically by the opentraces parser.
    """
    import shlex
    import stat

    # Resolve paths
    claude_dir = Path.home() / ".claude"
    target_hooks_dir = Path(hooks_dir) if hooks_dir else claude_dir / "hooks"
    target_settings = Path(settings_file) if settings_file else claude_dir / "settings.json"

    # Source hook scripts are shipped with the package
    src_hooks_dir = Path(__file__).parent / "hooks"
    hook_scripts = {
        "Stop": src_hooks_dir / "on_stop.py",
        "PostCompact": src_hooks_dir / "on_compact.py",
    }

    # Validate source scripts exist before touching anything
    for event, script_path in hook_scripts.items():
        if not script_path.exists():
            emit_json(error_response("MISSING_HOOK_SCRIPT", "install",
                                     f"Hook script not found: {script_path}"))
            sys.exit(5)

    if dry_run:
        plan = []
        for event, script_path in hook_scripts.items():
            dest = target_hooks_dir / f"opentraces_{script_path.name}"
            plan.append({"event": event, "source": str(script_path), "dest": str(dest)})
        human_echo("[dry-run] Would install hooks:")
        for p in plan:
            human_echo(f"  {p['event']}: {p['source']} -> {p['dest']}")
        human_echo(f"[dry-run] Would update: {target_settings}")
        emit_json({
            "status": "ok",
            "dry_run": True,
            "plan": plan,
            "settings_file": str(target_settings),
        })
        return

    # Refuse to clobber an existing settings.json that we cannot parse -
    # silently replacing it with {} would destroy unrelated Claude config.
    settings: dict = {}
    if target_settings.exists():
        try:
            raw = target_settings.read_text()
            settings = json.loads(raw)
            if not isinstance(settings, dict):
                raise ValueError("settings.json root is not a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            emit_json(error_response(
                "CORRUPT_SETTINGS", "install",
                f"Cannot parse {target_settings}: {e}. "
                "Fix or remove the file before running hooks install.",
            ))
            sys.exit(5)
        except OSError as e:
            emit_json(error_response("SETTINGS_READ_ERROR", "install",
                                     f"Cannot read {target_settings}: {e}"))
            sys.exit(5)

    # Create hooks directory
    target_hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: dict[str, str] = {}
    for event, script_path in hook_scripts.items():
        dest = target_hooks_dir / f"opentraces_{script_path.name}"
        dest.write_text(script_path.read_text())
        current_mode = dest.stat().st_mode
        dest.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed[event] = str(dest)
        human_echo(f"Installed: {dest}")

    # Merge hook registrations - path-safe quoting, append only if not already present
    hooks_cfg = settings.setdefault("hooks", {})
    added: list[str] = []
    for event, dest_path in installed.items():
        command = f"python3 {shlex.quote(dest_path)}"
        event_hooks = hooks_cfg.setdefault(event, [])
        already_registered = any(
            h.get("command") == command
            for h in event_hooks
            if isinstance(h, dict)
        )
        if not already_registered:
            event_hooks.append({"type": "command", "command": command})
            added.append(event)

    import os as _os_hooks
    _tmp = target_settings.with_suffix(".json.tmp")
    _tmp.write_text(json.dumps(settings, indent=2))
    _os_hooks.replace(str(_tmp), str(target_settings))

    if added:
        human_echo(f"Registered hooks in {target_settings}: {', '.join(added)}")
    else:
        human_echo(f"Hooks already registered in {target_settings}, no changes needed.")

    emit_json({
        "status": "ok",
        "installed": installed,
        "settings_file": str(target_settings),
        "hooks_added": added,
        "next_steps": [
            "Hooks are now active for all future Claude Code sessions.",
            "Re-run 'opentraces push' after sessions to include enriched data.",
        ],
    })
