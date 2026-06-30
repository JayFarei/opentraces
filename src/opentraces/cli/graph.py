"""``ot trail graph`` — GitButler-style ASCII visualization of commit + trace history.

Commit-primary by default: the git log is the spine, and each commit shows
the traces that contributed to it as nested segments above a ``┊●`` dot.

``--trace <id>`` switches to trace-primary mode: the spine is the trace,
and each commit the trace touched is a ``┊●`` line with entity-change
suffix decorators.

The renderer lives in :mod:`opentraces.clients.text.graph_renderer` and
implements the 7-rule GitButler manifesto (plan 043 Appendix A).
"""

from __future__ import annotations

from pathlib import Path

import click

from ._help import OpentracesCommand
from ._options import dump_json as _dump_json, project_dir_option
from ..clients.text import graph_renderer as _gr


@click.command(
    "graph",
    cls=OpentracesCommand,
    examples=[
        "opentraces trail graph",
        "opentraces trail graph --limit 50",
        "opentraces trail graph --trace abc12",
        "opentraces trail graph --since HEAD~20 --until HEAD",
    ],
    see_also=[
        ("opentraces trail blame", "show per-commit attribution for a SHA."),
        ("opentraces trace get", "view the full trace for an id."),
    ],
    option_groups=[
        ("Pagination", ["limit", "page", "show_all"]),
        ("Scope", ["trace_id", "since_ref", "until_ref", "project_dir"]),
        ("Output", ["show_entities", "full", "as_json", "no_color"]),
    ],
)
@click.option("--limit", type=int, default=20, show_default=True,
              help="Number of commits per page.")
@click.option("--page", type=int, default=1, show_default=True,
              help="Page number (1-indexed).")
@click.option("--trace", "trace_id", default=None,
              help="Pivot to trace-primary mode for the given trace id.")
@click.option("--since", "since_ref", default=None,
              help="Show commits after this ref.")
@click.option("--until", "until_ref", default=None,
              help="Show commits up to this ref.")
@click.option("--all", "show_all", is_flag=True,
              help="Disable pagination (alias for a large --limit).")
@click.option("--entities", "show_entities", is_flag=True,
              help="Include entity-change suffixes (requires entity cache).")
@click.option("--full", "full", is_flag=True,
              help="Print the complete per-commit graph; default output is "
                   "bounded to a scannable summary.")
@click.option("--json", "as_json", is_flag=True,
              help="Emit structured JSON instead of text.")
@click.option("--no-color", "no_color", is_flag=True,
              help="Disable ANSI colors.")
@project_dir_option
def graph_cmd(limit: int, page: int, trace_id: str | None,
              since_ref: str | None, until_ref: str | None,
              show_all: bool, show_entities: bool, full: bool, as_json: bool,
              no_color: bool, project_dir: Path | None) -> None:
    """Render commit + trace history.

    Commit-primary by default: the git log is the spine and each commit
    shows the traces that touched it. Pass ``--trace <id>`` to pivot to
    trace-primary mode, where the trace is the spine. Requires a populated
    attribution cache (run ``ot backfill`` if empty).
    """
    cwd = Path(project_dir or Path.cwd()).resolve()

    # Accept "t:<prefix>" (or bare prefix >=2 chars) on --trace and resolve
    # to the full id via the project's attribution traces.
    if trace_id:
        from ..core.trace_meta import (
            AmbiguousPrefixError,
            resolve_trace_id_prefix,
        )
        probe = trace_id
        if probe.lower().startswith("t:"):
            probe = probe[2:]
        # Only resolve when short (likely a prefix). Longer than 12 chars is
        # probably already a full id — skip the lookup to stay cheap.
        if 2 <= len(probe) < 12:
            try:
                resolved = resolve_trace_id_prefix(cwd, probe)
            except AmbiguousPrefixError as e:
                click.echo(f"Ambiguous trace prefix {probe!r}. "
                           f"Candidates: {', '.join(e.candidates[:6])}",
                           err=True)
                raise SystemExit(2) from e
            except ValueError as e:
                click.echo(str(e), err=True)
                raise SystemExit(2) from e
            if resolved:
                trace_id = resolved

    # Cache presence check — first-run guidance for the interactive renderer
    # only. It writes an advisory stderr hint, never stdout, so it is pure noise
    # for `--json` consumers; skip it on the JSON path so the structured output
    # stays bounded by the page (the presence read is whole-log, un-gated by the
    # paginated window, and would dominate an otherwise page-bounded request).
    if not as_json:
        try:
            from ..core.cache import AttributionCache
            cache = AttributionCache(cwd)
            has_any = bool(cache.list_attributed_shas())
        except Exception:
            has_any = False
        has_trail_events = False
        try:
            # Plan 120 (#120): bounded whole-log presence probe. The hint only
            # gates a stderr message, so a cheap existence check over the anchor
            # event type (parses only git_anchor_created blobs, <1% of the log)
            # is behaviour-equivalent to building the full projection and testing
            # .anchors_by_id, without the per-invocation whole-log walk.
            from ..core.trails import read_events_scoped

            has_trail_events = bool(
                read_events_scoped(cwd, event_types={"git_anchor_created"})
            )
        except Exception:
            has_trail_events = False
        if not has_any and not has_trail_events:
            click.echo(
                "Attribution cache is empty. "
                "Run `ot backfill` (or wait for the watcher).",
                err=True,
            )
            # Still render the commit graph without attribution info.

    opts = _gr.RenderOptions(
        width=80,
        color=not no_color,
        mode="trace" if trace_id else "commit",
        pivot_trace_id=trace_id,
        show_entities=show_entities,
        limit=100_000 if show_all else limit,
        page=page,
        since=since_ref,
        until=until_ref,
    )
    commits = _gr.load_commits_from_repo(cwd, opts)
    if not commits:
        if as_json:
            click.echo(_dump_json({
                    "mode": opts.mode,
                    "pivot_trace_id": opts.pivot_trace_id,
                    "commits": [],
                }))
            return
        click.echo("(no commits in range)")
        return
    if as_json:
        payload = {
            "mode": opts.mode,
            "pivot_trace_id": opts.pivot_trace_id,
            "commits": [
                {
                    "sha": commit.sha,
                    "short_sha": commit.short_sha,
                    "subject": commit.subject,
                    "timestamp": commit.timestamp,
                    "parents": commit.parents,
                    "traces": [
                        {
                            "trace_id": trace.trace_id,
                            "line_count": trace.line_count,
                            "files": trace.files,
                            "lifecycle": trace.lifecycle,
                            "source": trace.source,
                            "trail_evidence": trace.trail_evidence,
                        }
                        for trace in commit.traces
                    ],
                }
                for commit in commits
            ],
        }
        click.echo(_dump_json(payload))
        return
    out = _gr.render(commits, opts)
    if not full:
        # Default human output is bounded to a quick, scannable view; --full
        # prints the complete per-commit graph. Small graphs are within budget
        # and so render byte-identically with or without --full.
        out = _gr.bound_output(out)
    click.echo(out, nl=False)
