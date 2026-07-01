"""``ot trail`` — Trace Trails inspection commands."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json, project_dir_option
from .trail_helpers import (
    _audit_trail_capture,
    _collect_event_log_patch_ids,
    _emit_batch_track,
    _parse_track_since,
    _read_patches_from_file,
    _render_explain_step,
    _render_sync_summary,
    _render_trail_play_graph,
    _render_trail_play_table,
)


@click.group("trail", cls=OpentracesGroup)
def trail_group() -> None:
    """Inspect and sync VCS-anchored Trace Trails."""


@trail_group.command(
    "track",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail track tr1",
        "opentraces trail track tr1 --step 2",
        "opentraces trail track --patch 4f2ff654...",
        "opentraces trail track --anchor 8354f2b0...",
        "opentraces trail track --since 12h --json",
        "opentraces trail track --all --json --limit 50",
        "opentraces trail track --patches-from patches.txt --json",
        "opentraces trail track tr1 --warn-missing-patches --json",
    ],
    see_also=[
        ("opentraces trail blame", "map commits/files back to producing traces."),
        ("opentraces trail graph", "render commit + trace history."),
    ],
    option_groups=[
        (
            "Scope",
            [
                "trace_id",
                "trace_patch_id",
                "git_anchor_id",
                "step_index",
                "project_dir",
            ],
        ),
        ("Batch", ["since", "patches_from", "all_patches", "limit"]),
        (
            "Output",
            ["silent", "as_table", "as_json", "warn_missing_patches"],
        ),
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
    "--since",
    "since",
    default=None,
    help=(
        "Batch: track every Trace Patch whose event_time falls within the "
        "duration window (e.g. 12h, 30m, 2d, 60s) or after an ISO timestamp."
    ),
)
@click.option(
    "--patches-from",
    "patches_from",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help=(
        "Batch: read patch ids from FILE (one id per line, or JSONL with a "
        "patch_id / trace_patch_id field)."
    ),
)
@click.option(
    "--all",
    "all_patches",
    is_flag=True,
    help="Batch: track every Trace Patch in the project's trail.",
)
@click.option(
    "--limit",
    "limit",
    type=click.IntRange(min=1),
    default=None,
    help=(
        "Batch: bound work to the N most recent patches (default 50 for "
        "--all). Caps both enumeration and per-patch survival work, not just "
        "the emitted rows; the oldest patches are the slowest to resolve."
    ),
)
@click.option(
    "--warn-missing-patches",
    "warn_missing_patches",
    is_flag=True,
    help=(
        "With TRACE_ID: surface a structured warning when the trace has "
        "file_edits but zero trace_patch_created events (capture incomplete)."
    ),
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
@project_dir_option
def track_cmd(
    trace_id: str | None,
    trace_patch_id: str | None,
    git_anchor_id: str | None,
    step_index: int | None,
    since: str | None,
    patches_from: Path | None,
    all_patches: bool,
    limit: int | None,
    warn_missing_patches: bool,
    silent: bool,
    as_table: bool,
    as_json: bool,
    history_limit: int | None,
    project_dir: Path | None,
) -> None:
    """Walk and render trace lineage through Git history.

    Single-target scopes:
      * ``track <TRACE_ID>``        — full trace lineage (default)
      * ``track <TRACE_ID> --step N`` — single step's evidence
      * ``track --patch <ID>``      — one Trace Patch's survival
      * ``track --anchor <ID>``     — one Git Anchor's survival

    Batch scopes (emit one JSON line per patch — stream into ``jq -s``):
      * ``track --since 12h``       — every patch within the time window
      * ``track --all``             — every Trace Patch in the trail
      * ``track --patches-from FILE`` — ids from a text or JSONL file

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

    batch_flags = sum(1 for f in (since, patches_from, all_patches) if f)
    selectors = sum(1 for s in (trace_id, trace_patch_id, git_anchor_id) if s)

    if batch_flags > 0:
        if batch_flags > 1:
            click.echo(
                "Provide only one of --since, --patches-from, or --all "
                "(batch scopes are mutually exclusive).",
                err=True,
            )
            sys.exit(2)
        if selectors > 0:
            click.echo(
                "Batch flags (--since/--patches-from/--all) cannot be combined "
                "with TRACE_ID/--patch/--anchor — pick one scope.",
                err=True,
            )
            sys.exit(2)
        if as_table:
            click.echo(
                "--table is only valid with the full-trace view, not batch.",
                err=True,
            )
            sys.exit(2)
        if step_index is not None:
            click.echo(
                "--step is per-trace, not a batch flag.",
                err=True,
            )
            sys.exit(2)
        if warn_missing_patches:
            click.echo(
                "--warn-missing-patches requires a TRACE_ID; it is not a "
                "batch-scoped flag.",
                err=True,
            )
            sys.exit(2)

        repo = Path(project_dir or Path.cwd()).resolve()
        # F0 hang cure (#110): --limit bounds WORK, not just output. On a mature
        # trail the oldest patches carry the longest git survival walk (44s for
        # the oldest 10 vs 0.45s for the newest 10 on the live ~21k-patch log),
        # so an unbounded --all — or even the pre-cure ``[:limit]`` head slice,
        # which picked the SLOWEST/oldest patches — hangs. Default --all to the
        # 50 most recent patches; the enumerated selectors take the tail (newest)
        # so the per-patch survival loop is bounded to cheap, relevant patches.
        _DEFAULT_ALL_LIMIT = 50
        effective_limit = (
            limit if limit is not None else (_DEFAULT_ALL_LIMIT if all_patches else None)
        )
        try:
            if since is not None:
                cutoff = _parse_track_since(since)
                patch_ids = _collect_event_log_patch_ids(
                    repo, since=cutoff, limit=effective_limit
                )
            elif patches_from is not None:
                # An explicit id list is authoritative: honour its order and cap
                # from the head only when --limit was passed.
                patch_ids = _read_patches_from_file(patches_from)
                if effective_limit is not None:
                    patch_ids = patch_ids[:effective_limit]
            else:
                patch_ids = _collect_event_log_patch_ids(
                    repo, limit=effective_limit
                )
        except ValueError as exc:
            click.echo(str(exc), err=True)
            sys.exit(2)
        except Exception as exc:
            click.echo(f"Unable to enumerate patches: {exc}", err=True)
            sys.exit(2)

        if silent:
            return

        _emit_batch_track(repo, patch_ids, history_limit=history_limit)
        return

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
    if warn_missing_patches and not trace_id:
        click.echo(
            "--warn-missing-patches requires a TRACE_ID (per-trace audit).",
            err=True,
        )
        sys.exit(2)
    if limit is not None:
        click.echo(
            "--limit is only valid in batch mode (--since/--all/--patches-from).",
            err=True,
        )
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

    if warn_missing_patches and trace_id:
        try:
            audit = _audit_trail_capture(repo, trace_id)
        except Exception as exc:
            audit = {"error": str(exc), "incomplete": False}
        if isinstance(payload, dict):
            payload.setdefault("trail_capture_audit", audit)
            limitations = list(payload.get("limitations") or [])
            if audit.get("incomplete") and "trail_capture_incomplete" not in limitations:
                limitations.append("trail_capture_incomplete")
                payload["limitations"] = limitations

    if silent:
        return

    if as_json:
        click.echo(_dump_json(payload))
        return

    if renderer is not None:
        click.echo(renderer(payload))
        if warn_missing_patches and isinstance(payload, dict) and "trail_capture_incomplete" in (
            payload.get("limitations") or []
        ):
            audit = payload.get("trail_capture_audit") or {}
            click.echo(
                "  warning: trail_capture_incomplete "
                f"(file_edits={audit.get('file_edits_count')}, "
                f"patches={audit.get('patch_created_count')})"
            )
        return

    if as_table:
        click.echo(_render_trail_play_table(payload))
        return
    click.echo(_render_trail_play_graph(repo, payload))
    if warn_missing_patches and isinstance(payload, dict) and "trail_capture_incomplete" in (
        payload.get("limitations") or []
    ):
        audit = payload.get("trail_capture_audit") or {}
        click.echo(
            "  warning: trail_capture_incomplete "
            f"(file_edits={audit.get('file_edits_count')}, "
            f"patches={audit.get('patch_created_count')})"
        )


# Side-effect registration: importing trail_substrate decorates all hidden
# substrate commands onto trail_group (explain, resolve, sync, snapshots,
# snapshot, timeline, teleport, search, diff, attach, mature, rebuild).
from . import trail_substrate  # noqa: F401, E402
