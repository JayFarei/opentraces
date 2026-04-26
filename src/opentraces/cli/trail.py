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
    "resolve",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail resolve ot://trace/tr1/patches/tracepatch-sha256:abc/trail --json",
        "opentraces trail resolve ot://git-anchor/gitanchor-sha256:def --json",
        "opentraces trail resolve ot://file/src/app.py/line/42/origin --json",
    ],
    see_also=[
        ("opentraces trail explain", "explain the evidence chain for a Trace Patch."),
        ("opentraces trail follow", "follow a Trace Patch through Git history."),
    ],
    option_groups=[
        ("Scope", ["resource", "project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument("resource")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def resolve_cmd(resource: str, as_json: bool, project_dir: Path | None) -> None:
    """Resolve a stable ot:// Trace Trails resource."""
    from ..core.trails import resolve_resource

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        payload = resolve_resource(repo, resource)
    except ValueError as exc:
        click.echo(f"Trace Trail resource is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to resolve trace trail resource: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    click.echo(f"{payload.get('resource_type')}: {payload.get('relation')}")
    segment_id = payload.get("containing_segment_id")
    if segment_id:
        click.echo(f"  containing segment: {segment_id}")
    for limitation in payload.get("limitations") or []:
        click.echo(f"  limitation: {limitation}")


@trail_group.command(
    "follow",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail follow --patch tracepatch-sha256:abc --json",
        "opentraces trail follow --anchor gitanchor-sha256:def --json",
    ],
    see_also=[
        ("opentraces trail explain", "explain the evidence chain for a Trace Patch."),
    ],
    option_groups=[
        ("Scope", ["trace_patch_id", "git_anchor_id", "project_dir"]),
        ("History", ["history_limit"]),
        ("Output", ["as_json"]),
    ],
)
@click.option("--patch", "trace_patch_id", default=None, help="Trace Patch id to follow.")
@click.option("--anchor", "git_anchor_id", default=None, help="Git Anchor id to follow.")
@click.option(
    "--history-limit",
    "history_limit",
    type=click.IntRange(min=2),
    default=None,
    help="Max commits to observe per Git Anchor (default 500, min 2).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def follow_cmd(
    trace_patch_id: str | None,
    git_anchor_id: str | None,
    history_limit: int | None,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Follow a Trace Patch through later Git history."""
    from ..core.trails import follow_anchor, follow_patch

    if bool(trace_patch_id) == bool(git_anchor_id):
        click.echo("Provide exactly one of --patch or --anchor.", err=True)
        sys.exit(2)

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        payload = (
            follow_patch(repo, trace_patch_id, history_limit=history_limit)
            if trace_patch_id
            else follow_anchor(repo, git_anchor_id or "", history_limit=history_limit)
        )
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to follow trace trail: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    current = payload.get("current_survival") or {}
    label = payload.get("git_anchor_id") or payload.get("trace_patch_id")
    click.echo(f"Patch Trail {label}")
    click.echo(f"  survival: {current.get('survival_state') or 'unknown'}")
    path = current.get("path")
    line_range = current.get("range") or {}
    if path:
        click.echo(f"  at: {path}:{line_range.get('start_line') or '?'}")
    for limitation in (
        payload.get("trail_limitations") or current.get("limitations") or []
    ):
        click.echo(f"  limitation: {limitation}")


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


@trail_group.command(
    "attach",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail attach --trace tr_abc --commit HEAD",
        "opentraces trail attach --trace tr_abc --commit abc1234 --json",
    ],
    see_also=[
        ("opentraces trail explain", "show evidence chain for a trace step."),
        ("opentraces blame", "show commit attribution."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "commit", "project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.option("--trace", "trace_id", required=True, help="Trace id to attach.")
@click.option(
    "--commit", "commit", required=True, help="Git commit to anchor against."
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def attach_cmd(
    trace_id: str,
    commit: str,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Retroactively connect a trace's evidence to a Git commit.

    Use after hook failure or partial capture: attach searches the
    trace's Trace Patches against the commit and appends
    manual_attach-tagged Git Anchor events to the canonical event log.
    Source TrailEvents are never rewritten; the operation is fully
    append-only and idempotent.
    """
    from ..core.trails import attach_trace_to_commit

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        created = attach_trace_to_commit(repo, trace_id, commit)
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to attach trace: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "commit_ref": commit,
                    "created_anchors": created,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not created:
        click.echo(f"No new anchors for trace {trace_id} at {commit[:12]}.")
        click.echo(
            "  (already attached, no matching patches, or no exact-range match found)"
        )
        return
    click.echo(f"Attached {len(created)} anchor(s) for trace {trace_id}:")
    for anchor in created:
        path = anchor.get("path") or "?"
        sha = ((anchor.get("commit_id") or {}).get("hex") or commit)[:12]
        click.echo(
            f"  {anchor.get('git_anchor_id')} → {sha} {path} "
            f"({anchor.get('evidence_tier')})"
        )


@trail_group.command(
    "rebuild",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail rebuild",
        "opentraces trail rebuild --json",
    ],
    see_also=[
        ("opentraces trail explain", "show evidence chain for a trace step."),
        ("opentraces doctor", "verify event log integrity."),
    ],
    option_groups=[
        ("Scope", ["project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def rebuild_cmd(as_json: bool, project_dir: Path | None) -> None:
    """Re-derive Trace Trails advisory projections from the event log.

    Snapshot refs under refs/opentraces/local/traces/... are advisory
    indexes; the canonical store is the append-only event log. Use
    rebuild after manual ref cleanup, branch surgery, or recovery from
    a corrupted projection cache. The operation is idempotent.
    """
    from ..core.trails import rebuild_projections

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        summary = rebuild_projections(repo)
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to rebuild trail projections: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(summary, indent=2, sort_keys=True))
        return

    click.echo(
        f"Rebuilt snapshot projections from {summary['snapshot_events_seen']} "
        f"snapshot event(s):"
    )
    click.echo(f"  created:    {summary['snapshot_refs_created']}")
    click.echo(f"  unchanged:  {summary['snapshot_refs_unchanged']}")
    if summary["snapshot_refs_missing_object"]:
        click.echo(
            f"  missing:    {summary['snapshot_refs_missing_object']} "
            f"(snapshot tree no longer in object database)"
        )
