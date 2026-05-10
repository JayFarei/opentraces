"""CLI troubleshooting commands for the local OpenTraces bucket."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup


@click.group("bucket", cls=OpentracesGroup)
def bucket_group() -> None:
    """Inspect and troubleshoot the local trace bucket."""


@bucket_group.command("status", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_status_cmd(as_json: bool) -> None:
    """Show local bucket health, sync eligibility, and trail freshness."""
    from ..core.bucket_store import bucket_status

    payload = bucket_status(write_manifest=True)
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    bucket = payload["bucket"]
    config = payload.get("config") or {}
    remote_config = config.get("remote") or {}
    trace_records = bucket.get("trace_records") or {}
    trail = bucket.get("trail") or {}
    raw_sources = bucket.get("raw_sources") or {}
    trail_events = bucket.get("trail_events") or {}
    sync = bucket.get("sync") or {}
    click.echo(f"Bucket: {bucket.get('root')}")
    click.echo(f"  storage:    {config.get('storage', 'local')}")
    if remote_config.get("enabled"):
        click.echo(f"  remote:     {remote_config.get('url') or 'configured'}")
        click.echo(f"  sync policy: {remote_config.get('sync_policy', 'daemon')}")
    click.echo(f"  traces:     {trace_records.get('object_count', 0)}")
    click.echo(f"  raw sources: {raw_sources.get('object_count', 0)}")
    click.echo(f"  trail events: {trail_events.get('event_count', 0)}")
    click.echo(f"  syncable:   {trace_records.get('syncable_count', 0)}")
    click.echo(f"  stale sec:  {trace_records.get('security_stale_count', 0)}")
    click.echo(f"  unfiltered: {trace_records.get('unfiltered_count', 0)}")
    click.echo(f"  last ingest: {trace_records.get('last_write_at') or 'never'}")
    click.echo(f"  trail sync: {trail.get('last_projection_sync_at') or 'never'}")
    click.echo(f"  trail stale: {trail.get('stale_count', 0)}")
    click.echo(
        "  remote eligible: "
        + ("yes" if sync.get("eligible") else "no")
    )
    for reason in sync.get("blocked_reasons") or []:
        click.echo(f"    blocked: {reason}")


@bucket_group.command("manifest", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_manifest_cmd(as_json: bool) -> None:
    """Materialize and print the local bucket manifest."""
    from ..core.bucket_store import bucket_manifest, bucket_manifest_path

    manifest = bucket_manifest(write=True, include_objects=False)
    if as_json:
        click.echo(json.dumps({"status": "ok", "manifest": manifest}, indent=2, sort_keys=True))
        return
    click.echo(f"Bucket manifest: {bucket_manifest_path()}")
    click.echo(f"  digest: {manifest.get('digest')}")


@bucket_group.group("remote", cls=OpentracesGroup)
def bucket_remote_group() -> None:
    """Manage the configured private bucket remote."""


@bucket_remote_group.command("status", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_status_cmd(remote_root: Path | None, as_json: bool) -> None:
    """Compare local bucket digest with the configured private remote."""
    from ..core.bucket_remote import remote_status

    payload = {"status": "ok", "remote": remote_status(fake_root=remote_root)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    remote = payload["remote"]
    click.echo(f"Bucket remote: {remote.get('state')}")
    if remote.get("remote_root"):
        click.echo(f"  root: {remote.get('remote_root')}")
    if remote.get("repo_id"):
        click.echo(f"  repo: {remote.get('repo_id')}")
    if remote.get("local_digest"):
        click.echo(f"  local:  {remote.get('local_digest')}")
    if remote.get("remote_digest"):
        click.echo(f"  remote: {remote.get('remote_digest')}")
    if remote.get("advice"):
        click.echo(f"  advice: {remote.get('advice')}")


@bucket_remote_group.command("diff", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_diff_cmd(remote_root: Path | None, as_json: bool) -> None:
    """Compare local and remote bucket manifests."""
    from ..core.bucket_remote import remote_diff

    remote = remote_diff(fake_root=remote_root)
    payload = {"status": "ok", "remote": remote}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Bucket remote diff: {remote.get('state')}")
    click.echo(f"  different: {remote.get('different')}")


@bucket_remote_group.command("push", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--force", is_flag=True, help="Overwrite a remote-ahead or diverged bucket.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_push_cmd(remote_root: Path | None, force: bool, as_json: bool) -> None:
    """Mirror the local bucket into the configured private remote."""
    from ..core.bucket_remote import BucketRemoteError, remote_push

    try:
        remote = remote_push(fake_root=remote_root, force=force)
    except (BucketRemoteError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "remote": remote}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Bucket remote pushed: {remote.get('remote_root') or remote.get('repo_id')}")
    click.echo(
        f"  files: {remote.get('files_copied', remote.get('files_uploaded', 0))}"
    )


@bucket_remote_group.command("pull", cls=OpentracesCommand)
@click.option(
    "--root",
    "remote_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Fake remote root override. Defaults to configured bucket remote.",
)
@click.option("--force", is_flag=True, help="Overwrite a local-ahead or diverged bucket.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_remote_pull_cmd(remote_root: Path | None, force: bool, as_json: bool) -> None:
    """Restore the local bucket from the configured private remote."""
    from ..core.bucket_remote import BucketRemoteError, remote_pull

    try:
        remote = remote_pull(fake_root=remote_root, force=force)
    except (BucketRemoteError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "remote": remote}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Bucket remote pulled: {remote.get('remote_root') or remote.get('repo_id')}")
    click.echo(
        f"  files: {remote.get('files_copied', remote.get('files_downloaded', 0))}"
    )


@bucket_group.command("replay", cls=OpentracesCommand)
@click.option(
    "--repo",
    "repo",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Git repository to receive the bucket-exported Trace Trails ref.",
)
@click.option(
    "--repo-id",
    default=None,
    help="Bucket TrailEvents repo id. Required when the bucket has multiple exports.",
)
@click.option("--force", is_flag=True, help="Replace an existing differing Trace Trails ref.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def bucket_replay_cmd(
    repo: Path,
    repo_id: str | None,
    force: bool,
    as_json: bool,
) -> None:
    """Replay bucket-exported Trace Trails into a Git repository."""
    from ..core.bucket_store import restore_trail_events_to_repo

    try:
        replay = restore_trail_events_to_repo(repo, repo_id=repo_id, force=force)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "replay": replay}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Bucket replay: {replay.get('state')}")
    click.echo(f"  repo: {replay.get('repo')}")
    click.echo(f"  repo id: {replay.get('repo_id')}")
    click.echo(f"  events: {replay.get('events_imported', 0)}")
