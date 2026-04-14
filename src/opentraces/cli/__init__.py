"""CLI entry point for opentraces.

Every command emits structured JSON with next_steps and next_command fields.
Designed to be driven by Claude Code via bundled SKILL.md.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sys

import click

logger = logging.getLogger(__name__)

from pathlib import Path

from .. import __version__
from ..core.config import auth_identity, load_config, load_project_config, save_config, save_project_config
from ..core.workflow import (
    DEFAULT_AGENT,
    DEFAULT_PUSH_POLICY,
    DEFAULT_REMOTE_NAME,
    DEFAULT_REVIEW_POLICY,
    OPENTRACES_ASCII,
    OPENTRACES_TAGLINE,
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
    ("Core", ["add", "push", "pull", "list", "show", "status", "blame", "resume"]),
    (
        "Inbox",
        [
            "reject",
            "reset",
            "redact",
            "discard",
            "llm-review",
            "export",
            "tui",
            "web",
            "stats",
            "log",
            "graph",
            "assess",
        ],
    ),
    ("Project", ["init", "doctor", "remove"]),
    ("Resource", ["remote", "auth", "config", "setup", "completions"]),
]


# -- Color helpers ------------------------------------------------------------
#
# Thin wrappers around click.style. click.echo auto-strips ANSI when stdout is
# not a TTY, and respects the NO_COLOR env var, so these are safe to sprinkle
# through human output without guarding every call.

_STAGE_COLORS = {
    "inbox": "yellow",
    "staged": "cyan",
    "pushed": "green",
    "rejected": "red",
    "blocked": "red",
}


def _bold(text: str) -> str:
    return click.style(text, bold=True)


def _dim(text: str) -> str:
    return click.style(text, dim=True)


def _ok(text: str) -> str:
    return click.style(text, fg="green", bold=True)


def _warn(text: str) -> str:
    return click.style(text, fg="yellow")


def _err(text: str) -> str:
    return click.style(text, fg="red", bold=True)


def _stage_c(label: str, stage_key: str) -> str:
    color = _STAGE_COLORS.get(stage_key.lower())
    return click.style(label, fg=color) if color else label


def _describe_trace(record) -> tuple[str, str]:
    """Pick the best short label for a trace.

    Returns (label, source) where source is one of
    "task" | "step" | "tool" | "none".
    """
    task = getattr(record, "task", None)
    desc = getattr(task, "description", None) if task else None
    if desc:
        return desc.strip(), "task"
    for step in getattr(record, "steps", []) or []:
        content = getattr(step, "content", None)
        if content:
            flat = " ".join(content.split())
            if flat:
                return flat, "step"
    # Tool-only trace — synthesize a label from the first meaningful tool call.
    for step in getattr(record, "steps", []) or []:
        for tc in getattr(step, "tool_calls", []) or []:
            tool = getattr(tc, "tool_name", None)
            if not tool:
                continue
            raw_input = getattr(tc, "input", None)
            # input may be a dict OR a JSON string (parser-dependent).
            inp = None
            if isinstance(raw_input, dict):
                inp = raw_input
            elif isinstance(raw_input, str):
                try:
                    import json as _json
                    inp = _json.loads(raw_input)
                except Exception:
                    inp = None
            if isinstance(inp, dict):
                if tool == "Bash" and inp.get("command"):
                    return f"$ {inp['command']}", "tool"
                for key in ("description", "prompt", "file_path", "path", "query", "pattern"):
                    v = inp.get(key)
                    if v:
                        return f"{tool}: {v}", "tool"
            return f"{tool} call", "tool"
    return "untitled", "none"


# Plan 041 tier priority for picking the "best" git_link to display.
_TIER_PRIORITY = {
    "tool_emitted": 0,
    "tool_emitted_with_divergence": 1,
    "overlapping": 2,
    "orphan": 3,
}

_TIER_GLYPH = {
    "tool_emitted": ("✓", "green"),
    "tool_emitted_with_divergence": ("~", "yellow"),
    "overlapping": ("?", "bright_black"),
    "orphan": ("·", "bright_black"),
}


def _git_chip(record) -> tuple[str, str, str] | None:
    """Return (glyph, short_sha, color) for the best git_link, or None."""
    links = getattr(record, "git_links", None) or []
    if not links:
        return None
    best = min(links, key=lambda l: _TIER_PRIORITY.get(getattr(l, "tier", "orphan"), 99))
    sha = (getattr(best, "revision", "") or "")[:7]
    glyph, color = _TIER_GLYPH.get(best.tier, ("·", "bright_black"))
    return (glyph, sha, color)


def _status_cell(entry, record) -> tuple[str, str]:
    """Git-log-style status combining workflow stage + outcome.

    Returns (rich_markup, plain_text) so callers can render or emit JSON
    without re-deriving or stripping markup.
    """
    visible = resolve_visible_stage(entry.status if entry else None)
    if visible == "pushed":
        return "[green bold]✓ pushed[/]", "pushed"
    if visible == "rejected":
        return "[red]✗ rejected[/]", "rejected"
    if visible == "staged":
        return "[green]✓ staged[/]", "staged"

    # inbox — differentiate by outcome signals
    outcome = getattr(record, "outcome", None)
    if outcome:
        terminal = getattr(outcome, "terminal_state", None)
        success = getattr(outcome, "success", None)
        if success is False or terminal == "error":
            return "[red]✗ failed[/]", "failed"
        if terminal == "compacted":
            return "[yellow]~ compacted[/]", "compacted"
        if getattr(outcome, "committed", False):
            return "[green]✓ done[/]", "done"
    return "[dim]○ open[/]", "open"


def print_banner(*, tagline: str | None = OPENTRACES_TAGLINE, file=None) -> None:
    """Print the OT ASCII banner plus an optional tagline.

    Used at welcoming moments: ``--help``, the end of ``init``, and the end
    of ``setup`` subcommands. Respects ``--json`` mode (suppressed there).
    """
    if _json_mode:
        return
    click.echo(click.style(OPENTRACES_ASCII, fg="cyan", bold=True), file=file)
    if tagline:
        click.echo(f"\n  {_dim(tagline)}\n", file=file)


from ._help import OpentracesGroup


class GroupedGroup(OpentracesGroup):
    """Root group: ``COMMAND_SECTIONS``-based command listing.

    The banner + tagline + command-title bar are rendered by
    ``OpentracesGroup.format_help`` for every command and group; this
    subclass only overrides ``format_commands`` to swap the flat listing
    for the curated sections defined in ``COMMAND_SECTIONS``.
    """

    def _style_rows(self, rows: list[tuple[str, str]], name_width: int) -> list[tuple[str, str]]:
        # Prefix each command with a dim-magenta "ot" shorthand, pad the
        # name to a shared width so descriptions line up across all
        # sections, and italicize the descriptions for visual hierarchy.
        prefix = click.style("ot", fg="magenta", bold=True)
        styled = []
        for name, help_text in rows:
            padded = name.ljust(name_width)
            key = f"{prefix} {click.style(padded, fg='cyan', bold=True)}"
            styled.append((key, click.style(help_text, italic=True)))
        return styled

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # gh-style sectioned listing: CORE / INBOX / PROJECT / RESOURCE.
        sections: list[tuple[str, list[tuple[str, str]]]] = []
        for section_name, cmd_names in COMMAND_SECTIONS:
            rows: list[tuple[str, str]] = []
            for name in cmd_names:
                cmd = self.commands.get(name)
                if cmd is None or cmd.hidden:
                    continue
                rows.append((name, cmd.get_short_help_str(limit=formatter.width)))
            if rows:
                sections.append((section_name, rows))
        # Cross-section width alignment: pick the widest name across all
        # sections so descriptions align in one column from top to bottom.
        name_width = max(
            (len(n) for _, rows in sections for n, _ in rows),
            default=0,
        )
        for section_name, rows in sections:
            heading = f"{section_name.upper()} COMMANDS"
            with self._section(formatter, heading):
                formatter.write_dl(self._style_rows(rows, name_width))


def emit_json(data: dict) -> None:
    """Emit structured JSON after the sentinel for agent-native parsing.

    Emitted when ``--json`` is explicit OR when stdout is not a TTY
    (piped into another tool, Click test runner, etc.). Suppressed
    for interactive human sessions so the terminal stays clean.
    """
    if not _json_mode and sys.stdout.isatty():
        return
    click.echo(f"\n{SENTINEL}")
    click.echo(json.dumps(data, indent=2))


def human_echo(message: str = "", **kwargs) -> None:
    """Echo human-readable text, suppressed in --json mode."""
    if not _json_mode:
        click.echo(message, **kwargs)


def human_hint(hint: str | None) -> None:
    """Echo a Hint: line to human output when a hint is available."""
    if hint and not _json_mode:
        click.echo(f"{_warn('Hint:')} {hint}")


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


def _require_project_opted_in(action: str) -> None:
    """Hard gate: exit 2 if cwd has not run ``opentraces init``.

    Used by TUI / web / push — surfaces a clear error rather than
    silently acting on an uninitialized project.
    """
    from ..core.config import project_is_opted_in

    if not project_is_opted_in(Path.cwd()):
        click.echo(
            f"opentraces: this project has not opted in to {action}.\n"
            "Run 'opentraces init' here first — only initialized "
            "projects appear in the UI or get pushed upstream.",
            err=True,
        )
        sys.exit(2)


def _launch_tui_ui(fullscreen: bool = False, limit: int | None = 500) -> None:
    from ..core.config import get_project_traces_dir
    from ..clients.tui import OpenTracesApp

    _require_project_opted_in("review")
    project_staging = get_project_traces_dir(Path.cwd())
    app = OpenTracesApp(staging_dir=project_staging, fullscreen=fullscreen, limit=limit)
    app.run()


def _launch_web_ui(port: int = 5050, open_browser: bool = False) -> None:
    from ..core.config import get_project_traces_dir, get_project_state_path
    from ..clients.web_server import create_app

    _require_project_opted_in("review")
    project_staging = get_project_traces_dir(Path.cwd())
    project_state = get_project_state_path(Path.cwd())
    # Installed wheel: <site-packages>/opentraces/static/viewer
    pkg_path = Path(__file__).parent.parent / "static" / "viewer"
    if pkg_path.exists():
        viewer_dist = pkg_path
    else:
        # Editable install / source tree: web/viewer/dist at repo root
        viewer_dist = Path(__file__).parent.parent.parent.parent / "web" / "viewer" / "dist"
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
    # Intentionally no docstring: the tagline under the banner is the
    # description, so a Click ``help`` string here would duplicate it.
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
    from ..core.config import save_credentials, clear_credentials, CREDENTIALS_PATH

    config = load_config()

    # If user explicitly wants to re-auth (--token), skip the "already logged in" check
    if config.hf_token and not token:
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=config.hf_token)
            user_info = api.whoami()
            username = user_info.get("name", "unknown")
            click.echo(f"Already authenticated as {username}.")
            click.echo("Run 'opentraces auth login --token' to re-authenticate with a different token.")
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
    from ..core.config import clear_credentials, CREDENTIALS_PATH

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
        emit_json({"status": "needs_action", "authenticated": False, "next_command": "opentraces auth login"})
        return

    username = identity.get("name", "unknown")
    click.echo(f"Authenticated as {username}.")
    emit_json({"status": "ok", "authenticated": True, "username": username})


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
        from ..publish.huggingface.upload import HFUploader

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
    from ..core.config import get_projects_path

    if cfg is None:
        cfg = load_config()
    projects_path = get_projects_path(cfg)
    slug = project_dir.resolve().as_posix().replace("/", "-")
    session_dir = projects_path / slug
    return session_dir if session_dir.exists() else None


def _capture_sessions_into_project(session_dir: Path, project_dir: Path, cfg=None) -> tuple[int, int]:
    """Import existing session files into the project's local inbox."""
    from ..core.config import (
        load_project_config, get_project_traces_dir, get_project_state_path,
        project_is_opted_in,
    )
    from ..capture.claude_code import ClaudeCodeParser
    from ..core.pipeline import process_trace
    from ..core.state import StateManager, TraceStatus, ProcessedFile

    # Defence in depth: even if an entry-point forgot to gate, refuse to
    # create staging dirs or write traces into an uninitialized project.
    if not project_is_opted_in(project_dir):
        return 0, 0

    if cfg is None:
        cfg = load_config()

    proj_config = load_project_config(project_dir)
    review_policy = normalize_review_policy(proj_config.get("review_policy"))

    staging = get_project_traces_dir(project_dir)
    staging.mkdir(parents=True, exist_ok=True)

    parser = ClaudeCodeParser()

    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

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

            from ..core.workflow import decide_post_parse_status
            decided_status, block_reason = decide_post_parse_status(
                result, review_policy=review_policy
            )

            if decided_status == TraceStatus.BLOCKED:
                state.block_trace(
                    result.record.trace_id,
                    reason=block_reason or "security finding",
                    session_id=result.record.session_id,
                    file_path=str(staging_file),
                )
            elif decided_status == TraceStatus.COMMITTED:
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


@main.group()
def config() -> None:
    """Manage opentraces configuration."""
    pass


@config.command("show")
def config_show() -> None:
    """Display current configuration (secrets masked).

    TTY-aware: humans get a sectioned, colorized layout; pipes / --json
    get the same JSON dump as before so downstream tools don't break.
    """
    from ..core.config import CONFIG_PATH

    cfg = load_config()
    data = cfg.model_dump()
    if data.get("custom_redact_strings"):
        data["custom_redact_strings"] = ["***" for _ in data["custom_redact_strings"]]
    if data.get("hf_token"):
        data["hf_token"] = "***"

    if _json_mode or not sys.stdout.isatty():
        click.echo(json.dumps(data, indent=2))
        return

    _render_config_pretty(data, CONFIG_PATH)
    emit_json(data)


def _render_config_pretty(data: dict, config_path) -> None:
    """Sectioned human-readable rendering of the global config dict."""
    label_w = 22

    def _kv(key: str, value, dim_value: bool = False) -> None:
        rendered = _dim(str(value)) if dim_value else str(value)
        click.echo(f"  {_dim(key.ljust(label_w))}  {rendered}")

    def _section(title: str, hint: str | None = None) -> None:
        click.echo()
        head = click.style(title, fg="magenta", bold=True)
        suffix = f"  {_dim(hint)}" if hint else ""
        click.echo(f"{head}{suffix}")

    # Global block
    _section("GLOBAL", str(config_path))
    _kv("config version", data.get("config_version", "?"))
    token = data.get("hf_token")
    _kv("hf token", "*** (set)" if token else _dim("(not set)"))
    _kv("classifier sensitivity", data.get("classifier_sensitivity", "medium"))
    _kv("dataset visibility", data.get("dataset_visibility", "private"))
    custom_path = data.get("projects_path")
    if custom_path:
        _kv("projects path", custom_path)

    # Projects
    projects = data.get("projects") or {}
    excluded = set(data.get("excluded_projects") or [])
    _section("REGISTERED PROJECTS", f"({len(projects)})")
    if not projects:
        click.echo(f"  {_dim('(none — run opentraces init in a project to opt in)')}")
    else:
        for path in sorted(projects.keys()):
            mark = click.style("✓", fg="red") if path in excluded else click.style("✓", fg="green")
            click.echo(f"  {mark} {path}")
    if excluded:
        click.echo(f"  {_dim(f'{len(excluded)} excluded')}")

    # Redaction
    redact = data.get("custom_redact_strings") or []
    if redact:
        _section("REDACTION")
        _kv("custom strings", f"{len(redact)} (masked)")

    # Security · TruffleHog
    sec = data.get("security") or {}
    th = sec.get("trufflehog") or {}
    _section("SECURITY · TRUFFLEHOG")
    _kv("enabled", "yes" if th.get("enabled") else _dim("no"))
    _kv("verify secrets", "yes" if th.get("verify_secrets") else _dim("no"))

    # Security · LLM Review
    rl = sec.get("llm_review") or {}
    _section("SECURITY · LLM REVIEW")
    _kv("enabled", "yes" if rl.get("enabled") else _dim("no"))
    _kv("api format", rl.get("api_format", "?"))
    _kv("base url", rl.get("base_url") or _dim("(unset)"))
    _kv("model", rl.get("model") or _dim("(unset)"))
    api_key_env = rl.get("api_key_env")
    if api_key_env:
        present = "set" if os.environ.get(api_key_env) else click.style("NOT SET", fg="red")
        _kv("api key env", f"${api_key_env} ({present})")
    else:
        _kv("api key env", _dim("(unset — local server)"))
    _kv("timeout", f"{rl.get('timeout', '?')}s")
    _kv("prompt version", rl.get("prompt_version", "?"))
    click.echo()


@config.command("set")
@click.argument("key", required=False)
@click.argument("value", required=False)
@click.option(
    "--project", "scope_project", is_flag=True,
    help="Write to <repo>/.opentraces.json instead of the global config.",
)
@click.option(
    "--global", "scope_global", is_flag=True,
    help="Write to ~/.opentraces/config.json (default).",
)
@click.option("--append", "append_value", is_flag=True, help="Append to a list-typed key.")
def config_set(
    key: str | None,
    value: str | None,
    scope_project: bool,
    scope_global: bool,
    append_value: bool,
) -> None:
    """Set a configuration value.

      ot config set <key> <value> [--append] [--project|--global]

    Default scope is global; --project writes to <repo>/.opentraces.json.
    --append appends to list-typed keys (e.g. custom_redact_strings).
    """
    if scope_project and scope_global:
        click.echo("--project and --global are mutually exclusive.", err=True)
        sys.exit(2)

    if key is None or value is None:
        click.echo("Usage: ot config set <key> <value> [--project|--global]", err=True)
        sys.exit(2)

    if scope_project:
        # Write to repo marker via load/save_project_config helpers.
        from ..core.config import load_project_config, save_project_config
        proj_dir = Path.cwd()
        proj_cfg = load_project_config(proj_dir)
        if append_value:
            existing = proj_cfg.get(key)
            if not isinstance(existing, list):
                existing = [] if existing is None else [existing]
            if value not in existing:
                existing.append(value)
            proj_cfg[key] = existing
        else:
            proj_cfg[key] = value
        save_project_config(proj_dir, proj_cfg)
        click.echo(f"Set {key}={value} (project)")
        emit_json({"status": "ok", "scope": "project", "key": key, "value": value})
        return

    # Global scope (default).
    cfg = load_config()
    # Validate key against the Config model.
    if key not in type(cfg).model_fields:
        click.echo(
            f"Unknown config key '{key}'. Use 'ot config show' to see valid keys.",
            err=True,
        )
        sys.exit(2)
    if append_value:
        existing = getattr(cfg, key, None) or []
        if not isinstance(existing, list):
            click.echo(f"--append only valid for list-typed keys; '{key}' is {type(existing).__name__}.", err=True)
            sys.exit(2)
        if value not in existing:
            existing.append(value)
        setattr(cfg, key, existing)
    else:
        # Coerce simple scalar types from the string value.
        field_info = type(cfg).model_fields[key]
        annotation = field_info.annotation
        try:
            if annotation is bool or "bool" in str(annotation):
                coerced = value.lower() in {"true", "1", "yes", "on"}
            elif annotation is int or "int" in str(annotation):
                coerced = int(value)
            else:
                coerced = value
            setattr(cfg, key, coerced)
        except (ValueError, TypeError) as e:
            click.echo(f"Invalid value for {key}: {e}", err=True)
            sys.exit(2)

    save_config(cfg)
    click.echo(f"Set {key}={value} (global)")
    emit_json({"status": "ok", "scope": "global", "key": key, "value": value})


# --------------------------------------------------------------------------- #
# Plan-043 phase 6: identity finalization helper
# --------------------------------------------------------------------------- #

def _plan043_finalize_identity(project_dir: Path) -> None:
    """Record ``root_commit_sha`` and (if the user hasn't already answered)
    prompt for a first-run backfill over any discovered Claude JSONL corpus.

    Non-interactive sessions (``stdin`` not a tty) skip the prompt and leave
    the decision as ``None`` so the next interactive init will ask.

    Also handles the checkout-move case: if a ``~/.opentraces/projects/<slug>/``
    already records the same root-commit SHA under a different path, we
    print an informational line and reuse the existing slug by writing an
    updated ``project.json`` pointer (same slug, new path). This avoids
    creating duplicate attribution state.
    """
    from ..core import repo_identity as _ri
    from ..core.config import (
        get_first_run_backfill_decision,
        get_root_commit_sha,
        set_first_run_backfill_decision,
        set_root_commit_sha,
        get_project_dir,
    )

    root_sha = _ri.root_commit_sha(project_dir)
    if root_sha and get_root_commit_sha(project_dir) != root_sha:
        set_root_commit_sha(project_dir, root_sha)

    # Checkout-move: another slug already holds this root SHA.
    if root_sha:
        existing = _ri.discover_matching_project(root_sha)
        current_slug_dir = None
        try:
            current_slug_dir = get_project_dir(project_dir)
        except Exception:
            current_slug_dir = None
        if existing and current_slug_dir and existing.slug != current_slug_dir.name:
            click.echo(
                f"Found existing attribution data at {existing.old_path} "
                f"(slug={existing.slug}). Reusing that history under the new path."
            )
            # Update the existing slug's project.json to point at the new
            # path. We intentionally do NOT move data — the slug directory
            # stays put under ~/.opentraces/projects/.
            try:
                existing_slug_dir = existing.traces_dir.parent
                _ri.write_project_identity(
                    existing_slug_dir, project_dir=project_dir, root_sha=root_sha,
                )
            except Exception:
                pass
        elif current_slug_dir:
            # Stamp our own slug's project.json so future moves can find us.
            try:
                _ri.write_project_identity(
                    current_slug_dir, project_dir=project_dir, root_sha=root_sha
                )
            except Exception:
                pass

    # First-run backfill prompt.
    decision = get_first_run_backfill_decision(project_dir)
    if decision is not None:
        return  # already answered (Y, declined, or never)

    corpus = _ri.discover_claude_jsonl_corpus(project_dir) if root_sha else []
    if not corpus:
        return  # nothing to backfill; don't pester

    if not sys.stdin.isatty():
        return  # non-interactive: leave decision None, re-ask next time

    try:
        answer = click.prompt(
            f"Run initial attribution backfill over {len(corpus)} past session(s)? [Y/n/never]",
            default="Y",
            show_default=False,
        )
    except click.Abort:
        return
    a = (answer or "").strip().lower()
    if a in ("n", "no"):
        set_first_run_backfill_decision(project_dir, "declined")
        click.echo("(skipped; will ask again next init)")
        return
    if a == "never":
        set_first_run_backfill_decision(project_dir, "never")
        click.echo("(won't ask again; enable manually with 'opentraces backfill')")
        return
    # Any other answer treated as Y.
    set_first_run_backfill_decision(project_dir, "Y")
    try:
        from ..core import backfill as _bf
        report = _bf.run_full(project_dir)
        click.echo(
            f"Backfilled {report.commits_processed} commit(s); "
            f"{report.attributed_lines} line(s) attributed."
        )
    except Exception as e:  # pragma: no cover - surfaced in logs
        click.echo(f"(backfill failed: {e})", err=True)


@main.command(
    examples=[
        "opentraces init",
        "opentraces init --agent claude-code --review-policy auto",
        "opentraces init --remote owner/my-traces --public",
    ],
    see_also=[
        ("opentraces setup claude-code", "install Claude Code capture hooks"),
        ("opentraces auth login", "authenticate with HuggingFace"),
    ],
    option_groups=[
        ("Agents", ["agents", "no_hook", "import_existing"]),
        ("Policy", ["review_policy"]),
        ("Remote", ["remote", "is_private"]),
    ],
)
@click.option("--agent", "agents", multiple=True, type=click.Choice(list(SUPPORTED_AGENTS)), help="Agent runtime to connect")
@click.option("--review-policy", type=click.Choice(["review", "auto"]), default=None, help="Whether safe traces require review")
@click.option("--push-policy", type=click.Choice(["manual", "auto-push"]), default=None, hidden=True, help="Legacy: derived from review policy")
@click.option(
    "--import-existing/--start-fresh",
    "import_existing",
    default=None,
    help="Import existing Claude Code traces for this repo",
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
    """Initialize opentraces in the current project.

    Sets up the repo-local inbox, agent hooks, policies, and optional remote.
    """
    from ..core.config import _marker_path, load_project_config, save_project_config

    project_dir = Path.cwd()
    marker_file = _marker_path(project_dir)
    legacy_ot_dir = project_dir / ".opentraces"
    legacy_config_json = legacy_ot_dir / "config.json"
    legacy_config_yml = legacy_ot_dir / "config.yml"

    # Check if already initialized (new marker, or legacy in-repo dir).
    if marker_file.exists() or legacy_config_json.exists() or legacy_config_yml.exists():
        proj_config = load_project_config(project_dir)
        current_remote = proj_config.get("remote", "not set")
        # Plan-043 phase 6: on every init (even repeated), refresh root
        # commit identity + optionally prompt for first-run backfill.
        _plan043_finalize_identity(project_dir)
        # Backfill the global registry — projects that were initialized
        # before the registry existed (or before they got pruned) won't
        # appear in `opentraces list --projects` until we re-add them.
        from ..core.config import register_project as _register_project
        _cfg_for_register = load_config()
        if _register_project(_cfg_for_register, project_dir):
            save_config(_cfg_for_register)
            click.echo("(added to global opted-in registry)")
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
                        Option(value="review", label="Review every trace", hint="Traces land in Inbox for you to review"),
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
                        from ..core.config import save_credentials, CREDENTIALS_PATH

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

    # Register this project in the global opted-in list. This is the
    # user-visible consent record: `opentraces list --projects` reads it,
    # and every capture/TUI/web/push path cross-checks `.opentraces/
    # config.json` against it before doing anything.
    from ..core.config import register_project as _register_project
    _cfg_for_register = load_config()
    if _register_project(_cfg_for_register, project_dir):
        save_config(_cfg_for_register)

    # Note: traces and runtime state now live in ~/.opentraces/projects/<slug>/,
    # so the only opentraces artifact in the repo is the .opentraces.json marker —
    # which is meant to be committed. No .gitignore changes needed.

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
                    f"Import {existing_session_count} existing Claude Code trace(s) for this repo now?",
                    initial_value=True,
                    active="Import now",
                    inactive="Start fresh",
                )
            )
        except ImportError:
            import_existing = click.confirm(
                f"Import {existing_session_count} existing Claude Code trace(s) for this repo now?",
                default=True,
            )

    if existing_session_count and import_existing:
        imported_existing, import_errors = _capture_sessions_into_project(existing_session_dir, project_dir, cfg=cfg)

    # Plan-043 phase 6: record root commit + prompt for first-run backfill.
    _plan043_finalize_identity(project_dir)

    click.echo()
    print_banner(tagline=_ok("initialized"))
    click.echo(f"{_dim('Project: ')} {_bold(project_dir.name)}  {_dim(f'({review_policy} policy)')}")
    if remote:
        click.echo(f"  {_dim('Remote: ')} {remote}")
    else:
        click.echo(f"  {_dim('Remote: ')} {_warn('not set')} {_dim('(run')} opentraces remote set <owner>/<repo>{_dim(')')}")
    from ..core.config import get_project_traces_dir
    traces_dir = get_project_traces_dir(project_dir)
    click.echo(f"  Marker:  {marker_file}")
    click.echo(f"  Traces:  {traces_dir}")
    if hook_installed:
        click.echo(f"  Hook:    .claude/settings.json (SessionEnd)")
    if skill_installed:
        click.echo(f"  Skill:   .agents/skills/opentraces/SKILL.md")
    click.echo(f"  Agents:  {', '.join(selected_agents)}")
    click.echo(f"  Policy:  {review_policy}")
    click.echo(f"  Push:    {push_policy}")
    if existing_session_count:
        click.echo(f"  Existing Claude traces: {existing_session_count}")
        if imported_existing or import_errors:
            click.echo(f"  Imported existing: {imported_existing} ({import_errors} errors)")
        else:
            click.echo("  Existing traces were left untouched; new traces will capture automatically.")
    click.echo("\nRecommended flow:")
    if existing_session_count and imported_existing:
        click.echo("  1. Review the imported inbox with 'opentraces web' or 'opentraces tui'")
    elif existing_session_count:
        click.echo("  1. Decide whether to import past traces or just start from now on")
        click.echo(f"     Session dir: {existing_session_dir}")
    else:
        click.echo("  1. Start a connected agent; capture is automatic from now on")
    click.echo("  2. Review and stage inbox traces with 'opentraces add --all'")
    click.echo("  3. Publish staged traces with 'opentraces push'")

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
        "config_path": str(marker_file),
        "staging_path": str(traces_dir),
        "next_steps": [
            "Review imported traces with opentraces web" if imported_existing else (
                "Import past traces or start a connected agent; future traces will be captured automatically"
                if existing_session_count
                else "Start a connected agent, traces will be captured automatically"
            ),
        ],
        "next_command": "opentraces web" if imported_existing else "opentraces",
    })


@main.command()
def remove() -> None:
    """Remove opentraces from the current project."""
    from ..core.config import (
        _marker_path,
        get_project_dir,
        unregister_project as _unregister_project,
    )

    project_dir = Path.cwd()
    marker_file = _marker_path(project_dir)
    legacy_ot_dir = project_dir / ".opentraces"

    removed_hook = _remove_capture_hook(project_dir)

    # Resolve the global per-project dir BEFORE deleting the marker
    # (slug derivation needs the marker's project_id).
    global_dir = None
    try:
        global_dir = get_project_dir(project_dir)
    except RuntimeError:
        pass

    removed_local = False
    if marker_file.exists():
        marker_file.unlink()
        removed_local = True
    if legacy_ot_dir.exists():
        shutil.rmtree(legacy_ot_dir)
        removed_local = True

    removed_global = False
    if global_dir is not None and global_dir.exists():
        shutil.rmtree(global_dir)
        removed_global = True

    # Drop this project from the global opted-in registry so it no
    # longer appears in `opentraces list --projects`.
    cfg_for_unregister = load_config()
    if _unregister_project(cfg_for_unregister, project_dir):
        save_config(cfg_for_unregister)

    if removed_local:
        click.echo(f"Removed project marker: {marker_file}")
    if removed_global:
        click.echo(f"Removed local trace state: {global_dir}")
    if not (removed_local or removed_global):
        click.echo("No opentraces marker or local state found.")

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


@click.command("projects-list")
def projects_list_cmd() -> None:
    """List every project that has run ``opentraces init``.

    Reads from the global registry in ~/.opentraces/config.json. Each
    entry is cross-checked against on-disk ``.opentraces/config.json``
    presence so the list never lies about the real state.
    """
    from ..core.config import opted_in_projects, project_is_opted_in

    cfg = load_config()
    registered = opted_in_projects(cfg)

    if not registered:
        click.echo("No projects have opted in yet.")
        click.echo("Run 'opentraces init' inside a project to add it.")
        emit_json({"status": "ok", "projects": [], "count": 0})
        return

    rows: list[dict] = []
    click.echo(f"Opted-in projects ({len(registered)}):")
    for path_str in registered:
        exists = project_is_opted_in(Path(path_str))
        marker = "✓" if exists else "⚠"
        suffix = "" if exists else "  (registered but .opentraces.json missing — run 'opentraces remove' or re-init)"
        click.echo(f"  {marker} {path_str}{suffix}")
        rows.append({"path": path_str, "on_disk": exists})

    emit_json({"status": "ok", "projects": rows, "count": len(rows)})


def _detect_install_method() -> str:
    """Detect how opentraces was installed: pipx, brew, editable, or pip."""
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
    # "already at latest" is not an error — match specific phrases to avoid false positives
    if any(phrase in combined for phrase in ("already up to date", "already up-to-date", "already at latest", "already installed opentraces", "already installed")):
        human_echo("Already on the latest version.")
        return True

    human_echo(f"{method} upgrade failed: {result.stderr.strip()}")
    emit_json(error_response("UPGRADE_FAILED", "upgrade", result.stderr.strip()))
    sys.exit(4)


def _upgrade_impl(skill_only: bool) -> None:
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
    from ..core.config import project_is_opted_in

    project_dir = Path.cwd()

    if not project_is_opted_in(project_dir):
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

    try:
        from ..capture.skill.install import SkillInstaller
        global_skill = SkillInstaller().install()
        if global_skill.ok:
            human_echo("  Refreshed global skill: ~/.agents/skills/opentraces/")
    except Exception as e:
        human_echo(f"  Could not refresh global skill: {e}")

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
        "command": "opentraces _capture --project-dir .",
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

    tmp_path = settings_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(str(tmp_path), str(settings_path))
    return True


# Agent directory mapping: agent name -> skill directory relative to project root
AGENT_SKILL_DIRS = {
    "claude-code": ".claude/skills",
}


def _resolve_skill_source() -> Path | None:
    """Find SKILL.md from installed package or source tree."""
    # Installed package (wheel with force-include)
    pkg_path = Path(__file__).parent.parent / "skill" / "SKILL.md"
    if pkg_path.exists():
        return pkg_path
    # Editable install / source tree: skill/ at repo root
    repo_path = Path(__file__).parent.parent.parent.parent / "skill" / "SKILL.md"
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
@main.command()
@click.option(
    "--limit",
    type=int,
    default=10,
    show_default=True,
    help="Show N most-recent traces. Use 0 to list all.",
)
def status(limit: int) -> None:
    """Show status of the current opentraces project."""
    import time as _time
    from ..core.config import (
        load_project_config, get_project_traces_dir, get_project_state_path,
        project_is_opted_in,
    )
    from ..core.state import StateManager

    project_dir = Path.cwd()

    if not project_is_opted_in(project_dir):
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    proj_config = load_project_config(project_dir)
    remote = proj_config.get("remote", None)
    project_name = project_dir.name

    staging_dir = get_project_traces_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    # Stage counts come from state.json directly — O(entries) in memory,
    # no file I/O. Reading every staged JSONL here used to take seconds
    # on big inboxes and make the command feel frozen.
    counts = {stage: 0 for stage in ("inbox", "staged", "pushed", "rejected")}
    for entry in state._state.get("traces", {}).values():  # noqa: SLF001
        visible_stage = resolve_visible_stage(entry.get("status"))
        counts[visible_stage] = counts.get(visible_stage, 0) + 1
    # Staged files without a state entry fall under "inbox".
    total_files = sum(1 for _ in staging_dir.glob("*.jsonl")) if staging_dir.exists() else 0
    tracked = sum(counts.values())
    counts["inbox"] += max(0, total_files - tracked)

    # Project header — compact single-line banner + details, set off by a rule.
    from rich.console import Console as _HdrConsole
    from rich.rule import Rule as _HdrRule
    _hdr = _HdrConsole()
    _hdr.print()
    _hdr.print(
        f"  [bold]{project_name}[/]  "
        f"[dim]inbox · {proj_config.get('review_policy', 'review')} · "
        f"{', '.join(proj_config['agents'])}[/]",
        highlight=False,
    )
    visibility = proj_config.get("visibility", "private")
    if remote:
        _hdr.print(f"  [dim]remote:[/] {remote} [dim]({visibility})[/]", highlight=False)
    else:
        _hdr.print(f"  [dim]remote:[/] [yellow]not set[/]", highlight=False)
    _hdr.print(_HdrRule(style="dim"))
    _hdr.print()

    # Machine-readable mirror of visible rows for --json consumers.
    session_summary: list[dict] = []

    # Session list (only the top N by mtime)
    if total_files == 0:
        click.echo("0 traces in inbox")
    else:
        if limit and limit > 0 and total_files > limit:
            staged_files = sorted(
                staging_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limit]
        else:
            staged_files = sorted(
                staging_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

        shown = len(staged_files)
        if shown < total_files:
            pages = (total_files + shown - 1) // shown if shown else 1
            click.echo(
                f"{_bold(f'showing {shown} of {total_files}')} traces  "
                f"{_dim(f'(page 1 of ~{pages}; use --limit N or --limit 0 for more)')}"
            )
        else:
            click.echo(f"{_bold(str(total_files))} trace{'s' if total_files != 1 else ''}")
        click.echo()

        from opentraces_schema import TraceRecord
        from rich.console import Console as _Console
        from rich.table import Table as _Table
        from rich import box as _box

        now = _time.time()
        console = _Console()
        table = _Table(
            box=_box.SIMPLE_HEAD,
            show_edge=False,
            padding=(0, 1),
            header_style="dim",
        )
        table.add_column("ID", no_wrap=True)
        table.add_column("Age", no_wrap=True, justify="right")
        table.add_column("Status", no_wrap=True)
        table.add_column("Task", overflow="ellipsis", no_wrap=True)
        table.add_column("Commit", no_wrap=True)
        table.add_column("", no_wrap=True)  # task-source dot

        # Coverage counters drive the "setup hint" block below the legend.
        git_link_hits = 0  # rows with at least one git_link populated
        rows_rendered = 0

        for sf in staged_files:
            try:
                data = sf.read_text().strip()
                record = TraceRecord.model_validate_json(data)
                entry = state.get_trace(record.trace_id)
                visible_stage = resolve_visible_stage(entry.status if entry else None)

                # Relative time
                rel_time = "unknown"
                if record.timestamp_end:
                    try:
                        if hasattr(record.timestamp_end, "timestamp"):
                            ts = record.timestamp_end.timestamp()
                        else:
                            from datetime import datetime as _dt
                            ts = _dt.fromisoformat(
                                str(record.timestamp_end).replace("Z", "+00:00")
                            ).timestamp()
                        diff_seconds = now - ts
                        if diff_seconds < 3600:
                            rel_time = f"{int(diff_seconds / 60)}m ago"
                        elif diff_seconds < 86400:
                            rel_time = f"{int(diff_seconds / 3600)}h ago"
                        elif diff_seconds < 172800:
                            rel_time = "yesterday"
                        else:
                            rel_time = f"{int(diff_seconds / 86400)}d ago"
                    except (ValueError, TypeError, AttributeError):
                        pass

                title, source = _describe_trace(record)
                # Cap title so Rich can size the Task column predictably.
                if len(title) > 60:
                    title = title[:59] + "…"

                status_cell, status_plain = _status_cell(entry, record)

                chip = _git_chip(record)
                if chip is not None:
                    glyph, sha, color = chip
                    commit_cell = f"[{color}]{glyph}[/] [dim]{sha}[/]"
                else:
                    commit_cell = ""

                source_cell = {
                    "task": "[cyan]●[/]",             # parser-provided
                    "step": "[dim]○[/]",              # first step content
                    "tool": "[magenta]○[/]",          # tool-call summary (no narrative)
                    "none": "[red]○[/]",              # nothing usable
                }.get(source, "")

                short_id = record.trace_id[:8]
                table.add_row(
                    f"[dim]{short_id}[/]",
                    f"[dim]{rel_time}[/]",
                    status_cell,
                    title,
                    commit_cell,
                    source_cell,
                )
                rows_rendered += 1
                if chip is not None:
                    git_link_hits += 1
                tier = (
                    record.git_links[0].tier
                    if record.git_links
                    else None
                )
                session_summary.append({
                    "trace_id": record.trace_id,
                    "short_id": short_id,
                    "stage": visible_stage,
                    "status": status_plain,
                    "task": title,
                    "task_source": source,
                    "commit": chip[1] if chip else None,
                    "commit_tier": tier,
                    "age": rel_time,
                })
            except Exception:
                table.add_row(
                    "", "", "[red]? error[/]", f"[dim]{sf.name}[/]", "", "",
                )

        console.print(table)
        console.print()  # breathing room between table and legend
        console.print(
            "  [dim]status:[/]    [green bold]✓[/][dim] pushed[/]    "
            "[green]✓[/][dim] staged / done[/]    "
            "[yellow]~[/][dim] compacted[/]    "
            "[red]✗[/][dim] failed / rejected[/]    "
            "[dim]○ open[/]",
            highlight=False,
        )
        console.print(
            "  [dim]label:[/]     "
            "[cyan]●[/][dim] task[/]  [dim]○ step[/]  "
            "[magenta]○[/][dim] tool[/]  [red]○[/][dim] none[/]    "
            "[dim]commit:[/]  [green]✓[/][dim] emitted[/]  "
            "[yellow]~[/][dim] diverged[/]  [dim]? overlap  · orphan[/]",
            highlight=False,
        )

        # Setup hints — only shown when coverage is low across the visible
        # window. Dim and terse so they don't nag once hooks are active.
        if rows_rendered > 0:
            hints = []
            git_hook_installed = (project_dir / ".git" / "hooks" / "post-commit").exists()

            if git_link_hits == 0:
                if git_hook_installed:
                    hints.append(
                        "no commit links yet  "
                        f"{_dim('— links populate on next git commit')}"
                    )
                else:
                    hints.append(
                        "no commit links  "
                        f"{_dim('— run')} opentraces setup git "
                        f"{_dim('to install the post-commit hook')}"
                    )

            if hints:
                console.print()
                for h in hints:
                    console.print(f"  {_warn('hint:')} {h}", highlight=False)

    # Footer summary — set apart with a dim rule so the eye finds it last.
    try:
        from rich.console import Console as _Console
        from rich.rule import Rule as _Rule
        _footer_console = _Console()
        _footer_console.print()
        _footer_console.print(_Rule(style="dim"))
    except Exception:
        click.echo()
        click.echo(_dim("─" * 60))
    click.echo(
        f"  {_stage_c('inbox', 'inbox')} {_bold(str(counts['inbox']))}    "
        f"{_stage_c('staged', 'staged')} {_bold(str(counts['staged']))}    "
        f"{_stage_c('pushed', 'pushed')} {_bold(str(counts['pushed']))}    "
        f"{_stage_c('rejected', 'rejected')} {_bold(str(counts['rejected']))}"
    )
    click.echo()

    emit_json({
        "status": "ok",
        "project": project_name,
        "review_policy": proj_config["review_policy"],
        "push_policy": proj_config["push_policy"],
        "agents": proj_config["agents"],
        "remote": remote,
        "counts": counts,
        "total_staged": total_files,
        "sessions": session_summary,
    })


# Register subcommand modules (side-effect: @main.command() bindings)
from . import installers as _installers_module  # noqa: F401,E402
from . import publish as _publish_module  # noqa: F401,E402
from . import import_hf as _import_hf_module  # noqa: F401,E402
from . import _debug as __debug_module  # noqa: F401,E402
from . import inspect as _inspect_module  # noqa: F401,E402

# Standalone Click groups/commands declared without @main.group decoration
# need explicit registration. Step 13: completions noun + hidden __complete.
from .completions import completions as _completions_group  # noqa: E402
from ._complete import complete_cmd as _complete_cmd  # noqa: E402

main.add_command(_completions_group)
main.add_command(_complete_cmd)

# Plan-043 phase 1 — hidden `ot backfill` verb for the attribution cache.
from .backfill import backfill_cmd as _backfill_cmd  # noqa: E402

main.add_command(_backfill_cmd)

# Plan-043 phase 5 — `ot graph` GitButler-style renderer.
from .graph import graph_cmd as _graph_cmd  # noqa: E402

main.add_command(_graph_cmd)

# Plan-043 phase 4 — `ot blame <sha>` per-commit attribution lookup.
from .blame import blame_cmd as _blame_cmd  # noqa: E402

main.add_command(_blame_cmd)

# Plan-043 phase 3 — `ot watcher` background attribution watcher.
from ._watcher_register import register_watcher_commands as _reg_watcher  # noqa: E402

_reg_watcher(main)


# ---------------------------------------------------------------------------
# Step 11 — auth group (parallel surface to flat login/logout/whoami).
# Both surfaces share the _login_impl / _logout_impl / _auth_status_impl
# helpers defined earlier in this module. Step 15 removes the flat verbs.
# ---------------------------------------------------------------------------

@main.group("auth")
def _auth_group() -> None:
    """HuggingFace identity (login, logout, whoami)."""


@_auth_group.command("login")
@click.option("--token", is_flag=True, help="Paste a personal access token (required for pushing traces)")
def _auth_login(token: bool) -> None:
    """Log in to HuggingFace Hub."""
    _login_impl(token)


@_auth_group.command("logout")
def _auth_logout() -> None:
    """Log out from HuggingFace Hub."""
    _logout_impl()


@_auth_group.command("whoami")
def _auth_whoami() -> None:
    """Show the active HuggingFace identity."""
    _auth_status_impl()


# ---------------------------------------------------------------------------
# Flat workflow verbs registered at root: add, list, show, reject, reset,
# redact, discard. ot add refuses BLOCKED + REJECTED traces.
# ---------------------------------------------------------------------------

# Re-register existing trace.X commands at the root with the same name.
# Click commands are first-class objects — add_command attaches the same
# Command to two groups without copying logic.
from .trace import (  # noqa: E402
    trace_show as _trace_show_cmd,
    trace_list as _trace_list_cmd,
    trace_reject as _trace_reject_cmd,
    trace_reset as _trace_reset_cmd,
    trace_discard as _trace_discard_cmd,
)
main.add_command(_trace_show_cmd, name="show")
main.add_command(_trace_reject_cmd, name="reject")
main.add_command(_trace_reset_cmd, name="reset")
main.add_command(_trace_discard_cmd, name="discard")


@main.command("list")
@click.option(
    "--projects", "list_projects", is_flag=True,
    help="List every project that has run `ot init` instead of traces.",
)
@click.option("--remote", "remote_filter", default=None,
              help="Filter to traces missing on the named remote.")
@click.option("--stage", default=None, help="Filter by visible stage")
@click.option("--model", default=None, help="Filter by model")
@click.option("--agent", default=None, help="Filter by agent")
@click.option("--limit", type=int, default=20, help="Max rows to show")
@click.option("--by-commit", is_flag=True, help="Group by commit")
@click.pass_context
def list_cmd(
    ctx, list_projects: bool, remote_filter: str | None,
    stage: str | None, model: str | None, agent: str | None,
    limit: int, by_commit: bool,
) -> None:
    """List traces (or projects with --projects).

    Default: list traces in the local inbox. With --projects, list every
    directory that has run ot init. With --remote <name>, filter traces to
    those missing on that remote.
    """
    if list_projects:
        ctx.invoke(projects_list_cmd)
        return
    if remote_filter:
        # Per-remote pending list — uses pending_for() from Step 2.
        from ..core.config import get_project_state_path
        from ..core.state import StateManager
        state_path = get_project_state_path(Path.cwd())
        state = StateManager(state_path=state_path)
        traces = state.pending_for(remote_filter)
        if not traces:
            click.echo(f"No traces pending for remote '{remote_filter}'.")
            emit_json({"status": "ok", "traces": [], "remote": remote_filter})
            return
        for t in traces:
            click.echo(f"  {t.trace_id[:12]}  status={t.status.value}")
        emit_json({
            "status": "ok",
            "remote": remote_filter,
            "traces": [{"trace_id": t.trace_id, "status": t.status.value} for t in traces],
        })
        return
    # Default: delegate to the trace.list impl.
    ctx.invoke(_trace_list_cmd, stage=stage, model=model, agent=agent, limit=limit, by_commit=by_commit)


@main.command("add")
@click.argument("trace_ids", nargs=-1)
@click.option("--all", "stage_all", is_flag=True, help="Stage every Inbox-status trace for push.")
def add_cmd(trace_ids: tuple[str, ...], stage_all: bool) -> None:
    """Stage trace(s) for the next push.

    Variadic: pass one or more ids, or --all to stage every Inbox trace.
    Refuses BLOCKED + REJECTED traces with a clear pointer to ot redact /
    ot reject (Step 8 approval gate).
    """
    from ..core.state import TraceStatus
    from .trace import _trace_commit_impl, _load_project_state

    if not trace_ids and not stage_all:
        click.echo("Pass one or more trace ids, or --all to stage every Inbox trace.", err=True)
        sys.exit(2)

    state, _staging_dir = _load_project_state()

    # Resolve --all to the explicit list of staged trace ids.
    if stage_all:
        staged = state.get_traces_by_status(TraceStatus.STAGED)
        trace_ids = tuple(t.trace_id for t in staged)
        if not trace_ids:
            click.echo("Nothing to stage — inbox is empty.")
            return

    # Step 8 gate: refuse BLOCKED and REJECTED before doing any work.
    refused: list[tuple[str, str, str]] = []  # (id, status, reason)
    for tid in trace_ids:
        # Allow short-id prefix lookup (trace_commit_impl already does
        # full-id lookup; we duplicate a minimal check here for the gate).
        entry = state.get_trace(tid)
        if entry is None:
            # Try short-id prefix match
            matches = [
                e for e in state.get_traces_by_status(TraceStatus.BLOCKED)
                + state.get_traces_by_status(TraceStatus.REJECTED)
                if e.trace_id.startswith(tid)
            ]
            if matches:
                entry = matches[0]
        if entry is None:
            continue  # not blocked/rejected; let _trace_commit_impl handle the not-found
        if entry.status == TraceStatus.BLOCKED:
            refused.append((tid, "blocked", entry.block_reason or "security finding"))
        elif entry.status == TraceStatus.REJECTED:
            refused.append((tid, "rejected", "marked local-only"))

    if refused:
        for tid, status, reason in refused:
            if status == "blocked":
                click.echo(
                    f"Refusing to stage {tid[:12]}: {reason}\n"
                    f"  Run `ot redact {tid[:12]} <pattern>` to clean the offending content,\n"
                    f"  then `ot reset {tid[:12]} && ot add {tid[:12]}` — or `ot reject {tid[:12]}` to keep local-only.",
                    err=True,
                )
            else:
                click.echo(
                    f"Refusing to stage {tid[:12]}: {reason}\n"
                    f"  Run `ot reset {tid[:12]}` first to bring it back to Inbox.",
                    err=True,
                )
        sys.exit(2)

    # Otherwise dispatch to the existing per-trace commit impl.
    for tid in trace_ids:
        _trace_commit_impl(tid)


@main.command("redact")
@click.argument("trace_id")
@click.argument("pattern")
@click.option("--regex", "use_regex", is_flag=True, help="Interpret PATTERN as a regex.")
@click.option("--field", "field", default=None, help="Restrict to one field path (e.g. observations.stdout).")
@click.option("--step", "step_index", type=int, default=None, help="Restrict to one step index.")
def redact_cmd(trace_id: str, pattern: str, use_regex: bool, field: str | None, step_index: int | None) -> None:
    """Find and replace text content in a trace.

    Default: literal-string find-and-replace across every field of every
    step. Use --regex for pattern matching, --field to scope to one field
    (dotted path supported), --step to scope to one step. Replaces matches
    inline with [REDACTED]. Atomic in-place rewrite. Permanent — no undo.
    """
    from ..core.config import get_project_state_path, get_project_traces_dir
    from ..core.review import redact_pattern_and_persist
    from ..core.state import StateManager

    project_dir = Path.cwd()
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)
    staging_dir = get_project_traces_dir(project_dir)

    entry = state.get_trace(trace_id)
    if entry is None:
        # Try short-id prefix
        matches = [
            t for t in state._state.get("traces", {}).values()
            if t.get("trace_id", "").startswith(trace_id)
        ]
        if not matches:
            click.echo(f"Trace not found: {trace_id}", err=True)
            sys.exit(6)
        trace_id = matches[0]["trace_id"]
        entry = state.get_trace(trace_id)

    result = redact_pattern_and_persist(
        staging_dir, trace_id, pattern,
        regex=use_regex, field=field, step=step_index,
    )

    if hasattr(result, "error_code") and result.error_code:
        click.echo(f"redact failed: {result.error_message}", err=True)
        sys.exit(2)

    click.echo(f"Redacted {trace_id[:12]} ({getattr(result, 'replacements', '?')} replacement(s))")
    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "replacements": getattr(result, "replacements", None),
    })


@main.group(invoke_without_command=True)
@click.pass_context
def remote(ctx) -> None:
    """Manage the HF dataset remote."""
    if ctx.invoked_subcommand is None:
        project_dir = Path.cwd()
        proj_config, remotes = _read_remotes(project_dir)
        active = proj_config.get("active_remote")
        legacy = proj_config.get("remote") if not remotes else None
        if not remotes and not legacy:
            click.echo("No remote connected.")
            click.echo("  opentraces remote add <owner/name>     connect to an existing HF dataset")
            click.echo("  opentraces remote create <owner/name>  create a new one")
            emit_json({"status": "ok", "remote": None})
            return
        if legacy:
            vis = proj_config.get("visibility", "private")
            click.echo(f"{legacy} ({vis})")
            emit_json({"status": "ok", "remote": legacy, "visibility": vis})
            return
        name = active or next(iter(remotes))
        cfg = remotes[name]
        click.echo(f"{name} ({cfg.get('visibility', 'private')})")
        emit_json({"status": "ok", "remote": name, "visibility": cfg.get("visibility")})


# ---------------------------------------------------------------------------
# Helpers: URL expansion, remotes-dict access, repo_id resolution, HF probes.
# HF helpers are factored as module-level so tests can monkeypatch without
# reaching into ``huggingface_hub``.
# ---------------------------------------------------------------------------


def _expand_hf_url(url: str) -> str:
    """Expand short-form ``user/repo`` to ``hf://user/repo``.

    Full URLs (anything containing ``://``) are returned unchanged.
    """
    if "://" in url:
        return url
    if "/" in url:
        return f"hf://{url}"
    return url


def _read_remotes(project_dir: Path) -> tuple[dict, dict]:
    """Return (proj_config_dict, remotes_dict). remotes is the live ref.

    Strips the legacy ``remote`` / ``visibility`` keys that
    ``load_project_config`` synthesizes for back-compat callers — if we
    handed them straight back to ``save_project_config`` they would be
    interpreted as legacy migration triggers and re-create an ``origin``
    entry on the next write.
    """
    proj_config = load_project_config(project_dir)
    proj_config.pop("remote", None)
    proj_config.pop("visibility", None)
    remotes = proj_config.get("remotes")
    if remotes is None:
        proj_config["remotes"] = {}
        remotes = proj_config["remotes"]
    return proj_config, remotes


def _normalize_repo_id(repo: str, username_hint: str | None = None) -> str:
    """Normalize a user-provided identifier to ``owner/name``.

    Accepts ``owner/name``, bare ``name`` (prefixed with the authenticated
    HF user), or ``hf://owner/name``. Bare names require an authenticated
    user to resolve the prefix.
    """
    if "://" in repo:
        repo = repo.split("://", 1)[1]
    if "/" in repo:
        return repo
    if not username_hint:
        raise click.UsageError(
            f"'{repo}' is a short name. Use '<owner>/{repo}' or "
            "'opentraces auth login' first so we can resolve your HF user."
        )
    return f"{username_hint}/{repo}"


def _remote_probe(repo_id: str, token: str | None) -> dict | None:
    """Return dataset metadata dict if the repo exists on HF, None if not.

    Raises on transport errors so callers can distinguish "missing" from
    "network broken".
    """
    from huggingface_hub import HfApi
    try:
        from huggingface_hub.errors import RepositoryNotFoundError
    except ImportError:  # older huggingface_hub
        from huggingface_hub.utils import RepositoryNotFoundError  # type: ignore

    api = HfApi(token=token)
    try:
        info = api.dataset_info(repo_id)
    except RepositoryNotFoundError:
        return None
    return {"private": bool(getattr(info, "private", False))}


def _remote_create(repo_id: str, private: bool, token: str | None) -> bool:
    """Create an HF dataset. Returns True on create, False if it already existed."""
    from huggingface_hub import HfApi
    try:
        from huggingface_hub.errors import HfHubHTTPError
    except ImportError:
        from huggingface_hub.utils import HfHubHTTPError  # type: ignore

    api = HfApi(token=token)
    try:
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=False)
    except HfHubHTTPError as e:
        msg = str(e).lower()
        if "already" in msg or "409" in msg or "conflict" in msg:
            return False
        raise
    return True


def _remote_delete(repo_id: str, token: str | None) -> None:
    """Delete an HF dataset."""
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    api.delete_repo(repo_id=repo_id, repo_type="dataset")


@remote.command("add")
@click.argument("repo")
def remote_add(repo: str) -> None:
    """Connect to an existing HF dataset.

    REPO is ``owner/name``, a bare ``name`` (prefixed with your HF user),
    or ``hf://owner/name``. The dataset must already exist on HuggingFace;
    use ``opentraces remote create`` to make a new one.
    """
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token) if cfg.hf_token else None
    username = identity.get("name") if identity else None
    try:
        repo_id = _normalize_repo_id(repo, username)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    project_dir = Path.cwd()
    proj_config, remotes = _read_remotes(project_dir)
    if repo_id in remotes:
        click.echo(f"{repo_id} is already connected.", err=True)
        emit_json(error_response("ALREADY_CONNECTED", "remote",
                                 f"Remote {repo_id} already connected", None))
        sys.exit(2)

    try:
        info = _remote_probe(repo_id, cfg.hf_token)
    except Exception as e:
        click.echo(f"Failed to verify dataset on HuggingFace: {e}", err=True)
        emit_json(error_response("HF_ERROR", "remote", str(e), None))
        sys.exit(3)
    if info is None:
        click.echo(f"No dataset at {repo_id} on HuggingFace.", err=True)
        click.echo(f"  hint: opentraces remote create {repo_id}", err=True)
        emit_json(error_response("NOT_FOUND", "remote",
                                 f"Dataset {repo_id} does not exist on HF",
                                 f"Run: opentraces remote create {repo_id}"))
        sys.exit(3)

    visibility = "private" if info.get("private") else "public"
    full_url = _expand_hf_url(repo_id)
    remotes[repo_id] = {"url": full_url, "visibility": visibility}
    if not proj_config.get("active_remote"):
        proj_config["active_remote"] = repo_id
    save_project_config(project_dir, proj_config)
    click.echo(f"Connected to {repo_id} ({visibility})")
    emit_json({"status": "ok", "remote": repo_id, "visibility": visibility, "url": full_url})


@remote.command("create")
@click.argument("repo")
@click.option("--private/--public", "is_private", default=True,
              help="Dataset visibility on HuggingFace (default: private).")
def remote_create(repo: str, is_private: bool) -> None:
    """Create a new HF dataset and connect it.

    Fails if REPO already exists on HuggingFace — use
    ``opentraces remote add`` instead.
    """
    cfg = load_config()
    if not cfg.hf_token:
        click.echo("Not authenticated. Run 'opentraces auth login' first.", err=True)
        sys.exit(3)
    identity = _auth_identity(cfg.hf_token)
    username = identity.get("name") if identity else None
    try:
        repo_id = _normalize_repo_id(repo, username)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    project_dir = Path.cwd()
    proj_config, remotes = _read_remotes(project_dir)
    if repo_id in remotes:
        click.echo(f"{repo_id} is already connected.", err=True)
        sys.exit(2)

    try:
        created = _remote_create(repo_id, is_private, cfg.hf_token)
    except Exception as e:
        click.echo(f"Failed to create dataset: {e}", err=True)
        emit_json(error_response("HF_ERROR", "remote", str(e), None))
        sys.exit(3)
    if not created:
        click.echo(f"{repo_id} already exists on HuggingFace.", err=True)
        click.echo(f"  hint: opentraces remote add {repo_id}", err=True)
        emit_json(error_response("ALREADY_EXISTS", "remote",
                                 f"Dataset {repo_id} already exists",
                                 f"Run: opentraces remote add {repo_id}"))
        sys.exit(3)

    visibility = "private" if is_private else "public"
    full_url = _expand_hf_url(repo_id)
    remotes[repo_id] = {"url": full_url, "visibility": visibility}
    if not proj_config.get("active_remote"):
        proj_config["active_remote"] = repo_id
    save_project_config(project_dir, proj_config)
    click.echo(f"Created {repo_id} ({visibility}) and connected.")
    emit_json({"status": "ok", "remote": repo_id, "visibility": visibility,
               "url": full_url, "created": True})


@remote.command("remove")
@click.argument("repo", required=False, default=None)
@click.option("--delete-remote", is_flag=True,
              help="Also delete the dataset on HuggingFace (irreversible).")
@click.option("--yes", "confirmed", is_flag=True, help="Skip the confirmation prompt.")
def remote_remove(repo: str | None, delete_remote: bool, confirmed: bool) -> None:
    """Disconnect the remote from this project.

    REPO is optional when exactly one remote is connected. With
    ``--delete-remote``, also delete the dataset on HuggingFace.
    """
    from ..core.config import get_project_state_path
    from ..core.state import StateManager, UnknownRemoteError

    project_dir = Path.cwd()
    proj_config, remotes = _read_remotes(project_dir)

    if not remotes:
        click.echo("No remote connected.", err=True)
        sys.exit(2)

    if repo is None:
        if len(remotes) == 1:
            repo = next(iter(remotes))
        else:
            click.echo("Multiple remotes connected; specify which to remove:", err=True)
            for r in sorted(remotes):
                click.echo(f"  {r}", err=True)
            sys.exit(2)

    if repo not in remotes:
        click.echo(f"No remote '{repo}'.", err=True)
        sys.exit(2)

    if delete_remote and not confirmed:
        click.echo(f"About to delete {repo} on HuggingFace (irreversible).")
        if not click.confirm("Proceed?"):
            click.echo("Cancelled.")
            sys.exit(1)

    if delete_remote:
        cfg = load_config()
        if not cfg.hf_token:
            click.echo("Not authenticated. Run 'opentraces auth login' first.", err=True)
            sys.exit(3)
        try:
            _remote_delete(repo, cfg.hf_token)
            click.echo(f"Deleted {repo} on HuggingFace.")
        except Exception as e:
            click.echo(f"Failed to delete on HuggingFace: {e}", err=True)
            sys.exit(3)

    del remotes[repo]
    if proj_config.get("active_remote") == repo:
        proj_config["active_remote"] = next(iter(remotes), None)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)
    try:
        state.forget_remote(repo)
    except UnknownRemoteError:
        pass
    save_project_config(project_dir, proj_config)
    click.echo(f"Disconnected {repo}.")
    emit_json({"status": "ok", "remote": repo, "deleted_remote": delete_remote})


@remote.command("list")
@click.option("-v", "verbose", is_flag=True, help="Show URLs.")
def remote_list(verbose: bool) -> None:
    """List connected remotes (active marked with *)."""
    proj_config, remotes = _read_remotes(Path.cwd())
    active = proj_config.get("active_remote")

    if not remotes:
        click.echo("No remotes connected.")
        emit_json({"status": "ok", "remotes": {}, "active_remote": None})
        return

    payload = {}
    for name, cfg in sorted(remotes.items()):
        marker = "*" if name == active else " "
        if verbose:
            click.echo(f"  {marker} {name}\t{cfg['url']} ({cfg.get('visibility', 'private')})")
        else:
            click.echo(f"  {marker} {name}")
        payload[name] = cfg

    emit_json({"status": "ok", "remotes": payload, "active_remote": active})


# Step 4 also rebinds bare `ot remote remove <name>` semantics — the existing
# no-arg form (line ~2013) is kept for back-compat and removed in step 15.
# Click does NOT support overloading commands, so the new name-taking remove
# is exposed as the hidden `remote remove-named` + `remote rm` aliases above.
# Step 15 will rename remove-named -> remove and delete the legacy no-arg form.


@main.command(hidden=True)
@click.option("--auto", is_flag=True, help="Auto-approve (skip review)")
@click.option("--limit", type=int, default=0, help="Max traces to parse (0=all)")
def parse(auto: bool, limit: int) -> None:
    """Deprecated: use ``opentraces scan`` from inside an opted-in project."""
    click.echo(
        "opentraces parse is deprecated — staging is per-project now.\n"
        "Run `opentraces init` then `opentraces scan` inside a project.",
        err=True,
    )
    sys.exit(2)
    # The remainder is dead code retained so import-time references don't
    # break; structurally rewritten under the per-project layout.
    from ..core.config import get_projects_path, is_project_excluded, get_project_traces_dir
    from ..capture.claude_code import ClaudeCodeParser
    from ..core.pipeline import process_trace
    from ..core.state import StateManager, TraceStatus, ProcessedFile

    cfg = load_config()
    projects_path = get_projects_path(cfg)
    parser = ClaudeCodeParser()
    state = StateManager(state_path=Path("/tmp/opentraces-parse-state.json"))

    parsed_count = 0
    skipped_count = 0
    error_count = 0

    click.echo(f"Scanning traces in {projects_path}...")

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
            staging_file = get_project_traces_dir(project_dir) / f"{result.record.trace_id}.jsonl"
            staging_file.parent.mkdir(parents=True, exist_ok=True)
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
            "Run 'opentraces tui' to review staged traces" if not auto else "Run 'opentraces push' to upload",
        ],
        "next_command": "opentraces tui" if not auto else "opentraces push",
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
@click.option(
    "--limit",
    type=int,
    default=500,
    show_default=True,
    help="Maximum number of traces to load (most recent first). Use 0 for no limit.",
)
def tui(fullscreen: bool, limit: int) -> None:
    """Open the terminal inbox UI."""
    try:
        _launch_tui_ui(fullscreen=fullscreen, limit=limit if limit > 0 else None)
    except ImportError:
        click.echo("Textual not installed. Run: pip install opentraces[tui]")
        sys.exit(2)


def _reports_dir(project_dir: Path | None = None) -> Path:
    """Resolve where assess writes its local markdown report.

    Per-project reports land alongside the rest of the project's
    machine-local state under ``~/.opentraces/projects/<slug>/reports/``.
    Dataset assessments fall back to a global ``~/.opentraces/reports/``.
    """
    from ..core.config import OPENTRACES_DIR, get_project_dir, project_is_opted_in

    if project_dir and project_is_opted_in(project_dir):
        return get_project_dir(project_dir) / "reports"
    return OPENTRACES_DIR / "reports"


def _fetch_existing_card(uploader, repo_id: str) -> str | None:
    """Best-effort fetch of the remote README so user-edited prose
    survives an ``assess --dataset`` write. Returns None on any failure.
    """
    try:
        local_path = uploader.api.hf_hub_download(
            repo_id=repo_id,
            filename="README.md",
            repo_type="dataset",
        )
        return Path(local_path).read_text()
    except Exception:
        return None


def _assess_dataset(
    repo_id: str,
    judge: bool = False,
    judge_model: str = "haiku",
    limit: int = 0,
    dry_run: bool = False,
) -> None:
    """Assess a full HF dataset; optionally update its quality card.

    Downloads all shards via huggingface_hub (cached locally after first
    fetch). With ``dry_run`` set, prints the report and exits — no
    quality.json upload, no README rewrite, no local report written.
    """
    import time
    from datetime import datetime

    from ..quality.engine import assess_batch, generate_report
    from ..quality.gates import check_gate
    from ..quality.summary import build_summary
    from ..quality.display import format_assessment
    from ..publish.huggingface.upload import HFUploader, RemoteShardError
    from ..publish.huggingface.dataset_card import generate_dataset_card
    from ..core.config import load_config

    config = load_config()
    token = config.hf_token

    uploader = HFUploader(token=token or "", repo_id=repo_id)

    click.echo(f"Fetching traces from {repo_id}...")
    try:
        traces = uploader.fetch_all_remote_traces()
    except RemoteShardError as e:
        click.echo(f"Error: {e}", err=True)
        emit_json(error_response("SHARD_UNAVAILABLE", "network", str(e), retryable=True))
        sys.exit(4)

    if not traces:
        click.echo("No valid traces found in dataset.")
        emit_json(error_response("NO_TRACES", "assess", "No valid traces in dataset"))
        return

    if limit > 0:
        traces = traces[:limit]

    started = time.time()
    batch = assess_batch(traces, enable_judge=judge, judge_model=judge_model)
    gate = check_gate(batch)
    mode = "hybrid" if judge else "deterministic"
    summary = build_summary(batch, gate, mode=mode, judge_model=judge_model if judge else None)
    elapsed = time.time() - started

    click.echo("")
    click.echo(format_assessment(summary, batch, header=repo_id, elapsed_seconds=elapsed))

    report_path: Path | None = None
    if dry_run:
        click.echo("\n[dry-run] skipping quality.json + README + local report.")
    else:
        if not token:
            click.echo("\nNo HF token — skipping dataset card update.")
            click.echo("Run 'hf auth login' or set HF_TOKEN to enable.")
        else:
            click.echo("\nUpdating dataset card...")
            summary_dict = summary.to_dict()

            if uploader.upload_quality_json(summary_dict):
                click.echo("  quality.json uploaded")
            else:
                click.echo("  Warning: failed to upload quality.json")

            existing_card = _fetch_existing_card(uploader, repo_id)
            new_card = generate_dataset_card(
                repo_id=repo_id, traces=traces,
                existing_card=existing_card,
                quality_summary=summary_dict,
            )
            try:
                uploader.api.upload_file(
                    path_or_fileobj=io.BytesIO(new_card.encode("utf-8")),
                    path_in_repo="README.md",
                    repo_id=repo_id, repo_type="dataset",
                    commit_message="chore: update quality scores",
                )
                preserved = " (preserved hand-edited prose)" if existing_card else ""
                click.echo(f"  README.md updated{preserved}")
            except Exception as e:
                click.echo(f"  Warning: could not update README.md: {e}")

        slug = repo_id.replace("/", "-")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report = generate_report(batch)
        report_dir = _reports_dir()
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
        "dry_run": dry_run,
        "report_path": str(report_path) if report_path else None,
        "quality_summary": summary.to_dict(),
    })


def _load_local_traces() -> list:
    """Load traces for local assess: staged first, fall back to everything in the staging dir."""
    from opentraces_schema import TraceRecord
    from ..core.state import StateManager
    from ..core.config import get_project_state_path, get_project_traces_dir

    project_dir = Path.cwd()
    staging = get_project_traces_dir(project_dir)
    state = StateManager(state_path=get_project_state_path(project_dir))
    committed = state.get_committed_traces()

    def _load(path: Path) -> list:
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(TraceRecord.model_validate_json(line))
            except Exception:
                continue
        return out

    traces: list = []
    if committed:
        for trace_id in committed:
            f = staging / f"{trace_id}.jsonl"
            if f.exists():
                traces.extend(_load(f))
    elif staging.exists():
        for f in sorted(staging.glob("*.jsonl")):
            traces.extend(_load(f))
    return traces


def _remote_delta(repo_id: str, local_summary) -> dict[str, float] | None:
    """Pull remote quality.json and return {persona: local_avg - remote_avg}."""
    try:
        from huggingface_hub import HfApi
        from ..quality.summary import QualitySummary

        api = HfApi()
        content = api.hf_hub_download(
            repo_id=repo_id, filename="quality.json", repo_type="dataset"
        )
        with open(content) as f:
            remote_data = json.load(f)
        remote = QualitySummary.from_dict(remote_data)
        delta: dict[str, float] = {}
        for name, remote_ps in remote.persona_scores.items():
            local_ps = local_summary.persona_scores.get(name)
            if local_ps:
                delta[name] = round(local_ps.average - remote_ps.average, 1)
        return delta
    except Exception:
        return None


@main.command()
@click.option("--judge/--no-judge", default=False, help="Enable LLM judge for qualitative scoring")
@click.option("--judge-model", default="haiku", type=click.Choice(["haiku", "sonnet", "opus"]),
              help="Model for LLM judge")
@click.option("--limit", type=int, default=0, help="Max traces to assess (0=all)")
@click.option("--dataset", "dataset_repo", type=str, default=None,
              help="Assess a remote HF dataset (e.g. user/my-traces).")
@click.option("--dry-run", is_flag=True,
              help="Print the assessment only — no remote writes, no local report.")
@click.option("--explain", is_flag=True,
              help="Show the glossary and exit.")
def assess(judge: bool, judge_model: str, limit: int,
           dataset_repo: str | None, dry_run: bool, explain: bool) -> None:
    """Score trace quality.

    Local mode (default) assesses staged traces, falling back to every
    trace in the staging dir if nothing has been staged yet. Use
    --dataset user/repo to assess a remote HF dataset.

    Dimensions: Schema (safety), Conversation (SFT), Outcome (RL signal),
    Metrics (cost/cache), Metadata (search context). Run with --explain
    for the full glossary.
    """
    import time
    from datetime import datetime

    from ..quality.engine import assess_batch, generate_report
    from ..quality.gates import check_gate
    from ..quality.summary import build_summary
    from ..quality.display import format_assessment, format_glossary
    from ..core.config import (
        get_project_traces_dir, load_project_config, project_is_opted_in,
    )

    if explain:
        click.echo(format_glossary())
        return

    if dataset_repo:
        _assess_dataset(
            dataset_repo,
            judge=judge,
            judge_model=judge_model,
            limit=limit,
            dry_run=dry_run,
        )
        return

    project_dir = Path.cwd()
    if not project_is_opted_in(project_dir):
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        emit_json(error_response("NOT_INITIALIZED", "assessment", "Project not opted in", hint="Run opentraces init"))
        return

    traces = _load_local_traces()
    if limit > 0:
        traces = traces[:limit]
    if not traces:
        click.echo("No traces to assess. Capture some with a connected agent first.")
        emit_json(error_response("NO_TRACES", "assessment", "No valid traces"))
        return

    started = time.time()
    if judge:
        click.echo(f"Scoring {len(traces)} traces (LLM judge: {judge_model})...")
    batch = assess_batch(traces, enable_judge=judge, judge_model=judge_model)
    gate = check_gate(batch)
    mode = "hybrid" if judge else "deterministic"
    summary = build_summary(batch, gate, mode=mode, judge_model=judge_model if judge else None)
    elapsed = time.time() - started

    # Auto-include remote delta when a remote is configured.
    remote_delta = None
    proj_config = load_project_config(project_dir)
    repo_id = proj_config.get("remote")
    if repo_id:
        remote_delta = _remote_delta(repo_id, summary)

    click.echo("")
    click.echo(format_assessment(
        summary, batch,
        header=project_dir.name,
        elapsed_seconds=elapsed,
        remote_delta=remote_delta,
    ))

    report_path: Path | None = None
    if dry_run:
        click.echo("\n[dry-run] skipping local report write.")
    else:
        report_dir = _reports_dir(project_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = report_dir / f"assess-{ts}.md"
        report_path.write_text(generate_report(batch))
        click.echo(f"\nFull report: {report_path}")

    emit_json({
        "status": "ok",
        "command": "assess",
        "traces_assessed": len(traces),
        "dry_run": dry_run,
        "report_path": str(report_path) if report_path else None,
        "persona_averages": batch.persona_averages,
        "judge_enabled": judge,
        "quality_summary": summary.to_dict(),
        "remote_delta": remote_delta,
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

    from ..core.config import load_project_config
    proj_config = load_project_config(Path.cwd())
    config_remote = proj_config.get("remote")
    if config_remote:
        return config_remote

    return f"{username}/opentraces"
@main.command()
@click.option("--format", "output_format", required=True,
              type=click.Choice(["atif", "agent-trace"]))
@click.option("--output", "output_path", type=click.Path(),
              help="Output JSONL path (default: ./opentraces-export.jsonl)")
def export(output_format: str, output_path: str | None) -> None:
    """Export staged traces to another format."""
    from ..core.config import get_project_traces_dir
    from ..core.inbox import load_trace_records

    _require_project_opted_in("export")

    staging = get_project_traces_dir(Path.cwd())
    records = load_trace_records(staging)
    if not records:
        human_echo("no staged traces to export")
        emit_json({"status": "ok", "count": 0})
        return

    out = Path(output_path) if output_path else Path.cwd() / "opentraces-export.jsonl"

    if output_format == "agent-trace":
        from ..publish.agent_trace import export_to_jsonl
        n = export_to_jsonl(records, out)
        human_echo(f"exported {n} traces to {out} (agent-trace/v0.1.0)")
        emit_json({"status": "ok", "format": output_format, "count": n, "output": str(out)})
        return

    # ATIF still a stub: report clearly.
    human_echo("ATIF export: staged for future release")
    emit_json({"status": "ok", "format": output_format, "count": 0, "note": "ATIF stub"})


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
            "auth": {"description": "Show the active HuggingFace identity"},
            "logout": {"description": "Log out from HuggingFace Hub"},
            "web": {"description": "Open the browser inbox", "options": ["--port"]},
            "tui": {"description": "Open the terminal inbox"},
            "commit": {"description": "Commit inbox traces for push", "options": ["-m", "--all"]},
            "push": {"description": "Upload staged traces to HuggingFace Hub", "options": ["--private", "--public"]},
            "trace": {"description": "Manage individual traces", "subcommands": ["list", "show", "commit", "reject", "reset", "redact", "discard"]},
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
