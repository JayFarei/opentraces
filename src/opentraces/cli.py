"""CLI entry point for opentraces.

Every command emits structured JSON with next_steps and next_command fields.
Designed to be driven by Claude Code via bundled SKILL.md.
"""

from __future__ import annotations

import json
import sys

import click

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


@main.command()
def auth() -> None:
    """Authenticate with HuggingFace Hub."""
    config = load_config()
    if config.hf_token:
        click.echo("Already authenticated with HuggingFace Hub.")
        emit_json({
            "status": "ok",
            "authenticated": True,
            "next_steps": ["Run 'opentraces discover' to find available sessions"],
            "next_command": "opentraces discover",
        })
        return

    click.echo("No HF token found. Set HF_TOKEN environment variable or run:")
    click.echo("  huggingface-cli login")
    emit_json({
        "status": "needs_action",
        "authenticated": False,
        "next_steps": [
            "Set HF_TOKEN environment variable",
            "Or run: huggingface-cli login",
        ],
        "next_command": "huggingface-cli login",
    })


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
        data["custom_redact_strings"] = [
            s[:2] + "***" for s in data["custom_redact_strings"]
        ]
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
    from pathlib import Path
    from .config import get_projects_path, get_tier_for_project
    from .parsers.claude_code import ClaudeCodeParser
    from .security.scanner import scan_trace_record, two_pass_scan
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

            # Enrich: git signals
            project_dir = session_path.parent
            vcs, outcome = extract_git_signals(str(project_dir))
            record.environment.vcs = vcs
            if outcome.committed:
                record.outcome = outcome

            # Enrich: attribution
            attribution = build_attribution(record.steps, record.outcome.patch)
            record.attribution = attribution

            # Enrich: dependencies
            record.dependencies = extract_dependencies(str(project_dir))

            # Enrich: metrics (recompute with full data)
            record.metrics = compute_metrics(record.steps)

            # Security: scan based on tier
            if tier in (1, 2):
                scan_result = two_pass_scan(record)
                record.security.tier = tier
                record.security.redactions_applied = scan_result.redaction_count

            if tier == 2:
                classifier_result = classify_trace_record(record, cfg.classifier_sensitivity)
                record.security.flags_reviewed = len(classifier_result.flags)
                record.security.classifier_version = "0.1.0"

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
def review(web: bool, port: int) -> None:
    """Review pending traces before upload (Tier 3)."""
    from .state import STAGING_DIR

    if web:
        try:
            from .review.web.app import create_app
            app = create_app(str(STAGING_DIR))
            click.echo(f"Starting web review at http://localhost:{port}")
            click.echo("Press Ctrl+C to stop.")
            app.run(host="127.0.0.1", port=port, debug=False)
        except ImportError:
            click.echo("Flask not installed. Run: pip install opentraces[web]")
            sys.exit(2)
    else:
        from .review.cli_review import run_cli_review
        run_cli_review(STAGING_DIR)


@main.command()
@click.option("--approved-only", is_flag=True, help="Only push approved traces")
def push(approved_only: bool) -> None:
    """Upload approved traces to HuggingFace Hub."""
    from .config import get_dataset_name
    from .state import StateManager, TraceStatus, StagingLock, STAGING_DIR
    from .upload.hf_hub import HFUploader
    from opentraces_schema import TraceRecord

    cfg = load_config()
    if not cfg.hf_token:
        click.echo("Not authenticated. Run 'opentraces auth' first.")
        emit_json(error_response("NOT_AUTHENTICATED", "auth", "No HF token", "Run: opentraces auth"))
        sys.exit(3)

    state = StateManager()
    traces_to_upload = state.get_pending_upload_traces()

    if not traces_to_upload:
        click.echo("No traces ready for upload.")
        emit_json({"status": "ok", "uploaded": 0, "message": "No approved traces to upload"})
        return

    # Load trace records from staging files
    records = []
    for entry in traces_to_upload:
        if entry.file_path:
            from pathlib import Path
            staging_file = Path(entry.file_path)
            if staging_file.exists():
                try:
                    data = staging_file.read_text().strip()
                    record = TraceRecord.model_validate_json(data)
                    records.append(record)
                except Exception as e:
                    click.echo(f"  Error loading {entry.trace_id}: {e}", err=True)

    if not records:
        click.echo("No valid traces to upload.")
        return

    # Get username from HF
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=cfg.hf_token)
        user_info = api.whoami()
        username = user_info.get("name", "unknown")
    except Exception as e:
        click.echo(f"Could not get HF username: {e}")
        sys.exit(4)

    repo_id = get_dataset_name(cfg, username)
    click.echo(f"Uploading {len(records)} traces to {repo_id}...")

    try:
        with StagingLock():
            uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
            uploader.ensure_repo_exists()
            result = uploader.upload_traces(records)

            if result.success:
                for entry in traces_to_upload:
                    state.set_trace_status(entry.trace_id, TraceStatus.UPLOADED)
                click.echo(f"Uploaded {result.trace_count} traces as {result.shard_name}")
                emit_json({
                    "status": "ok",
                    "uploaded": result.trace_count,
                    "shard": result.shard_name,
                    "repo_url": result.repo_url,
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
    from pathlib import Path
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
