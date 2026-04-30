"""CLI commands for local executable datasets."""

from __future__ import annotations

import json
import os
import shutil
import sys

import click

from ._help import OpentracesCommand, OpentracesGroup
from ..core.datasets import (
    add_dataset_remote,
    append_rows,
    create_dataset,
    DatasetRemotePermissionError,
    DatasetRemoteSchemaAheadError,
    dataset_path,
    fake_remote_create,
    fake_remote_delete,
    fake_remote_probe,
    fake_remote_set_visibility,
    evaluate_publication_state,
    list_datasets,
    list_dataset_remotes,
    load_dataset,
    normalize_hf_repo_id,
    publish_dataset,
    read_row_index,
    remove_dataset_remote,
    repo_id_from_remote,
    set_dataset_remote_visibility,
    set_publication_review_status,
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


@dataset_group.command("status", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_status(name: str, as_json: bool) -> None:
    """Show row count and publication-state breakdown for a dataset."""
    try:
        dataset = load_dataset(name)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    try:
        state = evaluate_publication_state(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    row_count = len(read_row_index(name))
    payload: dict[str, object] = {
        "status": "ok",
        "dataset": name,
        "path": str(dataset.path),
        "row_count": row_count,
        "counts": _publication_counts(state),
    }
    if _is_manual_dataset(dataset):
        payload["manual"] = True
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    counts = payload["counts"]
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no rows"
    click.echo(f"{name}: rows={row_count}  {summary}")


@dataset_group.command("new", cls=OpentracesCommand)
@click.argument("name")
@click.option("--description", default=None, help="Dataset description.")
@click.option("--workflow", default=None, help="Workflow skill name.")
@click.option("--workflow-digest", default="sha256:unconfigured", help="Workflow digest.")
@click.option(
    "--rows-file",
    "rows_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help=(
        "Ad-hoc mode: JSONL file of rows to seed the dataset with. Requires "
        "--schema. Skips workflow creation and marks the dataset as manual."
    ),
)
@click.option(
    "--schema",
    "schema_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help=(
        "Ad-hoc mode: JSON Schema file describing rows in --rows-file. "
        "Required when --rows-file is set."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_new(
    name: str,
    description: str | None,
    workflow: str | None,
    workflow_digest: str,
    rows_file: str | None,
    schema_file: str | None,
    as_json: bool,
) -> None:
    """Create a local HF-shaped dataset with an OpenTraces sidecar.

    Two modes:

    * Workflow mode (default): synthesizes a workflow-driven dataset
      that is filled by ``opentraces dataset run``.
    * Ad-hoc mode (``--rows-file`` + ``--schema``): seeds a manual
      dataset directly from a JSONL file. ``dataset run`` is a no-op
      for manual datasets; review/approve/publish work as usual.
    """
    if rows_file or schema_file:
        if not (rows_file and schema_file):
            click.echo(
                "--rows-file and --schema must be provided together "
                "(ad-hoc dataset mode requires both).",
                err=True,
            )
            sys.exit(2)
        _create_manual_dataset(
            name=name,
            description=description,
            rows_file=rows_file,
            schema_file=schema_file,
            as_json=as_json,
        )
        return

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


# Sentinel value placed in ``manifest.workflow.skill`` to mark a dataset as
# ad-hoc / manual. The schema requires ``skill`` to be a non-empty string,
# so we use a recognizable string instead of None. Treated as "no workflow"
# by ``dataset run`` and exposed as ``manual: true`` on dataset payloads.
MANUAL_WORKFLOW_SKILL = "manual"


def _is_manual_dataset(dataset) -> bool:
    return dataset.manifest.workflow.skill == MANUAL_WORKFLOW_SKILL


def _create_manual_dataset(
    *,
    name: str,
    description: str | None,
    rows_file: str,
    schema_file: str,
    as_json: bool,
) -> None:
    """Synthesize a manual ad-hoc dataset from a JSONL + JSON Schema pair."""
    from datetime import datetime, timezone
    from pathlib import Path

    schema_path = Path(schema_file)
    rows_path = Path(rows_file)

    # Load + validate the schema file is JSON.
    try:
        schema_payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        click.echo(f"failed to read --schema {schema_file}: {exc}", err=True)
        sys.exit(2)
    if not isinstance(schema_payload, dict):
        click.echo("--schema must point to a JSON object schema", err=True)
        sys.exit(2)

    # Parse the JSONL rows file up-front so we fail before creating the
    # dataset directory if the file is malformed.
    rows: list[dict[str, object]] = []
    try:
        with rows_path.open("r", encoding="utf-8") as stream:
            for line_no, raw in enumerate(stream, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    click.echo(
                        f"--rows-file line {line_no} is not valid JSON: {exc}",
                        err=True,
                    )
                    sys.exit(2)
                if not isinstance(row, dict):
                    click.echo(
                        f"--rows-file line {line_no} must be a JSON object",
                        err=True,
                    )
                    sys.exit(2)
                rows.append(row)
    except OSError as exc:
        click.echo(f"failed to read --rows-file {rows_file}: {exc}", err=True)
        sys.exit(2)

    try:
        dataset = create_dataset(
            name,
            description=description,
            workflow_skill=MANUAL_WORKFLOW_SKILL,
            workflow_digest="sha256:manual",
            row_schema=schema_payload,
        )
    except (FileExistsError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)

    if rows:
        run_id = (
            "manual_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
        try:
            summary = append_rows(name, rows, run_id=run_id)
        except (FileNotFoundError, ValueError) as exc:
            # Cleanup partially-created dataset to leave a clean slate.
            shutil.rmtree(dataset.path, ignore_errors=True)
            click.echo(str(exc), err=True)
            sys.exit(3)
        if summary.validation_error_count:
            shutil.rmtree(dataset.path, ignore_errors=True)
            errors = summary.validation_errors[:3]
            click.echo(
                f"--rows-file failed schema validation: "
                f"{summary.validation_error_count} row(s) invalid; "
                f"first errors: {errors}",
                err=True,
            )
            sys.exit(2)

    # Reload dataset so manifest reflects any updates.
    dataset = load_dataset(name)
    payload = {"status": "ok", "dataset": _dataset_payload(dataset)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Manual dataset created: {dataset.name} ({len(rows)} row(s))")


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
    # Manual / ad-hoc datasets have no workflow to execute. Emit a
    # structured no-op response so agents can detect this cleanly
    # instead of getting a workflow_runner error.
    try:
        existing = load_dataset(name)
    except FileNotFoundError:
        existing = None
    if existing is not None and _is_manual_dataset(existing):
        payload = {
            "status": "manual_dataset_no_run_action",
            "dataset": name,
            "message": (
                "manual datasets are seeded by `dataset new --rows-file`; "
                "use review/approve/publish for the lifecycle"
            ),
        }
        if as_json:
            click.echo(json.dumps(payload, indent=2, sort_keys=True))
            return
        click.echo(payload["message"])
        return
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


@dataset_group.command("approve", cls=OpentracesCommand, hidden=True)
@click.argument("name")
@click.argument("row_ids", nargs=-1)
@click.option("--all", "all_rows", is_flag=True, help="Approve every reviewable row.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_approve(name: str, row_ids: tuple[str, ...], all_rows: bool, as_json: bool) -> None:
    """Mark selected dataset rows as publishable."""
    _dataset_review_transition(name, list(row_ids), all_rows, "publishable", as_json)


@dataset_group.command("reject", cls=OpentracesCommand, hidden=True)
@click.argument("name")
@click.argument("row_ids", nargs=-1)
@click.option("--all", "all_rows", is_flag=True, help="Reject every row.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_reject(name: str, row_ids: tuple[str, ...], all_rows: bool, as_json: bool) -> None:
    """Reject selected dataset rows from publication."""
    _dataset_review_transition(name, list(row_ids), all_rows, "rejected", as_json)


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
    payload: dict[str, object] = {
        "name": dataset.name,
        "path": str(dataset.path),
        "manifest": dataset.manifest.model_dump(mode="json", by_alias=True, exclude_none=True),
    }
    # Surface the manual marker + row count for ad-hoc datasets so agents
    # can detect them without re-reading the manifest skill string.
    if _is_manual_dataset(dataset):
        payload["manual"] = True
        try:
            payload["row_count"] = len(read_row_index(dataset.name))
        except (FileNotFoundError, OSError):
            payload["row_count"] = 0
    return payload


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
