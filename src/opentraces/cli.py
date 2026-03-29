"""CLI entry point for opentraces.

Every command emits structured JSON with next_steps and next_command fields.
Designed to be driven by Claude Code via bundled SKILL.md.
"""

from __future__ import annotations

import json
import sys

import click

from pathlib import Path

from . import __version__
from .config import load_config, save_config, Config

SENTINEL = "---OPENTRACES_JSON---"


def emit_json(data: dict) -> None:
    """Emit structured JSON after the sentinel for agent-native parsing."""
    click.echo(f"\n{SENTINEL}")
    click.echo(json.dumps(data, indent=2))


def error_response(code: str, kind: str, message: str, hint: str | None = None, retryable: bool = False) -> dict:
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


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """opentraces - Crowdsource agent traces to HuggingFace Hub."""
    pass


HF_OAUTH_CLIENT_ID = "dc6cdff4-4835-462b-84fa-6aa3328a26f9"
HF_OAUTH_SCOPES = "openid profile write-repos manage-repos"
HF_DEVICE_CODE_URL = "https://huggingface.co/oauth/device"
HF_TOKEN_URL = "https://huggingface.co/oauth/token"
HF_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


@main.command()
@click.option("--token", is_flag=True, help="Use token paste instead of browser login")
def login(token: bool) -> None:
    """Log in to HuggingFace Hub (like gh auth login)."""
    from .config import save_credentials, clear_credentials, CREDENTIALS_PATH

    config = load_config()
    if config.hf_token:
        # Already authenticated, verify and show status
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=config.hf_token)
            user_info = api.whoami()
            username = user_info.get("name", "unknown")
            click.echo(f"Already authenticated as {username}.")
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
        # Fallback: manual token paste (for CI, Docker, headless)
        _login_with_token(save_credentials, CREDENTIALS_PATH)
    else:
        # Primary: OAuth device code flow (like gh auth login)
        _login_with_device_code(save_credentials, CREDENTIALS_PATH)


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
    except Exception:
        pass

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
    token_input = click.prompt("Token", hide_input=True)

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


@main.command()
def logout() -> None:
    """Log out from HuggingFace Hub."""
    from .config import clear_credentials, CREDENTIALS_PATH

    if CREDENTIALS_PATH.exists():
        clear_credentials()
        click.echo("Logged out. Credentials removed.")
    else:
        click.echo("Not logged in (no stored credentials).")

    emit_json({"status": "ok", "authenticated": False})


@main.command(hidden=True)
def auth() -> None:
    """Authenticate with HuggingFace Hub (alias for login)."""
    # Backward compatibility alias
    config = load_config()
    if config.hf_token:
        click.echo("Authenticated. Run 'opentraces login' to manage credentials.")
        emit_json({"status": "ok", "authenticated": True})
        return
    click.echo("Not authenticated. Run 'opentraces login' to connect.")
    emit_json({"status": "needs_action", "authenticated": False, "next_command": "opentraces login"})


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
@click.option("--tier", type=int, help="Security tier (1, 2, or 3)")
@click.option("--exclude", type=str, help="Project path to exclude (appends)")
@click.option("--redact", type=str, help="Custom redaction string (appends)")
@click.option("--pricing-file", type=str, help="Path to custom pricing table")
@click.option("--classifier-sensitivity", type=click.Choice(["low", "medium", "high"]))
def config_set(
    project: str | None,
    tier: int | None,
    exclude: str | None,
    redact: str | None,
    pricing_file: str | None,
    classifier_sensitivity: str | None,
) -> None:
    """Set configuration values. Append-only for --exclude and --redact."""
    cfg = load_config()

    if project and tier is not None:
        from .config import ProjectConfig
        cfg.projects[project] = ProjectConfig(tier=tier)
        click.echo(f"Set tier {tier} for project: {project}")

    if tier is not None and not project:
        cfg.default_tier = tier
        click.echo(f"Set default tier to {tier}")

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
@click.option("--mode", type=click.Choice(["auto", "review"]), default=None, help="Sharing mode")
@click.option("--remote", type=str, default=None, help="HF dataset repo (owner/name)")
@click.option("--no-hook", is_flag=True, help="Skip Claude Code hook installation")
@click.option("--tier", type=click.IntRange(1, 3), default=None, hidden=True, help="Legacy tier (use --mode instead)")
def init(mode: str | None, remote: str | None, no_hook: bool, tier: int | None) -> None:
    """Initialize opentraces in the current project directory.

    Sets up local config and staging. Auth and remote are handled on first push,
    just like git (you don't need a GitHub account to run git init).
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
        current_mode = proj_config.get("mode", "review")
        current_remote = proj_config.get("remote", "not set")
        click.echo(f"Already initialized (mode: {current_mode}, remote: {current_remote})")
        click.echo("Run 'opentraces config set' to change settings.")
        emit_json({"status": "ok", "message": "Already initialized", "mode": current_mode})
        return

    # Legacy --tier mapping
    if tier is not None and mode is None:
        mode = "auto" if tier == 1 else "review"

    # Step 1: Mode selection (no auth needed)
    if mode is None:
        try:
            from pyclack.prompts import select, intro
            from pyclack.core import Option
            import asyncio

            async def _select_mode():
                intro("opentraces init")
                return await select(
                    "How should traces be shared?",
                    [
                        Option(value="auto", label="Auto", hint="scan, redact, push automatically after each session"),
                        Option(value="review", label="Review", hint="I review and approve traces before pushing"),
                    ],
                )
            mode = asyncio.run(_select_mode())
            if mode is None:
                click.echo("Cancelled.")
                sys.exit(0)
        except ImportError:
            mode = click.prompt(
                "Mode (auto=set-and-forget, review=human-in-the-loop)",
                type=click.Choice(["auto", "review"]),
                default="review",
            )

    # Step 4: Create directories and config
    ot_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    proj_config: dict = {"mode": mode, "visibility": "private"}
    if remote:
        proj_config["remote"] = remote
    save_project_config(project_dir, proj_config)

    # Add staging dir to .gitignore
    gitignore_path = project_dir / ".gitignore"
    gitignore_line = ".opentraces/staging/"
    if gitignore_path.exists():
        existing_gi = gitignore_path.read_text()
        if gitignore_line not in existing_gi.splitlines():
            with open(gitignore_path, "a") as f:
                if not existing_gi.endswith("\n"):
                    f.write("\n")
                f.write(f"{gitignore_line}\n")
    # Also add .opentraces/config.json
    if gitignore_path.exists():
        existing_gi = gitignore_path.read_text()
        if ".opentraces/config.json" not in existing_gi.splitlines():
            with open(gitignore_path, "a") as f:
                f.write(".opentraces/config.json\n")

    # Step 5: Hook installation
    hook_installed = False
    if not no_hook:
        hook_installed = _install_capture_hook(project_dir)

    # Summary
    click.echo(f"\nInitialized opentraces ({mode} mode) in {ot_dir}")
    if remote:
        click.echo(f"  Remote:  {remote}")
    else:
        click.echo(f"  Remote:  not set (will be configured on first push)")
    click.echo(f"  Config:  {config_json}")
    click.echo(f"  Staging: {staging_dir}")
    if hook_installed:
        click.echo(f"  Hook:    .claude/settings.json (SessionEnd)")
    if mode == "auto":
        click.echo(f"\nYour next Claude Code session will be captured and pushed automatically.")
        click.echo(f"  Run 'opentraces login' and 'opentraces push' to set up your remote.")
    else:
        click.echo(f"\nYour next Claude Code session will be captured locally.")
        click.echo(f"  Run 'opentraces review' to review, then 'opentraces commit' and 'opentraces push'.")

    emit_json({
        "status": "ok",
        "mode": mode,
        "remote": remote,
        "hook_installed": hook_installed,
        "config_path": str(config_json),
        "staging_path": str(staging_dir),
        "next_steps": [
            "Start a Claude Code session, traces will be captured automatically",
        ],
        "next_command": "opentraces status",
    })


def _install_capture_hook(project_dir: Path) -> bool:
    """Install a SessionEnd hook in .claude/settings.json for auto-parsing."""
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
                    click.echo("  Hook already installed")
                    return True

        # Add the hook
        session_end.append({"hooks": [hook_entry]})
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        click.echo("  Installed SessionEnd hook in .claude/settings.json")
        return True
    except Exception as e:
        click.echo(f"  Could not install hook: {e}")
        click.echo(f"  Add manually to .claude/settings.json")
        return False


@main.command("_capture", hidden=True)
@click.option("--session-dir", required=True, type=click.Path(exists=True), help="Path to Claude Code session dir")
@click.option("--project-dir", required=True, type=click.Path(exists=True), help="Path to project root")
def capture(session_dir: str, project_dir: str) -> None:
    """Capture a Claude Code session (hidden, for automation)."""
    from .config import load_project_config, get_project_staging_dir, get_project_state_path
    from .parsers.claude_code import ClaudeCodeParser
    from .security.scanner import two_pass_scan, apply_redactions
    from .security.anonymizer import anonymize_paths
    from .security.classifier import classify_trace_record
    from .enrichment.git_signals import extract_git_signals, detect_vcs, check_committed
    from .enrichment.attribution import build_attribution
    from .enrichment.dependencies import extract_dependencies
    from .enrichment.metrics import compute_metrics
    from .state import StateManager, TraceStatus, ProcessedFile

    session_path = Path(session_dir)
    proj_path = Path(project_dir)

    # Read project config
    proj_config = load_project_config(proj_path)
    mode = proj_config.get("mode", "review")
    tier = proj_config.get("tier", 2)  # backward compat for security pipeline

    # Setup project-local staging
    staging = get_project_staging_dir(proj_path)
    staging.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    parser = ClaudeCodeParser()

    # Use project-local state if available, otherwise global
    state_path = get_project_state_path(proj_path)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    parsed_count = 0
    error_count = 0

    # Find JSONL files in session dir
    session_files = list(session_path.glob("*.jsonl"))
    if not session_files:
        click.echo("No session files found.", err=True)
        return

    for sf in session_files:
        should_process, offset = state.should_reprocess(str(sf))
        if not should_process:
            continue

        try:
            record = parser.parse_session(sf, byte_offset=offset)
            if record is None:
                continue

            # Enrich: git signals
            vcs = detect_vcs(proj_path)
            record.environment.vcs = vcs
            if vcs.type == "git" and record.timestamp_start:
                ts_end = record.timestamp_end or record.timestamp_start
                outcome = check_committed(proj_path, record.timestamp_start, ts_end)
                if outcome.committed:
                    record.outcome = outcome

            # Enrich: attribution
            patch = record.outcome.patch if record.outcome else None
            attribution = build_attribution(record.steps, patch)
            record.attribution = attribution

            # Enrich: dependencies
            record.dependencies = extract_dependencies(str(proj_path))

            # Enrich: metrics
            record.metrics = compute_metrics(record.steps)

            # Security: scan and redact based on tier
            if tier in (1, 2):
                pass1, pass2 = two_pass_scan(record)
                total_redactions = apply_redactions(record)
                record.security.tier = tier
                record.security.redactions_applied = total_redactions

            if tier == 2:
                classifier_result = classify_trace_record(record, cfg.classifier_sensitivity)
                record.security.flags_reviewed = len(classifier_result.flags)
                record.security.classifier_version = "0.1.0"

            # Anonymize paths (always runs, auto-detects usernames even if USER env unset)
            import os as _os
            username = _os.environ.get("USER") or _os.environ.get("USERNAME") or None
            extra_usernames = cfg.custom_redact_strings or None

            def _anon(text: str | None) -> str | None:
                if not text:
                    return text
                return anonymize_paths(text, username=username, extra_usernames=extra_usernames)

            if record.task.description:
                record.task.description = _anon(record.task.description)
            for step in record.steps:
                step.content = _anon(step.content)
                if step.reasoning_content:
                    step.reasoning_content = _anon(step.reasoning_content)
                for tc in step.tool_calls:
                    for k, v in list(tc.input.items()):
                        if isinstance(v, str):
                            tc.input[k] = _anon(v)
                for obs in step.observations:
                    obs.content = _anon(obs.content)
                    obs.output_summary = _anon(obs.output_summary)
                for snip in step.snippets:
                    snip.file_path = _anon(snip.file_path) or snip.file_path
                    snip.text = _anon(snip.text)
            if record.outcome and record.outcome.patch:
                record.outcome.patch = _anon(record.outcome.patch)
            if record.attribution:
                for attr_file in record.attribution.files:
                    attr_file.path = _anon(attr_file.path) or attr_file.path

            # Stage to project-local staging
            jsonl_line = record.to_jsonl_line()
            staging_file = staging / f"{record.trace_id}.jsonl"
            staging_file.write_text(jsonl_line + "\n")

            state.set_trace_status(
                record.trace_id,
                TraceStatus.STAGED,
                session_id=record.session_id,
                file_path=str(staging_file),
            )

            # Track processed file
            stat = sf.stat()
            state.mark_file_processed(ProcessedFile(
                file_path=str(sf),
                inode=stat.st_ino,
                mtime=stat.st_mtime,
                last_byte_offset=stat.st_size,
            ))

            parsed_count += 1

        except Exception as e:
            error_count += 1
            click.echo(f"  Error: {sf.name}: {e}", err=True)

    click.echo(f"Captured {parsed_count} sessions ({error_count} errors)", err=True)


@main.command()
def status() -> None:
    """Show status of the current opentraces project."""
    import time as _time
    from .config import load_project_config, get_project_staging_dir, get_project_state_path

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"

    if not ot_dir.exists():
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    proj_config = load_project_config(project_dir)
    mode = proj_config.get("mode", "review")
    remote = proj_config.get("remote", None)
    project_name = project_dir.name

    mode_desc = {"auto": "set and forget", "review": "human in the loop"}
    desc = mode_desc.get(mode, mode)

    click.echo(f"{project_name} ({mode} mode, {desc})")
    if remote:
        click.echo(f"remote: {remote}")
    else:
        # Show the default that will be used on push
        click.echo("remote: <username>/opentraces (default, set on first push)")
    click.echo()

    # Load staged traces
    staging_dir = get_project_staging_dir(project_dir)
    staged_files = list(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []

    # Load state
    state_path = get_project_state_path(project_dir)
    project_state: dict = {}
    if state_path.exists():
        try:
            project_state = json.loads(state_path.read_text())
        except Exception:
            pass

    if not staged_files:
        click.echo("0 sessions staged")
    else:
        click.echo(f"{len(staged_files)} sessions staged")

        # Try to parse each trace for summary info
        from opentraces_schema import TraceRecord
        now = _time.time()
        for i, sf in enumerate(sorted(staged_files)):
            is_last = (i == len(staged_files) - 1)
            prefix = "└── " if is_last else "├── "
            try:
                data = sf.read_text().strip()
                record = TraceRecord.model_validate_json(data)
                # Relative timestamp
                if record.timestamp_end:
                    from datetime import datetime
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
                click.echo(f"{prefix}{rel_time:<12} \"{task_desc}\"  {n_steps} steps  {n_tools} tools  {n_flags} flags")
            except Exception:
                click.echo(f"{prefix}{sf.name}")

    # Count reviewed and pushed from global state
    from .state import StateManager, TraceStatus
    state = StateManager()
    reviewed = len(state.get_traces_by_status(TraceStatus.APPROVED))
    pushed = len(state.get_traces_by_status(TraceStatus.UPLOADED))
    click.echo(f"\n{reviewed} reviewed, {pushed} pushed")


@main.group(invoke_without_command=True)
@click.pass_context
def remote(ctx) -> None:
    """Manage the HF dataset remote."""
    if ctx.invoked_subcommand is None:
        # Default: show current remote
        from .config import load_project_config
        project_dir = Path.cwd()
        proj_config = load_project_config(project_dir)
        remote_name = proj_config.get("remote")

        if not remote_name:
            click.echo("No remote configured. Run 'opentraces remote set' to add one.")
            return

        click.echo(f"origin  {remote_name} (huggingface.co)")


@remote.command("set")
@click.argument("repo", required=False, default=None)
def remote_set(repo: str | None) -> None:
    """Set the dataset remote. Interactive if no argument given."""
    from .config import load_project_config, save_project_config

    project_dir = Path.cwd()

    if repo is not None:
        # Direct set: validate format
        if "/" not in repo or repo.count("/") != 1:
            click.echo("Invalid format. Use: owner/dataset")
            sys.exit(2)
        proj_config = load_project_config(project_dir)
        proj_config["remote"] = repo
        save_project_config(project_dir, proj_config)
        click.echo(f"Remote set to: {repo}")
        emit_json({"status": "ok", "remote": repo})
        return

    # Interactive: list existing opentraces datasets
    cfg = load_config()
    if not cfg.hf_token:
        click.echo("Not authenticated. Run 'opentraces login' first.")
        sys.exit(3)

    try:
        from huggingface_hub import HfApi
        api = HfApi(token=cfg.hf_token)
        username = api.whoami().get("name", "unknown")
    except Exception as e:
        click.echo(f"Could not get HF username: {e}")
        sys.exit(4)

    try:
        from .upload.hf_hub import HFUploader
        uploader = HFUploader(token=cfg.hf_token, repo_id="placeholder")
        existing = uploader.list_opentraces_datasets(username)
    except Exception:
        existing = []

    if existing:
        try:
            from pyclack.prompts import select
            from pyclack.core import Option
            import asyncio

            options = [Option(value=ds["id"], label=ds["id"]) for ds in existing]
            options.append(Option(value="__new__", label="Create new dataset..."))

            async def _select():
                return await select("Select dataset remote", options)
            choice = asyncio.run(_select())

            if choice == "__new__":
                repo = click.prompt("Dataset name", default=f"{username}/opentraces")
            elif choice is not None:
                repo = choice
            else:
                click.echo("Cancelled.")
                return
        except ImportError:
            for i, ds in enumerate(existing):
                click.echo(f"  {i+1}. {ds['id']}")
            click.echo(f"  {len(existing)+1}. Create new dataset")
            choice_num = click.prompt("Choose", type=int, default=1)
            if choice_num <= len(existing):
                repo = existing[choice_num - 1]["id"]
            else:
                repo = click.prompt("Dataset name", default=f"{username}/opentraces")
    else:
        repo = click.prompt("Dataset name", default=f"{username}/opentraces")

    proj_config = load_project_config(project_dir)
    proj_config["remote"] = repo
    save_project_config(project_dir, proj_config)
    click.echo(f"Remote set to: {repo}")
    emit_json({"status": "ok", "remote": repo})


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


@main.command()
def discover() -> None:
    """List available agent sessions across projects."""
    from .config import get_projects_path

    cfg = load_config()
    projects_path = get_projects_path(cfg)

    if not projects_path.exists():
        click.echo(f"No sessions found. Directory does not exist: {projects_path}")
        emit_json(error_response(
            code="NO_SESSIONS_FOUND",
            kind="not_found",
            message=f"{projects_path} not found",
            hint="Run Claude Code at least once to generate session logs, or use 'opentraces config set --projects-path' to specify a custom location",
        ))
        sys.exit(3)

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
        emit_json(error_response(
            code="NO_SESSIONS_FOUND",
            kind="not_found",
            message="No .jsonl session files found",
            hint="Run Claude Code to generate session logs",
        ))
        sys.exit(3)

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


@main.command()
@click.option("--auto", is_flag=True, help="Auto-approve for Tier 1 (open mode)")
@click.option("--limit", type=int, default=0, help="Max sessions to parse (0=all)")
def parse(auto: bool, limit: int) -> None:
    """Parse agent sessions into enriched JSONL traces."""
    from .config import get_projects_path, get_tier_for_project
    from .parsers.claude_code import ClaudeCodeParser
    from .security.scanner import scan_trace_record, two_pass_scan, apply_redactions
    from .security.anonymizer import anonymize_paths
    from .security.classifier import classify_trace_record
    from .enrichment.git_signals import extract_git_signals
    from .enrichment.attribution import build_attribution
    from .enrichment.dependencies import extract_dependencies
    from .enrichment.metrics import compute_metrics
    from .state import StateManager, TraceStatus, ProcessedFile, STAGING_DIR

    cfg = load_config()
    projects_path = get_projects_path(cfg)
    parser = ClaudeCodeParser()
    state = StateManager()
    tier = cfg.default_tier

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

            # Enrich: git signals (use session timestamps, not defaults)
            project_dir = session_path.parent
            from .enrichment.git_signals import detect_vcs, check_committed
            vcs = detect_vcs(project_dir)
            record.environment.vcs = vcs
            if vcs.type == "git" and record.timestamp_start:
                ts_end = record.timestamp_end or record.timestamp_start
                outcome = check_committed(project_dir, record.timestamp_start, ts_end)
                if outcome.committed:
                    record.outcome = outcome

            # Enrich: attribution
            patch = record.outcome.patch if record.outcome else None
            attribution = build_attribution(record.steps, patch)
            record.attribution = attribution

            # Enrich: dependencies
            record.dependencies = extract_dependencies(str(project_dir))

            # Enrich: metrics (recompute with full data)
            record.metrics = compute_metrics(record.steps)

            # Security: scan and redact based on tier
            project_path_str = str(session_path.parent)
            tier = get_tier_for_project(cfg, project_path_str)
            if tier == -1:
                skipped_count += 1
                continue  # Excluded project

            if tier in (1, 2):
                pass1, pass2 = two_pass_scan(record)
                total_redactions = apply_redactions(record)
                record.security.tier = tier
                record.security.redactions_applied = total_redactions

            if tier == 2:
                classifier_result = classify_trace_record(record, cfg.classifier_sensitivity)
                record.security.flags_reviewed = len(classifier_result.flags)
                record.security.classifier_version = "0.1.0"

            # Anonymize paths (always runs, auto-detects usernames even if USER env unset)
            import os as _os
            username = _os.environ.get("USER") or _os.environ.get("USERNAME") or None
            extra_usernames = cfg.custom_redact_strings or None

            def _anon(text: str | None) -> str | None:
                if not text:
                    return text
                return anonymize_paths(text, username=username, extra_usernames=extra_usernames)

            if record.task.description:
                record.task.description = _anon(record.task.description)
            for step in record.steps:
                step.content = _anon(step.content)
                if step.reasoning_content:
                    step.reasoning_content = _anon(step.reasoning_content)
                for tc in step.tool_calls:
                    for k, v in list(tc.input.items()):
                        if isinstance(v, str):
                            tc.input[k] = _anon(v)
                for obs in step.observations:
                    obs.content = _anon(obs.content)
                    obs.output_summary = _anon(obs.output_summary)
                for snip in step.snippets:
                    snip.file_path = _anon(snip.file_path) or snip.file_path
                    snip.text = _anon(snip.text)
            if record.outcome and record.outcome.patch:
                record.outcome.patch = _anon(record.outcome.patch)
            if record.attribution:
                for attr_file in record.attribution.files:
                    attr_file.path = _anon(attr_file.path) or attr_file.path

            # Stage the trace
            jsonl_line = record.to_jsonl_line()
            staging_file = STAGING_DIR / f"{record.trace_id}.jsonl"
            staging_file.write_text(jsonl_line + "\n")

            state.set_trace_status(
                record.trace_id,
                TraceStatus.APPROVED if auto else TraceStatus.STAGED,
                session_id=record.session_id,
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
            click.echo(f"  Parsed: {session_path.name} ({len(record.steps)} steps, {sum(len(s.tool_calls) for s in record.steps)} tool calls)")

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


@main.command()
@click.option("--web", is_flag=True, help="Launch local web review interface")
@click.option("--port", type=int, default=5050, help="Port for web review server")
@click.option("--tui", is_flag=True, help="Launch TUI review interface")
def review(web: bool, port: int, tui: bool) -> None:
    """Review pending traces before upload."""
    from .state import STAGING_DIR
    from .config import get_project_staging_dir

    if tui:
        click.echo("Install TUI with: pip install opentraces[tui]")
        return

    # Prefer project-local staging, fall back to global
    project_staging = get_project_staging_dir(Path.cwd())
    staging = project_staging if project_staging.exists() else STAGING_DIR

    if web:
        try:
            from .review.web.app import create_app
            from .config import get_project_state_path
            project_state = get_project_state_path(Path.cwd())
            state_path_str = str(project_state) if project_state.parent.exists() else None

            # Resolve viewer dist for SPA serving
            viewer_dist = Path(__file__).parent.parent.parent / "viewer" / "dist"
            if not viewer_dist.exists():
                viewer_dist = None

            app = create_app(
                str(staging),
                state_path=state_path_str,
                viewer_dist=str(viewer_dist) if viewer_dist else None,
            )
            click.echo(f"Starting web review at http://localhost:{port}")
            click.echo("Press Ctrl+C to stop.")
            app.run(host="127.0.0.1", port=port, debug=False)
        except ImportError:
            click.echo("Flask not installed. Run: pip install opentraces[web]")
            sys.exit(2)
    else:
        from .review.cli_review import run_cli_review
        run_cli_review(staging)


@main.command()
@click.option("--judge/--no-judge", default=False, help="Enable LLM judge for qualitative scoring")
@click.option("--judge-model", default="haiku", type=click.Choice(["haiku", "sonnet", "opus"]),
              help="Model for LLM judge")
@click.option("--limit", type=int, default=0, help="Max traces to assess (0=all)")
def assess(judge: bool, judge_model: str, limit: int) -> None:
    """Run quality assessment on staged traces."""
    staging = Path(".opentraces/staging")
    if not staging.exists():
        click.echo("No staged traces found. Run 'opentraces parse' first.")
        emit_json(error_response("NO_TRACES", "assessment", "No staged traces", hint="Run opentraces parse first"))
        return

    jsonl_files = sorted(staging.glob("*.jsonl"))
    if not jsonl_files:
        click.echo("No JSONL files in staging.")
        emit_json(error_response("NO_TRACES", "assessment", "No JSONL files in staging"))
        return

    from opentraces_schema import TraceRecord
    from .quality.engine import assess_batch, generate_report

    traces = []
    for f in jsonl_files:
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = TraceRecord.model_validate_json(line)
                traces.append(record)
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
    report = generate_report(batch)

    # Write to .gstack/qa/
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = Path(".gstack/qa")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"assess-{ts}.md"
    report_path.write_text(report)

    click.echo(f"\nReport written to {report_path}")
    click.echo(f"Traces assessed: {len(traces)}")
    for name, avg in batch.persona_averages.items():
        click.echo(f"  {name}: {avg:.1f}%")

    emit_json({
        "status": "ok",
        "command": "assess",
        "traces_assessed": len(traces),
        "report_path": str(report_path),
        "persona_averages": batch.persona_averages,
        "judge_enabled": judge,
        "next_steps": ["Review the report", "Run opentraces push to upload"],
        "next_command": "opentraces push",
    })


@main.command("commit")
@click.option("-m", "--message", type=str, default=None, help="Commit message")
@click.option("--all", "commit_all", is_flag=True, help="Commit all approved traces")
def commit_traces(message: str | None, commit_all: bool) -> None:
    """Bundle approved traces into a commit group for push."""
    from .state import StateManager, TraceStatus
    from .config import get_project_state_path
    from opentraces_schema import TraceRecord

    project_dir = Path.cwd()
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    approved = state.get_traces_by_status(TraceStatus.APPROVED)
    if not approved:
        click.echo("No approved traces to commit. Run 'opentraces review' first.")
        emit_json({"status": "ok", "committed": 0, "hint": "Run opentraces review to approve traces"})
        return

    if commit_all:
        trace_ids = [entry.trace_id for entry in approved]
    else:
        click.echo(f"{len(approved)} approved traces:\n")
        for i, entry in enumerate(approved):
            desc = "(no description)"
            if entry.file_path:
                try:
                    data = Path(entry.file_path).read_text().strip()
                    record = TraceRecord.model_validate_json(data)
                    desc = (record.task.description or "untitled")[:50]
                except Exception:
                    pass
            click.echo(f"  {i+1}. {entry.trace_id[:8]}  {desc}")

        click.echo()
        if click.confirm(f"Commit all {len(approved)} traces?", default=True):
            trace_ids = [entry.trace_id for entry in approved]
        else:
            click.echo("Cancelled.")
            return

    # Auto-generate message if not provided
    if message is None:
        descriptions = []
        from .config import get_project_staging_dir
        staging_dir = get_project_staging_dir(project_dir)
        for entry in approved:
            if entry.file_path:
                try:
                    data = Path(entry.file_path).read_text().strip()
                    record = TraceRecord.model_validate_json(data)
                    if record.task.description:
                        descriptions.append(record.task.description[:60])
                except Exception:
                    pass
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
def push(private: bool, public: bool, publish: bool, gated: bool, repo: str | None) -> None:
    """Upload committed traces to HuggingFace Hub."""
    from .config import (
        get_project_staging_dir,
        load_project_config, save_project_config,
    )
    from .state import StateManager, TraceStatus, StagingLock, STAGING_DIR
    from .upload.hf_hub import HFUploader
    from .upload.dataset_card import generate_dataset_card
    from opentraces_schema import TraceRecord

    cfg = load_config()
    if not cfg.hf_token:
        click.echo("Not authenticated. Run 'opentraces login' first.")
        emit_json(error_response("NOT_AUTHENTICATED", "auth", "No HF token", "Run: opentraces login"))
        sys.exit(3)

    if private and public:
        click.echo("Cannot use both --private and --public.")
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

    # If no remote was configured, offer interactive selection (like gh on first push)
    proj_config = load_project_config(Path.cwd())
    if not repo and not proj_config.get("remote"):
        click.echo(f"No remote configured. Using default: {repo_id}")
        try:
            from .upload.hf_hub import HFUploader as _HFUp
            _up = _HFUp(token=cfg.hf_token, repo_id="placeholder")
            existing = _up.list_opentraces_datasets(username)
            if existing:
                click.echo("\nExisting opentraces datasets:")
                for i, ds in enumerate(existing):
                    click.echo(f"  {i+1}. {ds['id']}")
                click.echo(f"  {len(existing)+1}. Create new: {repo_id}")
                try:
                    choice_num = click.prompt("Choose", type=int, default=len(existing)+1)
                    if choice_num <= len(existing):
                        repo_id = existing[choice_num - 1]["id"]
                except Exception:
                    pass
        except Exception:
            pass

        # Save the chosen remote for next time
        proj_config["remote"] = repo_id
        save_project_config(Path.cwd(), proj_config)
        click.echo(f"Remote set to: {repo_id}\n")

    # Handle --publish: just change visibility, no upload
    if publish:
        try:
            uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
            uploader.publish_dataset(repo_id)

            # Save visibility to project config
            try:
                proj_config = load_project_config(Path.cwd())
                proj_config["remote"] = repo_id
                proj_config["visibility"] = "public"
                ot_dir = Path.cwd() / ".opentraces"
                if ot_dir.exists():
                    save_project_config(Path.cwd(), proj_config)
            except Exception:
                pass

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

    # Handle --gated (can be combined with upload or standalone)
    if gated and not approved_only and private is False and public is False:
        # Standalone --gated usage (no upload flags)
        pass  # will apply gated after upload below, or standalone if no traces

    # Use project-local state if available, fall back to global
    from .config import get_project_state_path
    proj_state_path = get_project_state_path(Path.cwd())
    state = StateManager(
        state_path=proj_state_path if proj_state_path.exists() else None
    )
    # Check project mode for push behavior
    proj_config = load_project_config(Path.cwd())
    project_mode = proj_config.get("mode", "review")

    # Get committed traces (the standard path after review → commit)
    traces_to_upload = state.get_traces_by_status(TraceStatus.COMMITTED)

    # In auto mode, also pick up APPROVED traces (auto-committed by _capture)
    if not traces_to_upload and project_mode == "auto":
        traces_to_upload = state.get_traces_by_status(TraceStatus.APPROVED)

    # If review mode and there are approved but uncommitted traces, hint
    if not traces_to_upload:
        approved = state.get_traces_by_status(TraceStatus.APPROVED)
        if approved:
            click.echo(f"{len(approved)} approved traces found, but not yet committed.")
            click.echo("Run 'opentraces commit' to bundle them for push.")
            emit_json({"status": "ok", "uploaded": 0, "hint": "Run opentraces commit first"})
            return

    if not traces_to_upload:
        # If --gated was passed standalone, apply it even without uploading
        if gated:
            try:
                uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
                uploader.set_gated(repo_id)
                click.echo(f"Gated access enabled on {repo_id}")
            except Exception as e:
                click.echo(f"Failed to set gated access: {e}")
                sys.exit(4)
            return

        click.echo("No traces ready for upload.")
        emit_json({"status": "ok", "uploaded": 0, "message": "No approved traces to upload"})
        return

    # Load trace records from staging files, track which ones loaded successfully
    # Check project-local staging first, fall back to global
    project_staging = get_project_staging_dir(Path.cwd())
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

    # Determine visibility: --public/--private flags override config
    if public:
        is_private = False
    elif private:
        is_private = True
    else:
        is_private = cfg.dataset_visibility == "private"

    visibility_label = "private" if is_private else "public"
    click.echo(f"Uploading {len(records)} traces to {repo_id}...")

    try:
        with StagingLock():
            uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
            uploader.ensure_repo_exists(private=is_private)
            result = uploader.upload_traces(records)

            # Generate and upload dataset card
            if result.success:
                try:
                    existing_card = None
                    try:
                        from huggingface_hub import HfApi as _HfApi
                        _api = _HfApi(token=cfg.hf_token)
                        existing_card = _api.hf_hub_download(repo_id, "README.md", repo_type="dataset")
                        existing_card = Path(existing_card).read_text()
                    except Exception:
                        pass
                    card = generate_dataset_card(repo_id, records, existing_card)
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
                        uploader.set_gated(repo_id)
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
                except Exception:
                    pass

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




@main.command()
@click.option("--format", "output_format", required=True, type=click.Choice(["atif"]))
def export(output_format: str) -> None:
    """Export traces to other formats."""
    click.echo(f"Exporting to {output_format}...")
    emit_json({
        "status": "ok",
        "message": f"Export to {output_format} will be implemented later",
    })


@main.command()
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


@main.command()
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
    }
    click.echo(json.dumps(caps, indent=2))


@main.command()
def introspect() -> None:
    """Show full API schema for machine discovery."""
    from opentraces_schema import TraceRecord, SCHEMA_VERSION

    schema = {
        "name": "opentraces",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "trace_record_schema": TraceRecord.model_json_schema(),
        "commands": {
            "init": {"description": "One-stop setup: auth + mode + remote + hook", "options": ["--mode", "--remote", "--no-hook"]},
            "login": {"description": "Authenticate with HuggingFace Hub"},
            "config": {"description": "Manage configuration", "subcommands": ["set", "show"]},
            "discover": {"description": "List available sessions"},
            "parse": {"description": "Parse sessions into enriched JSONL", "options": ["--auto", "--limit"]},
            "review": {"description": "Review pending traces", "options": ["--web", "--port"]},
            "commit": {"description": "Bundle approved traces for push", "options": ["-m", "--all"]},
            "push": {"description": "Upload committed traces to HuggingFace Hub", "options": ["--private", "--public"]},
            "remote": {"description": "Manage dataset remote", "subcommands": ["set", "remove"]},
            "export": {"description": "Export to other formats", "options": ["--format atif"]},
            "status": {"description": "Show project status"},
            "capabilities": {"description": "Machine-discoverable feature list"},
            "introspect": {"description": "Full API schema (this command)"},
        },
        "exit_codes": {
            "0": "OK",
            "2": "Usage error",
            "3": "Missing config",
            "4": "Network error",
            "5": "Data corrupt",
            "7": "Lock/busy",
        },
    }
    click.echo(json.dumps(schema, indent=2))
