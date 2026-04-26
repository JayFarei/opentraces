"""``ot trail`` — Trace Trails inspection commands."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup


@click.group("trail", cls=OpentracesGroup)
def trail_group() -> None:
    """Explain VCS-anchored Trace Trails."""


@trail_group.command(
    "explain",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail explain --trace tr1 --step 1",
        "opentraces trail explain --trace tr1 --step 1 --json",
        "opentraces trail explain --commit abc1234 --json",
    ],
    see_also=[
        ("opentraces blame", "show commit attribution."),
        ("opentraces graph", "render commit + trace history."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "step_index", "commit", "project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("target", required=False)
@click.option("--trace", "trace_id", default=None, help="Trace id to explain.")
@click.option("--step", "step_index", default=None, type=int, help="Trace step index.")
@click.option("--commit", "commit", default=None, help="Git commit to explain.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def explain_cmd(
    target: str | None,
    trace_id: str | None,
    step_index: int | None,
    commit: str | None,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Explain the evidence chain for a trace step."""
    from ..core.trails import explain_commit, explain_file_line, explain_trace_step

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        if target:
            payload = explain_file_line(repo, target)
        elif commit:
            payload = explain_commit(repo, commit)
        elif trace_id and step_index is not None:
            payload = explain_trace_step(repo, trace_id, step_index)
        else:
            click.echo("Provide TARGET, --commit, or both --trace and --step.", err=True)
            sys.exit(2)
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to explain trace trail: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if target:
        click.echo(f"Line {payload['target']}")
        patch = payload.get("trace_patch")
        if patch:
            click.echo(
                f"  {patch.get('trace_id')} {patch.get('trace_patch_id')} "
                f"{patch.get('evidence_tier')}"
            )
        else:
            click.echo("  relation: unknown")
        return

    if commit:
        click.echo(f"Commit {payload['commit_sha'][:12]}")
        for patch in payload.get("trace_patches") or []:
            click.echo(
                f"  {patch.get('trace_id')} {patch.get('trace_patch_id')} "
                f"{patch.get('evidence_tier')}"
            )
        if not payload.get("trace_patches"):
            click.echo("  no Trace Patches anchored in this commit")
        return

    click.echo(f"Trace {payload['trace_id']} {payload['step_id']}")
    if payload.get("relation") == "anchored_in_git":
        anchor = payload.get("git_anchor") or {}
        click.echo(
            "  anchored in git: "
            f"{(anchor.get('commit_sha') or '')[:12]} "
            f"{anchor.get('path')}:{(anchor.get('range') or {}).get('start_line')}"
        )
        click.echo(
            f"  evidence: {payload.get('evidence_tier')} "
            f"({payload.get('evidence_firmness')})"
        )
    else:
        click.echo("  relation: unknown")
        for limitation in payload.get("limitations") or []:
            click.echo(f"  limitation: {limitation}")
    for claim in payload.get("unavailable_stronger_claims") or []:
        click.echo(f"  unavailable: {claim}")


@trail_group.command(
    "diff",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail diff --trace tr1 --from-step 1 --to-step 2",
        "opentraces trail diff --trace tr1 --from-step 1 --to-step 2 --json",
    ],
    see_also=[
        ("opentraces trail explain", "explain an anchored trace patch."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "from_step", "to_step", "project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.option("--trace", "trace_id", required=True, help="Trace id to diff.")
@click.option("--from-step", "from_step", required=True, type=int, help="Starting step snapshot.")
@click.option("--to-step", "to_step", required=True, type=int, help="Ending step snapshot.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def diff_cmd(
    trace_id: str,
    from_step: int,
    to_step: int,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Show the Trace Patch between two captured step snapshots."""
    from ..core.trails import diff_step_snapshots

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        payload = diff_step_snapshots(repo, trace_id, from_step, to_step)
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to diff trace snapshots: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if payload.get("relation") == "unknown":
        click.echo("Trace snapshot diff is unknown")
        for limitation in payload.get("limitations") or []:
            click.echo(f"  limitation: {limitation}")
        return
    click.echo(payload["trace_patch"]["patch"], nl=False)
