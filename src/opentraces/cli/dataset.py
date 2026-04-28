"""CLI commands for local executable datasets."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ..core.datasets import (
    create_dataset,
    dataset_path,
    export_jsonl,
    list_datasets,
    load_dataset,
    read_row_index,
    validate_row,
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
def dataset_group() -> None:
    """Manage local executable datasets."""


@dataset_group.group("schedule", cls=OpentracesGroup)
def dataset_schedule_group() -> None:
    """Manage local dataset schedules."""


@dataset_schedule_group.command("add", cls=OpentracesCommand)
@click.argument("name")
@click.option("--every", required=True, help="Local interval such as 2h.")
@click.option(
    "--executor",
    type=click.Choice(["claude-code-headless"]),
    default="claude-code-headless",
    show_default=True,
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_add(name: str, every: str, executor: str, as_json: bool) -> None:
    try:
        schedule = add_schedule(name, every=every, executor=executor)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    _emit_schedule_payload(schedule, as_json=as_json)


@dataset_schedule_group.command("list", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_list(as_json: bool) -> None:
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


@dataset_schedule_group.command("rm", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_rm(name: str, as_json: bool) -> None:
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


@dataset_group.command("doctor", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_doctor(name: str, as_json: bool) -> None:
    payload = _check_dataset(name)
    payload["doctor"] = {
        "status": "ok" if payload["valid"] else "error",
        "message": "local dataset is readable" if payload["valid"] else "local dataset has errors",
    }
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


@dataset_group.command("rm", cls=OpentracesCommand)
@click.argument("name")
@click.option("--yes", is_flag=True, help="Confirm removal.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_rm(name: str, yes: bool, as_json: bool) -> None:
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
