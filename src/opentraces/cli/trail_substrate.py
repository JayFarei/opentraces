"""Hidden substrate commands for ``ot trail`` — registered onto trail_group."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json, project_dir_option
from ..clients.text.colors import detect_color
from .trail import trail_group
from .trail_helpers import (
    _anchor_handle,
    _human_evidence,
    _patch_handle,
    _render_search_results,
    _render_trail_play_graph,
    _render_trail_play_table,
    _trace_handle,
)


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
@project_dir_option
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
        click.echo(_dump_json(payload))
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
@project_dir_option
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
        click.echo(_dump_json(payload))
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
@project_dir_option
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
        click.echo(_dump_json(payload))
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
@project_dir_option
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
        click.echo(_dump_json(payload))
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
@project_dir_option
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
        click.echo(_dump_json(payload))
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
@project_dir_option
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
        click.echo(_dump_json(payload))
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
        click.echo(_dump_json(payload))
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
        click.echo(_dump_json(payload))
        return
    click.echo(f"Trace workspace opened: {payload['project']}")
    click.echo(f"  events:    {payload['event_count']}")
    click.echo(f"  snapshots: {payload['snapshot_count']}")


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
        ("Remote bucket", ["remote_bucket", "force_remote_bucket", "bucket_repo_id"]),
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
    "--remote-bucket",
    is_flag=True,
    help="Pull the configured private bucket remote and replay TrailEvents before searching.",
)
@click.option(
    "--force-remote-bucket",
    is_flag=True,
    help="Allow --remote-bucket to overwrite local-ahead/diverged bucket or differing TrailEvents.",
)
@click.option(
    "--bucket-repo-id",
    default=None,
    help="Bucket TrailEvents repo id when the remote bucket has multiple repository exports.",
)
@project_dir_option
def search_cmd(
    trace_id: str | None,
    commit: str | None,
    path: str | None,
    survival: str | None,
    graph_mode: bool,
    table_mode: bool,
    no_color: bool,
    as_json: bool,
    remote_bucket: bool,
    force_remote_bucket: bool,
    bucket_repo_id: str | None,
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
    remote_bucket_payload = None
    if remote_bucket:
        try:
            from ._remote_bucket import pull_remote_bucket_for_trail

            remote_bucket_payload = pull_remote_bucket_for_trail(
                repo,
                force=force_remote_bucket,
                repo_id=bucket_repo_id,
            )
        except Exception as exc:
            click.echo(f"Unable to read remote bucket: {exc}", err=True)
            sys.exit(3)
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
    if remote_bucket_payload is not None:
        payload["remote_bucket"] = remote_bucket_payload

    if as_json:
        click.echo(_dump_json(payload))
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
@project_dir_option
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
        click.echo(_dump_json(payload))
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
@project_dir_option
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
        click.echo(_dump_json({
            "trace_id": trace_id,
            "commit_ref": commit,
            "created_anchors": created,
        }))
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
@project_dir_option
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
        click.echo(_dump_json(summary))
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
    "verify",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trail verify --mode quick --json",
        "opentraces trail verify --mode sample --sample-size 100 --json",
        "opentraces trail verify --mode full --progress plain --json",
    ],
    see_also=[
        ("opentraces doctor", "show install and trace-substrate health."),
        ("opentraces trail rebuild", "re-derive advisory projections."),
    ],
    option_groups=[
        ("Scope", ["project_dir"]),
        ("Verification", ["mode", "sample_size"]),
        ("Output", ["as_json", "progress_mode"]),
    ],
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--mode",
    "mode",
    type=click.Choice(["quick", "sample", "full"]),
    default="quick",
    show_default=True,
    help=(
        "quick summarizes the ref without reading all event bodies; sample "
        "checks a bounded first/last event sample; full verifies every event."
    ),
)
@click.option(
    "--sample-size",
    "sample_size",
    type=int,
    default=50,
    show_default=True,
    help="Events to sample from each end of the log in --mode sample.",
)
@click.option(
    "--progress",
    "progress_mode",
    type=click.Choice(["auto", "plain", "json", "never"]),
    default="auto",
    show_default=True,
    help=(
        "Progress reporting on stderr. Use 'json' for agent-readable JSONL "
        "heartbeats or 'plain' for human-readable heartbeats."
    ),
)
@project_dir_option
def verify_cmd(
    as_json: bool,
    mode: str,
    sample_size: int,
    progress_mode: str,
    project_dir: Path | None,
) -> None:
    """Verify or summarize the canonical Trace Trails event log."""
    from ._progress import build_cli_progress
    from ..core.trails import event_log_verification_status

    repo = Path(project_dir or Path.cwd()).resolve()
    reporter = build_cli_progress("trail verify", progress_mode)
    try:
        reporter.stage(f"{mode}_event_log", batches_seen=0)
        status = event_log_verification_status(
            repo,
            mode=mode,
            sample_size=sample_size,
        )
    except Exception as exc:
        click.echo(f"Unable to verify trail event log: {exc}", err=True)
        sys.exit(2)
    finally:
        reporter.done()

    status.setdefault("telemetry", {})
    status["telemetry"]["stages"] = reporter.telemetry()

    if as_json:
        click.echo(_dump_json(status))
    else:
        _render_verify_status(status)

    if status.get("state") in ("invalid", "error"):
        sys.exit(3)


def _render_verify_status(status: dict) -> None:
    state = status.get("state") or "missing"
    mode = status.get("mode") or "quick"
    click.echo(f"Trace Trail event log: {state} ({mode})")
    click.echo(f"  ref:     {status.get('ref') or '?'}")
    head = status.get("head")
    if head:
        click.echo(f"  head:    {str(head)[:12]}")
    click.echo(f"  source:  {status.get('verification_source') or '?'}")
    click.echo(f"  batches: {status.get('batch_count') or 0}")
    event_count = status.get("event_count")
    click.echo(f"  events:  {event_count if event_count is not None else 'unknown'}")

    def _checked(value: object, ok_text: str) -> str:
        if value is None:
            return "not checked"
        return ok_text if value else "invalid"

    click.echo(
        "  batch parents: "
        f"{_checked(status.get('batch_parents_linear'), 'linear')}"
    )
    click.echo(
        "  content hashes: "
        f"{_checked(status.get('content_hashes_valid'), 'valid')}"
    )
    click.echo(
        "  event chain: "
        f"{_checked(status.get('event_chain_valid'), 'valid')}"
    )
    if status.get("sampled_event_count") is not None:
        click.echo(f"  sampled: {status.get('sampled_event_count')} event(s)")
    for error in (status.get("errors") or [])[:5]:
        click.echo(f"  error: {error}")
    if state == "unverified_large":
        click.echo("  next: opentraces trail verify --mode full --progress plain")


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
@project_dir_option
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
        click.echo(_dump_json(summary))
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
