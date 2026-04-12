"""CLI inspect commands: stats, context, log, _list-sessions."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main
from .. import __version__  # noqa: F401
from ..core.workflow import resolve_visible_stage

logger = logging.getLogger("opentraces.cli.inspect")


def load_config():
    return _cli.load_config()


def load_project_config(*a, **k):
    return _cli.load_project_config(*a, **k)


def emit_json(data):
    return _cli.emit_json(data)


def error_response(*a, **k):
    return _cli.error_response(*a, **k)


def human_echo(*a, **k):
    return _cli.human_echo(*a, **k)


def human_hint(*a, **k):
    return _cli.human_hint(*a, **k)


def _auth_identity(*a, **k):
    return _cli._auth_identity(*a, **k)




# ---------------------------------------------------------------------------
# stats and context: aggregate views for agent consumption
# ---------------------------------------------------------------------------

@main.command()
def stats() -> None:
    """Show aggregate statistics for the current project inbox."""
    from ..core.config import get_project_staging_dir, get_project_state_path
    from ..core.state import StateManager
    from opentraces_schema import TraceRecord

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    if not ot_dir.exists():
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    staging_dir = get_project_staging_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    staged_files = sorted(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []

    counts = {stage: 0 for stage in ("inbox", "committed", "pushed", "rejected")}
    models: dict[str, int] = {}
    agents: dict[str, int] = {}
    total_steps = 0
    total_tokens = 0
    total_cost = 0.0
    timestamps = []

    for sf in staged_files:
        try:
            data = sf.read_text().strip()
            record = TraceRecord.model_validate_json(data.splitlines()[0])
            entry = state.get_trace(record.trace_id)
            visible_stage = resolve_visible_stage(entry.status if entry else None)
            counts[visible_stage] += 1

            model_name = record.agent.model or "unknown"
            models[model_name] = models.get(model_name, 0) + 1
            agents[record.agent.name] = agents.get(record.agent.name, 0) + 1

            total_steps += len(record.steps)
            if record.metrics:
                total_tokens += (record.metrics.total_input_tokens or 0) + (record.metrics.total_output_tokens or 0)
                total_cost += record.metrics.estimated_cost_usd or 0.0

            if record.timestamp_start:
                timestamps.append(str(record.timestamp_start) if isinstance(record.timestamp_start, str) else record.timestamp_start.isoformat())
            if record.timestamp_end:
                timestamps.append(str(record.timestamp_end) if isinstance(record.timestamp_end, str) else record.timestamp_end.isoformat())
        except Exception:
            continue

    result = {
        "status": "ok",
        "total_traces": len(staged_files),
        "counts": counts,
        "models": models,
        "agents": agents,
        "total_steps": total_steps,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "date_range": {
            "earliest": min(timestamps) if timestamps else None,
            "latest": max(timestamps) if timestamps else None,
        },
    }

    human_echo(f"Traces: {len(staged_files)}")
    human_echo(f"Steps:  {total_steps}")
    human_echo(f"Tokens: {total_tokens}")
    human_echo(f"Cost:   ${total_cost:.4f}")
    for stage, count in counts.items():
        if count > 0:
            human_echo(f"  {stage}: {count}")
    if models:
        human_echo("Models:")
        for m, c in sorted(models.items(), key=lambda x: -x[1]):
            human_echo(f"  {m}: {c}")

    emit_json(result)


@main.command()
def context() -> None:
    """Show full project context for agent consumption."""
    from ..core.config import get_project_staging_dir, get_project_state_path
    from ..core.state import StateManager
    from opentraces_schema import SCHEMA_VERSION

    project_dir = Path.cwd()
    ot_dir = project_dir / ".opentraces"
    if not ot_dir.exists():
        click.echo("Not an opentraces project.")
        human_hint("Run: opentraces init")
        emit_json(error_response("NOT_INITIALIZED", "project", "No .opentraces directory", "Run: opentraces init"))
        sys.exit(3)

    proj_config = load_project_config(project_dir)
    staging_dir = get_project_staging_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path if state_path.parent.exists() else None)

    # Count stages from state.json directly — reading every staged JSONL
    # here costs seconds on big inboxes and yields the same result.
    counts = {stage: 0 for stage in ("inbox", "committed", "pushed", "rejected")}
    for entry in state._state.get("traces", {}).values():  # noqa: SLF001
        visible_stage = resolve_visible_stage(entry.get("status"))
        counts[visible_stage] = counts.get(visible_stage, 0) + 1
    # Sessions that exist on disk but aren't tracked in state fall under "inbox".
    tracked_count = sum(counts.values())
    if staging_dir.exists():
        staged_file_count = sum(1 for _ in staging_dir.glob("*.jsonl"))
        untracked = max(0, staged_file_count - tracked_count)
        counts["inbox"] += untracked

    # Auth status
    cfg = load_config()
    identity = _auth_identity(cfg.hf_token)
    authenticated = identity is not None
    username = identity.get("name", "unknown") if identity else None

    # Suggest next action
    if not authenticated:
        suggested_next = "opentraces login"
    elif counts["inbox"] > 0:
        suggested_next = "opentraces session list --stage inbox"
    elif counts["committed"] > 0:
        suggested_next = "opentraces push"
    else:
        suggested_next = "opentraces status"

    result = {
        "status": "ok",
        "project": project_dir.name,
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "config": {
            "review_policy": proj_config.get("review_policy", "review"),
            "push_policy": proj_config.get("push_policy", "manual"),
            "agents": proj_config.get("agents", ["claude-code"]),
            "remote": proj_config.get("remote"),
            "visibility": proj_config.get("visibility", "private"),
        },
        "auth": {
            "authenticated": authenticated,
            "username": username,
        },
        "counts": counts,
        "total_traces": sum(counts.values()),
        "suggested_next": suggested_next,
    }

    human_echo(f"Project:  {project_dir.name}")
    human_echo(f"Remote:   {proj_config.get('remote', 'not set')}")
    human_echo(f"Auth:     {'yes (' + username + ')' if authenticated else 'no'}")
    human_echo(f"Inbox:    {counts['inbox']}  Committed: {counts['committed']}  Pushed: {counts['pushed']}")
    human_echo(f"Next:     {suggested_next}")

    emit_json(result)


# ---------------------------------------------------------------------------
# graph: hierarchical view of the trace ↔ commit graph, in both directions.
# ---------------------------------------------------------------------------


_TIER_PRESENT = {
    "tool_emitted": ("✓", "green"),
    "tool_emitted_with_divergence": ("~", "yellow"),
    "overlapping": ("?", "bright_black"),
    "orphan": ("·", "bright_black"),
}


def _git_log_commits(limit: int, cwd: Path) -> list[tuple[str, str, str]]:
    """Return [(sha, subject, relative_date)] for the last `limit` commits
    on the current branch. Empty list if not a git repo."""
    import subprocess
    try:
        res = subprocess.run(
            ["git", "log", "--first-parent", f"-n{max(limit, 1)}",
             "--format=%H%x01%s%x01%cr"],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return []
    if res.returncode != 0:
        return []
    out: list[tuple[str, str, str]] = []
    for line in res.stdout.strip().splitlines():
        parts = line.split("\x01")
        if len(parts) == 3:
            out.append((parts[0], parts[1], parts[2]))
    return out


def _render_graph(mode: str, limit: int, cwd: Path) -> str:
    """Render the graph to a styled string via Rich Console capture."""
    from io import StringIO
    from rich.console import Console
    from rich.tree import Tree

    from ..core.config import get_project_staging_dir
    from ..core.inbox import load_trace_records
    from ..enrichment.git import notes_store

    staging = get_project_staging_dir(cwd)
    records = load_trace_records(staging)
    records_by_id = {r.trace_id: r for r in records}

    buf = StringIO()
    # force_terminal + truecolor so captured output keeps ANSI when we pipe
    # it through the pager; plain consumers strip it naturally.
    console = Console(
        file=buf, force_terminal=True, color_system="truecolor", width=140
    )

    if mode == "commit":
        tree = Tree(
            f"[bold]commit graph[/] [dim](last {limit} commits · "
            f"{len(records)} staged traces)[/]"
        )
        commits = _git_log_commits(limit, cwd)
        linked_trace_ids: set[str] = set()

        if not commits:
            tree.add("[yellow]no git history here[/] [dim](not a git repo?)[/]")
        else:
            for sha, subject, when in commits:
                subject_short = subject if len(subject) <= 60 else subject[:59] + "…"
                commit_node = tree.add(
                    f"[green bold]●[/] [green]{sha[:8]}[/]  "
                    f"{subject_short}  [dim]({when})[/]"
                )
                try:
                    note_lines = notes_store.read(sha, cwd)
                except Exception:
                    note_lines = []
                links = [p for p in (notes_store.parse_link(l) for l in note_lines) if p]
                if not links:
                    commit_node.add("[dim]· no opentraces link[/]")
                    continue
                for (tid, _url) in links:
                    linked_trace_ids.add(tid)
                    rec = records_by_id.get(tid)
                    intent = None
                    tier = "tool_emitted"
                    if rec is not None:
                        try:
                            intent, _ = _cli._describe_trace(rec)
                        except Exception:
                            intent = None
                        if rec.git_links:
                            for gl in rec.git_links:
                                if (gl.revision or "").startswith(sha[:10]):
                                    tier = gl.tier
                                    break
                    glyph, color = _TIER_PRESENT.get(tier, ("·", "bright_black"))
                    intent_str = intent or "[dim](unknown — trace not staged)[/]"
                    if intent and len(intent) > 70:
                        intent_str = intent[:69] + "…"
                    commit_node.add(
                        f"[{color}]{glyph}[/] [cyan]{tid[:8]}[/]  {intent_str}"
                    )

        # Orphans — staged traces not linked to any visible commit.
        orphans = [r for r in records if r.trace_id not in linked_trace_ids and not r.git_links]
        if orphans:
            n = len(orphans)
            # Cap orphan list to `limit` to keep output bounded.
            shown = orphans[-limit:] if n > limit else orphans
            orphan_node = tree.add(
                f"[yellow]● inbox[/]  [dim]({n} uncorrelated"
                + (f"; showing {len(shown)} most recent" if n > limit else "")
                + ")[/]"
            )
            # newest first: sort by timestamp_end desc if available
            def _sort_k(r):
                ts = getattr(r, "timestamp_end", None)
                return str(ts) if ts else ""
            shown = sorted(shown, key=_sort_k, reverse=True)
            for rec in shown:
                try:
                    intent, _ = _cli._describe_trace(rec)
                except Exception:
                    intent = "(untitled)"
                if intent and len(intent) > 70:
                    intent = intent[:69] + "…"
                orphan_node.add(
                    f"[bright_black]○[/] [cyan]{rec.trace_id[:8]}[/]  {intent}"
                )

    else:  # mode == "session"
        # Most recent N sessions by timestamp_end, with each session's commits.
        def _ts(r):
            v = getattr(r, "timestamp_end", None)
            return str(v) if v else ""
        sessions = sorted(records, key=_ts, reverse=True)[:limit]
        tree = Tree(
            f"[bold]session graph[/] [dim](last {len(sessions)} of "
            f"{len(records)} staged traces)[/]"
        )

        for rec in sessions:
            try:
                intent, _src = _cli._describe_trace(rec)
            except Exception:
                intent = "(untitled)"
            if intent and len(intent) > 60:
                intent = intent[:59] + "…"
            steps = len(rec.steps) if rec.steps else 0
            cost = ""
            if rec.metrics and rec.metrics.estimated_cost_usd:
                cost = f" · ${rec.metrics.estimated_cost_usd:.2f}"
            sess_node = tree.add(
                f"[cyan bold]●[/] [cyan]{rec.trace_id[:8]}[/]  "
                f"{intent}  [dim]({steps}s{cost})[/]"
            )
            if rec.git_links:
                for gl in rec.git_links:
                    glyph, color = _TIER_PRESENT.get(gl.tier, ("·", "bright_black"))
                    sha = (gl.revision or "")[:10]
                    sess_node.add(
                        f"[{color}]{glyph} {gl.tier}[/]  → [green]{sha}[/]"
                    )
            else:
                sess_node.add(f"[dim](provisional — no commit yet)[/]")

    console.print(tree)
    # Legend
    console.print()
    console.print(
        "  [dim]tier:[/] [green]✓[/][dim] emitted[/]  "
        "[yellow]~[/][dim] diverged[/]  [dim]? overlap  · orphan[/]",
        highlight=False,
    )
    return buf.getvalue()


@main.command("graph")
@click.option("--commit", "mode", flag_value="commit", default=True,
              help="Commit spine: each commit with the sessions that produced it. (default)")
@click.option("--session", "mode", flag_value="session",
              help="Session spine: each session with the commits it produced.")
@click.option("--limit", type=int, default=20, show_default=True,
              help="Max rows on the spine.")
@click.option("--no-pager", is_flag=True,
              help="Print inline instead of paging long output.")
def graph_cmd(mode: str, limit: int, no_pager: bool) -> None:
    """Hierarchical view of the trace ↔ commit graph.

    Two symmetric modes:

    \b
      --commit   (default) commit spine → sessions under each commit.
                 Answers: "who authored what in this commit?"
      --session  session spine → commits under each session.
                 Answers: "what did this session actually ship?"
    """
    import shutil as _sh

    cwd = Path.cwd()
    ot_dir = cwd / ".opentraces"
    if not ot_dir.exists():
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    output = _render_graph(mode, limit, cwd)
    rows = _sh.get_terminal_size(fallback=(80, 24)).lines
    line_count = output.count("\n")

    should_page = (
        not no_pager
        and sys.stdout.isatty()
        and line_count > max(rows - 4, 10)
    )
    if should_page:
        # color=True keeps ANSI through less/pagers that honor it (e.g. 'less -R').
        click.echo_via_pager(output, color=True)
    else:
        click.echo(output, nl=False)

    emit_json({
        "status": "ok",
        "mode": mode,
        "limit": limit,
    })


@main.command()
@click.option(
    "--limit",
    type=int,
    default=30,
    show_default=True,
    help="Show at most N days of history. Use 0 for no limit.",
)
def log(limit: int) -> None:
    """List uploaded traces grouped by date."""
    from ..core.state import StateManager, TraceStatus
    from datetime import datetime

    state = StateManager()
    uploaded = state.get_traces_by_status(TraceStatus.UPLOADED)

    if not uploaded:
        click.echo("No traces have been pushed yet.")
        return

    # Group by date
    by_date: dict[str, int] = {}
    for entry in uploaded:
        if entry.uploaded_at:
            try:
                dt = datetime.fromisoformat(entry.uploaded_at)
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                date_str = "unknown"
        else:
            date_str = datetime.fromtimestamp(entry.created_at).strftime("%Y-%m-%d")
        by_date[date_str] = by_date.get(date_str, 0) + 1

    dates = sorted(by_date.keys(), reverse=True)
    total_days = len(dates)
    if limit > 0 and total_days > limit:
        dates = dates[:limit]

    for date_str in dates:
        count = by_date[date_str]
        click.echo(f"{date_str}  pushed {count} sessions")

    if limit > 0 and total_days > limit:
        click.echo(f"\n... {total_days - limit} older day(s) hidden. Use --limit 0 to show all.")


@main.command(hidden=True)
def discover() -> None:
    """List available agent sessions across projects."""
    from ..core.config import get_projects_path

    cfg = load_config()
    projects_path = get_projects_path(cfg)

    if not projects_path.exists():
        click.echo(f"No sessions found. Directory does not exist: {projects_path}")
        human_hint("Run Claude Code at least once to generate session logs, or use 'opentraces config set --projects-path' to specify a custom location")
        emit_json(error_response(
            code="NO_SESSIONS_FOUND",
            kind="not_found",
            message=f"{projects_path} not found",
            hint="Run Claude Code at least once to generate session logs, or use 'opentraces config set --projects-path' to specify a custom location",
        ))
        sys.exit(6)

    sessions = []
    for project_dir in sorted(projects_path.iterdir()):
        if not project_dir.is_dir():
            continue
        session_files = list(project_dir.glob("*.jsonl"))
        if session_files:
            sessions.append({
                "project": project_dir.name,
                "path": str(project_dir),
                "session_files": len(session_files),
            })

    if not sessions:
        click.echo("No session files found.")
        human_hint("Run Claude Code to generate session logs")
        emit_json(error_response(
            code="NO_SESSIONS_FOUND",
            kind="not_found",
            message="No .jsonl session files found",
            hint="Run Claude Code to generate session logs",
        ))
        sys.exit(6)

    click.echo(f"Found {len(sessions)} projects with sessions:\n")
    for s in sessions:
        click.echo(f"  {s['project']}: {s['session_files']} session file(s)")

    emit_json({
        "status": "ok",
        "sessions": sessions,
        "total_projects": len(sessions),
        "next_steps": ["Run 'opentraces parse' to parse sessions into enriched JSONL"],
        "next_command": "opentraces parse",
    })
