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
from ..core.config import load_project_config, save_config, save_project_config  # noqa: F401


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


def _refresh_dataset_card(uploader, repo_id: str, *, run_assess: bool = False,
                          fallback_traces=None) -> None:
    """Regenerate and upload the dataset README.md from current remote shards.

    Best-effort: any failure is logged as a warning but never raises.
    """
    from ..publish.huggingface.dataset_card import generate_dataset_card
    try:
        existing_card = None
        try:
            local = uploader.api.hf_hub_download(repo_id, "README.md", repo_type="dataset")
            existing_card = Path(local).read_text()
        except Exception as e:
            logger.debug("Could not fetch existing dataset card: %s", e)

        all_remote_traces = uploader.fetch_all_remote_traces()
        card_traces = all_remote_traces or (fallback_traces or [])
        if not card_traces:
            return

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
                    click.echo(
                        f"  Overall utility: {summary.overall_utility:.1f}% | "
                        f"Gate: {'PASS' if summary.gate_passed else 'FAIL'}"
                    )
                else:
                    click.echo("  Warning: quality.json upload failed -- quality scores excluded from README", err=True)
                    quality_summary = None
            except Exception as e:
                click.echo(f"  Warning: quality assessment failed: {e}", err=True)

        card = generate_dataset_card(repo_id, card_traces, existing_card, quality_summary=quality_summary)
        uploader.api.upload_file(
            path_or_fileobj=card.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="dataset",
        )
    except Exception as e:
        click.echo(f"  Warning: dataset card update failed: {e}", err=True)


def _maybe_migrate_remote(uploader, target_version: str,
                          migrate_remote: bool | None, assume_yes: bool) -> bool:
    """Detect outdated remote shards and migrate them in-place after consent.

    Behavior:
      - migrate_remote=True  → migrate without prompting
      - migrate_remote=False → skip without prompting
      - migrate_remote=None and assume_yes → migrate without prompting
      - migrate_remote=None and interactive → prompt
    Detection failures are logged at debug and treated as no-op.
    """
    try:
        report = uploader.detect_outdated_shards(target_version)
    except Exception as e:
        logger.debug("Could not inspect remote shards for schema drift: %s", e)
        return False
    if not report["total_outdated"]:
        return False

    version_summary = ", ".join(f"{v}={n}" for v, n in sorted(report["version_counts"].items()))
    click.echo(
        f"Remote has {report['total_outdated']} record(s) on older schema "
        f"(distribution: {version_summary}; target: {target_version})."
    )
    if migrate_remote is True:
        do_migrate = True
    elif migrate_remote is False:
        do_migrate = False
    elif assume_yes:
        do_migrate = True
    else:
        do_migrate = click.confirm(
            f"Migrate {len(report['shards'])} shard(s) to schema "
            f"{target_version}? This rewrites the affected JSONL files in place.",
            default=True,
        )
    if not do_migrate:
        click.echo(
            "  Skipping migration. Mixed schema versions may cause the HF "
            "dataset viewer to render inconsistent columns.",
            err=True,
        )
        return False

    click.echo(f"Migrating {len(report['shards'])} shard(s)...")
    mig = uploader.migrate_outdated_shards(target_version)
    click.echo(
        f"  Migrated {mig['migrated_records']} record(s) "
        f"across {len(mig['migrated_shards'])} shard(s)."
    )
    for err in mig["errors"]:
        click.echo(f"  Warning: shard {err['shard']} migration failed: {err['error']}", err=True)
    return mig["migrated_records"] > 0


def _normalize_repo_id(repo: str, username_hint: str | None = None) -> str:
    """Normalize a repo reference to ``owner/name``."""
    if "://" in repo:
        repo = repo.split("://", 1)[1]
    if "/" in repo or not username_hint:
        return repo
    return f"{username_hint}/{repo}"


def _remote_repo_id(remote_name: str, remotes: dict) -> str | None:
    """Return the dataset repo_id stored for *remote_name* if available."""
    cfg = remotes.get(remote_name) or {}
    url = cfg.get("url")
    if isinstance(url, str) and url:
        return url.split("://", 1)[1] if "://" in url else url
    if remote_name and remote_name != "origin":
        return remote_name
    return None


def _match_remote_key(remotes: dict, repo_id: str) -> str | None:
    """Find the remote key that already points at *repo_id*."""
    for name, cfg in remotes.items():
        url = cfg.get("url") if isinstance(cfg, dict) else None
        if name == repo_id:
            return name
        if isinstance(url, str):
            normalized = url.split("://", 1)[1] if "://" in url else url
            if normalized == repo_id:
                return name
    return None


def _resolve_push_target(proj_config: dict, username: str, repo: str | None = None) -> tuple[str, str]:
    """Return ``(remote_name, repo_id)`` for the current push/publish run."""
    remotes = proj_config.get("remotes") or {}
    active_remote = proj_config.get("active_remote")

    if repo:
        repo_id = _normalize_repo_id(repo, username)
        remote_name = _match_remote_key(remotes, repo_id) or repo_id
        return remote_name, repo_id

    if active_remote and active_remote in remotes:
        repo_id = _remote_repo_id(active_remote, remotes) or active_remote
        return active_remote, repo_id

    if len(remotes) == 1:
        remote_name = next(iter(remotes))
        repo_id = _remote_repo_id(remote_name, remotes) or remote_name
        return remote_name, repo_id

    fallback_repo = f"{username}/opentraces"
    return fallback_repo, fallback_repo


def _resolve_push_visibility(
    proj_config: dict,
    remote_name: str,
    *,
    default_visibility: str,
    private: bool,
    public: bool,
) -> str:
    """Return the visibility that should be used for this push/publish run."""
    if public:
        return "public"
    if private:
        return "private"

    remotes = proj_config.get("remotes") or {}
    remote_cfg = remotes.get(remote_name) or {}
    visibility = remote_cfg.get("visibility")
    if visibility in {"public", "private"}:
        return visibility

    proj_default = proj_config.get("default_visibility")
    if proj_default in {"public", "private"}:
        return proj_default
    return default_visibility


def _persist_push_target(
    project_dir: Path,
    proj_config: dict,
    remote_name: str,
    repo_id: str,
    visibility: str,
) -> None:
    """Persist the chosen push target back into the new remotes shape."""
    payload = dict(proj_config)
    payload.pop("remote", None)
    payload.pop("visibility", None)

    remotes = dict(payload.get("remotes") or {})
    remote_cfg = dict(remotes.get(remote_name) or {})
    remote_cfg["url"] = f"hf://{repo_id}"
    remote_cfg["visibility"] = visibility
    remotes[remote_name] = remote_cfg
    payload["remotes"] = remotes
    if not payload.get("active_remote"):
        payload["active_remote"] = remote_name
    save_project_config(project_dir, payload)


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
        ("opentraces setup llm-review", "configure the Tier 2 reviewer"),
    ],
    option_groups=[
        ("Visibility", ["private", "public", "publish", "gated"]),
        ("Destination", ["repo"]),
        ("Quality gates", ["run_assess", "llm_review"]),
        ("Pipeline overrides", ["no_trufflehog"]),
        ("Schema migration", ["migrate_remote", "assume_yes"]),
    ],
)
@click.option("--private", is_flag=True, help="Force private visibility (overrides config)")
@click.option("--public", is_flag=True, help="Force public visibility (overrides config)")
@click.option("--publish", is_flag=True, help="Change an existing private dataset to public (no upload)")
@click.option("--gated", is_flag=True, help="Enable gated access (auto-approve) on the dataset")
@click.option("--repo", default=None, help="HF dataset repo (default: username/opentraces)")
@click.option("--assess/--no-assess", "run_assess", default=True, help="Run quality scoring and include badges in the dataset card (card itself always refreshes). Default: enabled.")
@click.option("--llm-review", "llm_review", is_flag=True,
              help="Require a clean Tier 2 verdict on every staged trace before upload")
@click.option("--no-trufflehog", "no_trufflehog", is_flag=True,
              help="Skip Tier 1.5 TruffleHog scanning for this push only")
@click.option("--migrate-remote/--no-migrate-remote", "migrate_remote", default=None,
              help="Auto-migrate older-schema shards on the remote. Default: prompt.")
@click.option("--yes", "-y", "assume_yes", is_flag=True,
              help="Skip interactive prompts (e.g. assume yes to schema migration).")
def push(private: bool, public: bool, publish: bool, gated: bool, repo: str | None,
         run_assess: bool, llm_review: bool, no_trufflehog: bool,
         migrate_remote: bool | None, assume_yes: bool) -> None:
    """Upload staged traces to HuggingFace Hub."""
    from ..core.config import (
        get_project_traces_dir, load_project_config, save_project_config,
        project_is_opted_in,
    )
    from ..core.inbox import load_traces
    from ..core.state import StateManager, TraceStatus, TraceLock
    from ..publish.huggingface.upload import HFUploader
    from ..publish.huggingface.dataset_card import generate_dataset_card
    from opentraces_schema import TraceRecord

    # Hard opt-in gate: refuse to push from any directory that has not
    # run `opentraces init`. This is the last line of defence against
    # accidentally uploading traces from an unconsented project.
    if not project_is_opted_in(Path.cwd()):
        click.echo(
            "opentraces: cannot push — this project has not opted in.\n"
            "Run 'opentraces init' here first. Push only operates on "
            "projects that have been explicitly initialized.",
            err=True,
        )
        sys.exit(2)

    # Plan 032: --llm-review gate — abort before touching the uploader if any
    # committed trace lacks a verdict or has a blocking one.
    #
    # Scope the check to the staged (COMMITTED) set only. Iterating every
    # JSONL in the staging dir would pull in inbox traces the user hasn't
    # reviewed yet and block the push even though those traces aren't
    # part of this upload — bug reported from the TUI push modal.
    if llm_review:
        from ..core.config import get_project_state_path as _get_state_path
        staging = get_project_traces_dir(Path.cwd())
        _state_path = _get_state_path(Path.cwd())
        _state = StateManager(state_path=_state_path)
        _committed_ids = {
            e.trace_id for e in _state.get_traces_by_status(TraceStatus.COMMITTED)
        }
        pending_block: list[str] = []
        if staging.exists() and _committed_ids:
            for rec in load_traces(staging):
                if rec.get("trace_id") not in _committed_ids:
                    continue
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
            human_echo("Aborting: --llm-review requires a clean verdict for every staged trace.")
            for entry in pending_block:
                human_echo(f"  - {entry}")
            human_hint("Run: opentraces llm-review")
            emit_json(error_response(
                "LLM_REVIEW_BLOCKED", "upload",
                f"{len(pending_block)} staged trace(s) lack a shareable verdict",
                "Run 'opentraces llm-review' to produce verdicts, then retry.",
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
        human_hint("Run: opentraces auth login")
        emit_json(error_response("NOT_AUTHENTICATED", "auth", "No HF token", "Run: opentraces auth login"))
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

    proj_config = load_project_config(Path.cwd())
    proj_config.pop("remote", None)
    proj_config.pop("visibility", None)
    remotes = proj_config.get("remotes") or {}
    remote_name, repo_id = _resolve_push_target(proj_config, username, repo)

    # If no remote was configured, run the shared interactive selector.
    if not repo and not remotes:
        click.echo("No remote configured.")
        selected_repo, selected_vis = _choose_remote_interactively(f"{username}/{DEFAULT_REMOTE_NAME}")
        if selected_repo:
            repo_id = selected_repo
            remote_name = _match_remote_key(remotes, repo_id) or repo_id
            remotes[remote_name] = {
                "url": f"hf://{repo_id}",
                "visibility": selected_vis or "private",
            }
            proj_config["remotes"] = remotes
            proj_config["active_remote"] = remote_name
            save_project_config(Path.cwd(), proj_config)
            click.echo(f"Remote set to: {repo_id} ({remotes[remote_name]['visibility']})\n")
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
                _persist_push_target(Path.cwd(), proj_config, remote_name, repo_id, "public")
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

    from ..core.config import get_project_state_path
    proj_state_path = get_project_state_path(Path.cwd())
    state = StateManager(state_path=proj_state_path)
    # Get committed traces
    traces_to_upload = state.get_traces_by_status(TraceStatus.COMMITTED)

    # Run remote schema migration regardless of whether we have new traces to
    # upload. A repo can drift between schema versions across contributors,
    # and the user should be able to clean it up by just running 'push' even
    # when their inbox is empty.
    try:
        from opentraces_schema import SCHEMA_VERSION as _TARGET_SCHEMA
        _mig_uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
        if _maybe_migrate_remote(_mig_uploader, _TARGET_SCHEMA, migrate_remote, assume_yes):
            # Migration changed remote shards. Refresh the README so the card
            # reflects the now-uniform schema even on migration-only runs.
            _refresh_dataset_card(_mig_uploader, repo_id, run_assess=run_assess)
    except Exception as e:
        logger.debug("Pre-upload migration check skipped: %s", e)

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
        emit_json({"status": "ok", "uploaded": 0, "message": "No staged traces ready to upload"})
        return

    # Load trace records from staging files, track which ones loaded successfully
    records = []
    loaded_trace_ids = set()
    missing_file_path: list[str] = []
    missing_on_disk: list[str] = []
    load_errors: list[tuple[str, str]] = []
    for entry in traces_to_upload:
        if not entry.file_path:
            missing_file_path.append(entry.trace_id)
            continue
        staging_file = Path(entry.file_path)
        if not staging_file.exists():
            missing_on_disk.append(entry.trace_id)
            continue
        try:
            data = staging_file.read_text().strip()
            record = TraceRecord.model_validate_json(data)
            records.append(record)
            loaded_trace_ids.add(entry.trace_id)
        except Exception as e:
            click.echo(f"  Error loading {entry.trace_id}: {e}", err=True)
            load_errors.append((entry.trace_id, str(e)))

    if not records:
        # Distinguish "load failure" from "nothing to push" — there were
        # committed entries, none of them loaded. Exit non-zero so the
        # web viewer / any caller doesn't mistake it for a successful push.
        total = len(traces_to_upload)
        click.echo(f"No valid traces to upload ({total} committed entry/entries could not be loaded).")
        if missing_file_path:
            click.echo(f"  {len(missing_file_path)} entry/entries missing file_path (state drift).")
        if missing_on_disk:
            click.echo(f"  {len(missing_on_disk)} staging file(s) not found on disk.")
        if load_errors:
            click.echo(f"  {len(load_errors)} file(s) failed schema validation.")
        emit_json(error_response(
            "UPLOAD_LOAD_FAILED", "upload",
            f"{total} committed trace(s) could not be loaded",
            "Check file_path in state.json and the staging directory; re-run 'opentraces _scan' to rehydrate.",
        ))
        sys.exit(4)

    # Run configured post-processor chain — one pre-upload pass.
    from ..core.config import ProjectConfig as _ProjectConfig
    try:
        _raw = load_project_config(Path.cwd())
        proj_cfg_obj = _ProjectConfig.model_validate(_raw) if _raw else None
    except Exception:
        proj_cfg_obj = None

    processor_specs = proj_cfg_obj.post_processors if proj_cfg_obj else []
    if processor_specs:
        from ..core.processors import run_chain
        processed_records = []
        for rec in records:
            res = run_chain(rec, processor_specs)
            processed_records.append(res.record)
        records = processed_records

    visibility_label = _resolve_push_visibility(
        proj_config,
        remote_name,
        default_visibility=cfg.dataset_visibility,
        private=private,
        public=public,
    )
    is_private = visibility_label == "private"

    try:
        with TraceLock(Path.cwd()):
            uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
            try:
                uploader.ensure_repo_exists(private=is_private)
            except Exception as e:
                code, kind, message, hint = _cli._classify_hf_repo_error(e, repo_id)
                if kind == "unknown":
                    raise
                click.echo(message, err=True)
                if hint:
                    click.echo(f"  hint: {hint}", err=True)
                    human_hint(hint)
                emit_json(error_response(code, kind, message, hint))
                sys.exit(3)

            # Dedup + lineage: one-pass scan of remote shards.
            remote_index = uploader.fetch_remote_index()
            remote_hashes = remote_index.content_hashes
            # Cache the index for TUI "supersedes remote" hints + later
            # `opentraces pull` staleness checks. Best-effort: a failed
            # write should not block the push.
            try:
                from ..core.config import get_project_dir as _proj_dir
                from ..publish.huggingface.remote_index import cache_path_for as _cache_path
                remote_index.save(_cache_path(_proj_dir(Path.cwd())))
            except Exception as _e:  # pragma: no cover — best-effort cache
                logging.getLogger(__name__).debug("remote_index cache save failed: %s", _e)
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
                        remote_name=remote_name,
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
                _refresh_dataset_card(uploader, repo_id, run_assess=run_assess, fallback_traces=records)

            if result.success:
                # Apply gated access if requested
                if gated:
                    try:
                        uploader.set_gated()
                    except Exception as e:
                        click.echo(f"  Warning: failed to set gated access: {e}", err=True)

                # Only mark traces that were actually loaded and uploaded.
                # Per Step 3: thread the active remote name through so
                # uploaded_to[<remote>] is recorded for per-remote replay.
                from ..core.publish_flow import mark_uploaded as _mark_uploaded
                _mark_uploaded(
                    state,
                    (e.trace_id for e in traces_to_upload if e.trace_id in loaded_trace_ids),
                    remote_name=remote_name,
                )

                # Print visibility-aware success message
                if is_private:
                    click.echo(f"Pushed {result.trace_count} traces (private) -- only you can see this dataset")
                    click.echo("  Run 'opentraces push --publish' when ready to share")
                else:
                    click.echo(f"Pushed {result.trace_count} traces (public) -- visible to everyone")

                # Save remote URL and visibility to project config
                try:
                    proj_config = load_project_config(Path.cwd())
                    _persist_push_target(Path.cwd(), proj_config, remote_name, repo_id, visibility_label)
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



