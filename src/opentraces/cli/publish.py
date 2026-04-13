"""CLI publish commands: push traces to HuggingFace Hub."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main
from ..core.config import load_project_config, save_config  # noqa: F401


def load_config():
    return _cli.load_config()
from ..core.workflow import (  # noqa: F401
    DEFAULT_REMOTE_NAME,
    normalize_push_policy,
    normalize_review_policy,
)

logger = logging.getLogger("opentraces.cli.publish")


def emit_json(data):
    return _cli.emit_json(data)


def error_response(*a, **k):
    return _cli.error_response(*a, **k)


def human_echo(*a, **k):
    return _cli.human_echo(*a, **k)


def human_hint(*a, **k):
    return _cli.human_hint(*a, **k)


def _auth_identity(*a, **k):
    return _cli._auth_identity(*a, **k)


def _default_repo(*a, **k):
    return _cli._default_repo(*a, **k)


def _is_interactive_terminal():
    return _cli._is_interactive_terminal()


def _resolve_repo_id(*a, **k):
    return _cli._resolve_repo_id(*a, **k)




@main.command(
    examples=[
        "opentraces push",
        "opentraces push --private",
        "opentraces push --llm-review   # gate upload on Tier 2 verdict",
    ],
    see_also=[
        ("opentraces assess", "score traces before upload"),
        ("opentraces setup review-llm", "configure the Tier 2 reviewer"),
    ],
    option_groups=[
        ("Visibility", ["private", "public", "publish", "gated"]),
        ("Destination", ["repo"]),
        ("Quality gates", ["run_assess", "llm_review"]),
        ("Pipeline overrides", ["no_trufflehog", "no_intent"]),
    ],
)
@click.option("--private", is_flag=True, help="Force private visibility (overrides config)")
@click.option("--public", is_flag=True, help="Force public visibility (overrides config)")
@click.option("--publish", is_flag=True, help="Change an existing private dataset to public (no upload)")
@click.option("--gated", is_flag=True, help="Enable gated access (auto-approve) on the dataset")
@click.option("--repo", default=None, help="HF dataset repo (default: username/opentraces)")
@click.option("--assess", "run_assess", is_flag=True, help="Score traces after upload and include in dataset card")
@click.option("--llm-review", "llm_review", is_flag=True,
              help="Require a clean Tier 2 verdict on every committed trace before upload")
@click.option("--no-trufflehog", "no_trufflehog", is_flag=True,
              help="Skip Tier 1.5 TruffleHog scanning for this push only")
@click.option("--no-intent", "no_intent", is_flag=True,
              help="Skip Intent enrichment for this push only")
def push(private: bool, public: bool, publish: bool, gated: bool, repo: str | None,
         run_assess: bool, llm_review: bool, no_trufflehog: bool, no_intent: bool) -> None:
    """Upload committed traces to HuggingFace Hub."""
    from ..core.config import get_project_staging_dir, load_project_config, save_project_config
    from ..core.inbox import load_traces
    from ..core.state import StateManager, TraceStatus, StagingLock
    from ..publish.huggingface.upload import HFUploader
    from ..publish.huggingface.dataset_card import generate_dataset_card
    from opentraces_schema import TraceRecord

    # Plan 032: --llm-review gate — abort before touching the uploader if any
    # committed trace lacks a verdict or has a blocking one.
    if llm_review:
        staging = get_project_staging_dir(Path.cwd())
        pending_block: list[str] = []
        if staging.exists():
            for rec in load_traces(staging):
                meta = (rec.get("metadata") or {}).get("llm_review") or {}
                status = meta.get("status")
                shareable = meta.get("shareable")
                missed = meta.get("missed_sensitive_data")
                if status != "complete":
                    pending_block.append(
                        f"{rec.get('trace_id', '?')} (not reviewed)"
                    )
                    continue
                if shareable == "no" or missed == "yes":
                    pending_block.append(
                        f"{rec.get('trace_id', '?')} (verdict: shareable={shareable}, missed={missed})"
                    )
        if pending_block:
            human_echo("Aborting: --llm-review requires a clean verdict for every committed trace.")
            for entry in pending_block:
                human_echo(f"  - {entry}")
            human_hint("Run: opentraces review-llm")
            emit_json(error_response(
                "LLM_REVIEW_BLOCKED", "upload",
                f"{len(pending_block)} trace(s) lack a shareable verdict",
                "Run 'opentraces review-llm' to produce verdicts, then retry.",
            ))
            sys.exit(3)

    if no_trufflehog:
        # One-shot override. Only relevant when Tier 1.5 is enabled in config;
        # push does not invoke trufflehog directly today, but logging the
        # override keeps it obvious in CI if the push later inherits the tier.
        human_echo("[--no-trufflehog] Tier 1.5 scanning skipped for this push.")

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
    from ..core.config import get_project_state_path
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

    # Intent enrichment (plan 038) + post-processor chain — one pre-upload pass.
    # Precedence: CLI flag (--no-intent) > project intent.mode > global intent.mode.
    from ..core.config import ProjectConfig as _ProjectConfig
    try:
        _raw = load_project_config(Path.cwd())
        proj_cfg_obj = _ProjectConfig.model_validate(_raw) if _raw else None
    except Exception:
        proj_cfg_obj = None
    effective_intent_mode = (
        "off" if no_intent
        else (proj_cfg_obj.intent.mode if (proj_cfg_obj and proj_cfg_obj.intent) else cfg.intent.mode)
    )
    if effective_intent_mode == "on":
        from ..enrichment.intent import enrich_intent
        from ..enrichment.intent_backends import claude_cli_client
        enriched = []
        for rec in records:
            try:
                enriched.append(enrich_intent(rec, claude_cli_client))
            except Exception as exc:  # pragma: no cover - defensive
                click.echo(f"  intent enrichment failed for {rec.trace_id}: {exc}", err=True)
                enriched.append(rec)
        records = enriched

    processor_specs = proj_cfg_obj.post_processors if proj_cfg_obj else []
    push_processors = [s for s in processor_specs if s.when == "push"]
    if push_processors:
        from ..core.processors import run_chain
        processed_records = []
        for rec in records:
            res = run_chain(rec, push_processors, when="push")
            processed_records.append(res.record)
        records = processed_records

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
                    from ..core.publish_flow import mark_uploaded as _mark_uploaded
                    _mark_uploaded(
                        state,
                        (e.trace_id for e in traces_to_upload if e.trace_id in duplicate_trace_ids),
                    )
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
                            from ..quality.engine import assess_batch
                            from ..quality.gates import check_gate
                            from ..quality.summary import build_summary
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
                    uploader.api.upload_file(
                        path_or_fileobj=io.BytesIO(card.encode("utf-8")),
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
                from ..core.publish_flow import mark_uploaded as _mark_uploaded
                _mark_uploaded(
                    state,
                    (e.trace_id for e in traces_to_upload if e.trace_id in loaded_trace_ids),
                )

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
                from ..core.publish_flow import mark_failed as _mark_failed
                for entry in traces_to_upload:
                    _mark_failed(state, entry.trace_id, result.error)
                click.echo(f"Upload failed: {result.error}")
                emit_json(error_response("UPLOAD_FAILED", "network", str(result.error), retryable=True))
                sys.exit(4)

    except RuntimeError as e:
        click.echo(f"Error: {e}")
        sys.exit(7)




