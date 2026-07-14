"""CLI commands for local executable datasets."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from importlib import resources
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json
from opentraces_schema import DatasetSecurityPolicy

from ..security.dataset_rows import DATASET_ROW_FLOOR
from ..security.config import SECURITY_TOOL_NAMES
from ..core.datasets import (
    add_dataset_remote,
    apply_dataset_security_edit,
    append_rows,
    create_dataset,
    DatasetPublishWithheldError,
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
    read_json,
    read_row_index,
    read_row_provenance,
    read_rows_by_id,
    remove_dataset_remote,
    repo_id_from_remote,
    save_manifest,
    set_dataset_remote_visibility,
    set_publication_review_status,
    source_provenance_for_query,
)
from ..core.workflow_runner import (
    DatasetRunLockError,
    ExecutorUnavailableError,
    WorkflowNeedsJudgmentError,
    WorkflowScriptError,
    run_dataset_workflow,
)
from ..core.workflow_judgment import (
    RC_NEEDS_JUDGMENT,
    build_needs_judgment,
    load_answers as load_judgment_answers,
)
from ..core.workflows import (
    WorkflowIntegrityError,
    create_workflow,
    load_workflow,
    resolve_workflow_reference,
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


class WorkflowBindError(Exception):
    """A ``dataset new --workflow <ref>`` bind that must fail before any create.

    #190: every ``--workflow`` is resolved BEFORE creating a dataset directory.
    An unresolvable ref (or a required-but-absent workflow) raises this, which
    ``dataset_new`` maps to a structured ``rc=2`` error envelope with nothing
    created. ``code`` names the failure class; ``workflow`` echoes the ref.
    """

    def __init__(self, code: str, message: str, *, workflow: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.workflow = workflow


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


@dataset_group.command("security", cls=OpentracesCommand)
@click.argument("name")
@click.option(
    "--tool",
    "tools",
    multiple=True,
    type=click.Choice(SECURITY_TOOL_NAMES),
    help="Security tool to enable or disable for this dataset.",
)
@click.option("--enable", is_flag=True, help="Enable every --tool for this dataset.")
@click.option("--disable", is_flag=True, help="Disable every --tool for this dataset.")
@click.option(
    "--unsafe-override",
    is_flag=True,
    help="Allow disabling a required tool; the opt-out is recorded in the manifest.",
)
@click.option("--reason", default=None, help="Reason recorded with an unsafe override.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_security_cmd(
    name: str,
    tools: tuple[str, ...],
    enable: bool,
    disable: bool,
    unsafe_override: bool,
    reason: str | None,
    as_json: bool,
) -> None:
    """Inspect or edit one dataset's security policy.

    The dataset's security policy is owned by its workflow contract and stored
    in the dataset manifest, not in global config. Optional tools can be toggled
    per dataset; a required tool can only be disabled with ``--unsafe-override``
    when the workflow contract permits it.
    """
    try:
        dataset = load_dataset(name)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)

    policy = dataset.manifest.security
    changes = {"enabled": [], "disabled": []}
    try:
        if enable and disable:
            raise ValueError("Use either --enable or --disable, not both")
        if tools and not (enable or disable):
            raise ValueError("--tool requires --enable or --disable")
        if not tools and (enable or disable):
            raise ValueError("--enable/--disable require at least one --tool")
        if tools:
            policy, changes = apply_dataset_security_edit(
                policy,
                enable=tools if enable else (),
                disable=tools if disable else (),
                unsafe_override=unsafe_override,
                reason=reason,
            )
            updated = dataset.manifest.model_copy(update={"security": policy})
            save_manifest(dataset.path, updated)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)

    payload = {
        "status": "ok",
        "dataset": name,
        "security": _dataset_security_payload(policy),
        "changes": changes,
    }
    if as_json:
        click.echo(_dump_json(payload))
        return

    click.echo(f"Dataset '{name}' security policy ({policy.source}):")
    enabled_tools = policy.enabled_tools
    click.echo("  enabled: " + (", ".join(enabled_tools) if enabled_tools else "none"))
    if policy.required_tools:
        click.echo("  required: " + ", ".join(policy.required_tools))
    if changes["enabled"]:
        click.echo("  changed on: " + ", ".join(changes["enabled"]))
    if changes["disabled"]:
        click.echo("  changed off: " + ", ".join(changes["disabled"]))
    missing = policy.missing_required_tools()
    if missing:
        click.echo("  WARNING required tools disabled by override: " + ", ".join(missing))


def _dataset_security_payload(policy: DatasetSecurityPolicy) -> dict[str, object]:
    data = policy.model_dump(mode="json")
    data["scope"] = "dataset"
    data["required_satisfied"] = policy.required_tools_satisfied()
    data["missing_required_tools"] = policy.missing_required_tools()
    return data


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


def _fail_hf(exc: Exception, repo_id: str) -> None:
    """Exit 3 with actionable guidance for HF / network errors; re-raise others.

    Thin wrapper over the shared ``cli._fail_hf_or_reraise`` so the dataset and
    bucket remote surfaces handle rejected tokens / outages identically.
    """
    from opentraces import cli as root_cli

    root_cli._fail_hf_or_reraise(exc, repo_id)


@dataset_remote_group.command("add", cls=OpentracesCommand, hidden=True)
@click.argument("name")
@click.argument("repo")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remote_add(name: str, repo: str, as_json: bool) -> None:
    """Connect a local dataset to an existing HuggingFace dataset remote.

    Plan 087: folded into the idempotent ``dataset remote create`` (which now
    binds an existing repo as well as creating a new one). Kept hidden + callable
    as a legacy alias for the bind-only behaviour.
    """
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
    """Bind a HuggingFace dataset remote, creating it if it doesn't exist.

    Plan 087 — idempotent create-or-bind (absorbs the former ``add``): creates a
    private-by-default HF dataset and binds it; if the repo ALREADY exists, binds
    the existing one (honouring its current visibility) instead of erroring.
    """
    token, username = _hf_auth()
    repo_id = repo
    try:
        repo_id = normalize_hf_repo_id(repo, username)
        created = _remote_create(repo_id, is_private, token)
        if created:
            visibility = "private" if is_private else "public"
            summary = add_dataset_remote(name, repo_id, visibility=visibility)
            verb = "created"
        else:
            # Already exists -> bind it (the former `add` behaviour), reflecting
            # the existing repo's visibility rather than the --private flag.
            info = _remote_probe(repo_id, token)
            if info is None:
                raise ValueError(
                    f"{repo_id} exists on HuggingFace but could not be read. "
                    "Check your access permissions."
                )
            repo_id = info.get("id") or repo_id
            visibility = "private" if info.get("private") else "public"
            summary = add_dataset_remote(name, repo_id, visibility=visibility)
            verb = "connected"
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001 - HF/network errors must not traceback
        _fail_hf(exc, repo_id)
    _emit_remote_payload(summary, as_json=as_json, verb=verb)


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
        click.echo(_dump_json({"status": "ok", "dataset": name, "remotes": payload}))
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
    repo_id = ""
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
    except Exception as exc:  # noqa: BLE001 - HF/network errors must not traceback
        _fail_hf(exc, repo_id)
    payload = _remote_payload(summary)
    payload["deleted_remote"] = delete_remote
    if as_json:
        click.echo(_dump_json({"status": "ok", "remote": payload}))
        return
    click.echo(f"Disconnected {summary.name}.")


@dataset_remote_group.command("visibility", cls=OpentracesCommand)
@click.argument("name")
@click.argument("remote", required=False, default=None)
@click.option("--private", "private_flag", is_flag=True, default=False,
              help="Set the bound HuggingFace dataset remote to private.")
@click.option("--public", "public_flag", is_flag=True, default=False,
              help="Set the bound HuggingFace dataset remote to public.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_remote_visibility(
    name: str,
    remote: str | None,
    private_flag: bool,
    public_flag: bool,
    as_json: bool,
) -> None:
    """Change a bound HuggingFace dataset remote between private and public."""
    if private_flag and public_flag:
        click.echo("--private and --public are mutually exclusive.", err=True)
        sys.exit(2)
    if not (private_flag or public_flag):
        click.echo("Specify --private or --public.", err=True)
        sys.exit(2)
    make_private = private_flag
    repo_id = ""
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
    except Exception as exc:  # noqa: BLE001 - HF/network errors must not traceback
        _fail_hf(exc, repo_id)
    _emit_remote_payload(summary, as_json=as_json, verb="updated")


@dataset_schedule_group.command("add", cls=OpentracesCommand)
@click.argument("name")
@click.option("--every", required=True, help="Local interval such as 30s, 15m, 2h, or 1d.")
@click.option(
    "--executor",
    type=click.Choice(["current-agent", "script"]),
    default="script",
    show_default=True,
    help="Executor each scheduled run uses.",
)
@click.option(
    "--approve-new",
    is_flag=True,
    help="Mark rows appended by scheduled runs publishable before publication.",
)
@click.option(
    "--publish",
    is_flag=True,
    help="After each scheduled run, publish publishable rows to the active remote.",
)
@click.option(
    "--publish-check-only",
    is_flag=True,
    help="After each scheduled run, stage and validate publication without upload.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_schedule_add(
    name: str,
    every: str,
    executor: str,
    approve_new: bool,
    publish: bool,
    publish_check_only: bool,
    as_json: bool,
) -> None:
    """Add a local schedule for running a dataset workflow."""
    try:
        schedule = add_schedule(
            name,
            every=every,
            executor=executor,
            approve_new=approve_new,
            publish=publish,
            publish_check_only=publish_check_only,
        )
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
        click.echo(_dump_json({
            "status": "ok",
            "schema_version": "opentraces.dataset.schedule_list.v1",
            "schedules": schedules,
        }))
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
        # Confirm a schedule exists first so a missing schedule exits 3
        # ("schedule not found"), consistent with show/pause/resume/remove,
        # rather than silently returning an empty log at exit 0.
        read_schedule(name)
        logs = read_schedule_logs(name, tail=tail)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "dataset": name, "logs": logs}
    if as_json:
        click.echo(_dump_json(payload))
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
        click.echo(_dump_json(payload))
        return
    click.echo(f"Schedule removed: {name}")


@dataset_group.command("list", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_list(as_json: bool) -> None:
    """List local HF-shaped datasets."""
    datasets = [_dataset_payload(dataset) for dataset in list_datasets()]
    if as_json:
        click.echo(_dump_json({
            "status": "ok",
            "schema_version": "opentraces.dataset.list.v1",
            "datasets": datasets,
        }))
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
    # Issue #84: surface the non-overridable reader floor so it is inspectable.
    # The floor runs over every row regardless of the workflow author's contract;
    # ``floor_satisfied`` is false iff any row's execution evidence misses it
    # (e.g. a row appended before the floor existed — the gate blocks those).
    floor_unsatisfied = any(
        "dataset_reader_floor_unsatisfied" in entry.block_reasons
        for entry in state.rows.values()
    )
    payload["security"] = {
        "reader_floor": list(DATASET_ROW_FLOOR),
        "floor_satisfied": not floor_unsatisfied,
    }
    # Cluster F D9: status surfaces the same row_quality block list does.
    rq = _compute_row_quality_summary(name)
    if rq is not None:
        payload["row_quality"] = rq
    if as_json:
        click.echo(_dump_json(payload))
        return
    counts = payload["counts"]
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no rows"
    click.echo(f"{name}: rows={row_count}  {summary}")


@dataset_group.command("new", cls=OpentracesCommand)
@click.argument("name")
@click.option("--description", default=None, help="Dataset description.")
@click.option("--workflow", default=None, help="Workflow skill name or path to a Markdown workflow file/package.")
@click.option("--from-skill", "from_skill", default=None, help="Create a skill-episodes dataset for this skill.")
@click.option(
    "--workflow-digest",
    default=None,
    help=(
        "Optional digest pin override for a resolved --workflow. #190 removed "
        "the sha256:unconfigured default: an unresolvable --workflow now exits "
        "rc=2 rather than binding opaquely, so a resolved workflow's real digest "
        "is always what gets pinned."
    ),
)
@click.option("--query-name", default=None, help="Remembered trace query name for workflow runs.")
@click.option(
    "--query-scope",
    type=click.Choice(["all-projects", "project", "cwd", "trace"]),
    default="all-projects",
    show_default=True,
    help="Remembered trace query scope.",
)
@click.option("--query-lex", default=None, help="Remembered trace query lexical text.")
@click.option("--query-semantic", default=None, help="Remembered semantic trace query text.")
@click.option(
    "--query-source",
    type=click.Choice(["index", "projection"]),
    default=None,
    help="Remembered local trace query source.",
)
@click.option("--query-project", default=None, help="Remembered trace query project slug.")
@click.option("--query-candidate-kind", default=None, help="Remembered trace query candidate kind.")
@click.option("--query-arg", "query_args", multiple=True, help="Extra remembered trace query arg as key=value.")
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
        "JSON Schema file describing dataset rows. With --rows-file this seeds "
        "an ad-hoc dataset; without --rows-file this defines workflow output."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_new(
    name: str,
    description: str | None,
    workflow: str | None,
    from_skill: str | None,
    workflow_digest: str | None,
    query_name: str | None,
    query_scope: str,
    query_lex: str | None,
    query_semantic: str | None,
    query_source: str | None,
    query_project: str | None,
    query_candidate_kind: str | None,
    query_args: tuple[str, ...],
    rows_file: str | None,
    schema_file: str | None,
    as_json: bool,
) -> None:
    """Create a local HF-shaped dataset with an OpenTraces sidecar.

    \b
    Modes:

    Workflow mode: default; fill rows later with ``dataset run``.

    Skill mode: pass ``--from-skill <skill>`` for skill-episodes rows.

    Ad-hoc mode: pass ``--rows-file`` and ``--schema`` for manual rows.
    """
    started = time.monotonic()
    if from_skill and workflow:
        click.echo("Use either --from-skill or --workflow, not both.", err=True)
        sys.exit(2)
    if from_skill and schema_file:
        click.echo("--from-skill uses the skill-episodes schema; omit --schema.", err=True)
        sys.exit(2)
    if from_skill and rows_file:
        click.echo("Use either --from-skill or --rows-file, not both.", err=True)
        sys.exit(2)
    if rows_file:
        if not schema_file:
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
        schema_payload = _load_schema_file(schema_file) if schema_file else None
        if from_skill:
            skill_workflow = _ensure_skill_episodes_workflow()
            workflow = skill_workflow.name
            workflow_digest = skill_workflow.digest
            schema_payload = _load_builtin_workflow_schema("skill-episodes-v1")
            description = description or f"Skill episodes for {from_skill}."
            query_name = query_name or f"{name}-{from_skill}-query"
            query_args = (
                *query_args,
                f"skill={from_skill}",
                "candidate_kind=skill_invocation",
            )
        (
            workflow_skill,
            resolved_digest,
            workflow_config,
            workflow_security,
        ) = _resolve_workflow_for_dataset(
            workflow,
            workflow_digest,
        )
        candidate_query = _candidate_query_for_dataset(
            dataset_name=name,
            query_name=query_name,
            query_scope=query_scope,
            query_lex=query_lex,
            query_semantic=query_semantic,
            query_source=query_source,
            query_project=query_project,
            query_candidate_kind=query_candidate_kind,
            query_args=query_args,
        )
        source_provenance = (
            source_provenance_for_query(
                candidate_query,
                include_bucket_snapshot=False,
            )
            if candidate_query is not None
            else None
        )
        dataset = create_dataset(
            name,
            description=description,
            workflow_skill=workflow_skill,
            workflow_digest=resolved_digest,
            workflow_config=workflow_config,
            security=workflow_security,
            row_schema=schema_payload,
            candidate_query=candidate_query,
            source_provenance=source_provenance,
        )
    except WorkflowBindError as exc:
        # #190: resolve-before-create failed — nothing was created. Invalid
        # usage is rc=2, with a structured error envelope.
        payload = {
            "status": "error",
            "error": {
                "code": exc.code,
                "message": exc.message,
                "workflow": exc.workflow,
            },
        }
        if as_json:
            click.echo(_dump_json(payload))
        else:
            click.echo(exc.message, err=True)
        sys.exit(2)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "dataset": _dataset_payload(dataset),
        "telemetry": {"duration_ms": round((time.monotonic() - started) * 1000, 2)},
    }
    if from_skill:
        payload["next_command"] = f"opentraces dataset run {name} --executor script --json"
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Dataset created: {dataset.name}")
    _render_dataset_security_hint(dataset)


def _load_schema_file(schema_file: str) -> dict[str, object]:
    schema_path = Path(schema_file)
    try:
        schema_payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read --schema {schema_file}: {exc}") from exc
    if not isinstance(schema_payload, dict):
        raise ValueError("--schema must point to a JSON object schema")
    return schema_payload


def _ensure_skill_episodes_workflow():
    try:
        return load_workflow("skill-episodes-v1")
    except FileNotFoundError:
        return create_workflow("skill-episodes-v1", template="skill-episodes-v1")


def _load_builtin_workflow_schema(template: str) -> dict[str, object]:
    try:
        schema_text = (
            resources.files("opentraces.workflow_templates")
            .joinpath(template, "schemas", "row.schema.json")
            .read_text(encoding="utf-8")
        )
        payload = json.loads(schema_text)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load built-in workflow schema {template}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"built-in workflow schema {template} is not a JSON object")
    return payload


def _candidate_query_for_dataset(
    *,
    dataset_name: str,
    query_name: str | None,
    query_scope: str,
    query_lex: str | None,
    query_semantic: str | None,
    query_source: str | None,
    query_project: str | None,
    query_candidate_kind: str | None,
    query_args: tuple[str, ...],
) -> dict[str, object] | None:
    if query_lex and query_semantic:
        raise ValueError("Use either --query-lex or --query-semantic, not both")
    args: dict[str, object] = {}
    if query_lex:
        args["lex"] = query_lex
    if query_semantic:
        args["semantic"] = query_semantic
    if query_source:
        args["source"] = query_source
    if query_project:
        args["project"] = query_project
    if query_candidate_kind:
        args["candidate_kind"] = query_candidate_kind
    for raw in query_args:
        if "=" not in raw:
            raise ValueError("--query-arg values must use key=value syntax")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError("--query-arg values must use key=value syntax")
        args[key] = value
    if not args and not query_name:
        return None
    return {
        "name": query_name or f"{dataset_name}-query",
        "scope": query_scope,
        "args": args,
    }


def _resolve_workflow_for_dataset(
    workflow: str | None,
    workflow_digest: str | None,
) -> tuple[str | None, str, dict[str, object] | None, DatasetSecurityPolicy | None]:
    """Resolve a ``--workflow`` ref to (skill, real digest, config, security).

    #190: EVERY ``--workflow`` is resolved before the dataset directory is
    created. The pre-#190 silent fallback that bound an unresolvable bare name
    to the ``sha256:unconfigured`` default is deleted — an unresolvable ref (or
    a required-but-absent workflow) raises :class:`WorkflowBindError`, mapped to
    ``rc=2`` with nothing created. A resolved workflow's REAL digest is always
    what gets pinned; ``workflow_digest`` no longer resurrects an opaque bind.
    """
    if not workflow:
        # The automated path requires a workflow. Manual (``--rows-file``) and
        # ``--from-skill`` datasets are handled before this seam is reached.
        raise WorkflowBindError(
            "workflow_required",
            "dataset new requires --workflow <ref> (or --rows-file for a manual "
            "dataset, or --from-skill for a skill-episodes dataset).",
        )
    if not _looks_like_workflow_path(workflow):
        # A bare name must reference an installed workflow package. If it
        # resolves, seed the dataset's security policy from its contract and pin
        # its real digest; if it does not resolve, the bind fails (rc=2) — there
        # is no opaque bare-name bind anymore.
        try:
            installed = load_workflow(workflow)
        except (FileNotFoundError, ValueError) as exc:
            raise WorkflowBindError(
                "workflow_unresolved",
                f"workflow does not resolve: {workflow} ({exc}). Install it with "
                "`opentraces workflow create` or pass a path to a Markdown "
                "workflow file/package.",
                workflow=workflow,
            ) from exc
        security = (
            DatasetSecurityPolicy.from_contract(
                installed.security, source_workflow_digest=installed.digest
            )
            if installed.security is not None
            else None
        )
        return installed.name, installed.digest, None, security

    try:
        package = resolve_workflow_reference(workflow)
    except (FileNotFoundError, ValueError) as exc:
        raise WorkflowBindError(
            "workflow_unresolved",
            f"workflow path does not resolve: {workflow} ({exc}).",
            workflow=workflow,
        ) from exc
    config: dict[str, object] = {
        "source": str(package.path),
        "source_type": package.source_type,
    }
    if package.entrypoint is not None:
        config["entrypoint"] = str(package.entrypoint)
    if package.description:
        config["description"] = package.description
    security = (
        DatasetSecurityPolicy.from_contract(
            package.security, source_workflow_digest=package.digest
        )
        if package.security is not None
        else None
    )
    return package.name, package.digest, config, security


def _looks_like_workflow_path(value: str) -> bool:
    path = Path(value).expanduser()
    lower = value.lower()
    return (
        path.exists()
        or "/" in value
        or "\\" in value
        or lower.endswith(".md")
        or lower.endswith(".markdown")
    )


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

    rows_path = Path(rows_file)

    # Load + validate the schema file is JSON.
    try:
        schema_payload = _load_schema_file(schema_file)
    except ValueError as exc:
        click.echo(str(exc), err=True)
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
        click.echo(_dump_json(payload))
        return
    click.echo(f"Manual dataset created: {dataset.name} ({len(rows)} row(s))")
    _render_dataset_security_hint(dataset)


@dataset_group.command("run", cls=OpentracesCommand)
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="Execute without appending rows or advancing cursors.")
@click.option(
    "--executor",
    type=click.Choice(["current-agent", "script"]),
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
@click.option(
    "--facet",
    "facet_filters",
    multiple=True,
    help=(
        "Repeatable name=value metadata scope refinement, composing with "
        "--scope/--project/--trace (issue #212) and with any facets "
        "persisted on the dataset's candidate query. Narrowed to the subset "
        "of the `trace query --facet` vocabulary resolvable at O(manifest) "
        "cost: model, agent.name, agent.version. Any other name is rejected "
        "with a pointer to `trace query --facet` for the full vocabulary. "
        "Enforced end-to-end: emitted rows outside the resolved match set "
        "are rejected, never appended."
    ),
)
@click.option("--model", "facet_model", default=None, help="Convenience alias for --facet model=<value>.")
@click.option("--agent", "facet_agent", default=None, help="Convenience alias for --facet agent.name=<value>.")
@click.option("--limit", type=int, default=None, help="Candidate limit.")
@click.option(
    "--privacy-tier",
    type=click.Choice(["off", "low", "medium", "high"]),
    default=None,
    help="Privacy tier to apply while appending workflow rows.",
)
@click.option(
    "--trail-freshness",
    "trail_freshness_policy",
    type=click.Choice(["warn", "fail", "ignore"]),
    default="warn",
    show_default=True,
    help="How to handle stale Trace Trail projections during composition.",
)
@click.option("--since-last-run", is_flag=True, help="Use the dataset cursor.")
@click.option(
    "--sync",
    "sync",
    is_flag=True,
    help=(
        "Project only bucket data newer than the recorded watermark (a delta, "
        "not a full re-run); advance the watermark on success. A no-op when the "
        "bucket has not advanced since the last sync."
    ),
)
@click.option("--reconcile", is_flag=True, help="Run a full reconciliation scan.")
@click.option("--scheduled", is_flag=True, help="Mark this run as scheduler initiated.")
@click.option(
    "--approve-new",
    is_flag=True,
    help="Mark rows appended by this run publishable after security gates.",
)
@click.option(
    "--publish",
    is_flag=True,
    help="After the run, publish publishable rows to the active remote.",
)
@click.option(
    "--publish-check-only",
    is_flag=True,
    help="After the run, stage and validate publication without upload.",
)
@click.option("--verbose", is_flag=True, help="Include run artefact paths.")
@click.option("--resume", default=None, help="Reserved run resume id.")
@click.option(
    "--answers",
    "answers_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Recorded judgment answers (#186); re-run after a needs-judgment rc=10.",
)
@click.option(
    "--strict/--no-strict",
    default=False,
    help=(
        "Fail (non-zero) before execution if the installed workflow no longer "
        "matches the digest it was pinned to; default warns and proceeds."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_run(
    name: str,
    dry_run: bool,
    executor: str | None,
    scope: str,
    project: str | None,
    trace_id: str | None,
    facet_filters: tuple[str, ...],
    facet_model: str | None,
    facet_agent: str | None,
    limit: int | None,
    privacy_tier: str | None,
    trail_freshness_policy: str,
    since_last_run: bool,
    sync: bool,
    reconcile: bool,
    scheduled: bool,
    approve_new: bool,
    publish: bool,
    publish_check_only: bool,
    verbose: bool,
    resume: str | None,
    answers_path: str | None,
    strict: bool,
    as_json: bool,
) -> None:
    """Run the dataset workflow in dry-run, current-agent, or script mode."""
    started = time.monotonic()
    if resume:
        click.echo("--resume is reserved for future interrupted-run recovery.", err=True)
        sys.exit(10)
    if publish and publish_check_only:
        click.echo("Use either --publish or --publish-check-only, not both.", err=True)
        sys.exit(2)
    if approve_new and not (publish or publish_check_only):
        click.echo("--approve-new requires --publish or --publish-check-only.", err=True)
        sys.exit(2)
    if dry_run and (approve_new or publish or publish_check_only):
        click.echo("--dry-run cannot be combined with publication automation.", err=True)
        sys.exit(2)
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
            "telemetry": {"duration_ms": round((time.monotonic() - started) * 1000, 2)},
        }
        if as_json:
            click.echo(_dump_json(payload))
            return
        click.echo(payload["message"])
        return
    from ..core.dataset_facets import parse_facet_filters, validate_facet_names

    facets: dict[str, str] = {}
    try:
        facets.update(parse_facet_filters(facet_filters))
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    if facet_model:
        facets.setdefault("model", facet_model)
    if facet_agent:
        facets.setdefault("agent.name", facet_agent)
    if facets:
        try:
            validate_facet_names(facets)
        except ValueError as exc:
            click.echo(str(exc), err=True)
            sys.exit(2)

    scope_payload = {"scope": scope}
    if project:
        scope_payload["project"] = project
    if trace_id:
        scope_payload["trace_id"] = trace_id
    if facets:
        scope_payload["facets"] = facets
    if since_last_run:
        scope_payload["since_last_run"] = True
    if sync:
        scope_payload["sync"] = True
    if reconcile:
        scope_payload["reconcile"] = True
    answers = None
    if answers_path:
        try:
            answers = load_judgment_answers(answers_path)
        except (ValueError, OSError) as exc:
            click.echo(f"could not read --answers file: {exc}", err=True)
            sys.exit(2)
    try:
        result = run_dataset_workflow(
            name,
            dry_run=dry_run,
            executor=executor,
            scope=scope_payload,
            limit=limit,
            scheduled=scheduled,
            privacy_tier=privacy_tier,
            trail_freshness_policy=trail_freshness_policy,
            answers=answers,
            strict=strict,
            sync=sync,
        )
    except WorkflowNeedsJudgmentError as exc:
        # #186 handshake: the workflow needs recorded judgments. Emit the
        # structured requests + instruction and exit rc=10 (the slicing
        # precedent). Under --json, stdout is pure JSON; otherwise the requests
        # and instruction go to stderr as prose.
        envelope = build_needs_judgment(
            workflow=exc.workflow_name,
            judgment_requests=exc.judgment_requests,
        )
        if as_json:
            click.echo(_dump_json(envelope))
        else:
            for request in envelope["judgment_requests"]:
                click.echo(
                    f"[judgment] {request.get('id')}: {request.get('prompt')}",
                    err=True,
                )
            click.echo(envelope["instruction"], err=True)
        sys.exit(RC_NEEDS_JUDGMENT)
    except (
        FileNotFoundError,
        ValueError,
        ExecutorUnavailableError,
        DatasetRunLockError,
        WorkflowIntegrityError,
        WorkflowScriptError,
    ) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)

    payload = {
        "status": result.status,
        "run_id": result.run_id,
        "run": {
            **result.run_record.model_dump(mode="json"),
            "would_append_count": result.append_summary.would_append_count,
            # #212 review fix (finding 1c): rows the runner rejected because
            # their source trace id fell outside a faceted run's resolved
            # match set -- zero on every unfaceted run.
            "facet_rejected_count": result.append_summary.facet_rejected_count,
        },
        "cursor_advanced": result.cursor_advanced,
        "telemetry": {"duration_ms": round((time.monotonic() - started) * 1000, 2)},
    }
    if result.delta_scope is not None:
        payload["delta_scope"] = result.delta_scope
    if approve_new and result.append_summary.row_ids:
        review_state = set_publication_review_status(
            name,
            result.append_summary.row_ids,
            "publishable",
            reviewer="dataset-run-automation",
        )
        payload["review"] = {
            "approved_new_count": sum(
                1
                for row_id in result.append_summary.row_ids
                if review_state.rows[row_id].status == "publishable"
            ),
            "row_ids": result.append_summary.row_ids,
        }
    if publish or publish_check_only:
        try:
            token, _username = _hf_auth()
            publication = publish_dataset(
                name,
                check_only=publish_check_only,
                token=token,
            )
        except DatasetPublishWithheldError as exc:
            _emit_publish_withheld(exc, as_json=as_json)
            sys.exit(exc.exit_code)
        except (DatasetRemotePermissionError, DatasetRemoteSchemaAheadError, ValueError) as exc:
            click.echo(str(exc), err=True)
            sys.exit(3)
        payload["publish"] = _publish_summary_payload(publication)
    if verbose:
        payload["artefacts"] = {"run_dir": str(result.run_dir)}
    if as_json:
        click.echo(_dump_json(payload))
        return
    if result.status == "instructions":
        click.echo(f"Run packet ready: {result.run_dir}")
    else:
        click.echo(
            f"Run {result.run_id}: appended={result.append_summary.appended_count} "
            f"duplicates={result.append_summary.duplicate_count} "
            f"invalid={result.append_summary.validation_error_count}"
        )


_INTERACTIVE_REVIEW_DECOMMISSIONED = (
    "Interactive TUI/web review clients are decommissioned for now. "
    "Use `opentraces dataset review <name> --json` to inspect rows, "
    "then `opentraces dataset review approve|reject|reset <name> ...`."
)


@dataset_group.command("review", cls=OpentracesCommand)
@click.argument("args", nargs=-1)
@click.option(
    "--tui",
    "mode",
    flag_value="tui",
    default=None,
    hidden=True,  # removed interactive client; flag kept callable, off --help
    help="Removed interactive review client; use `--json` plus approve/reject/reset.",
)
@click.option(
    "--web",
    "mode",
    flag_value="web",
    hidden=True,  # removed interactive client; flag kept callable, off --help
    help="Removed interactive review client; use `--json` plus approve/reject/reset.",
)
@click.option(
    "--all",
    "all_rows",
    is_flag=True,
    help="With `approve`, `reject`, or `reset`, apply to every eligible row.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_review(args: tuple[str, ...], mode: str | None, all_rows: bool, as_json: bool) -> None:
    """Review, approve, reject, or reset dataset rows."""
    if not args:
        click.echo(
            "Usage: ot dataset review <name> OR "
            "ot dataset review approve|reject|reset <name> [ROW_ID...]",
            err=True,
        )
        sys.exit(2)
    if args[0] in {"approve", "reject", "reset"}:
        if len(args) < 2:
            click.echo(
                f"Usage: ot dataset review {args[0]} <name> [ROW_ID...]",
                err=True,
            )
            sys.exit(2)
        decision = {
            "approve": "publishable",
            "reject": "rejected",
            "reset": "reset",
        }[args[0]]
        _dataset_review_transition(args[1], list(args[2:]), all_rows, decision, as_json)
        return
    if len(args) > 1:
        click.echo("Usage: ot dataset review <name>", err=True)
        sys.exit(2)
    if mode in {"tui", "web"}:
        if as_json:
            click.echo(_dump_json({
                "status": "error",
                "error": "interactive_review_decommissioned",
                "message": _INTERACTIVE_REVIEW_DECOMMISSIONED,
            }))
        else:
            click.echo(_INTERACTIVE_REVIEW_DECOMMISSIONED, err=True)
        sys.exit(2)
    name = args[0]
    try:
        state = evaluate_publication_state(name)
        dataset = load_dataset(name)
        row_schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
        rows_by_id = read_rows_by_id(name)
        provenance_by_id = read_row_provenance(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    review_rows = {}
    for row_id, entry in sorted(state.rows.items()):
        review_rows[row_id] = {
            **entry.model_dump(mode="json", exclude_none=True),
            "data": rows_by_id.get(row_id),
            "provenance": provenance_by_id.get(row_id),
        }
    payload = {
        "status": "ok",
        "dataset": name,
        "mode": mode or "cli",
        "schema": {
            "path": dataset.manifest.schema_ref.path,
            "digest": dataset.manifest.schema_ref.digest,
            "json_schema": row_schema,
        },
        "counts": _publication_counts(state),
        "rows": review_rows,
    }
    if as_json:
        click.echo(_dump_json(payload))
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
@click.option(
    "--min-retention",
    "min_retention",
    type=click.FloatRange(min=0.0, max=1.0),
    default=None,
    help=(
        "Drop rows whose mean retention_fraction across patches_with_survival "
        "is below this threshold (0.0-1.0)."
    ),
)
@click.option(
    "--exclude-state",
    "exclude_states",
    multiple=True,
    default=(),
    help=(
        "Drop rows that have any patch with this survival_state. Repeatable: "
        "--exclude-state lost --exclude-state never_committed."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_publish(
    name: str,
    remote: str | None,
    check_only: bool,
    resume: str | None,
    min_retention: float | None,
    exclude_states: tuple[str, ...],
    as_json: bool,
) -> None:
    """Publish reviewed dataset rows and contract files to the active remote.

    Cluster F D8: ``--min-retention`` and ``--exclude-state`` filter rows by
    survival quality before staging. Both compose. Under ``--check-only`` the
    drop counts are reported in the JSON ``publish.filter`` block without
    uploading.
    """
    try:
        token, _username = _hf_auth()
        summary = publish_dataset(
            name,
            to=remote,
            check_only=check_only,
            resume=resume,
            token=token,
            min_retention=min_retention,
            exclude_states=list(exclude_states) if exclude_states else None,
        )
    except DatasetPublishWithheldError as exc:
        _emit_publish_withheld(exc, as_json=as_json)
        sys.exit(exc.exit_code)
    except DatasetRemotePermissionError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except DatasetRemoteSchemaAheadError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    publish_payload = _publish_summary_payload(summary)
    payload = {"status": "ok", "publish": publish_payload}
    if as_json:
        click.echo(_dump_json(payload))
        return
    line = (
        f"{summary.message}: rows={summary.new_row_count} "
        f"needs_review={summary.needs_review_count} blocked={summary.blocked_count}"
    )
    if summary.filter_summary is not None:
        line += (
            f" filter_dropped={summary.filter_summary.get('rows_dropped_total', 0)}"
        )
    click.echo(line)


@dataset_group.command("verify", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def dataset_verify(name: str, as_json: bool) -> None:
    """Replay the bound workflow and grade the dataset seal's explainability.

    Re-executes the workflow against the bucket (with recorded answers) in a
    side-effect-free mode and byte-compares the re-run to the stored rows,
    classifying the outcome as ``reproduces`` / ``bucket-advanced`` /
    ``integrity-failure``. An integrity failure exits non-zero.
    """
    from ..core.dataset_verify import verify_dataset, verify_envelope

    try:
        result = verify_dataset(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except (ExecutorUnavailableError, WorkflowIntegrityError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    envelope = verify_envelope(result)
    if as_json:
        click.echo(_dump_json(envelope))
    else:
        line = f"{result.verdict}: {result.detail}"
        if result.verdict == "bucket-advanced":
            line += f" (delta={len(result.delta)})"
        click.echo(line)
    if result.exit_code != 0:
        sys.exit(result.exit_code)


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
        click.echo(_dump_json(payload))
        return
    click.echo(f"Dataset removed: {name}")


def _compute_row_quality_summary(name: str) -> dict[str, object] | None:
    """Cluster F D9: roll up survival info across the dataset's rows.

    Returns ``None`` when the dataset has no readable rows. The summary
    is computed from ``patches_with_survival`` on each row.
    """
    try:
        from ..core.datasets import read_rows_by_id
        rows = read_rows_by_id(name)
    except (FileNotFoundError, OSError):
        return None
    if not rows:
        # Empty datasets get a zero summary so listing a fresh dataset
        # still shows the structure callers can rely on.
        return {
            "total_rows": 0,
            "rows_with_anchored_patches": 0,
            "rows_with_lost_patches": 0,
            "rows_fully_alive": 0,
            "mean_row_retention": None,
            "survival_distribution": {},
        }
    total_rows = len(rows)
    rows_with_anchored = 0
    rows_with_lost = 0
    rows_fully_alive = 0
    survival_distribution: dict[str, int] = {}
    row_means: list[float] = []

    for row in rows.values():
        psv = row.get("patches_with_survival") or []
        if not isinstance(psv, list) or not psv:
            continue
        states = [
            p.get("survival_state")
            for p in psv
            if isinstance(p, dict) and p.get("survival_state")
        ]
        if not states:
            continue
        rows_with_anchored += 1
        if "lost" in states:
            rows_with_lost += 1
        if all(s in ("alive_on_path", "alive_transformed") for s in states):
            rows_fully_alive += 1
        for s in states:
            survival_distribution[s] = survival_distribution.get(s, 0) + 1
        fractions = [
            p.get("retention_fraction")
            for p in psv
            if isinstance(p, dict) and isinstance(p.get("retention_fraction"), (int, float))
        ]
        if fractions:
            row_means.append(sum(fractions) / len(fractions))

    mean_row_retention: float | None = None
    if row_means:
        mean_row_retention = round(sum(row_means) / len(row_means), 4)
    return {
        "total_rows": total_rows,
        "rows_with_anchored_patches": rows_with_anchored,
        "rows_with_lost_patches": rows_with_lost,
        "rows_fully_alive": rows_fully_alive,
        "mean_row_retention": mean_row_retention,
        "survival_distribution": survival_distribution,
    }


def _dataset_payload(dataset) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": dataset.name,
        "path": str(dataset.path),
        "manifest": dataset.manifest.model_dump(mode="json", by_alias=True, exclude_none=True),
        "security": _dataset_security_payload(dataset.manifest.security),
    }
    # Surface the manual marker + row count for ad-hoc datasets so agents
    # can detect them without re-reading the manifest skill string.
    if _is_manual_dataset(dataset):
        payload["manual"] = True
        try:
            payload["row_count"] = len(read_row_index(dataset.name))
        except (FileNotFoundError, OSError):
            payload["row_count"] = 0
    # Cluster F D9: row_quality summary on every dataset payload.
    rq = _compute_row_quality_summary(dataset.name)
    if rq is not None:
        payload["row_quality"] = rq
    return payload


def _render_dataset_security_hint(dataset) -> None:
    policy = dataset.manifest.security
    enabled_tools = policy.enabled_tools
    click.echo(
        "Dataset security tools enabled: "
        + (", ".join(enabled_tools) if enabled_tools else "none")
    )
    if policy.required_tools:
        click.echo("Required by workflow: " + ", ".join(policy.required_tools))
    click.echo(f"Manage with: opentraces dataset security {dataset.name}")


def _remote_payload(summary) -> dict[str, object]:
    return {
        "dataset": summary.dataset_name,
        "name": summary.name,
        "url": summary.url,
        "visibility": summary.visibility,
        "active": summary.active,
    }


def _publish_withheld_payload(exc: DatasetPublishWithheldError) -> dict[str, object]:
    """The frozen ``opentraces.dataset.publish.clearance.v1`` refusal envelope.

    ``status='refused'`` + the enumerable ``{published, refused}`` partition,
    mirroring bucket sync's Door-A auditable refusal (zero bytes egressed).
    """
    partition = exc.partition or {}
    return {
        "status": "refused",
        "schema_version": "opentraces.dataset.publish.clearance.v1",
        "publish": {
            "refused": partition.get("refused") or [],
            "published": partition.get("published") or [],
            "message": str(exc),
        },
    }


def _emit_publish_withheld(exc: DatasetPublishWithheldError, *, as_json: bool) -> None:
    if as_json:
        click.echo(_dump_json(_publish_withheld_payload(exc)))
        return
    click.echo(str(exc), err=True)
    for row in exc.partition.get("refused") or []:
        click.echo(
            f"  refused {row.get('row_id')} "
            f"(trace {row.get('trace_id')}): {row.get('sub_reason')}",
            err=True,
        )


def _publish_summary_payload(summary) -> dict[str, object]:
    payload: dict[str, object] = {
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
    }
    if summary.filter_summary is not None:
        payload["filter"] = summary.filter_summary
    return payload


def _emit_remote_payload(summary, *, as_json: bool, verb: str) -> None:
    payload = _remote_payload(summary)
    if as_json:
        click.echo(_dump_json({"status": "ok", "remote": payload}))
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
        click.echo(_dump_json(payload))
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
        click.echo(_dump_json(payload))
        return
    click.echo(f"{name}: updated {len(selected)} row(s)")
