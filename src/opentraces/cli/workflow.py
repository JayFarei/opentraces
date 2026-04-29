"""CLI commands for local dataset workflow skills."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ..core.workflows import (
    WorkflowPackage,
    create_workflow,
    list_workflows,
    load_workflow,
    remove_workflow,
)


@click.group("workflow", cls=OpentracesGroup)
def workflow_group() -> None:
    """Manage local dataset workflow skills."""


@workflow_group.command("list", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_list(as_json: bool) -> None:
    """List installed dataset workflow skill packages."""
    workflows = [_workflow_payload(workflow) for workflow in list_workflows()]
    if as_json:
        click.echo(json.dumps({"status": "ok", "workflows": workflows}, indent=2, sort_keys=True))
        return
    for workflow in workflows:
        click.echo(f"{workflow['name']}  {workflow['digest']}")


@workflow_group.command("show", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_show(name: str, as_json: bool) -> None:
    """Show one dataset workflow package and digest."""
    try:
        workflow = load_workflow(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    payload = {"status": "ok", "workflow": _workflow_payload(workflow)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"{workflow.name}  {workflow.digest}")


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
    click.echo(f"Workflow created: {workflow.name}")


@workflow_group.command("edit", cls=OpentracesCommand)
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_edit(name: str, as_json: bool) -> None:
    """Open a local dataset workflow skill for editing."""
    try:
        workflow = load_workflow(name)
    except (FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    skill_path = workflow.path / "SKILL.md"
    payload = {
        "status": "ok",
        "workflow": _workflow_payload(workflow),
        "edit_path": str(skill_path),
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    editor = os.environ.get("EDITOR")
    if editor:
        raise SystemExit(subprocess.call([editor, str(skill_path)]))
    click.echo(f"Workflow path: {workflow.path}")
    click.echo(f"Skill file:     {skill_path}")
    click.echo("Set EDITOR to open it directly.")


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
