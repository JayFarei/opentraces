"""``ot trail`` — Trace Trails inspection commands."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from ._help import OpentracesCommand, OpentracesGroup
from ..clients.text.colors import Role, detect_color, paint, render_handle


@click.group("trail", cls=OpentracesGroup)
def trail_group() -> None:
    """Inspect and sync VCS-anchored Trace Trails."""


@trail_group.command(
    "explain",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail explain --trace tr1 --step 1",
        "opentraces trail explain --trace tr1 --step 1 --json",
        "opentraces trail explain --commit abc1234 --json",
    ],
    see_also=[
        ("opentraces trail track", "walk and render trace lineage."),
        ("opentraces trail blame", "show commit attribution."),
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
                f"  {_trace_handle(patch, color=False)} "
                f"{_patch_handle(patch, color=False)} "
                f"{_human_evidence(patch.get('evidence_tier'))}"
            )
        else:
            click.echo("  relation: unknown")
        return

    if commit:
        click.echo(f"Commit {payload['commit_sha'][:12]}")
        for patch in payload.get("trace_patches") or []:
            click.echo(
                f"  {_trace_handle(patch, color=False)} "
                f"{_patch_handle(patch, color=False)} "
                f"{_human_evidence(patch.get('evidence_tier'))}"
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
    elif payload.get("patch_status") == "no_patch":
        click.echo("  patch status: no_patch")
        click.echo("  relation: no_patch")
    else:
        click.echo("  relation: unknown")
        for limitation in payload.get("limitations") or []:
            click.echo(f"  limitation: {limitation}")
    for claim in payload.get("unavailable_stronger_claims") or []:
        click.echo(f"  unavailable: {claim}")


@trail_group.command(
    "resolve",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail resolve ot://trace-patch/sha256/4f2ff6541cdee78eaea8bd2910157a7176e3c21f5d936a7f4f4561d08f024982/trail --json",
        "opentraces trail resolve ot://git-anchor/sha256/8354f2b00a5b4e80975bf0e763651c782249098f5cdb74b6e32720613a1bfc8a --json",
        "opentraces trail resolve ot://file/src/app.py/line/42/origin --json",
    ],
    see_also=[
        ("opentraces trail explain", "explain the evidence chain for a Trace Patch."),
        ("opentraces trail sync", "sync a Trace Patch with current Git history."),
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
    "sync",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail sync --patch 4f2ff6541cdee78eaea8bd2910157a7176e3c21f5d936a7f4f4561d08f024982 --json",
        "opentraces trail sync --anchor 8354f2b00a5b4e80975bf0e763651c782249098f5cdb74b6e32720613a1bfc8a --json",
    ],
    see_also=[
        ("opentraces trail track", "walk lineage with --patch/--anchor scope."),
    ],
    option_groups=[
        ("Scope", ["trace_patch_id", "git_anchor_id", "project_dir"]),
        ("History", ["history_limit"]),
        ("Output", ["as_json"]),
    ],
)
@click.option("--patch", "trace_patch_id", default=None, help="Trace Patch id to sync.")
@click.option("--anchor", "git_anchor_id", default=None, help="Git Anchor id to sync.")
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
def sync_cmd(
    trace_patch_id: str | None,
    git_anchor_id: str | None,
    history_limit: int | None,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Sync OpenTraces' trail state with the latest Git history."""
    from ..core.trails import sync_anchor, sync_patch

    if bool(trace_patch_id) == bool(git_anchor_id):
        click.echo("Provide exactly one of --patch or --anchor.", err=True)
        sys.exit(2)

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        payload = (
            sync_patch(repo, trace_patch_id, history_limit=history_limit)
            if trace_patch_id
            else sync_anchor(repo, git_anchor_id or "", history_limit=history_limit)
        )
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to sync trace trail: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    current = payload.get("current_survival") or {}
    label = (
        _anchor_handle(payload, color=False)
        if payload.get("git_anchor_id")
        else _patch_handle(payload, color=False)
    )
    click.echo(f"Trail sync {label}")
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
    "snapshots",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail snapshots --trace tr1",
        "opentraces trail snapshots --trace tr1 --json",
    ],
    see_also=[
        ("opentraces trail snapshot checkout", "materialize a rewind point."),
        ("opentraces trail resume", "fork from a snapshot-backed step."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "project_dir"]),
        ("Output", ["as_json"]),
    ],
)
@click.option("--trace", "trace_id", required=True, help="Trace id to inspect.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def snapshots_cmd(trace_id: str, as_json: bool, project_dir: Path | None) -> None:
    """List Trace Snapshot rewind candidates for a trace."""
    from ..core.trails import list_trace_snapshots

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        payload = list_trace_snapshots(repo, trace_id)
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to list trace snapshots: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    click.echo(f"Trace Snapshots for {trace_id}")
    if not payload["snapshots"]:
        click.echo("  no snapshots found")
        return
    for snapshot in payload["snapshots"]:
        tree_hex = ((snapshot.get("tree_id") or {}).get("hex") or "")[:12]
        click.echo(
            f"  {snapshot.get('step_id') or '?'} "
            f"{snapshot.get('role') or 'after'} "
            f"{snapshot.get('snapshot_id')} tree {tree_hex}"
        )
        for limitation in snapshot.get("limitations") or []:
            click.echo(f"    limitation: {limitation}")


@trail_group.group("snapshot", cls=OpentracesGroup, hidden=True)
def snapshot_group() -> None:
    """Trace Snapshot rewind commands."""


@snapshot_group.command(
    "checkout",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail snapshot checkout ot://trace-snapshot/sha256/abc --dry-run --json",
    ],
    see_also=[
        ("opentraces trail snapshots", "list rewind candidates."),
    ],
    option_groups=[
        ("Scope", ["snapshot_ref", "project_dir", "target_dir"]),
        ("Output", ["dry_run", "as_json"]),
    ],
)
@click.argument("snapshot_ref")
@click.option("--dry-run", "dry_run", is_flag=True, help="Build the rewind packet only.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Materialization directory. Defaults to an isolated opentraces worktree path.",
)
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def snapshot_checkout_cmd(
    snapshot_ref: str,
    dry_run: bool,
    as_json: bool,
    target_dir: Path | None,
    project_dir: Path | None,
) -> None:
    """Materialize or preview a Trace Snapshot rewind."""
    from ..core.trails import snapshot_checkout_packet

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        payload = snapshot_checkout_packet(
            repo,
            snapshot_ref,
            dry_run=dry_run,
            target=target_dir,
        )
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to checkout trace snapshot: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if payload.get("relation") == "unknown":
        click.echo("Trace snapshot checkout is unknown")
        for limitation in payload.get("limitations") or []:
            click.echo(f"  limitation: {limitation}")
        return
    click.echo(f"Snapshot: {payload.get('snapshot_id')}")
    click.echo(f"Tree:     {((payload.get('tree_id') or {}).get('hex') or '')}")
    click.echo(f"Path:     {(payload.get('materialization') or {}).get('path')}")


@trail_group.command(
    "timeline",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail timeline tr1 --json",
        "opentraces trail timeline tr1 --table",
    ],
    see_also=[
        ("opentraces trail track", "walk and render trace lineage."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "project_dir"]),
        ("Output", ["as_table", "as_json"]),
    ],
)
@click.argument("trace_id")
@click.option("--table", "as_table", is_flag=True, help="Emit compact human table.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def timeline_cmd(
    trace_id: str,
    as_table: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Show the observed Trace Trails timeline for a trace."""
    from ..core.trails import play_trace_timeline

    if as_table and as_json:
        click.echo("Provide only one of --table or --json.", err=True)
        sys.exit(2)

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        payload = play_trace_timeline(repo, trace_id)
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to show trace timeline: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if as_table:
        click.echo(_render_trail_play_table(payload))
        return

    click.echo(_render_trail_play_graph(repo, payload))


def _render_sync_summary(payload: dict[str, Any]) -> str:
    """Compact render of a sync_patch / sync_anchor payload."""
    current = payload.get("current_survival") or {}
    label = (
        _anchor_handle(payload, color=False)
        if payload.get("git_anchor_id")
        else _patch_handle(payload, color=False)
    )
    lines = [f"Trail track {label}"]
    lines.append(f"  survival: {current.get('survival_state') or 'unknown'}")
    path = current.get("path")
    line_range = current.get("range") or {}
    if path:
        lines.append(f"  at: {path}:{line_range.get('start_line') or '?'}")
    for limitation in (
        payload.get("trail_limitations") or current.get("limitations") or []
    ):
        lines.append(f"  limitation: {limitation}")
    return "\n".join(lines)


def _render_explain_step(payload: dict[str, Any]) -> str:
    """Compact render of an explain_trace_step payload (the --step branch)."""
    lines = [f"Trace {payload['trace_id']} {payload['step_id']}"]
    if payload.get("relation") == "anchored_in_git":
        anchor = payload.get("git_anchor") or {}
        lines.append(
            "  anchored in git: "
            f"{(anchor.get('commit_sha') or '')[:12]} "
            f"{anchor.get('path')}:{(anchor.get('range') or {}).get('start_line')}"
        )
        lines.append(
            f"  evidence: {payload.get('evidence_tier')} "
            f"({payload.get('evidence_firmness')})"
        )
    elif payload.get("patch_status") == "no_patch":
        lines.append("  patch status: no_patch")
        lines.append("  relation: no_patch")
    else:
        lines.append("  relation: unknown")
        for limitation in payload.get("limitations") or []:
            lines.append(f"  limitation: {limitation}")
    for claim in payload.get("unavailable_stronger_claims") or []:
        lines.append(f"  unavailable: {claim}")
    return "\n".join(lines)


@trail_group.command(
    "track",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail track tr1",
        "opentraces trail track tr1 --step 2",
        "opentraces trail track --patch 4f2ff654...",
        "opentraces trail track --anchor 8354f2b0...",
        "opentraces trail track tr1 --silent",
    ],
    see_also=[
        ("opentraces trail blame", "map commits/files back to producing traces."),
        ("opentraces trail graph", "render commit + trace history."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "trace_patch_id", "git_anchor_id", "step_index", "project_dir"]),
        ("Output", ["silent", "as_table", "as_json"]),
        ("History", ["history_limit"]),
    ],
)
@click.argument("trace_id", required=False)
@click.option(
    "--patch",
    "trace_patch_id",
    default=None,
    help="Track a single Trace Patch (no TRACE_ID needed).",
)
@click.option(
    "--anchor",
    "git_anchor_id",
    default=None,
    help="Track a single Git Anchor (no TRACE_ID needed).",
)
@click.option(
    "--step",
    "step_index",
    default=None,
    type=int,
    help="With TRACE_ID: focus on a single trace step's evidence.",
)
@click.option(
    "--silent",
    "silent",
    is_flag=True,
    help="Run the walk without printing the rendered output (scripts/CI).",
)
@click.option("--table", "as_table", is_flag=True, help="Compact tabular event view.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--history-limit",
    "history_limit",
    type=click.IntRange(min=2),
    default=None,
    help="Max commits walked per Git Anchor (default 500).",
)
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def track_cmd(
    trace_id: str | None,
    trace_patch_id: str | None,
    git_anchor_id: str | None,
    step_index: int | None,
    silent: bool,
    as_table: bool,
    as_json: bool,
    history_limit: int | None,
    project_dir: Path | None,
) -> None:
    """Walk and render trace lineage through Git history.

    Three scopes:
      * ``track <TRACE_ID>``        — full trace lineage (default)
      * ``track <TRACE_ID> --step N`` — single step's evidence
      * ``track --patch <ID>``      — one Trace Patch's survival
      * ``track --anchor <ID>``     — one Git Anchor's survival

    ``--silent`` runs the operation without printing — useful from
    scripts and CI. ``--json`` emits the full structured payload.
    """
    from ..core.trails import (
        explain_trace_step,
        play_trace_timeline,
        sync_anchor,
        sync_patch,
    )

    if as_table and as_json:
        click.echo("Provide only one of --table or --json.", err=True)
        sys.exit(2)

    selectors = sum(1 for s in (trace_id, trace_patch_id, git_anchor_id) if s)
    if selectors == 0:
        click.echo("Provide TRACE_ID, --patch, or --anchor.", err=True)
        sys.exit(2)
    if selectors > 1:
        click.echo("Provide only one of TRACE_ID, --patch, or --anchor.", err=True)
        sys.exit(2)
    if step_index is not None and not trace_id:
        click.echo("--step requires a TRACE_ID.", err=True)
        sys.exit(2)
    if as_table and (trace_patch_id or git_anchor_id or step_index is not None):
        click.echo("--table is only valid with the full-trace view.", err=True)
        sys.exit(2)

    repo = Path(project_dir or Path.cwd()).resolve()

    try:
        if trace_patch_id:
            payload = sync_patch(repo, trace_patch_id, history_limit=history_limit)
            renderer = _render_sync_summary
        elif git_anchor_id:
            payload = sync_anchor(repo, git_anchor_id, history_limit=history_limit)
            renderer = _render_sync_summary
        elif step_index is not None:
            payload = explain_trace_step(repo, trace_id, step_index)
            renderer = _render_explain_step
        else:
            payload = play_trace_timeline(repo, trace_id)
            renderer = None  # rendered below via graph/table helpers
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to track trail: {exc}", err=True)
        sys.exit(2)

    if silent:
        return

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if renderer is not None:
        click.echo(renderer(payload))
        return

    if as_table:
        click.echo(_render_trail_play_table(payload))
        return
    click.echo(_render_trail_play_graph(repo, payload))


@trail_group.group("teleport", cls=OpentracesGroup, hidden=True)
def teleport_group() -> None:
    """Deprecated alias for ``ot trace teleport``."""


@teleport_group.command(
    "export",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail teleport export tr1 --output ./tr1.trace-workspace",
    ],
    option_groups=[
        ("Scope", ["trace_id"]),
        ("Output", ["output", "as_json"]),
    ],
)
@click.argument("trace_id")
@click.option(
    "--output",
    "output",
    required=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Directory to write the portable trace workspace.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def teleport_export_cmd(trace_id: str, output: Path, as_json: bool) -> None:
    """Export a trace and retained Git evidence as a portable workspace."""
    from ..core.trails import export_trace_workspace

    try:
        payload = export_trace_workspace(Path.cwd(), trace_id, output)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to export trace workspace: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Trace teleported: {payload['output']}")
    click.echo(f"  events:    {payload['event_count']}")
    click.echo(f"  snapshots: {payload['snapshot_count']}")


@teleport_group.command(
    "open",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail teleport open ./tr1.trace-workspace --project ./blank --json",
    ],
    option_groups=[
        ("Scope", ["workspace", "project"]),
        ("Output", ["as_json"]),
    ],
)
@click.argument(
    "workspace",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--project",
    "project",
    required=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Blank directory where the trace workspace should be opened.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def teleport_open_cmd(workspace: Path, project: Path, as_json: bool) -> None:
    """Open a portable trace workspace into a blank project directory."""
    from ..core.trails import open_trace_workspace

    try:
        payload = open_trace_workspace(workspace, project)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to open trace workspace: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Trace workspace opened: {payload['project']}")
    click.echo(f"  events:    {payload['event_count']}")
    click.echo(f"  snapshots: {payload['snapshot_count']}")


def _load_trace_step_summaries(repo: Path, trace_id: str) -> dict[str, str]:
    from ..core.config import get_project_traces_dir

    try:
        traces_dir = get_project_traces_dir(repo)
    except Exception:
        return {}
    paths = [traces_dir / f"{trace_id}.jsonl"]
    paths.extend(sorted(path for path in traces_dir.glob("*.jsonl") if path not in paths))
    for path in paths:
        if not path.exists():
            continue
        try:
            first_line = path.read_text().splitlines()[0]
            record = json.loads(first_line)
        except Exception:
            continue
        if record.get("trace_id") != trace_id and record.get("session_id") != trace_id:
            continue
        summaries: dict[str, str] = {}
        for step in record.get("steps") or []:
            step_index = step.get("step_index")
            if step_index is None:
                continue
            content = _compact_text(step.get("content") or "")
            if content:
                summaries[f"s{step_index}"] = content
        return summaries
    return {}


def _compact_text(value: str, *, limit: int = 72) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _step_sort_key(step_id: str) -> tuple[int, str]:
    raw = step_id[1:] if step_id.startswith("s") else step_id
    return (int(raw), step_id) if raw.isdigit() else (sys.maxsize, step_id)


def _snapshot_ref_for_command(item: dict[str, Any]) -> str:
    snapshot_ref = item.get("snapshot_ref") or {}
    return snapshot_ref.get("ref") or item.get("snapshot_id") or "unknown"


def _commit_hex(item: dict[str, Any]) -> str | None:
    commit_id = item.get("commit_id") or {}
    if isinstance(commit_id, dict):
        return commit_id.get("hex")
    if isinstance(commit_id, str):
        return commit_id
    return None


def _trail_play_limitations(payload: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    limitations: list[str] = []
    for limitation in payload.get("limitations") or []:
        if limitation not in seen:
            limitations.append(str(limitation))
            seen.add(str(limitation))
    for item in payload.get("timeline") or []:
        for limitation in item.get("limitations") or []:
            if limitation not in seen:
                limitations.append(str(limitation))
                seen.add(str(limitation))
    return limitations


def _group_trail_play_steps(timeline: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in timeline:
        step_id = item.get("step_id") or "unscoped"
        grouped.setdefault(step_id, []).append(item)
    return grouped


def _meaningful_trail_play_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    meaningful = {
        "trace_snapshot_created",
        "trace_patch_created",
        "git_anchor_created",
        "patch_trail_observation_created",
        "trace_step_capture_incomplete",
    }
    return [item for item in items if item.get("event_type") in meaningful]


def _snapshot_handle(row: dict[str, Any], *, color: bool = False) -> str:
    token = f"snap:{_short_digest(row.get('snapshot_id'))}"
    if not color:
        return token
    prefix = paint(Role.ID_PREFIX, "snap:", use_color=True)
    body = paint(Role.TRACE_ID, token[5:], use_color=True)
    return f"{prefix}{body}"


def _step_label(step_id: str) -> str:
    return f"step:{step_id}"


def _tree_label(item: dict[str, Any]) -> str:
    tree_hex = ((item.get("tree_id") or {}).get("hex") or "")[:8]
    return f"tree:{tree_hex or 'unknown'}"


def _trail_play_anchors_by_patch(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    anchors: dict[str, dict[str, Any]] = {}
    for item in items:
        if item.get("event_type") != "git_anchor_created":
            continue
        trace_patch_id = item.get("trace_patch_id")
        if trace_patch_id:
            anchors[str(trace_patch_id)] = item
    return anchors


def _trail_play_patch_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("trace_patch_id"))
        for item in items
        if item.get("event_type") == "trace_patch_created"
        and item.get("trace_patch_id")
    }


def _trail_play_children(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patch_ids = _trail_play_patch_ids(items)
    children: list[dict[str, Any]] = []
    for item in items:
        if (
            item.get("event_type") == "git_anchor_created"
            and item.get("trace_patch_id") in patch_ids
        ):
            continue
        children.append(item)
    return children


def _trail_play_landing_state(anchor: dict[str, Any] | None) -> str:
    if not anchor:
        return "unanchored"
    return _landing_state(
        {
            "git_anchor_id": anchor.get("git_anchor_id"),
            "commit_sha": _commit_hex(anchor),
            "evidence_tier": anchor.get("evidence_tier"),
        }
    )


def _trail_play_evidence(anchor: dict[str, Any] | None) -> str:
    if not anchor:
        return "unknown evidence · unknown"
    firmness = anchor.get("evidence_firmness") or "unknown"
    return f"{_human_evidence(anchor.get('evidence_tier'))} \u00B7 {firmness}"


def _trail_play_status(timeline: list[dict[str, Any]]) -> str:
    if not timeline:
        return "missing timeline"
    snapshots = [
        item for item in timeline if item.get("event_type") == "trace_snapshot_created"
    ]
    noun = "snapshot" if len(snapshots) == 1 else "snapshots"
    return f"replayable \u00B7 resumable from {len(snapshots)} {noun}"


def _trail_play_count_summary(timeline: list[dict[str, Any]]) -> str:
    snapshots = [
        item for item in timeline if item.get("event_type") == "trace_snapshot_created"
    ]
    patch_ids = _trail_play_patch_ids(timeline)
    anchors_by_patch = _trail_play_anchors_by_patch(timeline)
    anchored_count = len(patch_ids & set(anchors_by_patch))
    snapshot_noun = "snapshot" if len(snapshots) == 1 else "snapshots"
    parts = [f"{len(snapshots)} {snapshot_noun}"]
    if patch_ids:
        parts.append(
            _trace_patch_count_label(
                len(patch_ids),
                anchored=anchored_count == len(patch_ids),
            )
        )
    else:
        parts.append("0 Trace Patches")
    return " \u00B7 ".join(parts)


def _append_trail_play_snapshot_lines(
    lines: list[str],
    item: dict[str, Any],
    *,
    prefix: str,
    branch: str,
    stem: str,
    trace_id: str,
    step_id: str,
) -> None:
    role = item.get("snapshot_role") or "after"
    detail_prefix = f"{prefix}{stem}  "
    lines.append(
        f"{prefix}{branch}\u25C7 {_snapshot_handle(item)}  {_tree_label(item)}  {role}"
    )
    lines.append(
        f"{detail_prefix}checkout: opentraces trail snapshot checkout "
        f"{_snapshot_ref_for_command(item)}"
    )
    if role == "after":
        lines.append(
            f"{detail_prefix}resume: opentraces trail resume {trace_id} --at-step {step_id}"
        )


def _append_trail_play_patch_lines(
    lines: list[str],
    item: dict[str, Any],
    *,
    anchor: dict[str, Any] | None,
    prefix: str,
    branch: str,
    stem: str,
) -> None:
    detail_prefix = f"{prefix}{stem}  "
    path = item.get("file_path") or "unknown"
    lines.append(
        f"{prefix}{branch}\u25C7 {_patch_handle(item, color=False)}  "
        f"{path}  {_trail_play_landing_state(anchor)}"
    )
    lines.append(f"{detail_prefix}\u2502 evidence: {_trail_play_evidence(anchor)}")
    if anchor:
        commit = _commit_handle(_commit_hex(anchor), color=False)
        anchor_handle = _anchor_handle(anchor, color=False)
        lines.append(f"{detail_prefix}\u2570\u25CF {commit}  {anchor_handle}")
    else:
        lines.append(f"{detail_prefix}\u2570? no reliable landing")


def _render_trail_play_graph(repo: Path, payload: dict[str, Any]) -> str:
    trace_id = payload.get("trace_id") or "unknown"
    timeline = payload.get("timeline") or []
    workspace_source = payload.get("workspace_source") or {}
    step_summaries = _load_trace_step_summaries(repo, str(trace_id))
    workspace_id = workspace_source.get("workspace_id")
    workspace_label = workspace_id or workspace_source.get("type") or "local_project"
    source_repo = (
        "unavailable"
        if workspace_source.get("type") == "trace_workspace"
        else str(repo)
    )
    status = _trail_play_status(timeline)
    trace = _trace_handle(str(trace_id), color=False)

    lines = [
        f"Trace timeline for {trace}",
        f"Workspace: {workspace_label}",
        f"Source repo: {source_repo}",
        _trail_play_count_summary(timeline),
        "",
        f"\u256D\u25C6 {trace}",
        f"\u2502 status: {status}",
        f"\u2502 workspace: {workspace_label} \u00B7 source repo {source_repo}",
        "\u2502",
    ]

    grouped = _group_trail_play_steps(timeline)
    step_ids = sorted(grouped, key=_step_sort_key)
    for step_position, step_id in enumerate(step_ids):
        summary = step_summaries.get(step_id)
        meaningful = _meaningful_trail_play_items(grouped[step_id])
        anchors_by_patch = _trail_play_anchors_by_patch(meaningful)
        children = _trail_play_children(meaningful)
        step_is_last = step_position == len(step_ids) - 1
        step_branch = "\u2570" if step_is_last else "\u251C"
        step_prefix = "   " if step_is_last else "\u2502  "
        step_title = _step_label(step_id)
        if summary:
            lines.append(f"{step_branch}\u25C6 {step_title}  {summary}")
        else:
            lines.append(f"{step_branch}\u25C6 {step_title}")

        for item_position, item in enumerate(children):
            child_is_last = item_position == len(children) - 1
            branch = "\u2570" if child_is_last else "\u251C"
            stem = " " if child_is_last else "\u2502"
            event_type = item.get("event_type")
            if event_type == "trace_snapshot_created":
                _append_trail_play_snapshot_lines(
                    lines,
                    item,
                    prefix=step_prefix,
                    branch=branch,
                    stem=stem,
                    trace_id=str(trace_id),
                    step_id=step_id,
                )
            elif event_type == "trace_patch_created":
                _append_trail_play_patch_lines(
                    lines,
                    item,
                    anchor=anchors_by_patch.get(str(item.get("trace_patch_id"))),
                    prefix=step_prefix,
                    branch=branch,
                    stem=stem,
                )
            elif event_type == "git_anchor_created":
                lines.append(
                    f"{step_prefix}{branch}\u25CF "
                    f"{_commit_handle(_commit_hex(item), color=False)}  "
                    f"{_anchor_handle(item, color=False)}  "
                    f"{_trail_play_evidence(item)}"
                )
            elif event_type == "trace_step_capture_incomplete":
                lines.append(
                    f"{step_prefix}{branch}? limitation trace_step_capture_incomplete"
                )
            elif event_type == "patch_trail_observation_created":
                state = item.get("survival_state") or "observed"
                lines.append(f"{step_prefix}{branch}\u25C7 patch trail {state}")
        if not children:
            lines.append(f"{step_prefix}\u2570? no replayable events")
        if not step_is_last:
            lines.append("\u2502")

    limitations = _trail_play_limitations(payload)
    if limitations:
        lines.append("")
        lines.append(f"Limitations: {', '.join(limitations)}")
    return "\n".join(lines)


def _trail_play_limitations_cell(item: dict[str, Any]) -> str:
    limitations = item.get("limitations") or []
    return ",".join(str(limitation) for limitation in limitations) or "-"


def _trail_play_table_row(
    seq: str,
    step: str,
    kind: str,
    obj: str,
    file_commit: str,
    evidence: str,
    limitations: str,
) -> str:
    return (
        f"{seq:<4} {step:<5} {kind:<13} {obj:<12} "
        f"{file_commit:<12} {evidence:<20} {limitations}"
    )


def _render_trail_play_table(payload: dict[str, Any]) -> str:
    rows = [
        "SEQ  STEP  KIND          OBJECT        FILE/COMMIT  "
        "EVIDENCE             LIMITATIONS"
    ]
    for item in _meaningful_trail_play_items(payload.get("timeline") or []):
        seq = str(item.get("event_sequence") or "")
        step = item.get("step_id") or "-"
        event_type = item.get("event_type")
        limitations = _trail_play_limitations_cell(item)
        if event_type == "trace_snapshot_created":
            tree_hex = ((item.get("tree_id") or {}).get("hex") or "")[:8] or "-"
            role = item.get("snapshot_role") or "after"
            capture_status = item.get("capture_status") or "unknown"
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "snapshot",
                    _snapshot_handle(item),
                    f"tree:{tree_hex}",
                    f"{role} {capture_status}",
                    limitations,
                )
            )
        elif event_type == "trace_patch_created":
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "trace_patch",
                    _patch_handle(item, color=False),
                    item.get("file_path") or "-",
                    "-",
                    limitations,
                )
            )
        elif event_type == "git_anchor_created":
            commit = (_commit_hex(item) or "")[:8] or "-"
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "git_anchor",
                    _anchor_handle(item, color=False),
                    f"c:{commit}",
                    _trail_play_evidence(item),
                    limitations,
                )
            )
        elif event_type == "patch_trail_observation_created":
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "patch_trail",
                    _patch_handle(item, color=False),
                    item.get("path") or "-",
                    item.get("survival_state") or "observed",
                    limitations,
                )
            )
        elif event_type == "trace_step_capture_incomplete":
            rows.append(
                _trail_play_table_row(
                    seq,
                    step,
                    "limitation",
                    "-",
                    "-",
                    "trace_step_capture_incomplete",
                    limitations,
                )
            )
    return "\n".join(rows)


EVIDENCE_LABELS = {
    "exact_blob_hash": "exact blob match",
    "exact_range_hash": "exact range match",
    "git_clean_range_hash": "normalized range match",
    "patch_id": "patch-id match",
    "structural_symbol": "structural symbol match",
    "formatter_divergent": "formatter-divergent match",
    "overlapping_hunk": "overlapping hunk",
    "time_file_overlap": "weak time/file overlap",
    "orphan": "no reliable landing",
    "unknown": "unknown evidence",
}

SURVIVAL_GLYPHS = {
    "alive_on_path": "\u2713",
    "alive_moved": "\u21B7",
    "alive_transformed": "~",
    "partially_preserved": "\u25D0",
    "repaired": "!",
    "reverted": "\u00D7",
    "lost": "\u00D7",
    "unknown": "?",
}


def _short_digest(value: str | None, length: int = 8) -> str:
    if not value:
        return "unknown"
    tail = value.rsplit(":", 1)[-1]
    return tail[:length] or value[:length]


def _patch_handle(row: dict[str, Any], *, color: bool) -> str:
    token = f"tp:{_short_digest(row.get('trace_patch_id'))}"
    if not color:
        return token
    prefix = paint(Role.ID_PREFIX, "tp:", use_color=True)
    body = paint(Role.TRACE_ID, token[3:], use_color=True)
    return f"{prefix}{body}"


def _anchor_handle(row: dict[str, Any], *, color: bool) -> str:
    token = f"ga:{_short_digest(row.get('git_anchor_id'))}"
    if not color:
        return token
    prefix = paint(Role.ID_PREFIX, "ga:", use_color=True)
    body = paint(Role.TRACE_ID, token[3:], use_color=True)
    return f"{prefix}{body}"


def _trace_handle(row_or_trace_id: dict[str, Any] | str | None, *, color: bool) -> str:
    trace_id = (
        row_or_trace_id.get("trace_id")
        if isinstance(row_or_trace_id, dict)
        else row_or_trace_id
    )
    return render_handle("t", trace_id or "unknown", use_color=color)


def _commit_handle(row_or_sha: dict[str, Any] | str | None, *, color: bool) -> str:
    sha = (
        row_or_sha.get("commit_sha")
        if isinstance(row_or_sha, dict)
        else row_or_sha
    )
    return render_handle("c", sha or "unknown", use_color=color)


def _human_evidence(tier: str | None) -> str:
    return EVIDENCE_LABELS.get(tier or "unknown", (tier or "unknown").replace("_", " "))


def _landing_state(row: dict[str, Any]) -> str:
    if not row.get("git_anchor_id") or not row.get("commit_sha"):
        return "orphan"
    tier = row.get("evidence_tier")
    if tier in {"exact_blob_hash", "exact_range_hash", "patch_id"}:
        return "landed_exact"
    if tier == "git_clean_range_hash":
        return "landed_normalized"
    return "landed_divergent"


def _survival_state(row: dict[str, Any]) -> str:
    current = row.get("current_survival") or {}
    state = row.get("survival_state") or current.get("survival_state")
    if state:
        return state
    if row.get("commit_sha"):
        return "alive_on_path"
    return "unknown"


def _git_subject(repo: Path, sha: str | None) -> str:
    if not sha:
        return ""
    proc = subprocess.run(
        ["git", "show", "-s", "--format=%s", sha],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _render_search_table(results: list[dict[str, Any]], *, color: bool) -> str:
    lines = [
        "TRACE PATCH  STEP   FILE                            LANDING     EVIDENCE             STATE"
    ]
    for row in results:
        trace_patch = _patch_handle(row, color=color)
        step = str(row.get("step_index") if row.get("step_index") is not None else "?")
        path = row.get("file_path") or row.get("path") or "?"
        commit = _commit_handle(row, color=color) if row.get("commit_sha") else "-"
        evidence = _human_evidence(row.get("evidence_tier"))
        state = _survival_state(row)
        lines.append(
            f"{trace_patch:<12} {step:<6} {path[:31]:<31} {commit:<11} "
            f"{evidence[:19]:<19} {state}"
        )
    return "\n".join(lines)


def _trace_patch_count_label(count: int, *, anchored: bool | None = None) -> str:
    noun = f"{count} Trace Patch{'' if count == 1 else 'es'}"
    if anchored is True:
        return f"{noun} anchored in Git"
    if anchored is False:
        return f"{noun} not anchored in Git"
    return noun


def _trace_patch_branch_for_trace(
    row: dict[str, Any],
    *,
    color: bool,
    last: bool,
) -> list[str]:
    trace_patch = _patch_handle(row, color=color)
    commit = _commit_handle(row, color=color)
    survival = _survival_state(row)
    glyph = SURVIVAL_GLYPHS.get(survival, "?")
    evidence = _human_evidence(row.get("evidence_tier"))
    firmness = row.get("firmness") or row.get("evidence_firmness") or "unknown"
    path = row.get("file_path") or row.get("path") or "?"
    step = row.get("step_index") if row.get("step_index") is not None else "?"
    line_count = row.get("line_count") or 0
    count_text = f"+{line_count}" if line_count else "change"
    branch = "\u2570" if last else "\u251C"
    stem = " " if last else "\u2502"
    lines = [
        f"{branch}\u25C7 {trace_patch}  {path}  {_landing_state(row)}",
        f"{stem}  \u2502 step: {step} \u00B7 {count_text}",
        f"{stem}  \u2502 evidence: {evidence} \u00B7 {firmness}",
    ]
    if row.get("commit_sha"):
        lines.append(f"{stem}  \u2570\u25CF {commit}  {glyph} {survival}")
    else:
        lines.append(f"{stem}  \u2570? no reliable landing")
    return lines


def _trace_patch_branch_for_commit(
    row: dict[str, Any],
    *,
    color: bool,
    last: bool,
) -> list[str]:
    trace_patch = _patch_handle(row, color=color)
    trace = _trace_handle(row, color=color)
    evidence = _human_evidence(row.get("evidence_tier"))
    firmness = row.get("firmness") or row.get("evidence_firmness") or "unknown"
    path = row.get("file_path") or row.get("path") or "?"
    step = row.get("step_index") if row.get("step_index") is not None else "?"
    survival = _survival_state(row)
    branch = "\u2570" if last else "\u251C"
    stem = " " if last else "\u2502"
    return [
        f"{branch}\u25C7 {trace_patch}  {path}  {_landing_state(row)}  {survival}",
        f"{stem}  \u2502 evidence: {evidence} \u00B7 {firmness}",
        f"{stem}  \u2570\u25C6 {trace}  step {step}",
    ]


def _trace_patch_branch_for_path(
    row: dict[str, Any],
    *,
    color: bool,
    last: bool,
) -> list[str]:
    trace_patch = _patch_handle(row, color=color)
    trace = _trace_handle(row, color=color)
    commit = _commit_handle(row, color=color)
    evidence = _human_evidence(row.get("evidence_tier"))
    firmness = row.get("firmness") or row.get("evidence_firmness") or "unknown"
    path = row.get("file_path") or row.get("path") or "?"
    step = row.get("step_index") if row.get("step_index") is not None else "?"
    survival = _survival_state(row)
    branch = "\u2570" if last else "\u251C"
    stem = " " if last else "\u2502"
    return [
        f"{branch}\u25C7 {trace_patch}  {path}  {_landing_state(row)}  {survival}",
        f"{stem}  \u2502 step: {step}",
        f"{stem}  \u2502 evidence: {evidence} \u00B7 {firmness}",
        f"{stem}  \u251C\u25C6 {trace}",
        f"{stem}  \u2570\u25CF {commit}",
    ]


def _render_trace_search(
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    trace_id = query.get("trace_id") or ""
    if table:
        lines = [
            f"Trace trail for {_trace_handle(trace_id, color=color)}",
            "",
            _trace_patch_count_label(len(results)),
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)

    trace = _trace_handle(trace_id or results[0].get("trace_id"), color=color)
    anchored_count = sum(1 for row in results if row.get("commit_sha"))
    anchored_label = (
        _trace_patch_count_label(len(results), anchored=True)
        if anchored_count == len(results)
        else _trace_patch_count_label(len(results))
    )
    lines = [
        f"Trace trail for {trace}",
        "",
        anchored_label,
        "",
        f"\u256D\u25C6 {trace}",
        "\u2502",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_trace(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("\u2502")
    first = results[0]
    first_step = (
        first.get("step_index") if first.get("step_index") is not None else "?"
    )
    lines.extend(
        [
            "",
            "Next:",
            f"  otd trail explain --trace {first.get('trace_id')} --step {first_step}",
            f"  otd trail sync --patch {first.get('trace_patch_id')}",
        ]
    )
    return "\n".join(lines)


def _render_commit_search(
    repo: Path,
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    commit_sha = query.get("commit_sha") or ""
    commit = _commit_handle(commit_sha, color=color)
    subject = _git_subject(repo, commit_sha)
    trace_count = len({row.get("trace_id") for row in results if row.get("trace_id")})
    file_count = len({row.get("file_path") for row in results if row.get("file_path")})
    if table:
        lines = [
            f"Trace trail evidence in {query.get('commit')}",
            f"Resolved {query.get('commit')} -> {commit}",
            "",
            (
                f"{len(results)} anchored Trace Patch"
                f"{'' if len(results) == 1 else 'es'} \u00B7 "
                f"{trace_count} trace{'' if trace_count == 1 else 's'} \u00B7 "
                f"{file_count} file{'' if file_count == 1 else 's'}"
            ),
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)

    row = results[0]
    lines = [
        f"Trace trail evidence in {query.get('commit')}",
        f"Resolved {query.get('commit')} -> {commit}",
        "",
        (
            f"{len(results)} anchored Trace Patch"
            f"{'' if len(results) == 1 else 'es'} \u00B7 "
            f"{trace_count} trace{'' if trace_count == 1 else 's'} \u00B7 "
            f"{file_count} file{'' if file_count == 1 else 's'}"
        ),
        "",
        f"\u25CF {commit}  {subject}",
        "\u2502",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_commit(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("\u2502")
    return "\n".join(lines)


def _render_path_search(
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    path = query.get("path") or "?"
    if table:
        lines = [
            f"Trace trail for path {path}",
            "",
            (
                f"{len(results)} committed Trace Patch"
                f"{'' if len(results) == 1 else 'es'} touching this file"
            ),
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)

    row = results[0]
    lines = [
        f"Trace trail for path {path}",
        "",
        (
            f"{len(results)} committed Trace Patch"
            f"{'' if len(results) == 1 else 'es'} touching this file"
        ),
        "",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_path(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("\u2502")
    first = results[0]
    first_step = (
        first.get("step_index") if first.get("step_index") is not None else "?"
    )
    lines.extend(
        [
            "",
            "Next:",
            f"  otd trail explain --trace {first.get('trace_id')} --step {first_step}",
        ]
    )
    return "\n".join(lines)


def _render_survival_search(
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    *,
    color: bool,
    table: bool,
) -> str:
    survival = query.get("survival") or "unknown"
    if table:
        lines = [
            f"Trace trails with survival {survival}",
            "",
            f"{len(results)} Trace Patch{'' if len(results) == 1 else 'es'}",
            "",
            _render_search_table(results, color=color),
        ]
        return "\n".join(lines)
    lines = [
        f"Trace trails with survival {survival}",
        "",
        f"{len(results)} Trace Patch{'' if len(results) == 1 else 'es'}",
        "",
    ]
    for index, row in enumerate(results):
        lines.extend(
            _trace_patch_branch_for_path(
                row,
                color=color,
                last=index == len(results) - 1,
            )
        )
        if index != len(results) - 1:
            lines.append("\u2502")
    return "\n".join(lines)


def _render_empty_search(
    query: dict[str, str | None],
    limitations: list[str],
    *,
    color: bool,
) -> str:
    qtype = query.get("type")
    if qtype == "patches_per_trace":
        lines = [
            f"Trace trail for {_trace_handle(query.get('trace_id'), color=color)}",
            "",
            "No committed Trace Patches found.",
            "",
            "Possible reasons:",
            "  - this was a research-only or review session",
            "  - the work has not landed in local Git history",
            "  - the patch was transformed beyond current matching thresholds",
            "  - TrailEvents are unavailable for this project",
        ]
    elif qtype == "anchors_per_commit":
        lines = [
            f"Trace trail evidence in {query.get('commit')}",
            "",
            "No anchored Trace Patches found for this commit.",
        ]
    elif qtype == "patches_touching_file":
        lines = [
            f"Trace trail for path {query.get('path')}",
            "",
            "No committed Trace Patches touching this file were found.",
        ]
    else:
        lines = [
            f"Trace trails with survival {query.get('survival')}",
            "",
            "No Trace Patches found in this survival state.",
        ]
    for limitation in limitations:
        lines.append(f"  limitation: {limitation}")
    return "\n".join(lines)


def _render_search_results(
    repo: Path,
    query: dict[str, str | None],
    results: list[dict[str, Any]],
    limitations: list[str],
    *,
    color: bool,
    graph_mode: bool,
    table_mode: bool,
) -> str:
    if not results:
        return _render_empty_search(query, limitations, color=color)
    table = table_mode
    qtype = query.get("type")
    if qtype == "patches_per_trace":
        return _render_trace_search(query, results, color=color, table=table)
    if qtype == "anchors_per_commit":
        return _render_commit_search(repo, query, results, color=color, table=table)
    if qtype == "patches_touching_file":
        return _render_path_search(query, results, color=color, table=table)
    return _render_survival_search(query, results, color=color, table=table)


@trail_group.command(
    "search",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail search --trace tr1 --json",
        "opentraces trail search --commit HEAD --json",
        "opentraces trail search --path src/app.py --json",
        "opentraces trail search --survival reverted --json",
    ],
    see_also=[
        ("opentraces trail track", "trace-scoped walk + render."),
        ("opentraces trail blame", "show reviewer-facing attribution."),
        ("opentraces trail graph", "render commit + trace navigation."),
    ],
    option_groups=[
        ("Scope", ["trace_id", "commit", "path", "survival", "project_dir"]),
        ("Output", ["as_json", "graph_mode", "table_mode", "no_color"]),
    ],
)
@click.option("--trace", "trace_id", default=None, help="Find patches for a trace.")
@click.option("--commit", "commit", default=None, help="Find anchors for a commit.")
@click.option("--path", "path", default=None, help="Find committed patches touching a file.")
@click.option(
    "--survival",
    "survival",
    default=None,
    help="Find Patch Trails by current survival state, e.g. reverted.",
)
@click.option("--graph", "graph_mode", is_flag=True, help="Show rail/lineage view (default).")
@click.option("--table", "table_mode", is_flag=True, help="Force compact table view.")
@click.option("--no-color", "no_color", is_flag=True, help="Disable ANSI color.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--project",
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Project directory (default: CWD).",
)
def search_cmd(
    trace_id: str | None,
    commit: str | None,
    path: str | None,
    survival: str | None,
    graph_mode: bool,
    table_mode: bool,
    no_color: bool,
    as_json: bool,
    project_dir: Path | None,
) -> None:
    """Search the Trail Query projection."""
    from ..core.trails.query import resolve_commit_ref, build_trail_query_projection
    from ..core.trails.contract import (
        PROJECTION_NAME,
        PROJECTION_VERSION,
        SEARCH_SCHEMA_VERSION,
        project_identity,
        projection_watermark,
        typed_limitations,
    )
    from ..core.trails.ids import ID_ALGORITHM

    if graph_mode and table_mode:
        click.echo("Use only one of --graph or --table.", err=True)
        sys.exit(2)

    selectors = [value for value in (trace_id, commit, path, survival) if value]
    if len(selectors) != 1:
        click.echo("Provide exactly one of --trace, --commit, --path, or --survival.", err=True)
        sys.exit(2)

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        projection = build_trail_query_projection(repo)
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to search trace trails: {exc}", err=True)
        sys.exit(2)

    query: dict[str, str | None] = {}
    results: list[dict]
    limitations = list(projection.limitations)
    if trace_id:
        resolved_trace = projection.resolve_trace_prefix(trace_id) or trace_id
        query = {
            "type": "patches_per_trace",
            "semantic_type": "trace_to_patches",
            "trace_id": resolved_trace,
        }
        results = [
            projection.with_current_survival(row)
            for row in projection.patches_for_trace(resolved_trace)
        ]
    elif commit:
        commit_sha = resolve_commit_ref(repo, commit)
        if commit_sha is None:
            click.echo(f"Unknown commit: {commit}", err=True)
            sys.exit(2)
        query = {
            "type": "anchors_per_commit",
            "semantic_type": "commit_to_anchors",
            "commit": commit,
            "commit_sha": commit_sha,
        }
        results = projection.anchors_for_commit_with_survival(commit_sha)
    elif path:
        query = {
            "type": "patches_touching_file",
            "semantic_type": "path_to_patches",
            "path": path,
        }
        results = [
            projection.with_current_survival(row)
            for row in projection.patches_touching_file(path)
        ]
    else:
        query = {
            "type": "patch_trails_by_survival",
            "semantic_type": "survival_to_patch_trails",
            "survival": survival,
        }
        if survival != "reverted":
            limitations.append("unsupported_survival_filter")
            results = []
        else:
            results = projection.reverted_patch_trails()

    head_sha = resolve_commit_ref(repo, "HEAD")
    projection_summary = projection.to_summary()
    payload = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "projection_version": PROJECTION_VERSION,
        "query": query,
        "results": results,
        "result_count": len(results),
        "projection": {
            **projection_summary,
            "name": PROJECTION_NAME,
            "version": PROJECTION_VERSION,
            "object_ref_contract": "opentraces.object_ref.v1",
            "object_id_algorithm": ID_ALGORITHM,
            "high_watermark": projection_watermark(
                events_seen=projection.events_seen,
                last_event_id=projection.last_event_id,
                digest=projection.projection_digest,
            ),
            "git": {
                "repo_ref": "ot://repo/local",
                "head_sha": head_sha,
                "as_of_ref": "HEAD",
            },
            "stale": False,
            "limitation_details": typed_limitations(projection.limitations),
        },
        "project": project_identity(repo),
        "event_log_ref": projection.event_log_ref,
        "limitations": limitations,
        "limitation_details": typed_limitations(limitations),
    }

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    click.echo(
        _render_search_results(
            repo,
            query,
            results,
            limitations,
            color=detect_color(no_color, sys.stdout),
            graph_mode=graph_mode,
            table_mode=table_mode,
        )
    )


@trail_group.command(
    "diff",
    cls=OpentracesCommand,
    hidden=True,
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
    hidden=True,
    examples=[
        "opentraces trail attach --trace tr_abc --commit HEAD",
        "opentraces trail attach --trace tr_abc --commit abc1234 --json",
    ],
    see_also=[
        ("opentraces trail explain", "show evidence chain for a trace step."),
        ("opentraces trail blame", "show commit attribution."),
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
    "mature",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail mature",
        "opentraces trail mature --commits 100 --json",
        "opentraces trail mature --commit HEAD --json",
    ],
    see_also=[
        ("opentraces trail attach", "manually attach one trace to one commit."),
        ("opentraces trail explain", "show evidence chain for a trace step."),
    ],
    option_groups=[
        ("Scope", ["project_dir", "commits", "commit_refs"]),
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
@click.option(
    "--commits",
    type=int,
    default=50,
    show_default=True,
    help="Recent HEAD commits to search when --commit is not provided.",
)
@click.option(
    "--commit",
    "commit_refs",
    multiple=True,
    help="Specific commit/ref to search. May be passed multiple times.",
)
def mature_cmd(
    as_json: bool,
    project_dir: Path | None,
    commits: int,
    commit_refs: tuple[str, ...],
) -> None:
    """Continuously mature Trace Patches into Git Anchors.

    This command searches existing commits for Trace Patches that were created
    after those commits landed. It appends search-completed events, including
    unknowns, and is idempotent under the current attribution version.
    """
    from ..core.trails import mature_trails

    repo = Path(project_dir or Path.cwd()).resolve()
    try:
        summary = mature_trails(
            repo,
            commit_refs=commit_refs or None,
            max_commits=commits,
        ).to_dict()
    except ValueError as exc:
        click.echo(f"Trace Trail event log is invalid: {exc}", err=True)
        sys.exit(3)
    except Exception as exc:
        click.echo(f"Unable to mature trace trails: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(summary, indent=2, sort_keys=True))
        if summary["errors"]:
            sys.exit(2)
        return

    click.echo(
        f"Matured Trace Trails across {summary['commits_considered']} commit(s):"
    )
    click.echo(f"  searches: {summary['searches_completed']}")
    click.echo(f"  anchors:  {summary['anchors_created']}")
    if summary["errors"]:
        click.echo(f"  errors:   {len(summary['errors'])}")
        for error in summary["errors"]:
            click.echo(f"    {error}")
        sys.exit(2)


@trail_group.command(
    "rebuild",
    cls=OpentracesCommand,
    hidden=True,
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
