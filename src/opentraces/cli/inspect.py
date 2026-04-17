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
from ..core.trace_meta import short_trace_id
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

@main.command(
    examples=[
        "opentraces stats",
    ],
    see_also=[
        ("opentraces status", "compact snapshot of inbox + remote."),
        ("opentraces assess", "score trace quality."),
    ],
)
def stats() -> None:
    """Show aggregate statistics for the current inbox.

    Rolls up every local trace into counts, token totals, cost
    estimates, and a model breakdown. Useful for a one-shot "what have
    I captured" answer.
    """
    from ..core.config import (
        get_project_traces_dir, get_project_state_path, project_is_opted_in,
    )
    from ..core.state import StateManager
    from opentraces_schema import TraceRecord

    project_dir = Path.cwd()
    if not project_is_opted_in(project_dir):
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    staging_dir = get_project_traces_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    staged_files = sorted(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []

    counts = {stage: 0 for stage in ("inbox", "staged", "pushed", "rejected")}
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


@main.command(hidden=True)
def context() -> None:
    """Show full project context for agent consumption.

    Hidden surface used by the opentraces skill and other automation. Humans
    should use ``opentraces status`` instead.
    """
    from ..core.config import get_project_traces_dir, get_project_state_path, project_is_opted_in
    from ..core.state import StateManager
    from opentraces_schema import SCHEMA_VERSION

    project_dir = Path.cwd()
    if not project_is_opted_in(project_dir):
        click.echo("Not an opentraces project.")
        human_hint("Run: opentraces init")
        emit_json(error_response("NOT_INITIALIZED", "project", "No .opentraces.json marker", "Run: opentraces init"))
        sys.exit(3)

    proj_config = load_project_config(project_dir)
    staging_dir = get_project_traces_dir(project_dir)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    # Count stages from state.json directly — reading every staged JSONL
    # here costs seconds on big inboxes and yields the same result.
    counts = {stage: 0 for stage in ("inbox", "staged", "pushed", "rejected")}
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
        suggested_next = "opentraces auth login"
    elif counts["inbox"] > 0:
        suggested_next = "opentraces list --stage inbox"
    elif counts["staged"] > 0:
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
    human_echo(f"Inbox:    {counts['inbox']}  Staged: {counts['staged']}  Pushed: {counts['pushed']}")
    human_echo(f"Next:     {suggested_next}")

    emit_json(result)


# ---------------------------------------------------------------------------
# graph: hierarchical view of the trace ↔ commit graph, in both directions.
# ---------------------------------------------------------------------------


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


_TIER_LABELS = {
    "tool_emitted":                  ("✓", "tier.emitted",  "emitted"),
    "tool_emitted_with_divergence":  ("~", "tier.diverged", "diverged"),
    "overlapping":                   ("?", "tier.overlap",  "overlap"),
    "orphan":                        ("·", "tier.orphan",   "orphan"),
}


def _truncate(text: str, width: int) -> str:
    """UTF-aware ellipsize (safe for the column cap)."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _render_spine_gap(console) -> None:
    """One spine row — the ``┊`` continuation between stacks."""
    console.print("[stack.body]┊[/]", highlight=False)


def _render_stack(console, head: str, body: list[str]) -> None:
    """Render one stack branching off the continuous left spine.

    Shape — a head row that opens off the spine, body rows that hang off
    the spine with a ``●`` node, and a ``├╯`` row that rejoins the spine::

        ┊╭┄ <head>
        ┊●   <body 1>
        ┊●   <body 2>
        ├╯
    """
    console.print(f"[stack.body]┊[/][stack.head]╭┄[/] {head}", highlight=False)
    for line in body:
        console.print(f"[stack.body]┊[/][stack.head]●[/]   {line}", highlight=False)
    console.print("[stack.head]├╯[/]", highlight=False)


def _render_base(console, head: str) -> None:
    """Render the common-ancestor footer: spine terminates at ``┴``."""
    console.print(f"[stack.base]┴[/] {head}", highlight=False)


def _git_head_info(cwd: Path) -> tuple[str, str, str] | None:
    """Resolve the common base (origin/HEAD or main) for the footer.

    Returns (sha, branch_label, short_subject) or None.
    """
    import subprocess
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        try:
            res = subprocess.run(
                ["git", "log", "-1", "--format=%H%x01%s%x01%cr", ref],
                cwd=cwd, capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return None
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split("\x01")
            if len(parts) == 3:
                return parts[0], ref, parts[1]
    return None


def _render_graph(mode: str, limit: int, cwd: Path) -> str:
    """Render the graph to a styled string via a themed Rich Console capture."""
    from io import StringIO

    from ..core.config import get_project_traces_dir
    from ..core.inbox import load_trace_records
    from ..core.theme import get_console
    from ..enrichment.git import notes_store

    staging = get_project_traces_dir(cwd)
    records = load_trace_records(staging)
    records_by_id = {r.trace_id: r for r in records}

    buf = StringIO()
    console = get_console(file=buf, force_terminal=True, width=140)

    mode_label = "commit graph" if mode == "commit" else "trace graph"
    console.print()
    console.print(
        f"  [strong]{mode_label}[/]  [muted]({len(records)} staged traces)[/]",
        highlight=False,
    )
    console.print()

    # Collect stacks as (head_markup, body_markup_lines). Render at the end
    # so we know which one is last and can join the spine to `base`.
    stacks: list[tuple[str, list[str]]] = []

    if mode == "commit":
        commits = _git_log_commits(limit, cwd)
        if not commits:
            console.print("  [warning]no git history here[/] [muted](not a git repo?)[/]",
                          highlight=False)
            return buf.getvalue()

        linked_trace_ids: set[str] = set()
        for sha, subject, when in commits:
            head = (
                f"[commit.sha]{sha[:8]}[/]  "
                f"{_truncate(subject, 60)}  "
                f"[stack.label][{when}][/]"
            )
            try:
                note_lines = notes_store.read(sha, cwd)
            except Exception:
                note_lines = []
            links = [p for p in (notes_store.parse_link(l) for l in note_lines) if p]

            body: list[str] = []
            if not links:
                body.append("[muted]· no opentraces link[/]")
            else:
                for tid, _url in links:
                    linked_trace_ids.add(tid)
                    rec = records_by_id.get(tid)
                    label = None
                    tier = "tool_emitted"
                    if rec is not None:
                        try:
                            label, _ = _cli._describe_trace(rec)
                        except Exception:
                            label = None
                        if rec.git_links:
                            for gl in rec.git_links:
                                if (gl.revision or "").startswith(sha[:10]):
                                    tier = gl.tier
                                    break
                    glyph, style, _w = _TIER_LABELS.get(
                        tier, ("·", "tier.orphan", "orphan")
                    )
                    label_str = _truncate(label or "(unknown — trace not staged)", 70)
                    body.append(
                        f"[{style}]{glyph}[/] [trace.id]{tid[:8]}[/]  {label_str}"
                    )
            stacks.append((head, body))

        # Orphan inbox bucket — its own stack on the same spine.
        orphans = [
            r for r in records
            if r.trace_id not in linked_trace_ids and not r.git_links
        ]
        if orphans:
            def _ts(r):
                v = getattr(r, "timestamp_end", None)
                return str(v) if v else ""
            orphans.sort(key=_ts, reverse=True)
            shown = orphans[:limit]
            head = (
                f"[warning]inbox[/]  "
                f"[muted][{len(orphans)} uncorrelated"
                + (f"; showing {len(shown)} most recent" if len(orphans) > limit else "")
                + "][/]"
            )
            body = []
            for rec in shown:
                try:
                    label, _ = _cli._describe_trace(rec)
                except Exception:
                    label = "(untitled)"
                body.append(
                    f"[tier.orphan]○[/] [trace.id]{short_trace_id(rec.trace_id)}[/]  "
                    f"{_truncate(label, 70)}"
                )
            stacks.append((head, body))

        base = _git_head_info(cwd)
        base_head = None
        if base is not None:
            base_sha, ref, subject = base
            base_head = (
                f"[commit.sha]{base_sha[:8]}[/]  "
                f"[stack.label][{ref}][/]  "
                f"{_truncate(subject, 60)}"
            )

    else:  # trace-spine mode
        def _ts(r):
            v = getattr(r, "timestamp_end", None)
            return str(v) if v else ""
        traces = sorted(records, key=_ts, reverse=True)[:limit]
        if not traces:
            console.print("  [muted]no staged traces.[/]", highlight=False)
            return buf.getvalue()

        for rec in traces:
            try:
                label, _src = _cli._describe_trace(rec)
            except Exception:
                label = "(untitled)"
            steps = len(rec.steps) if rec.steps else 0
            cost_part = ""
            if rec.metrics and rec.metrics.estimated_cost_usd:
                cost_part = f" · ${rec.metrics.estimated_cost_usd:.2f}"
            head = (
                f"[trace.id]{short_trace_id(rec.trace_id)}[/]  "
                f"{_truncate(label, 60)}  "
                f"[stack.label][{steps}s{cost_part}][/]"
            )

            body: list[str] = []
            if rec.git_links:
                for gl in rec.git_links:
                    glyph, style, word = _TIER_LABELS.get(
                        gl.tier, ("·", "tier.orphan", "orphan")
                    )
                    sha = (gl.revision or "")[:10]
                    body.append(
                        f"[{style}]{glyph} {word}[/]  → [commit.sha]{sha}[/]"
                    )
            else:
                body.append("[muted]· provisional (no commit yet)[/]")
            stacks.append((head, body))

        base_head = None  # trace mode has no commit ancestor footer

    # Emit the stacks around one continuous spine.
    # Lead with a ┊ so the very first ╭┄ reads as "branching off a spine"
    # rather than floating on its own.
    _render_spine_gap(console)
    for idx, (head, body) in enumerate(stacks):
        _render_stack(console, head, body)
        # Spine continues between stacks and before the base.
        if idx < len(stacks) - 1 or base_head is not None:
            _render_spine_gap(console)

    if base_head is not None:
        _render_base(console, base_head)

    console.print()
    console.print(
        "  [muted]tier:[/]  "
        "[tier.emitted]✓[/][muted] emitted[/]  "
        "[tier.diverged]~[/][muted] diverged[/]  "
        "[muted]? overlap  · orphan[/]",
        highlight=False,
    )
    console.print()
    return buf.getvalue()


@main.command("graph")
@click.option("--commit", "mode", flag_value="commit", default=True,
              help="Commit spine: each commit with the traces that produced it. (default)")
@click.option("--trace", "mode", flag_value="trace",
              help="Trace spine: each trace with the commits it produced.")
@click.option("--session", "mode", flag_value="trace", hidden=True,
              help="Deprecated alias for --trace.")
@click.option("--limit", type=int, default=20, show_default=True,
              help="Max rows on the spine.")
@click.option("--no-pager", is_flag=True,
              help="Print inline instead of paging long output.")
def graph_cmd(mode: str, limit: int, no_pager: bool) -> None:
    """Stack view of the trace ↔ commit graph.

    \b
      --commit   (default) commit spine → traces under each commit.
                 Answers: "who authored what in this commit?"
      --trace    trace spine → commits under each trace.
                 Answers: "what did this trace actually ship?"
    """
    import shutil as _sh
    from ..core.config import project_is_opted_in

    cwd = Path.cwd()
    if not project_is_opted_in(cwd):
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
        click.echo_via_pager(output, color=True)
    else:
        click.echo(output, nl=False)

    emit_json({"status": "ok", "mode": mode, "limit": limit})


def _log_fmt_tokens(n: int) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tokens"
    if n >= 1000:
        return f"{n / 1000:.1f}k tokens"
    return f"{n} tokens"


def _log_short_model(m: str) -> str:
    if not m:
        return "—"
    for prefix in ("claude-", "anthropic/"):
        if m.startswith(prefix):
            m = m[len(prefix):]
            break
    if len(m) > 20:
        m = m[:19] + "…"
    return m


@main.command(
    examples=[
        "opentraces log",
        "opentraces log --verbose",
        "opentraces log --limit 0",
    ],
    see_also=[
        ("opentraces list", "list every local trace, staged or not."),
        ("opentraces status", "compact snapshot of inbox + remote."),
        ("opentraces stats", "aggregate token and cost totals."),
    ],
)
@click.option(
    "--limit",
    type=int,
    default=30,
    show_default=True,
    help="Show at most N days of history. Use 0 for no limit.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Expand each day into per-trace rows with model, tokens, and task description (reads each trace file).",
)
def log(limit: int, verbose: bool) -> None:
    """List the recent traces that have been pushed, grouped by date.

    Default output is one row per day with the push count, destination
    remote(s), and the local time range of pushes. Pass ``--verbose`` to
    expand each day into per-trace rows with trace id, model, token
    count, and task description; the verbose view reads each trace
    file so it is slower on large inboxes.

    Only the UPLOADED stage is walked, so in-progress Inbox or staged
    work is ignored.
    """
    from ..core.state import StateManager, TraceStatus
    from ..core.config import get_project_state_path, project_is_opted_in
    from datetime import datetime

    project_dir = Path.cwd()
    if not project_is_opted_in(project_dir):
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    state = StateManager(state_path=get_project_state_path(project_dir))
    uploaded = state.get_traces_by_status(TraceStatus.UPLOADED)

    if not uploaded:
        human_echo("No traces have been pushed yet.")
        emit_json({"status": "ok", "limit": limit, "days": [], "total_days": 0})
        return

    buckets: dict[str, list] = {}
    for entry in uploaded:
        dt = None
        if entry.uploaded_at:
            try:
                parsed = datetime.fromisoformat(entry.uploaded_at.replace("Z", "+00:00"))
                dt = parsed.astimezone()
            except Exception:
                dt = None
        if dt is None:
            dt = datetime.fromtimestamp(entry.created_at)
        date_str = dt.strftime("%Y-%m-%d")
        buckets.setdefault(date_str, []).append((dt, entry))

    dates = sorted(buckets.keys(), reverse=True)
    total_days = len(dates)
    if limit > 0 and total_days > limit:
        dates = dates[:limit]

    records_cache: dict[str, object] = {}
    if verbose:
        from opentraces_schema import TraceRecord

        for date_str in dates:
            for _, entry in buckets[date_str]:
                try:
                    path = Path(entry.file_path) if entry.file_path else None
                    if path and path.exists():
                        first_line = path.read_text().strip().splitlines()[0]
                        records_cache[entry.trace_id] = TraceRecord.model_validate_json(first_line)
                except Exception:
                    continue

    days_payload: list[dict] = []
    for date_str in dates:
        entries = sorted(buckets[date_str], key=lambda x: x[0])
        count = len(entries)

        remotes_seen: dict[str, int] = {}
        for _, entry in entries:
            for remote_name in (entry.uploaded_to or {}):
                remotes_seen[remote_name] = remotes_seen.get(remote_name, 0) + 1
        remote_str = ", ".join(f"→ {r}" for r in remotes_seen) if remotes_seen else "→ (no remote)"

        first_time = entries[0][0].strftime("%H:%M")
        last_time = entries[-1][0].strftime("%H:%M")
        time_range = first_time if first_time == last_time else f"{first_time}–{last_time}"

        day_tokens = 0
        day_cost = 0.0
        trace_rows: list[dict] = []
        if verbose:
            for dt, entry in entries:
                record = records_cache.get(entry.trace_id)
                model = ""
                task_desc = ""
                tokens = 0
                cost = 0.0
                if record is not None:
                    if getattr(record, "agent", None) is not None:
                        model = record.agent.model or ""
                    if getattr(record, "task", None) is not None and record.task.description:
                        task_desc = record.task.description.strip().splitlines()[0]
                    if getattr(record, "metrics", None) is not None:
                        tokens = (record.metrics.total_input_tokens or 0) + (record.metrics.total_output_tokens or 0)
                        cost = record.metrics.estimated_cost_usd or 0.0
                day_tokens += tokens
                day_cost += cost
                trace_rows.append({
                    "dt": dt,
                    "trace_id": entry.trace_id,
                    "model": model,
                    "task": task_desc,
                    "tokens": tokens,
                    "cost": cost,
                })

        header = f"{date_str}  {count} pushed   {remote_str}   {time_range}"
        if verbose:
            extras = []
            tok_fmt = _log_fmt_tokens(day_tokens)
            if tok_fmt:
                extras.append(tok_fmt)
            if day_cost:
                extras.append(f"~${day_cost:.2f}")
            if extras:
                header += f"   ({', '.join(extras)})"
        human_echo(header)

        if verbose:
            for row in trace_rows:
                short_id = row["trace_id"][:8]
                time_str = row["dt"].strftime("%H:%M")
                model_str = _log_short_model(row["model"])
                task_str = row["task"][:60].rstrip()
                tok_str = _log_fmt_tokens(row["tokens"])
                line = f"  {short_id}  {time_str}  {model_str:<18}  {task_str}"
                if tok_str:
                    line += f"   [{tok_str}]"
                human_echo(line.rstrip())

        days_payload.append({
            "date": date_str,
            "count": count,
            "remotes": list(remotes_seen.keys()),
            "first_pushed_at": entries[0][0].isoformat(),
            "last_pushed_at": entries[-1][0].isoformat(),
            "tokens": day_tokens if verbose else None,
            "cost_usd": round(day_cost, 4) if verbose else None,
            "traces": [
                {
                    "trace_id": r["trace_id"],
                    "pushed_at": r["dt"].isoformat(),
                    "model": r["model"],
                    "task": r["task"],
                    "tokens": r["tokens"],
                    "cost_usd": round(r["cost"], 4),
                }
                for r in trace_rows
            ] if verbose else None,
        })

    if limit > 0 and total_days > limit:
        human_echo(f"\n... {total_days - limit} older day(s) hidden. Use --limit 0 to show all.")

    emit_json({"status": "ok", "limit": limit, "days": days_payload, "total_days": total_days})


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
