"""CLI commands for local dataset workflow skills."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

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


@workflow_group.command("optimize", cls=OpentracesCommand)
@click.option(
    "--workflow", "workflow_name", default=None,
    help="Scored-rollout workflow that projects captured traces into rows. [default: skill-opt-v1]",
)
@click.option("--project", default=None, help="Restrict the bucket scan to one project slug.")
@click.option("--budget", default=4, show_default=True, type=int, help="Edit budget (textual learning rate).")
@click.option("--budget-floor", default=2, show_default=True, type=int, help="Minimum edit budget under decay.")
@click.option(
    "--schedule", default="cosine", show_default=True,
    type=click.Choice(["constant", "linear", "cosine"]), help="Edit-budget schedule.",
)
@click.option("--selection-fraction", default=0.4, show_default=True, type=float, help="Held-out selection split fraction.")
@click.option("--seed", default="skillopt", show_default=True, help="Deterministic split seed.")
@click.option("--max-steps", default=8, show_default=True, type=int, help="Maximum propose-and-rank steps per epoch.")
@click.option("--epochs", default=1, show_default=True, type=int, help="Optimization epochs (slow/meta update runs at each boundary).")
@click.option(
    "--proposer", "proposer_kind", default="default", show_default=True,
    type=click.Choice(["default", "llm"]),
    help="Reflection proposer: 'default' (deterministic) or 'llm' (full C.2 chain via the offline fake client).",
)
@click.option("--no-slow-update", is_flag=True, help="Disable the epoch-boundary slow/meta update.")
@click.option("--initial-skill", "initial_skill_path", type=click.Path(exists=True, dir_okay=False), default=None, help="Starting skill markdown (defaults to a managed starter).")
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default=None, help="Output directory for best_skill.md + edit_apply_report.json.")
@click.option("--dry-run", is_flag=True, help="Run the loop and write artifacts without promoting the winning skill.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_optimize(
    workflow_name: str | None,
    project: str | None,
    budget: int,
    budget_floor: int,
    schedule: str,
    selection_fraction: float,
    seed: str,
    max_steps: int,
    epochs: int,
    proposer_kind: str,
    no_slow_update: bool,
    initial_skill_path: str | None,
    out_dir: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Optimize a skill from scored rollouts (SkillOpt, arXiv 2605.23904).

    Runs the scored-rollout workflow over already-captured traces, splits the
    rows into a train and a held-out selection set by trace-id hash, proposes
    bounded edits, accepts only strictly-improving candidates, and writes
    ``best_skill.md`` plus ``edit_apply_report.json``. Without ``--dry-run`` the
    winning skill is also promoted to a managed location. The live agent
    re-rollout and the task-outcome scorer are a later slice, so the held-out
    gate uses the deterministic proxy in ``consumers.skill_opt.engine``.
    """
    from ..consumers.skill_opt.proposers import (
        DeterministicOptimizerClient,
        default_proposer,
        make_llm_proposer,
    )
    from ..consumers.skill_opt.runner import DEFAULT_WORKFLOW, SkillOptRequest, run

    proposer = (
        make_llm_proposer(DeterministicOptimizerClient())
        if proposer_kind == "llm"
        else default_proposer
    )

    run_id = "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out_path = Path(out_dir).expanduser() if out_dir else Path("runs") / "skill-opt" / run_id
    initial_skill = (
        Path(initial_skill_path).read_text(encoding="utf-8")
        if initial_skill_path
        else SkillOptRequest.initial_skill
    )

    request = SkillOptRequest(
        out_dir=out_path,
        workflow_name=workflow_name or DEFAULT_WORKFLOW,
        project=project,
        budget=budget,
        budget_floor=budget_floor,
        schedule=schedule,
        selection_fraction=selection_fraction,
        seed=seed,
        max_steps=max_steps,
        epochs=epochs,
        initial_skill=initial_skill,
        dry_run=dry_run,
        slow_update=not no_slow_update,
        proposer=proposer,
    )
    outcome = run(request)

    payload = {
        "status": "ok",
        "dry_run": dry_run,
        "best_skill": str(outcome.best_skill_path),
        "edit_apply_report": str(outcome.report_path),
        "promoted": str(outcome.promoted_path) if outcome.promoted_path else None,
        **outcome.metadata,
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(
        f"SkillOpt: {outcome.rollout_rows} rollout row(s), "
        f"{outcome.accepted} accepted / {outcome.rejected} rejected edit(s), "
        f"score {outcome.initial_score:.3f} -> {outcome.best_score:.3f}"
    )
    click.echo(f"best_skill.md: {outcome.best_skill_path}")
    click.echo(f"edit_apply_report.json: {outcome.report_path}")
    if outcome.promoted_path:
        version = outcome.metadata.get("promoted_version")
        click.echo(f"promoted: {outcome.promoted_path}" + (f" (v{version})" if version else ""))
    elif not dry_run and outcome.metadata.get("promote_skipped_reason"):
        click.echo(f"not promoted: {outcome.metadata['promote_skipped_reason']}")


def _workflow_payload(workflow: WorkflowPackage) -> dict[str, object]:
    return {
        "name": workflow.name,
        "description": workflow.description,
        "digest": workflow.digest,
        "path": str(workflow.path),
    }
