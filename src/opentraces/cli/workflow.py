"""CLI commands for local dataset workflow skills."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json
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
        click.echo(_dump_json({"status": "ok", "workflows": payload}))
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
        click.echo(_dump_json(payload))
        return
    click.echo(f"Workflow created: {workflow.name}  {workflow.path}")


@workflow_group.command("templates", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_templates(as_json: bool) -> None:
    """List built-in workflow templates available to `workflow create`."""
    templates = list_workflow_templates()
    if as_json:
        click.echo(_dump_json({"status": "ok", "templates": templates}))
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
        click.echo(_dump_json(payload))
        return
    click.echo(f"Workflow removed: {name}")


@workflow_group.command("optimize", cls=OpentracesCommand, hidden=True)
@click.option(
    "--workflow", "workflow_name", default=None,
    help="Scored-rollout workflow that projects captured traces into rows. [default: skill-opt-v1]",
)
@click.option("--project", default=None, help="Restrict the bucket scan to one project slug.")
@click.option("--budget", default=4, show_default=True, type=int, help="Edit budget (textual learning rate).")
@click.option("--budget-floor", default=2, show_default=True, type=int, help="Minimum edit budget under decay.")
@click.option(
    "--schedule", default="cosine", show_default=True,
    type=click.Choice(["constant", "linear", "cosine", "autonomous"]), help="Edit-budget schedule.",
)
@click.option("--selection-fraction", default=0.4, show_default=True, type=float, help="Held-out selection split fraction.")
@click.option("--test-fraction", default=0.2, show_default=True, type=float, help="Held-out test split fraction.")
@click.option("--seed", default="skillopt", show_default=True, help="Deterministic split seed.")
@click.option("--max-steps", default=8, show_default=True, type=int, help="Maximum propose-and-rank steps per epoch.")
@click.option("--epochs", default=1, show_default=True, type=int, help="Optimization epochs (slow/meta update runs at each boundary).")
@click.option("--reflection-minibatch-size", default=8, show_default=True, type=int, help="Reflection minibatch size Bm.")
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
    test_fraction: float,
    seed: str,
    max_steps: int,
    epochs: int,
    reflection_minibatch_size: int,
    proposer_kind: str,
    no_slow_update: bool,
    initial_skill_path: str | None,
    out_dir: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Optimize a skill from scored rollouts (SkillOpt, arXiv 2605.23904).

    Runs the scored-rollout workflow over already-captured traces, splits the
    rows into Dtrain / Dsel / Dtest by trace-id hash, proposes bounded edits,
    accepts only strictly-improving candidates on Dsel, reports held-out Dtest,
    and writes ``best_skill.md`` plus ``edit_apply_report.json``. Without
    ``--dry-run`` the winning skill is also promoted to a managed location.
    """
    from ..consumers.skill_opt.proposers import (
        DeterministicOptimizerClient,
        default_proposer,
        make_llm_proposer,
    )
    from ..consumers.skill_opt.runner import DEFAULT_WORKFLOW, SkillOptRequest, run
    from ..core.workflow_runner import ExecutorUnavailableError, WorkflowScriptError

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
        test_fraction=test_fraction,
        seed=seed,
        max_steps=max_steps,
        epochs=epochs,
        reflection_minibatch_size=reflection_minibatch_size,
        initial_skill=initial_skill,
        dry_run=dry_run,
        slow_update=not no_slow_update,
        proposer=proposer,
    )
    try:
        outcome = run(request)
    except (WorkflowScriptError, ExecutorUnavailableError) as exc:
        # World-not-ready (no rollout rows, missing workflow/script): a clean
        # error envelope + exit 3, never a raw traceback.
        if as_json:
            click.echo(_dump_json({
                "status": "error",
                "error": {
                    "code": "WORKFLOW_FAILED",
                    "kind": type(exc).__name__,
                    "message": str(exc),
                    "hint": "capture traces first, then re-run "
                            "`opentraces workflow optimize`",
                    "retryable": True,
                },
            }))
        click.echo(str(exc), err=True)
        sys.exit(3)

    payload = {
        "status": "ok",
        "dry_run": dry_run,
        "best_skill": str(outcome.best_skill_path),
        "edit_apply_report": str(outcome.report_path),
        "promoted": str(outcome.promoted_path) if outcome.promoted_path else None,
        **outcome.metadata,
    }
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(
        f"SkillOpt: {outcome.rollout_rows} rollout row(s), "
        f"{outcome.accepted} accepted / {outcome.rejected} rejected edit(s), "
        f"selection {outcome.initial_score:.3f} -> {outcome.best_score:.3f}, "
        f"test {outcome.test_score:.3f}"
    )
    click.echo(f"best_skill.md: {outcome.best_skill_path}")
    click.echo(f"edit_apply_report.json: {outcome.report_path}")
    if outcome.promoted_path:
        version = outcome.metadata.get("promoted_version")
        click.echo(f"promoted: {outcome.promoted_path}" + (f" (v{version})" if version else ""))
    elif not dry_run and outcome.metadata.get("promote_skipped_reason"):
        click.echo(f"not promoted: {outcome.metadata['promote_skipped_reason']}")


@workflow_group.command("skill-intelligence", cls=OpentracesCommand, hidden=True)
@click.option("--project", default=None, help="Restrict the corpus audit to one project slug.")
@click.option("--skill", default=None, help="Force the selected skill pack instead of audit selection.")
@click.option(
    "--min-episodes",
    default=30,
    show_default=True,
    type=int,
    help="Minimum usable episode count before seeded CI corpus is required.",
)
@click.option("--seed", default="skill-intelligence", show_default=True, help="Split seed.")
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Output directory for audit, datasets, rollouts, and report.",
)
@click.option("--dry-run", is_flag=True, help="Write artifacts without promoting a skill.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_skill_intelligence(
    project: str | None,
    skill: str | None,
    min_episodes: int,
    seed: str,
    out_dir: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Run the internal Skill Intelligence eval pipeline."""
    from ..consumers.skill_intelligence import SkillIntelligenceRequest, run_pipeline

    out_path = (
        Path(out_dir).expanduser()
        if out_dir
        else Path("runs") / "skill-intelligence-pipeline"
    )
    result = run_pipeline(
        SkillIntelligenceRequest(
            out_dir=out_path,
            project=project,
            skill=skill,
            min_usable_episodes=min_episodes,
            seed=seed,
            dry_run=dry_run,
        )
    )
    payload = {
        "status": "ok",
        "dry_run": dry_run,
        "selected_skill": result.selected_skill,
        "report_path": str(result.report_path),
        "markdown_report_path": str(result.markdown_report_path),
        "audit_path": str(result.audit_path),
        "case_study_dir": str(result.case_study_dir),
        "dtest_score": result.dtest_score,
        "dsel_before": result.dsel_before,
        "dsel_after": result.dsel_after,
        "accepted": result.accepted,
        "split_counts": result.split_counts,
        **result.metadata,
    }
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(
        f"Skill Intelligence: {result.selected_skill}, "
        f"Dsel {result.dsel_before:.3f} -> {result.dsel_after:.3f}, "
        f"Dtest {result.dtest_score:.3f}"
    )
    click.echo(f"report: {result.report_path}")
    click.echo(f"case study: {result.case_study_dir}")
    if not dry_run:
        click.echo("promotion: manual/default-off; no skill was automatically promoted")


@workflow_group.command("verifier-factory", cls=OpentracesCommand, hidden=True)
@click.option("--project", default=None, help="Restrict mining to one project slug.")
@click.option(
    "--example",
    "examples",
    multiple=True,
    help="Restrict to skill:archetype example(s); repeatable. Default: the three bundled examples.",
)
@click.option(
    "--min-episodes",
    default=30,
    show_default=True,
    type=int,
    help="Minimum usable episodes before a skill is flagged data-gapped.",
)
@click.option("--seed", default="verifier-factory", show_default=True, help="Split seed.")
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Output directory for candidates, packages, and index.",
)
@click.option(
    "--promote",
    is_flag=True,
    help="(Reserved) explicit human approval is always required; this flag never auto-promotes.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def workflow_verifier_factory(
    project: str | None,
    examples: tuple[str, ...],
    min_episodes: int,
    seed: str,
    out_dir: str | None,
    promote: bool,
    as_json: bool,
) -> None:
    """Mine skill_invocation evidence into trace-grounded verifier packages (dry-run).

    Always a dry run: it writes a ``skill-verifier-candidates-v1`` summary plus one
    verifier package per example (spec.yaml, fixtures, scorer.py, Dtrain/Dsel/Dtest
    rows, report) and prints each package path with its Dsel/Dtest scores. No verifier
    is generated for promotion and no skill is changed without explicit human approval.
    """
    from ..consumers.verifier_factory import (
        DEFAULT_EXAMPLES,
        VerifierFactoryRequest,
        run_factory,
    )

    if examples:
        parsed: list[tuple[str, str]] = []
        for spec in examples:
            if ":" not in spec:
                raise click.BadParameter(f"--example must be skill:archetype, got {spec!r}")
            skill, archetype_id = spec.split(":", 1)
            parsed.append((skill.strip(), archetype_id.strip()))
        example_tuple = tuple(parsed)
    else:
        example_tuple = DEFAULT_EXAMPLES

    out_path = (
        Path(out_dir).expanduser() if out_dir else Path("runs") / "skill-verifier-factory"
    )
    result = run_factory(
        VerifierFactoryRequest(
            out_dir=out_path,
            project=project,
            examples=example_tuple,
            min_usable_episodes=min_episodes,
            seed=seed,
        )
    )
    packages = [
        {
            "archetype_id": p.archetype_id,
            "skill_id": p.skill_id,
            "package_dir": str(p.package_dir),
            "spec_path": str(p.spec_path),
            "report_path": str(p.report_path),
            "dsel_before": p.dsel_before,
            "dsel_after": p.dsel_after,
            "dtest_score": p.dtest_score,
            "accepted": p.accepted,
            "split": p.split_counts,
            "addressable_markers": list(p.addressable_markers),
            "limitations": list(p.limitations),
        }
        for p in result.packages
    ]
    payload = {
        "status": "ok",
        "dry_run": True,
        "promotion": "manual_required_default_off",
        "automatic_promotion": False,
        "candidates_path": str(result.candidates_path),
        "index_path": str(result.index_path),
        "packages": packages,
    }
    if as_json:
        click.echo(_dump_json(payload))
        return
    click.echo(f"Verifier candidates: {result.candidates_path}")
    for pkg in packages:
        click.echo(
            f"  {pkg['archetype_id']} ({pkg['skill_id']}): "
            f"Dsel {pkg['dsel_before']:.3f} -> {pkg['dsel_after']:.3f}, "
            f"Dtest {pkg['dtest_score']:.3f}  ->  {pkg['spec_path']}"
        )
    click.echo("promotion: manual/default-off; no verifier or skill was promoted")


def _workflow_payload(workflow: WorkflowPackage) -> dict[str, object]:
    return {
        "name": workflow.name,
        "description": workflow.description,
        "digest": workflow.digest,
        "path": str(workflow.path),
    }
