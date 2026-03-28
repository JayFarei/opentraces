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
@click.option("--tier", type=click.IntRange(1, 3), default=None, help="Security tier (1, 2, or 3)")
def init(tier: int | None) -> None:
    """Initialize opentraces in the current project directory."""
    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    staging_dir = ot_dir / "staging"
    config_file = ot_dir / "config.yml"

    if config_file.exists():
        click.echo(f"Already initialized: {config_file}")
        emit_json({"status": "ok", "message": "Already initialized", "config": str(config_file)})
        return

    # Prompt interactively if --tier not provided
    if tier is None:
        tier = click.prompt(
            "Security tier (1=redact secrets, 2=classifier review, 3=manual review)",
            type=click.IntRange(1, 3),
            default=2,
        )

    # Create directories
    ot_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Write config.yml
    config_content = (
        "# opentraces configuration\n"
        "# https://opentraces.ai/docs/security-tiers\n"
        f"tier: {tier}\n"
    )
    config_file.write_text(config_content)

    # Add staging dir to .gitignore
    gitignore_path = project_dir / ".gitignore"
    gitignore_line = ".opentraces/staging/"
    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        if gitignore_line not in existing.splitlines():
            with open(gitignore_path, "a") as f:
                if not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"{gitignore_line}\n")
            click.echo(f"  Added '{gitignore_line}' to .gitignore")
    else:
        click.echo("  No .gitignore found, skipping")

    click.echo(f"\nInitialized opentraces (tier {tier}) in {ot_dir}")
    click.echo(f"  Config:  {config_file}")
    click.echo(f"  Staging: {staging_dir}")
    click.echo(f"\nNext steps:")
    click.echo(f"  opentraces _capture --session-dir <path> --project-dir .")
    click.echo(f"  opentraces status")

    emit_json({
        "status": "ok",
        "tier": tier,
        "config_path": str(config_file),
        "staging_path": str(staging_dir),
        "next_steps": [
            "Run 'opentraces _capture' after a Claude Code session",
            "Run 'opentraces status' to see staged traces",
        ],
        "next_command": "opentraces status",
    })


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
    tier = proj_config.get("tier", 3)

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

    # Update project state
    state_path = get_project_state_path(proj_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    project_state = {}
    if state_path.exists():
        try:
            project_state = _json.loads(state_path.read_text())
        except Exception:
            pass
    project_state["last_capture"] = str(Path(session_dir))
    project_state["total_staged"] = project_state.get("total_staged", 0) + parsed_count
    state_path.write_text(_json.dumps(project_state, indent=2))

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
    tier = proj_config.get("tier", 3)
    remote = proj_config.get("remote", None)
    project_name = project_dir.name

    # Tier descriptions
    tier_desc = {1: "redact secrets", 2: "classifier review", 3: "manual review"}
    desc = tier_desc.get(tier, "unknown")

    click.echo(f"{project_name} (tier {tier}, {desc})")
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


@main.command()
def remote() -> None:
    """Show the configured remote dataset."""
    from .config import load_project_config

    project_dir = Path.cwd()
    proj_config = load_project_config(project_dir)
    remote_name = proj_config.get("remote")

    if not remote_name:
        click.echo("No remote configured. Run 'opentraces push' to create one.")
        return

    click.echo(f"origin  {remote_name} (huggingface.co)")


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
@click.option("--auto", is_flag=True, help="Auto-approve for Tier 1 (danger mode)")
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
    """Review pending traces before upload (Tier 3)."""
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
            app = create_app(str(staging))
            click.echo(f"Starting web review at http://localhost:{port}")
            click.echo("Press Ctrl+C to stop.")
            app.run(host="127.0.0.1", port=port, debug=False)
        except ImportError:
            click.echo("Flask not installed. Run: pip install opentraces[web]")
            sys.exit(2)
    else:
        from .review.cli_review import run_cli_review
        run_cli_review(staging)


def _resolve_repo_id(username: str, repo_flag: str | None = None) -> str:
    """Resolve the HF dataset repo_id using priority chain.

    Priority:
      1. --repo flag (highest)
      2. .opentraces/config.yml 'remote' field
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
@click.option("--approved-only", is_flag=True, help="Only push approved traces")
@click.option("--private", is_flag=True, help="Force private visibility (overrides config)")
@click.option("--public", is_flag=True, help="Force public visibility (overrides config)")
@click.option("--publish", is_flag=True, help="Change an existing private dataset to public (no upload)")
@click.option("--gated", is_flag=True, help="Enable gated access (auto-approve) on the dataset")
@click.option("--repo", default=None, help="HF dataset repo (default: username/opentraces)")
def push(approved_only: bool, private: bool, public: bool, publish: bool, gated: bool, repo: str | None) -> None:
    """Upload approved traces to HuggingFace Hub."""
    from .config import (
        get_dataset_name, get_project_staging_dir,
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

    # Resolve repo_id: --repo flag > config remote > default
    repo_id = _resolve_repo_id(username, repo)

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

    state = StateManager()
    if approved_only:
        traces_to_upload = state.get_traces_by_status(TraceStatus.APPROVED)
    else:
        traces_to_upload = state.get_pending_upload_traces()

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


@main.command("import")
@click.option("--from", "from_format", required=True, type=click.Choice(["dataclaw"]))
@click.argument("path")
def import_traces(from_format: str, path: str) -> None:
    """Import traces from other formats."""
    from .state import StateManager, TraceStatus, STAGING_DIR

    input_path = Path(path)
    if not input_path.exists():
        click.echo(f"File not found: {path}")
        emit_json(error_response("FILE_NOT_FOUND", "not_found", f"{path} not found"))
        sys.exit(3)

    if from_format == "dataclaw":
        from .parsers.dataclaw_import import import_dataclaw
        records = import_dataclaw(input_path)
        click.echo(f"Imported {len(records)} traces from DataClaw format")

        state = StateManager()
        STAGING_DIR.mkdir(parents=True, exist_ok=True)

        for record in records:
            jsonl_line = record.to_jsonl_line()
            staging_file = STAGING_DIR / f"{record.trace_id}.jsonl"
            staging_file.write_text(jsonl_line + "\n")
            state.set_trace_status(
                record.trace_id, TraceStatus.STAGED,
                session_id=record.session_id,
                file_path=str(staging_file),
            )

        emit_json({
            "status": "ok",
            "imported": len(records),
            "format": from_format,
            "next_steps": ["Run 'opentraces review' to review imported traces"],
            "next_command": "opentraces review",
        })


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
        "security_tiers": [1, 2, 3],
        "import_formats": ["dataclaw"],
        "export_formats": ["atif"],
        "features": [
            "passive_capture",
            "recursive_subagent_loading",
            "full_snippet_extraction",
            "attribution_blocks",
            "tier2_classifier",
            "web_review",
            "sharded_upload",
            "contributor_dashboard",
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
            "auth": {"description": "Authenticate with HuggingFace Hub"},
            "config": {"description": "Manage configuration", "subcommands": ["set", "show"]},
            "discover": {"description": "List available sessions"},
            "parse": {"description": "Parse sessions into enriched JSONL", "options": ["--auto", "--limit"]},
            "review": {"description": "Review pending traces", "options": ["--web", "--port"]},
            "push": {"description": "Upload to HuggingFace Hub", "options": ["--approved-only"]},
            "import": {"description": "Import from other formats", "options": ["--from dataclaw"]},
            "export": {"description": "Export to other formats", "options": ["--format atif"]},
            "migrate": {"description": "Schema version check + migration"},
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
