"""CLI hidden debug/audit commands: _capture, _assess-remote, _audit-spec, _audit-run."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main

logger = logging.getLogger("opentraces.cli._debug")


def load_config():
    return _cli.load_config()


def emit_json(data):
    return _cli.emit_json(data)


def error_response(*a, **k):
    return _cli.error_response(*a, **k)


def human_echo(*a, **k):
    return _cli.human_echo(*a, **k)


def _auth_identity(*a, **k):
    return _cli._auth_identity(*a, **k)


def _capture_sessions_into_project(*a, **k):
    return _cli._capture_sessions_into_project(*a, **k)


def _current_project_session_dir(*a, **k):
    return _cli._current_project_session_dir(*a, **k)




@main.command("_capture", hidden=True)
@click.option("--session-dir", required=False, default=None, type=click.Path(), help="Path to Claude Code session dir (auto-discovered if omitted)")
@click.option("--project-dir", required=True, type=click.Path(exists=True), help="Path to project root")
def capture(session_dir: str | None, project_dir: str) -> None:
    """Capture a Claude Code session (hidden, for automation)."""
    proj_path = Path(project_dir)

    if session_dir:
        session_path = Path(session_dir)
        if not session_path.exists():
            click.echo(f"Session dir not found: {session_dir}", err=True)
            return
    else:
        session_path = _current_project_session_dir(proj_path)
        if session_path is None:
            click.echo("No session dir found for this project.", err=True)
            return

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
    import subprocess
    from datetime import datetime

    from ..quality.engine import assess_batch, generate_report
    from ..quality.gates import check_gate
    from ..quality.summary import build_summary
    from ..publish.huggingface.upload import HFUploader
    from ..publish.huggingface.dataset_card import generate_dataset_card
    from ..core.config import load_config
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
        try:
            subprocess.run(["hf-mount", "stop", mount_path], capture_output=True, timeout=30)
        except Exception as _e:
            logger.warning("hf-mount stop failed: %s", _e)


@main.command("_audit-spec", hidden=True)
@click.option("--model", default="haiku", type=click.Choice(["haiku", "sonnet", "opus"]),
              help="Draft model for proposing intent entries")
@click.option("--non-interactive", is_flag=True, default=False,
              help="Fail if the spec is incomplete (no prompts, no writes)")
@click.option("--spec-path", default=None, type=click.Path(),
              help="Override field_intent.yaml path (for testing)")
def audit_spec(model: str, non_interactive: bool, spec_path: str | None) -> None:
    """Fill missing entries in field_intent.yaml (dev-only, hidden)."""
    from ..quality.field_intent.generator import SPEC_PATH, fill_interactive
    path = Path(spec_path) if spec_path else SPEC_PATH
    rc = fill_interactive(path, model=model, non_interactive=non_interactive)
    if rc != 0:
        raise SystemExit(rc)


@main.command("_audit-run", hidden=True)
@click.option("--sample", type=int, default=10, help="Number of traces to sample")
@click.option("--dataset", default=None, help="Remote HF dataset (user/repo) instead of local staging")
@click.option("--model", default="haiku", type=click.Choice(["haiku", "sonnet", "opus"]))
@click.option("--staging-dir", default=None, type=click.Path(),
              help="Override staging dir (default: .opentraces/staging)")
@click.option("--spec-path", default=None, type=click.Path(),
              help="Override field_intent.yaml path (for testing)")
@click.option("--output-dir", default=None, type=click.Path(),
              help="Where to write audit_report.md / findings.json (default: .opentraces/reports)")
def audit_run(sample: int, dataset: str | None, model: str,
              staging_dir: str | None, spec_path: str | None,
              output_dir: str | None) -> None:
    """Run field-intent audit on sampled traces (dev-only, hidden)."""
    from ..quality.field_intent.auditor import (
        SPEC_PATH, audit_run as _run, findings_to_json, format_report,
    )
    spec = Path(spec_path) if spec_path else SPEC_PATH
    staging = Path(staging_dir) if staging_dir else None
    try:
        report = _run(
            sample=sample, staging_dir=staging, dataset=dataset,
            spec_path=spec, model=model,
        )
    except RuntimeError as e:
        click.echo(str(e), err=True)
        raise SystemExit(2)

    out_dir = Path(output_dir) if output_dir else Path(".opentraces/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "audit_report.md"
    js = out_dir / "findings.json"
    md.write_text(format_report(report))
    js.write_text(findings_to_json(report))
    click.echo(
        f"Audited {report.traces_sampled} traces, {len(report.findings)} findings. "
        f"{md} + {js}"
    )


