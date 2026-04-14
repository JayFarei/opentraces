"""``ot graph`` — GitButler-style ASCII visualization of commit + trace history.

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

from ..clients.text import graph_renderer as _gr


@click.command("graph")
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
@click.option("--no-color", "no_color", is_flag=True,
              help="Disable ANSI colors.")
@click.option("--project", "project_dir", type=click.Path(
                  exists=True, file_okay=False, dir_okay=True, path_type=Path),
              default=None, help="Project directory (default: CWD).")
def graph_cmd(limit: int, page: int, trace_id: str | None,
              since_ref: str | None, until_ref: str | None,
              show_all: bool, show_entities: bool, no_color: bool,
              project_dir: Path | None) -> None:
    """Render commit + trace history."""
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

    # Cache presence check — first run guidance.
    try:
        from ..core.cache import AttributionCache
        cache = AttributionCache(cwd)
        has_any = bool(cache.list_attributed_shas())
    except Exception:
        has_any = False
    if not has_any:
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
        click.echo("(no commits in range)")
        return
    click.echo(_gr.render(commits, opts), nl=False)
