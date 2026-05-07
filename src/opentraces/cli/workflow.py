"""CLI commands for local dataset workflow skills."""

from __future__ import annotations

import json
import sys

import click

from ._help import OpentracesCommand, OpentracesGroup
from ..core.datasets import list_datasets
from ..core.workflows import (
    WorkflowPackage,
    create_workflow,
    list_workflow_templates,
    list_workflows,
    remove_workflow,
)


@click.group("workflow", cls=OpentracesGroup)
def workflow_group() -> None:
    """Manage local dataset workflow skills."""


def _datasets_by_workflow_skill() -> dict[str, list[str]]:
    """Reverse-index dataset bindings: ``skill_name -> [dataset_name, ...]``.

    The cross-reference is the only thing the CLI uniquely knows. Browsing
    files is the shell's job; computing which datasets bind a workflow
    requires reading every dataset manifest and is what justifies a CLI
    command at all.
    """
    index: dict[str, list[str]] = {}
    for dataset in list_datasets():
        skill = dataset.manifest.workflow.skill
        if skill:
            index.setdefault(skill, []).append(dataset.name)
    return index


@workflow_group.command("list", cls=OpentracesCommand)
@click.option("--digest", "show_digest", is_flag=True, help="Also show the content digest.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_list(show_digest: bool, as_json: bool) -> None:
    """List installed workflows with their path and bound datasets."""
    bindings = _datasets_by_workflow_skill()
    workflows = list_workflows()
    payload = [
        {
            **_workflow_payload(workflow),
            "datasets": sorted(bindings.get(workflow.name, [])),
        }
        for workflow in workflows
    ]
    if as_json:
        click.echo(json.dumps({"status": "ok", "workflows": payload}, indent=2, sort_keys=True))
        return
    if not workflows:
        click.echo("No workflows installed.")
        return
    for entry in payload:
        users = ", ".join(entry["datasets"]) or "<unused>"
        line = f"{entry['name']}\t{entry['path']}\t{users}"
        if show_digest:
            line = f"{line}\t{entry['digest']}"
        click.echo(line)


@workflow_group.command("create", cls=OpentracesCommand)
@click.argument("name")
@click.option("--template", default="default", show_default=True, help="Workflow template.")
@click.option("--description", default=None, help="Workflow description for SKILL.md.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_create(
    name: str,
    template: str,
    description: str | None,
    as_json: bool,
) -> None:
    """Scaffold a new local dataset workflow skill."""
    try:
        workflow = create_workflow(name, description=description, template=template)
    except (FileExistsError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "workflow": _workflow_payload(workflow)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Workflow created: {workflow.name}  {workflow.path}")


@workflow_group.command("templates", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_templates(as_json: bool) -> None:
    """List built-in workflow templates available to `workflow create`."""
    templates = list_workflow_templates()
    if as_json:
        click.echo(json.dumps({"status": "ok", "templates": templates}, indent=2, sort_keys=True))
        return
    for template in templates:
        click.echo(template)


@workflow_group.command("remove", cls=OpentracesCommand)
@click.argument("name")
@click.option("--yes", is_flag=True, help="Confirm removal.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_remove(name: str, yes: bool, as_json: bool) -> None:
    """Remove an installed workflow skill package."""
    if not yes:
        click.echo("Pass --yes to remove a workflow.", err=True)
        sys.exit(2)
    try:
        removed_path = remove_workflow(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {
        "status": "ok",
        "removed": {
            "name": name,
            "path": str(removed_path),
        },
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Workflow removed: {name}")


def _workflow_payload(workflow: WorkflowPackage) -> dict[str, object]:
    return {
        "name": workflow.name,
        "description": workflow.description,
        "digest": workflow.digest,
        "path": str(workflow.path),
    }
