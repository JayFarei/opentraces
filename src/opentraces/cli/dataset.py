"""CLI commands for local executable datasets."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ..core.datasets import (
    add_dataset_remote,
    apply_remote_dataset,
    create_dataset,
    DatasetRemotePermissionError,
    DatasetRemoteSchemaAheadError,
    dataset_path,
    doctor_byte_identity,
    fake_remote_create,
    fake_remote_delete,
    fake_remote_probe,
    fake_remote_set_visibility,
    evaluate_publication_state,
    export_jsonl,
    list_datasets,
    list_dataset_remotes,
    load_dataset,
    normalize_hf_repo_id,
    publish_dataset,
    pull_dataset,
    read_row_index,
    remote_status_summary,
    remove_dataset_remote,
    repo_id_from_remote,
    set_dataset_remote_visibility,
    set_publication_review_status,
    validate_row,
    withdraw_dataset_row,
)
from ..core.workflow_runner import (
    DatasetRunLockError,
    ExecutorUnavailableError,
    run_dataset_workflow,
)
from ..core.schedules import (
    add_schedule,
    list_schedules,
    pause_schedule,
    read_schedule,
    read_schedule_logs,
    remove_schedule,
    resume_schedule,
)


@click.group("dataset", cls=OpentracesGroup)
@click.pass_context
def dataset_group(_ctx: click.Context) -> None:
    """Manage local executable datasets."""
    # Plan 058: migrate legacy inbox state into default-inbox only when
    # there is actual legacy project state. Clean dataset-only users should
    # not see a synthetic default-inbox entry.
    from ..core.default_inbox import run_bridge_once

    run_bridge_once()


@dataset_group.group("remote", cls=OpentracesGroup)
def dataset_remote_group() -> None:
    """Manage dataset-scoped HuggingFace remotes."""


@dataset_group.group("schedule", cls=OpentracesGroup)
def dataset_schedule_group() -> None:
    """Manage local dataset schedules."""


def _remote_probe(repo_id: str, token: str | None) -> dict | None:
    fake = fake_remote_probe(repo_id)
    if fake is not None:
        return fake
    if os.environ.get("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT"):
        return None
    from opentraces import cli as root_cli

    return root_cli._remote_probe(repo_id, token)


def _remote_create(repo_id: str, private: bool, token: str | None) -> bool:
    if fake_remote_create(repo_id, private):
        return True
    if os.environ.get("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT"):
        return False
    from opentraces import cli as root_cli

    return root_cli._remote_create(repo_id, private, token)


def _remote_delete(repo_id: str, token: str | None) -> None:
    if os.environ.get("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT"):
        fake_remote_delete(repo_id)
        return
    from opentraces import cli as root_cli

    root_cli._remote_delete(repo_id, token)


def _remote_set_visibility(repo_id: str, private: bool, token: str | None) -> None:
    if os.environ.get("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT"):
        fake_remote_set_visibility(repo_id, private)
        return
    from opentraces import cli as root_cli

    root_cli._remote_set_visibility(repo_id, private, token)


def _hf_auth() -> tuple[str | None, str | None]:
    from opentraces import cli as root_cli

    cfg = root_cli.load_config()
    identity = root_cli._auth_identity(cfg.hf_token) if cfg.hf_token else None
    username = identity.get("name") if identity else None
    return cfg.hf_token, username


@dataset_remote_group.command("add", cls=OpentracesCommand)
@click.argument("name")
@click.argument("repo")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remote_add(name: str, repo: str, as_json: bool) -> None:
    """Connect a local dataset to an existing HuggingFace dataset remote."""
    token, username = _hf_auth()
    try:
        repo_id = normalize_hf_repo_id(repo, username)
        info = _remote_probe(repo_id, token)
        if info is None:
            raise ValueError(
                f"No dataset at {repo_id} on HuggingFace. "
                f"Run: opentraces dataset remote create {name} {repo_id}"
            )
        repo_id = info.get("id") or repo_id
        visibility = "private" if info.get("private") else "public"
        summary = add_dataset_remote(name, repo_id, visibility=visibility)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_remote_payload(summary, as_json=as_json, verb="connected")


@dataset_remote_group.command("create", cls=OpentracesCommand)
@click.argument("name")
@click.argument("repo")
@click.option(
    "--private/--public",
    "is_private",
    default=True,
    help="HF dataset visibility. Defaults to private.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remote_create(name: str, repo: str, is_private: bool, as_json: bool) -> None:
    """Create a private-by-default HuggingFace dataset remote and bind it."""
    token, username = _hf_auth()
    try:
        repo_id = normalize_hf_repo_id(repo, username)
        created = _remote_create(repo_id, is_private, token)
        if not created:
            raise ValueError(
                f"{repo_id} already exists on HuggingFace. "
                f"Run: opentraces dataset remote add {name} {repo_id}"
            )
        visibility = "private" if is_private else "public"
        summary = add_dataset_remote(name, repo_id, visibility=visibility)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_remote_payload(summary, as_json=as_json, verb="created")


@dataset_remote_group.command("list", cls=OpentracesCommand)
@click.argument("name")
@click.option("-v", "--verbose", is_flag=True, help="Also show full URLs.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remote_list(name: str, verbose: bool, as_json: bool) -> None:
    """List remotes bound to a local dataset."""
    try:
        remotes = list_dataset_remotes(name)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        remote.name: {
            "url": remote.url,
            "visibility": remote.visibility,
            "active": remote.active,
        }
        for remote in remotes
    }
    if as_json:
        click.echo(json.dumps({"status": "ok", "dataset": name, "remotes": payload}, indent=2, sort_keys=True))
        return
    if not remotes:
        click.echo("No remotes connected.")
        return
    for remote in remotes:
        marker = "*" if remote.active else " "
        line = f"  {marker} {remote.name} ({remote.visibility})"
        if verbose:
            line = f"{line}\t{remote.url}"
        click.echo(line)


@dataset_remote_group.command("remove", cls=OpentracesCommand)
@click.argument("name")
@click.argument("remote", required=False, default=None)
@click.option("--delete-remote", is_flag=True, help="Also delete the HF dataset.")
@click.option("--yes", "confirmed", is_flag=True, help="Skip destructive confirmation.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remote_remove(
    name: str,
    remote: str | None,
    delete_remote: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    """Disconnect a dataset remote, optionally deleting the HF dataset."""
    try:
        dataset = load_dataset(name)
        resolved = remote or (
            dataset.manifest.active_remote
            if len(dataset.manifest.remotes) != 1
            else next(iter(dataset.manifest.remotes), None)
        )
        if delete_remote:
            if not confirmed:
                click.echo("Pass --yes to delete the remote dataset.", err=True)
                sys.exit(2)
            if resolved is None or resolved not in dataset.manifest.remotes:
                raise ValueError(f"remote not found: {resolved}")
            token, _username = _hf_auth()
            repo_id = repo_id_from_remote(resolved, dataset.manifest.remotes[resolved])
            _remote_delete(repo_id, token)
        summary = remove_dataset_remote(name, remote)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = _remote_payload(summary)
    payload["deleted_remote"] = delete_remote
    if as_json:
        click.echo(json.dumps({"status": "ok", "remote": payload}, indent=2, sort_keys=True))
        return
    click.echo(f"Disconnected {summary.name}.")


@dataset_remote_group.command("visibility", cls=OpentracesCommand)
@click.argument("name")
@click.argument("remote", required=False, default=None)
@click.option("--private", "make_private", flag_value=True, default=None)
@click.option("--public", "make_private", flag_value=False)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remote_visibility(
    name: str,
    remote: str | None,
    make_private: bool | None,
    as_json: bool,
) -> None:
    """Change a bound HuggingFace dataset remote between private and public."""
    if make_private is None:
        click.echo("Specify --private or --public.", err=True)
        sys.exit(2)
    try:
        dataset = load_dataset(name)
        resolved = remote or (
            dataset.manifest.active_remote
            if len(dataset.manifest.remotes) != 1
            else next(iter(dataset.manifest.remotes), None)
        )
        if resolved is None or resolved not in dataset.manifest.remotes:
            raise ValueError(f"remote not found: {resolved}")
        token, _username = _hf_auth()
        repo_id = repo_id_from_remote(resolved, dataset.manifest.remotes[resolved])
        _remote_set_visibility(repo_id, make_private, token)
        visibility = "private" if make_private else "public"
        summary = set_dataset_remote_visibility(name, resolved, visibility=visibility)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_remote_payload(summary, as_json=as_json, verb="updated")


@dataset_schedule_group.command("add", cls=OpentracesCommand)
@click.argument("name")
@click.option("--every", required=True, help="Local interval such as 30s, 15m, 2h, or 1d.")
@click.option(
    "--executor",
    type=click.Choice(["current-agent", "claude-code-headless"]),
    default="claude-code-headless",
    show_default=True,
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_add(name: str, every: str, executor: str, as_json: bool) -> None:
    """Add a local schedule for running a dataset workflow."""
    try:
        schedule = add_schedule(name, every=every, executor=executor)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_schedule_payload(schedule, as_json=as_json)


@dataset_schedule_group.command("list", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_list(as_json: bool) -> None:
    """List local dataset workflow schedules."""
    schedules = [_schedule_payload(schedule) for schedule in list_schedules()]
    if as_json:
        click.echo(json.dumps({"status": "ok", "schedules": schedules}, indent=2, sort_keys=True))
        return
    for schedule in schedules:
        click.echo(f"{schedule['dataset']}  {schedule['every']}  enabled={schedule['enabled']}")


@dataset_schedule_group.command("show", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_show(name: str, as_json: bool) -> None:
    """Show one dataset workflow schedule."""
    try:
        schedule = read_schedule(name)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_schedule_payload(schedule, as_json=as_json)


@dataset_schedule_group.command("pause", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_pause(name: str, as_json: bool) -> None:
    """Pause a dataset workflow schedule."""
    try:
        schedule = pause_schedule(name)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_schedule_payload(schedule, as_json=as_json)


@dataset_schedule_group.command("resume", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_resume(name: str, as_json: bool) -> None:
    """Resume a paused dataset workflow schedule."""
    try:
        schedule = resume_schedule(name)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_schedule_payload(schedule, as_json=as_json)


@dataset_schedule_group.command("logs", cls=OpentracesCommand)
@click.argument("name")
@click.option("--tail", is_flag=True, help="Show only recent schedule logs.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_logs(name: str, tail: bool, as_json: bool) -> None:
    """Show local scheduler log lines for a dataset."""
    try:
        logs = read_schedule_logs(name, tail=tail)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "dataset": name, "logs": logs}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    for line in logs:
        click.echo(line)


@dataset_schedule_group.command("remove", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_remove(name: str, as_json: bool) -> None:
    """Remove a dataset workflow schedule."""
    try:
        schedule = remove_schedule(name)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "removed": {
            "dataset": name,
            "schedule": _schedule_payload(schedule),
        },
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Schedule removed: {name}")


@dataset_group.command("list", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_list(as_json: bool) -> None:
    """List local HF-shaped datasets."""
    datasets = [_dataset_payload(dataset) for dataset in list_datasets()]
    if as_json:
        click.echo(json.dumps({"status": "ok", "datasets": datasets}, indent=2, sort_keys=True))
        return
    for dataset in datasets:
        click.echo(f"{dataset['name']}  {dataset['path']}")


@dataset_group.command("new", cls=OpentracesCommand)
@click.argument("name")
@click.option("--description", default=None, help="Dataset description.")
@click.option("--workflow", default=None, help="Workflow skill name.")
@click.option("--workflow-digest", default="sha256:unconfigured", help="Workflow digest.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_new(
    name: str,
    description: str | None,
    workflow: str | None,
    workflow_digest: str,
    as_json: bool,
) -> None:
    """Create a local HF-shaped dataset with an OpenTraces sidecar."""
    try:
        dataset = create_dataset(
            name,
            description=description,
            workflow_skill=workflow,
            workflow_digest=workflow_digest,
        )
    except (FileExistsError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "dataset": _dataset_payload(dataset)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Dataset created: {dataset.name}")


@dataset_group.command("show", cls=OpentracesCommand)
@click.argument("name")
@click.option("--row", "row_id", default=None, help="Show a row by row_id.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_show(name: str, row_id: str | None, as_json: bool) -> None:
    """Show a dataset manifest or one public row by row_id."""
    try:
        dataset = load_dataset(name)
        if row_id:
            row = _load_row(dataset.path, row_id)
            payload = {"status": "ok", "dataset": name, "row_id": row_id, "row": row}
        else:
            payload = {"status": "ok", "dataset": _dataset_payload(dataset)}
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    if row_id:
        click.echo(json.dumps(payload["row"], sort_keys=True))
    else:
        click.echo(f"{dataset.name}  {dataset.path}")


@dataset_group.command("check", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_check(name: str, as_json: bool) -> None:
    """Validate the local dataset layout, schema, and row files."""
    try:
        payload = _check_dataset(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"{name}: {payload['row_count']} rows")


@dataset_group.command("run", cls=OpentracesCommand)
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="Execute without appending rows or advancing cursors.")
@click.option(
    "--executor",
    type=click.Choice(["current-agent", "claude-code-headless"]),
    default=None,
    help="Workflow executor.",
)
@click.option(
    "--scope",
    type=click.Choice(["all-projects", "project", "cwd", "trace"]),
    default="all-projects",
    show_default=True,
    help="Candidate query scope.",
)
@click.option("--project", default=None, help="Project slug for --scope project.")
@click.option("--trace", "trace_id", default=None, help="Trace ID for --scope trace.")
@click.option("--limit", type=int, default=None, help="Candidate limit.")
@click.option("--since-last-run", is_flag=True, help="Use the dataset cursor.")
@click.option("--reconcile", is_flag=True, help="Run a full reconciliation scan.")
@click.option("--scheduled", is_flag=True, help="Mark this run as scheduler initiated.")
@click.option("--verbose", is_flag=True, help="Include run artefact paths.")
@click.option("--resume", default=None, help="Reserved run resume id.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_run(
    name: str,
    dry_run: bool,
    executor: str | None,
    scope: str,
    project: str | None,
    trace_id: str | None,
    limit: int | None,
    since_last_run: bool,
    reconcile: bool,
    scheduled: bool,
    verbose: bool,
    resume: str | None,
    as_json: bool,
) -> None:
    """Run the dataset workflow in dry-run, current-agent, or headless mode."""
    if resume:
        click.echo("--resume is reserved for future interrupted-run recovery.", err=True)
        sys.exit(10)
    scope_payload = {"scope": scope}
    if project:
        scope_payload["project"] = project
    if trace_id:
        scope_payload["trace_id"] = trace_id
    if since_last_run:
        scope_payload["since_last_run"] = True
    if reconcile:
        scope_payload["reconcile"] = True
    try:
        result = run_dataset_workflow(
            name,
            dry_run=dry_run,
            executor=executor,
            scope=scope_payload,
            limit=limit,
            scheduled=scheduled,
        )
    except (FileNotFoundError, ValueError, ExecutorUnavailableError, DatasetRunLockError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)

    payload = {
        "status": result.status,
        "run_id": result.run_id,
        "run": {
            **result.run_record.model_dump(mode="json"),
            "would_append_count": result.append_summary.would_append_count,
        },
        "cursor_advanced": result.cursor_advanced,
    }
    if verbose:
        payload["artefacts"] = {"run_dir": str(result.run_dir)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    if result.status == "instructions":
        click.echo(f"Run packet ready: {result.run_dir}")
    else:
        click.echo(
            f"Run {result.run_id}: appended={result.append_summary.appended_count} "
            f"duplicates={result.append_summary.duplicate_count} "
            f"invalid={result.append_summary.validation_error_count}"
        )


@dataset_group.command("review", cls=OpentracesCommand)
@click.argument("args", nargs=-1)
@click.option("--tui", "mode", flag_value="tui", default=None, help="Open TUI review.")
@click.option("--web", "mode", flag_value="web", help="Open web review.")
@click.option("--all", "all_rows", is_flag=True, help="With `reset`, reset every row to policy defaults.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_review(args: tuple[str, ...], mode: str | None, all_rows: bool, as_json: bool) -> None:
    """Review dataset rows, or reset selected rows to policy defaults."""
    if not args:
        click.echo("Usage: ot dataset review <name> OR ot dataset review reset <name> [ROW_ID...]", err=True)
        sys.exit(2)
    if args[0] == "reset":
        if len(args) < 2:
            click.echo("Usage: ot dataset review reset <name> [ROW_ID...]", err=True)
            sys.exit(2)
        _dataset_review_transition(args[1], list(args[2:]), all_rows, "reset", as_json)
        return
    if len(args) > 1:
        click.echo("Usage: ot dataset review <name>", err=True)
        sys.exit(2)
    name = args[0]
    try:
        state = evaluate_publication_state(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "dataset": name,
        "mode": mode or "cli",
        "counts": _publication_counts(state),
        "rows": {
            row_id: entry.model_dump(mode="json", exclude_none=True)
            for row_id, entry in sorted(state.rows.items())
        },
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(
        f"{name}: {payload['counts'].get('needs_review', 0)} rows need review, "
        f"{payload['counts'].get('publishable', 0)} publishable"
    )


@dataset_group.command("publish", cls=OpentracesCommand)
@click.argument("name")
@click.option("--to", "remote", default=None, help="Remote name or owner/name override.")
@click.option("--check-only", is_flag=True, help="Run all gates and stage without upload.")
@click.option("--resume", default=None, help="Resume a previous publication run id.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_publish(
    name: str,
    remote: str | None,
    check_only: bool,
    resume: str | None,
    as_json: bool,
) -> None:
    """Publish reviewed dataset rows and contract files to the active remote."""
    try:
        summary = publish_dataset(name, to=remote, check_only=check_only, resume=resume)
    except DatasetRemotePermissionError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except DatasetRemoteSchemaAheadError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "publish": {
            "dataset": summary.dataset_name,
            "remote": summary.remote_name,
            "repo_id": summary.repo_id,
            "run_id": summary.run_id,
            "uploaded": summary.uploaded,
            "check_only": summary.check_only,
            "new_row_count": summary.new_row_count,
            "duplicate_count": summary.duplicate_count,
            "needs_review_count": summary.needs_review_count,
            "blocked_count": summary.blocked_count,
            "staged_files": summary.staged_files,
            "remote_head_before": summary.remote_head_before,
            "remote_head_after": summary.remote_head_after,
            "attempts": summary.attempts,
            "message": summary.message,
        },
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(
        f"{summary.message}: rows={summary.new_row_count} "
        f"needs_review={summary.needs_review_count} blocked={summary.blocked_count}"
    )


@dataset_group.command("apply", cls=OpentracesCommand)
@click.argument("remote")
@click.option("--as", "as_name", default=None, help="Local dataset name.")
@click.option("--read-only", is_flag=True, help="Apply without workflow contribution setup.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_apply(remote: str, as_name: str | None, read_only: bool, as_json: bool) -> None:
    """Create a local dataset from a remote HF dataset contract."""
    try:
        dataset = apply_remote_dataset(remote, as_name=as_name, read_only=read_only)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "dataset": _dataset_payload(dataset)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Dataset applied: {dataset.name}")


@dataset_group.command("pull", cls=OpentracesCommand)
@click.argument("name")
@click.option("--remote", default=None, help="Remote name.")
@click.option("--data", "with_data", is_flag=True, help="Download and import row shards.")
@click.option("--shards", default=None, help="Reserved shard range selector.")
@click.option("--force-pull", is_flag=True, help="Allow additive pull with unpublished local rows.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_pull(
    name: str,
    remote: str | None,
    with_data: bool,
    shards: str | None,
    force_pull: bool,
    as_json: bool,
) -> None:
    """Refresh a dataset remote contract and optionally import row shards."""
    if shards and not with_data:
        click.echo("--shards requires --data.", err=True)
        sys.exit(2)
    try:
        summary = pull_dataset(name, remote=remote, data=with_data, force_pull=force_pull)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "pull": {
            "dataset": summary.dataset_name,
            "remote": summary.remote_name,
            "repo_id": summary.repo_id,
            "metadata_refreshed": summary.metadata_refreshed,
            "data": summary.data,
            "imported_count": summary.imported_count,
            "duplicate_count": summary.duplicate_count,
        },
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Pulled {summary.imported_count} row(s) into {summary.dataset_name}")


@dataset_group.command("withdraw", cls=OpentracesCommand)
@click.argument("name")
@click.argument("row_id")
@click.option("--reason", required=True, help="Withdrawal reason code.")
@click.option("--hard", is_flag=True, help="Rewrite local shards for legal hard-delete.")
@click.option("--confirm", default=None, help="Required literal HARD_DELETE for --hard.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_withdraw(
    name: str,
    row_id: str,
    reason: str,
    hard: bool,
    confirm: str | None,
    as_json: bool,
) -> None:
    """Record a row withdrawal tombstone, or hard-delete with confirmation."""
    try:
        record = withdraw_dataset_row(
            name,
            row_id,
            reason=reason,
            hard=hard,
            confirm=confirm,
        )
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "withdrawal": {
            "target": record.target,
            "target_id": record.target_id,
            "reason": record.reason,
            "requested_at": record.requested_at,
            "hard": hard,
        },
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Withdrawal recorded: {record.target_id}")


@dataset_group.command("status", cls=OpentracesCommand)
@click.argument("name")
@click.option("--remote", "include_remote", is_flag=True, help="Include remote binding status.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_status(name: str, include_remote: bool, as_json: bool) -> None:
    """Show dataset row, publication, and optional remote status."""
    try:
        dataset = load_dataset(name)
        state = evaluate_publication_state(name)
        counts = _publication_counts(state)
        payload = {
            "status": "ok",
            "dataset": _dataset_payload(dataset),
            "publication": counts,
            "row_index_count": len(read_row_index(name)),
        }
        if include_remote:
            remote_block: dict[str, object] = {
                "active_remote": dataset.manifest.active_remote,
                "remotes": {
                    remote_name: remote.model_dump(mode="json")
                    for remote_name, remote in dataset.manifest.remotes.items()
                },
            }
            # Plan 058 V17: surface remote head, withdrawal tombstones, and the
            # byte-identity check in a single status payload.
            remote_block.update(remote_status_summary(name))
            payload["remote"] = remote_block
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"{name}: rows={payload['row_index_count']} publication={payload['publication']}")


@dataset_group.command("info", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_info(name: str, as_json: bool) -> None:
    """Show local manifest plus remote summary for a dataset.

    Plan 058 surfaces ``ot dataset info <name>`` as the human-readable
    counterpart to the JSON-rich ``status --remote`` output. It composes
    the manifest, publication counts, row-index size, and the same remote
    summary block (head/withdrawals/byte_identity) used by V17 status.
    """

    try:
        dataset = load_dataset(name)
        state = evaluate_publication_state(name)
        payload = {
            "status": "ok",
            "dataset": _dataset_payload(dataset),
            "publication": _publication_counts(state),
            "row_index_count": len(read_row_index(name)),
            "remote": {
                "active_remote": dataset.manifest.active_remote,
                "remotes": {
                    remote_name: remote.model_dump(mode="json")
                    for remote_name, remote in dataset.manifest.remotes.items()
                },
                **remote_status_summary(name),
            },
        }
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    active = payload["remote"]["active_remote"] or "<no remote>"
    click.echo(
        f"{name}: rows={payload['row_index_count']} active_remote={active} "
        f"publication={payload['publication']}"
    )


@dataset_group.command("approve", cls=OpentracesCommand)
@click.argument("name")
@click.argument("row_ids", nargs=-1)
@click.option("--all", "all_rows", is_flag=True, help="Approve every reviewable row.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_approve(name: str, row_ids: tuple[str, ...], all_rows: bool, as_json: bool) -> None:
    """Mark selected dataset rows as publishable."""
    _dataset_review_transition(name, list(row_ids), all_rows, "publishable", as_json)


@dataset_group.command("reject", cls=OpentracesCommand)
@click.argument("name")
@click.argument("row_ids", nargs=-1)
@click.option("--all", "all_rows", is_flag=True, help="Reject every row.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_reject(name: str, row_ids: tuple[str, ...], all_rows: bool, as_json: bool) -> None:
    """Reject selected dataset rows from publication."""
    _dataset_review_transition(name, list(row_ids), all_rows, "rejected", as_json)


@dataset_group.command("doctor", cls=OpentracesCommand)
@click.argument("name")
@click.option("--byte-identity", is_flag=True, help="Check remembered published files against the remote.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_doctor(name: str, byte_identity: bool, as_json: bool) -> None:
    """Run dataset health checks, including optional remote byte identity."""
    payload = _check_dataset(name)
    payload["doctor"] = {
        "status": "ok" if payload["valid"] else "error",
        "message": "local dataset is readable" if payload["valid"] else "local dataset has errors",
    }
    if byte_identity:
        payload["byte_identity"] = doctor_byte_identity(name)
        if payload["byte_identity"]["status"] == "error":
            payload["doctor"]["status"] = "error"
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(payload["doctor"]["message"])


@dataset_group.command("export", cls=OpentracesCommand)
@click.argument("name")
@click.option("--format", "fmt", type=click.Choice(["jsonl"]), required=True, help="Export format.")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_export(name: str, fmt: str, output: Path, as_json: bool) -> None:
    """Export public dataset rows as plain JSONL."""
    try:
        export = export_jsonl(name, output)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "format": fmt, "export": export}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Exported {export['row_count']} rows to {output}")


@dataset_group.command("remove", cls=OpentracesCommand)
@click.argument("name")
@click.option("--yes", is_flag=True, help="Confirm removal.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remove(name: str, yes: bool, as_json: bool) -> None:
    """Remove a local dataset after explicit confirmation."""
    if not yes:
        click.echo("Pass --yes to remove a dataset.", err=True)
        sys.exit(2)
    path = dataset_path(name)
    if not path.exists():
        click.echo(f"dataset not found: {name}", err=True)
        sys.exit(3)
    shutil.rmtree(path)
    payload = {"status": "ok", "removed": {"name": name, "path": str(path)}}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Dataset removed: {name}")


def _dataset_payload(dataset) -> dict[str, object]:
    return {
        "name": dataset.name,
        "path": str(dataset.path),
        "manifest": dataset.manifest.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


def _remote_payload(summary) -> dict[str, object]:
    return {
        "dataset": summary.dataset_name,
        "name": summary.name,
        "url": summary.url,
        "visibility": summary.visibility,
        "active": summary.active,
    }


def _emit_remote_payload(summary, *, as_json: bool, verb: str) -> None:
    payload = _remote_payload(summary)
    if as_json:
        click.echo(json.dumps({"status": "ok", "remote": payload}, indent=2, sort_keys=True))
        return
    click.echo(f"Remote {verb}: {summary.name} ({summary.visibility})")


def _schedule_payload(schedule) -> dict[str, object]:
    return {
        "dataset": schedule.dataset,
        "enabled": schedule.enabled,
        "every": schedule.every,
        "executor": schedule.executor,
        "trigger": schedule.trigger,
        "last_run_status": schedule.last_run_status,
    }


def _emit_schedule_payload(schedule, *, as_json: bool) -> None:
    payload = {"status": "ok", "schedule": _schedule_payload(schedule)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(
        f"{schedule.dataset}: every={schedule.every} "
        f"executor={schedule.executor} enabled={schedule.enabled}"
    )


def _check_dataset(name: str) -> dict[str, object]:
    dataset = load_dataset(name)
    schema_path = dataset.path / dataset.manifest.schema_ref.path
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    data_file = dataset.path / "data" / "train.jsonl"
    row_count = 0
    validation_errors = []
    for line_no, line in enumerate(data_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row_count += 1
        row = json.loads(line)
        errors = validate_row(row, schema)
        if errors:
            validation_errors.append({"line": line_no, "errors": errors})
    row_index_count = len(read_row_index(name))
    return {
        "status": "ok",
        "dataset": name,
        "valid": not validation_errors,
        "row_count": row_count,
        "row_index_count": row_index_count,
        "validation_errors": validation_errors,
    }


def _publication_counts(state) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in state.rows.values():
        counts[entry.status] = counts.get(entry.status, 0) + 1
    return counts


def _dataset_review_transition(
    name: str,
    row_ids: list[str],
    all_rows: bool,
    status: str,
    as_json: bool,
) -> None:
    try:
        current = evaluate_publication_state(name)
        selected = (
            list(current.rows)
            if all_rows
            else row_ids
        )
        if not selected:
            raise ValueError("provide at least one row_id or pass --all")
        state = set_publication_review_status(name, selected, status)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "dataset": name,
        "counts": _publication_counts(state),
        "row_ids": selected,
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"{name}: updated {len(selected)} row(s)")


def _load_row(root: Path, row_id: str) -> dict[str, object]:
    row_index = root / ".opentraces" / "row_index.jsonl"
    for line in row_index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("row_id") != row_id:
            continue
        data_file = root / entry["data_file"]
        data_line = data_file.read_text(encoding="utf-8").splitlines()[entry["line"] - 1]
        return json.loads(data_line)
    raise ValueError(f"row not found: {row_id}")
