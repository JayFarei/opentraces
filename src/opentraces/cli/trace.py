"""CLI trace commands: CRUD for trace review actions.

Contains the ``trace`` subgroup (query, map, slice, get, index, teleport,
compare, discover) plus the ``list`` and ``resume`` commands re-exposed at
the flat root and under ``trail``. The legacy flat inbox commands (show,
reject, reset, discard) were removed; they remain absent from all groups.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import click

from opentraces import cli as _cli
from ._envelope import envelope as _envelope
from ._help import OpentracesCommand, OpentracesGroup
from ._options import dump_json as _dump_json
from ._progress import progress_option
from ..core.trace_meta import short_trace_id
from ..core.trace_stage import resolve_visible_stage


def _resolve_trace_id(trace_id: str) -> str | None:
    """Resolve a short-id or ``t:`` prefix to the canonical full trace_id.

    Returns the full id on a unique match, ``None`` on no match or
    ambiguous prefix. Keeps reject/reset/discard behaviourally consistent
    with show/resume/redact which already accept short forms.
    """
    from ..core.trace_meta import (
        AmbiguousPrefixError,
        resolve_trace_id_prefix,
    )
    try:
        return resolve_trace_id_prefix(Path.cwd(), trace_id)
    except (AmbiguousPrefixError, ValueError):
        return None

logger = logging.getLogger("opentraces.cli.trace")


_is_interactive_terminal = _cli._is_interactive_terminal
human_echo = _cli.human_echo
emit_json = _cli.emit_json
error_response = _cli.error_response


def _trace_query_diag_payload(sync_result, query_source: str) -> dict | None:
    try:
        from ..core import search_diag

        if not search_diag.enabled():
            return None
        return {
            "cheap_sync": (
                {
                    "synced": bool(sync_result.synced),
                    "query_source": sync_result.query_source,
                    "changed_trace_ids": list(sync_result.changed_trace_ids),
                    "deleted_trace_ids": list(sync_result.deleted_trace_ids),
                }
                if sync_result is not None
                else {"synced": False, "query_source": query_source}
            ),
            "search_diag": search_diag.snapshot(),
        }
    except Exception:
        return None


def _db_table_sizes(db_path: Path) -> dict:
    """Read-only per-table byte breakdown for a SQLite DB (issue #27 item A).

    Uses the ``dbstat`` virtual table to attribute on-disk bytes to each table
    and index so the issue-#22 / item-A bloat becomes measurable. ``dbstat`` is
    only available when SQLite was compiled with ``SQLITE_ENABLE_DBSTAT_VTAB``;
    when it is not (or the DB is locked / unreadable) the block degrades to
    ``{"tables": None, "tables_error": <reason>}`` rather than failing the
    command. The connection is opened ``mode=ro&immutable=1`` so this never
    mutates either DB (no WAL checkpoint, no journal, no page writes).
    """
    import sqlite3
    from urllib.parse import quote

    entry: dict = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": None,
        "tables": None,
    }
    if not db_path.exists():
        return entry
    try:
        entry["size_bytes"] = db_path.stat().st_size
    except OSError:
        pass
    uri = f"file:{quote(str(db_path), safe='/')}?mode=ro&immutable=1"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        rows = conn.execute(
            "select name, sum(pgsize) as bytes, sum(pgsize) / "
            "(select page_size from pragma_page_size) as pages "
            "from dbstat group by name order by bytes desc"
        ).fetchall()
        entry["tables"] = {
            str(name): {"bytes": int(byt or 0), "pages": int(pages or 0)}
            for (name, byt, pages) in rows
        }
    except sqlite3.OperationalError as exc:
        # dbstat vtab not compiled in, or schema introspection unavailable.
        entry["tables"] = None
        entry["tables_error"] = str(exc)
    except sqlite3.DatabaseError as exc:
        entry["tables"] = None
        entry["tables_error"] = str(exc)
    finally:
        if conn is not None:
            conn.close()
    return entry


def _compact_db_entry(entry: dict) -> dict:
    """Aggregate-scalar projection of a ``_db_table_sizes`` entry.

    The full per-table map (20+ FTS shadow tables per DB) blew the
    agent-facing envelope token budget for ``trace index status --json``.
    The default JSON render keeps only the bloat-actionable scalars (issue
    #40 visibility: total size, table count, dominant table); the full
    breakdown stays reachable via ``--verbose`` and the text render.
    """
    compact: dict = {
        "path": entry.get("path"),
        "exists": entry.get("exists"),
        "size_bytes": entry.get("size_bytes"),
        "table_count": None,
    }
    tables = entry.get("tables")
    if tables is None:
        if "tables_error" in entry:
            compact["tables_error"] = entry["tables_error"]
        return compact
    compact["table_count"] = len(tables)
    compact["tables_bytes_total"] = sum(
        int(stat.get("bytes", 0)) for stat in tables.values()
    )
    if tables:
        name, stat = max(
            tables.items(), key=lambda item: int(item[1].get("bytes", 0))
        )
        compact["largest_table"] = {"name": name, "bytes": int(stat.get("bytes", 0))}
    return compact


def _approx_bootstrap_trace_count() -> int:
    """Cheap pre-flight count of trace sources for the bootstrap signpost.

    Globs the on-disk trace stores (bucket envelopes, per-project ``traces``
    dirs, and the staging layer) WITHOUT running the expensive
    ``sync_trace_records_from_local_stores`` pass that the real ingest does.
    This is only used to put an order-of-magnitude number in the stderr
    "this may take a while" notice (issue #27 item M); it never gates behavior.
    Returns ``0`` if nothing is countable rather than raising.
    """
    from ..core import paths

    count = 0
    try:
        bucket_traces = paths.OPENTRACES_DIR / "bucket" / "traces" / "v1"
        if bucket_traces.exists():
            count += sum(1 for _ in bucket_traces.glob("*/*/trace.json"))
        projects_dir = getattr(paths, "PROJECTS_DIR", None)
        if projects_dir is not None and projects_dir.exists():
            count += sum(1 for _ in projects_dir.glob("*/traces/*.jsonl"))
        staging_dir = getattr(paths, "STAGING_DIR", None)
        if staging_dir is not None and staging_dir.exists():
            count += sum(1 for _ in staging_dir.glob("*.jsonl"))
    except OSError:
        return count
    return count


# ---------------------------------------------------------------------------
# Standalone trace commands (registered at root in cli/__init__).
# ---------------------------------------------------------------------------


@click.group("trace", cls=OpentracesGroup)
def trace_group() -> None:
    """Search, map, slice, and retrieve retained traces."""


@trace_group.command("discover", cls=OpentracesCommand, hidden=True)  # plan 087 — niche query variant (grouped topic capsule); core search is `trace query`. Callable, off --help.
@click.argument("topic_terms", nargs=-1, required=True)
@click.option(
    "--by",
    "group_by",
    type=click.Choice(["day"], case_sensitive=False),
    default="day",
    show_default=True,
    help="Group the discovery packet.",
)
@click.option("--limit", type=int, default=50, show_default=True, help="Maximum trace cards.")
@click.option(
    "--per-group",
    type=int,
    default=5,
    show_default=True,
    help="Maximum cards per group.",
)
@click.option("--project", default=None, help="Project slug to search.")
@click.option("--cwd", "current_cwd", is_flag=True, help="Search only the current opted-in project.")
@click.option("--latest-generation/--include-superseded", default=True, help="Suppress older generations by default.")
@click.option("--force-rebuild", is_flag=True, help="Rejected: search commands are read-only.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_discover(
    topic_terms: tuple[str, ...],
    group_by: str,
    limit: int,
    per_group: int,
    project: str | None,
    current_cwd: bool,
    latest_generation: bool,
    force_rebuild: bool,
    as_json: bool,
) -> None:
    """Build a deterministic topic capsule from retained traces."""
    from ..core.discovery import discover
    from ..core.trace_search_snapshot import SearchSnapshotNeedsRebuild

    topic = " ".join(topic_terms)
    if current_cwd and project:
        click.echo("Use either --cwd or --project, not both.", err=True)
        sys.exit(2)
    if current_cwd:
        from ..core.config import get_project_dir, project_is_opted_in

        cwd = Path.cwd()
        if not project_is_opted_in(cwd):
            click.echo("Not an opentraces project. Run 'opentraces init' first.", err=True)
            sys.exit(3)
        project = get_project_dir(cwd).name

    if force_rebuild:
        click.echo(
            "Search commands are read-only. Run 'opentraces trace index' to rebuild the search snapshot.",
            err=True,
        )
        sys.exit(2)

    try:
        packet = discover(
            topic,
            by=group_by.lower(),
            limit=limit,
            per_group=per_group,
            latest_generation=latest_generation,
            project=project,
        )
    except SearchSnapshotNeedsRebuild as exc:
        # Query verbs auto-rebuild the compact snapshot once before this fires
        # (issue #30); reaching here means the automatic rebuild itself failed.
        payload = {
            "status": "maintenance_needed",
            "reason": exc.reason,
            "advice": "opentraces trace index",
        }
        if as_json:
            click.echo(_dump_json(payload))
            sys.exit(3)
        click.echo(
            f"Trace search snapshot automatic rebuild failed ({exc.reason}). "
            "Run 'opentraces trace index'.",
            err=True,
        )
        sys.exit(3)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)

    payload = {
        "status": "ok",
        "discovery": packet.model_dump(mode="json"),
        "search_diagnostics": packet.search_diagnostics,
    }
    if as_json:
        click.echo(_dump_json(payload))
        return

    click.echo(f"{packet.topic}  {packet.total_cards} trace cards")
    for group in packet.groups:
        click.echo(f"{group.label}: {group.count}")
        for card in group.cards:
            click.echo(f"  {card.trace_id}  {card.provenance_color or 'unknown'}  {card.headline}")


@trace_group.command("query", cls=OpentracesCommand)
@click.argument("lex_terms", nargs=-1)
@click.option("--lex", default=None, help="Lexical query text.")
@click.option("--semantic", default=None, help="Semantic service/library query text.")
@click.option("--skill", default=None, help="Exact skill.name facet.")
@click.option("--tool", default=None, help="Exact tool.name facet.")
@click.option("--files", default=None, help="File glob filter over indexed paths.")
@click.option("--file-kind", default=None, help="File extension/kind filter.")
@click.option(
    "--file-op",
    type=click.Choice(["edit", "read"], case_sensitive=False),
    default=None,
    help="Derived file.operation filter (closed vocabulary).",
)
@click.option("--signal", default=None, help="Deterministic signal filter.")
@click.option("--facet", "facet_filters", multiple=True, help="Generic facet filter as name=value.")
@click.option("--metadata", "metadata_filters", multiple=True, help="Indexed unit metadata filter as key=value.")
@click.option("--provider", default=None, help="Exact provider.kind facet.")
@click.option("--cmd-family", default=None, help="Derived bash.command_family facet.")
@click.option(
    "--bash-action",
    type=click.Choice(["test", "service_probe"], case_sensitive=False),
    default=None,
    help="Derived bash.action facet (closed vocabulary).",
)
@click.option("--test", "test_framework", default=None, help="Derived test.framework facet.")
@click.option("--service", default=None, help="Derived service.name facet.")
@click.option("--service-channel", default=None, help="Derived service.channel facet.")
@click.option("--dependency", default=None, help="Exact dependency.name facet.")
@click.option("--git-tier", default=None, help="Exact git_link_tier facet.")
@click.option("--survival", default=None, help="Derived Trace Trail survival state.")
@click.option("--since", default=None, help="ISO date/time or duration such as 7d.")
@click.option(
    "--candidate-kind",
    type=click.Choice(
        [
            "bug_fix",
            "trace",
            "trace_map_node",
            "trace_slice",
            "trace_intent_candidate",
            "patch",
            "skill_invocation",
            "tool_sequence",
            "test_or_error_signal",
            "git_anchor",
        ],
        case_sensitive=False,
    ),
    default=None,
    help="Candidate label / unit-type escape hatch (closed vocabulary).",
)
@click.option("--success/--no-success", default=None, help="Filter outcome.success (explicit True/False).")
@click.option(
    "--unknown-success",
    is_flag=True,
    default=False,
    help="Match traces whose outcome.success is null/missing (mutually exclusive with --success/--no-success).",
)
@click.option("--committed/--uncommitted", default=None, help="Filter outcome.committed (explicit True/False).")
@click.option(
    "--unknown-committed",
    is_flag=True,
    default=False,
    help="Match traces whose outcome.committed is null/missing (mutually exclusive with --committed/--uncommitted).",
)
@click.option("--project", default=None, help="Project slug to search.")
@click.option("--cwd", "current_cwd", is_flag=True, help="Search only the current opted-in project.")
@click.option("--limit", type=int, default=20, show_default=True, help="Maximum candidates.")
@click.option("--page-token", default=None, help="Cursor token returned by the previous page.")
@click.option("--latest-generation/--include-superseded", default=True, help="Suppress older generations by default.")
@click.option(
    "--include-slice",
    type=click.Choice(["intent", "evidence"]),
    default=None,
    help="Embed a bounded Trace Map slice in each candidate.",
)
@click.option("--max-slice-nodes", type=int, default=40, show_default=True, help="Maximum nodes for --include-slice.")
@click.option(
    "--sort",
    "sort_order",
    type=click.Choice(["relevance", "time", "recency"]),
    default="relevance",
    show_default=True,
    help="Result order: relevance (score), time (oldest first), or recency (newest first).",
)
@click.option("--force-rebuild", is_flag=True, help="Rejected: search commands are read-only.")
@click.option(
    "--remote",
    "remote",
    default=None,
    help=(
        "Pull the configured private bucket remote and rebuild the search "
        "snapshot before querying (names the remote repo for the trail). "
        "Symmetric with `trace get --remote`."
    ),
)
@click.option(
    "--remote-bucket",
    is_flag=True,
    hidden=True,
    help="Deprecated alias for --remote (still works; uses the configured remote).",
)
@click.option(
    "--force-remote-bucket",
    is_flag=True,
    hidden=True,
    help="Allow the remote pull to overwrite a local-ahead or diverged bucket.",
)
@click.option(
    "--source",
    "query_source",
    type=click.Choice(["index", "projection"]),
    default="index",
    show_default=True,
    help="Local query source.",
)
@click.option("--vec", default=None, help="Reserved vector query mode.")
@click.option("--hyde", default=None, help="Reserved HyDE query mode.")
@click.option(
    "--fresh",
    is_flag=True,
    help=(
        "Strict freshness: fail with maintenance_needed if a fully up-to-date "
        "snapshot cannot be proven (no serve-stale fallback)."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_query(
    lex_terms: tuple[str, ...],
    lex: str | None,
    semantic: str | None,
    skill: str | None,
    tool: str | None,
    files: str | None,
    file_kind: str | None,
    file_op: str | None,
    signal: str | None,
    facet_filters: tuple[str, ...],
    metadata_filters: tuple[str, ...],
    provider: str | None,
    cmd_family: str | None,
    bash_action: str | None,
    test_framework: str | None,
    service: str | None,
    service_channel: str | None,
    dependency: str | None,
    git_tier: str | None,
    survival: str | None,
    since: str | None,
    candidate_kind: str | None,
    success: bool | None,
    unknown_success: bool,
    committed: bool | None,
    unknown_committed: bool,
    project: str | None,
    current_cwd: bool,
    limit: int,
    page_token: str | None,
    latest_generation: bool,
    include_slice: str | None,
    max_slice_nodes: int,
    sort_order: str,
    force_rebuild: bool,
    remote: str | None,
    remote_bucket: bool,
    force_remote_bucket: bool,
    query_source: str,
    vec: str | None,
    hyde: str | None,
    fresh: bool,
    as_json: bool,
) -> None:
    """Search local retained traces and return bounded candidate packets."""
    from ..core.trace_search_snapshot import (
        SearchFilters,
        SearchSnapshotNeedsRebuild,
        candidate_packet_for_hit,
        search_traces,
    )

    if lex_terms:
        if lex:
            click.echo("Use either positional search terms or --lex, not both.", err=True)
            sys.exit(2)
        if semantic:
            click.echo("Use either positional search terms or --semantic, not both.", err=True)
            sys.exit(2)
        lex = " ".join(lex_terms)
    if lex and semantic:
        click.echo("Use either --lex or --semantic, not both.", err=True)
        sys.exit(2)
    if vec or hyde:
        click.echo(
            "Vector and HyDE trace query modes are reserved in M1. "
            "Use --lex or exact filters, or provision a future vector index.",
            err=True,
        )
        raise click.exceptions.Exit(10)
    if current_cwd and project:
        click.echo("Use either --cwd or --project, not both.", err=True)
        sys.exit(2)
    if current_cwd:
        from ..core.config import get_project_dir, project_is_opted_in

        cwd = Path.cwd()
        if not project_is_opted_in(cwd):
            click.echo("Not an opentraces project. Run 'opentraces init' first.", err=True)
            sys.exit(3)
        project = get_project_dir(cwd).name
    if unknown_success and success is not None:
        click.echo(
            "Use either --success/--no-success or --unknown-success, not both.",
            err=True,
        )
        sys.exit(2)
    if unknown_committed and committed is not None:
        click.echo(
            "Use either --committed/--uncommitted or --unknown-committed, not both.",
            err=True,
        )
        sys.exit(2)
    if not any([
        lex,
        semantic,
        skill,
        tool,
        files,
        file_kind,
        file_op,
        signal,
        facet_filters,
        metadata_filters,
        provider,
        cmd_family,
        bash_action,
        test_framework,
        service,
        service_channel,
        dependency,
        git_tier,
        survival,
        since,
        candidate_kind,
        success is not None,
        unknown_success,
        committed is not None,
        unknown_committed,
        project,
    ]):
        click.echo(
            "Provide --lex, --skill, --tool, --files, --signal, --facet, "
            "--semantic, --metadata, named filters, --candidate-kind, --success, "
            "--committed, --unknown-success, --unknown-committed, --cwd, "
            "or --project.",
            err=True,
        )
        sys.exit(3)
    remote_bucket_payload = None
    # v7 L4: `--remote` is the advertised name; `--remote-bucket` is a hidden
    # deprecated alias that maps onto the same configured-remote pull path.
    want_remote_pull = bool(remote) or remote_bucket
    if force_remote_bucket and not want_remote_pull:
        click.echo("--force-remote-bucket requires --remote.", err=True)
        sys.exit(2)
    if force_rebuild:
        click.echo(
            "Search commands are read-only. Run 'opentraces trace index' to rebuild the search snapshot.",
            err=True,
        )
        sys.exit(2)
    if query_source != "index":
        click.echo(
            "--source is no longer a query-time lifecycle selector. "
            "Run 'opentraces trace index' to rebuild the search snapshot.",
            err=True,
        )
        sys.exit(2)
    if metadata_filters:
        click.echo(
            "--metadata filters are not part of the compact trace search snapshot yet.",
            err=True,
        )
        sys.exit(2)
    if candidate_kind not in (None, "trace", "bug_fix"):
        click.echo(
            "--candidate-kind is trace-level in the compact search snapshot. "
            "Use trace, bug_fix, or hydrate a specific trace with trace get/map.",
            err=True,
        )
        sys.exit(2)
    if want_remote_pull:
        try:
            from ._remote_bucket import pull_remote_bucket_for_trace

            remote_bucket_payload = pull_remote_bucket_for_trace(
                force=force_remote_bucket,
            )
            if remote:
                remote_bucket_payload["requested_repo"] = remote
        except Exception as exc:
            click.echo(f"Unable to read remote bucket: {exc}", err=True)
            sys.exit(3)

    try:
        page = search_traces(
            lex,
            SearchFilters(
                skill=skill,
                tool=tool,
                files=files,
                file_kind=file_kind,
                file_op=file_op,
                signal=signal,
                facet_filters=facet_filters,
                provider=provider,
                cmd_family=cmd_family,
                bash_action=bash_action,
                test_framework=test_framework,
                service=service,
                service_channel=service_channel,
                dependency=dependency,
                git_tier=git_tier,
                survival=survival,
                since=since,
                success=success,
                success_unknown=unknown_success,
                committed=committed,
                committed_unknown=unknown_committed,
                candidate_kind=candidate_kind,
                latest_generation=latest_generation,
                project=project,
                sort_order=sort_order,
            ),
            limit=limit,
            cursor=page_token,
            semantic=semantic,
            strict_freshness=fresh,
        )
    except SearchSnapshotNeedsRebuild as exc:
        # v7 V11: the search index self-maintains invisibly. When the corpus is
        # too large to build the cold snapshot inline, the guard (issue #124)
        # must NOT surface its internal `cold_build_too_large` sentinel to the
        # caller — the query just returns an honest empty result instead of
        # leaking an index-maintenance state (the audit-P2 fix). Genuine
        # self-heal failures (issue #30) and the explicit `--fresh` strict path
        # keep the maintenance_needed signal.
        if exc.reason == "cold_build_too_large":
            payload = _envelope(
                "opentraces.trace.query.v1",
                source="snapshot",
                sort=sort_order,
                semantic_query=None,
                total=0,
                total_returned=0,
                limit=limit,
                next_page_token=None,
                has_more=False,
                candidates=[],
                search_diagnostics={
                    "used_search_snapshot": False,
                    "used_fts": False,
                    "rows_examined": 0,
                    "hits_returned": 0,
                    "hydrated_count": 0,
                    "raw_trace_scan": False,
                    "wrote_to_index": False,
                    "rebuilt_index": False,
                    "python_full_corpus_sort": False,
                },
            )
            if remote_bucket_payload is not None:
                payload["remote_bucket"] = remote_bucket_payload
            if as_json:
                click.echo(_dump_json(payload))
            return
        # search_traces auto-rebuilds the compact snapshot once before raising
        # (issue #30); reaching here means the automatic rebuild itself failed
        # or `--fresh` could not prove freshness.
        payload = {
            "status": "maintenance_needed",
            "reason": exc.reason,
            "advice": "opentraces trace index",
            "search_diagnostics": {
                "used_search_snapshot": False,
                "used_fts": False,
                "rows_examined": 0,
                "hits_returned": 0,
                "hydrated_count": 0,
                "raw_trace_scan": False,
                "wrote_to_index": False,
                "rebuilt_index": False,
                "python_full_corpus_sort": False,
            },
        }
        if exc.path is not None:
            payload["path"] = str(exc.path)
        if as_json:
            click.echo(_dump_json(payload))
            sys.exit(3)
        click.echo(
            f"Trace search snapshot automatic rebuild failed ({exc.reason}). "
            "Run 'opentraces trace index'.",
            err=True,
        )
        sys.exit(3)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    candidates = []
    hydrated_count = 0
    for hit in page.hits:
        packet, hydrated = candidate_packet_for_hit(
            hit,
            include_slice=include_slice,
            max_slice_nodes=max_slice_nodes,
        )
        candidates.append(packet)
        hydrated_count += hydrated
    diagnostics = page.diagnostics.as_dict()
    diagnostics["hydrated_count"] = hydrated_count
    payload = _envelope(
        "opentraces.trace.query.v1",
        source="snapshot",
        sort=sort_order,
        semantic_query=None,
        total=page.total,
        total_returned=len(candidates),
        limit=limit,
        next_page_token=page.next_page_token,
        has_more=page.next_page_token is not None,
        candidates=[packet.model_dump(mode="json") for packet in candidates],
        search_diagnostics=diagnostics,
    )
    if remote_bucket_payload is not None:
        payload["remote_bucket"] = remote_bucket_payload
    # Issue #91: surface the served snapshot's freshness telemetry. On an
    # actively-capturing box a stale-but-servable snapshot now returns
    # status=ok + results + freshness.stale=true (serve last-known-good)
    # instead of dead-ending at maintenance_needed; a clean read reports
    # freshness.stale=false with build provenance. (This is the real writer
    # the old dead ``warnings`` block always wanted — keyed ``freshness``, not
    # the legacy ``trail_freshness``/``warnings`` names.)
    if page.freshness is not None:
        payload["freshness"] = page.freshness
    if semantic:
        from ..core.semantic import expand_semantic_query

        payload["semantic_query"] = expand_semantic_query(semantic)
    if as_json:
        click.echo(_dump_json(payload))
        return
    for packet in candidates:
        click.echo(f"{packet.trace_id}  {packet.title}")


@trace_group.command("skills", cls=OpentracesCommand, hidden=True)  # v7 7->4 collapse: folded into `trace query --skill`. Callable, off --help.
@click.option("--skill", default=None, help="Show one exact skill name.")
@click.option("--project", default=None, help="Project slug to search.")
@click.option("--cwd", "current_cwd", is_flag=True, help="Search only the current opted-in project.")
@click.option("--since", default=None, help="ISO date/time or duration such as 7d.")
@click.option("--limit", type=int, default=50, show_default=True, help="Maximum skills.")
@click.option("--page-token", default=None, help="Cursor token returned by the previous page.")
@click.option("--latest-generation/--include-superseded", default=True, help="Suppress older generations by default.")
@click.option("--force-rebuild", is_flag=True, help="Rejected: search commands are read-only.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_skills(
    skill: str | None,
    project: str | None,
    current_cwd: bool,
    since: str | None,
    limit: int,
    page_token: str | None,
    latest_generation: bool,
    force_rebuild: bool,
    as_json: bool,
) -> None:
    """List skills observed in retained traces, ranked by usage."""
    from ..core.trace_search_snapshot import (
        SearchFilters,
        SearchSnapshotNeedsRebuild,
        list_skill_usage,
    )

    if current_cwd and project:
        click.echo("Use either --cwd or --project, not both.", err=True)
        sys.exit(2)
    if current_cwd:
        from ..core.config import get_project_dir, project_is_opted_in

        cwd = Path.cwd()
        if not project_is_opted_in(cwd):
            click.echo("Not an opentraces project. Run 'opentraces init' first.", err=True)
            sys.exit(3)
        project = get_project_dir(cwd).name
    if force_rebuild:
        click.echo(
            "Search commands are read-only. Run 'opentraces trace index' to rebuild the search snapshot.",
            err=True,
        )
        sys.exit(2)

    started = time.monotonic()
    try:
        page = list_skill_usage(
            SearchFilters(
                skill=skill,
                project=project,
                since=since,
                latest_generation=latest_generation,
            ),
            limit=limit,
            cursor=page_token,
        )
    except SearchSnapshotNeedsRebuild as exc:
        # v7 V11 / audit-P2: the skill facet self-maintains invisibly. When the
        # corpus is too large to build the cold snapshot inline, NEVER surface
        # the internal `cold_build_too_large` sentinel — return an honest empty
        # "no skills" result (this hidden verb is folded into `trace query
        # --skill`, and both must be sentinel-free). Genuine self-heal failures
        # keep the maintenance_needed signal.
        if exc.reason == "cold_build_too_large":
            empty = {
                "status": "ok",
                "source": "snapshot",
                "total_skills": 0,
                "total_invocations": 0,
                "limit": limit,
                "next_page_token": None,
                "has_more": False,
                "skills": [],
                "search_diagnostics": {
                    "used_search_snapshot": False,
                    "used_fts": False,
                    "rows_examined": 0,
                    "hits_returned": 0,
                    "hydrated_count": 0,
                    "raw_trace_scan": False,
                    "wrote_to_index": False,
                    "rebuilt_index": False,
                    "python_full_corpus_sort": False,
                },
                "telemetry": {"duration_ms": round((time.monotonic() - started) * 1000, 2)},
            }
            if as_json:
                click.echo(_dump_json(empty))
                return
            click.echo("No skill invocations found.")
            return
        payload = {
            "status": "maintenance_needed",
            "reason": exc.reason,
            "advice": "opentraces trace index",
            "search_diagnostics": {
                "used_search_snapshot": False,
                "used_fts": False,
                "rows_examined": 0,
                "hits_returned": 0,
                "hydrated_count": 0,
                "raw_trace_scan": False,
                "wrote_to_index": False,
                "rebuilt_index": False,
                "python_full_corpus_sort": False,
            },
        }
        if exc.path is not None:
            payload["path"] = str(exc.path)
        if as_json:
            click.echo(_dump_json(payload))
            sys.exit(3)
        click.echo(
            f"Trace search snapshot automatic rebuild failed ({exc.reason}). "
            "Run 'opentraces trace index'.",
            err=True,
        )
        sys.exit(3)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)

    payload = {
        "status": "ok",
        "source": "snapshot",
        "total_skills": page.total_skills,
        "total_invocations": page.total_invocations,
        "limit": limit,
        "next_page_token": page.next_page_token,
        "has_more": page.next_page_token is not None,
        "skills": [item.as_dict() for item in page.skills],
        "search_diagnostics": page.diagnostics.as_dict(),
        "telemetry": {"duration_ms": round((time.monotonic() - started) * 1000, 2)},
    }
    if as_json:
        click.echo(_dump_json(payload))
        return

    if not page.skills:
        click.echo("No skill invocations found.")
        return
    for item in page.skills:
        click.echo(f"{item.skill_name}\t{item.invocation_count}\t{item.trace_count}")


@trace_group.group("index", cls=OpentracesGroup, invoke_without_command=True, hidden=True)  # v7 7->4 collapse: index self-maintains behind query; callable plumbing, off --help.
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@progress_option
@click.pass_context
def trace_index_group(ctx: click.Context, as_json: bool, progress_mode: str) -> None:
    """Rebuild and inspect the local trace search snapshot."""
    if ctx.invoked_subcommand is None:
        _trace_index_rebuild_impl(as_json, progress_mode=progress_mode)


@trace_index_group.command("rebuild", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--legacy",
    "rebuild_legacy",
    is_flag=True,
    help="Also rebuild the optional legacy Trace Index (trail-enriched map/get/slice); the default path already serves map/get/slice from the bucket.",
)
@progress_option
def trace_index_rebuild_cmd(as_json: bool, rebuild_legacy: bool, progress_mode: str) -> None:
    """Rebuild the local read-only trace search snapshot."""
    _trace_index_rebuild_impl(
        as_json, rebuild_legacy=rebuild_legacy, progress_mode=progress_mode
    )


def _trace_index_rebuild_impl(
    as_json: bool,
    *,
    rebuild_legacy: bool = False,
    progress_mode: str = "auto",
) -> None:
    """Rebuild the read-only search snapshot.

    The default path is intentionally snapshot-only: it reads retained bucket /
    legacy trace records directly and never tries to converge the legacy Trace
    Index cache first. map/get/slice serve from the bucket, so the legacy Trace
    Index is optional; building it via ``--legacy`` is an explicit operator
    action for trail-enriched legacy consumers only, because it can be multi-GB
    and minutes-long on an upgraded dev box.
    """
    from ..core.trace_index import (
        default_index_path,
        rebuild_index,
    )
    from ..core.trace_search_snapshot import build_trace_search_snapshot
    from ._progress import build_cli_progress

    started = time.monotonic()
    healed_legacy_index = False
    legacy_rebuild_forced = False
    legacy_index_missing = not default_index_path().exists()
    legacy_bootstrap_duration_ms = None
    legacy_rebuild_summary = None
    # Shared progress/heartbeat contract (issue #88). The reporter is built at
    # the TOP — BEFORE the --legacy branch — so its background heartbeat covers
    # the command's slowest mode (the blocking legacy ``rebuild_index()``), not
    # just the snapshot build. The sink is stderr-only (click.echo(err=True)),
    # so the stdout --json payload below stays a single clean object.
    # traces_total is seeded CLI-side; core reports only traces_seen.
    total_traces = _approx_bootstrap_trace_count()
    reporter = build_cli_progress("trace index rebuild", progress_mode)
    reporter.set_total(traces_total=total_traces)
    try:
        if rebuild_legacy:
            click.echo(
                "Rebuilding legacy Trace Index explicitly "
                f"(~{total_traces} traces). This can take several minutes on a "
                "large bucket; no output until it completes.",
                err=True,
            )
            _bootstrap_started = time.monotonic()
            # Beat the heartbeat through the long blocking legacy rebuild too.
            reporter.stage("rebuilding_legacy_index", traces_total=total_traces)
            summary = rebuild_index(default_index_path())
            legacy_bootstrap_duration_ms = round(
                (time.monotonic() - _bootstrap_started) * 1000,
                2,
            )
            click.echo(
                f"Legacy Trace Index rebuild done in "
                f"{time.monotonic() - _bootstrap_started:.1f}s "
                f"(~{total_traces} traces).",
                err=True,
            )
            healed_legacy_index = True
            legacy_rebuild_forced = True
            legacy_rebuild_summary = {
                "trace_count": summary.trace_count,
                "unit_count": summary.unit_count,
                "map_node_count": summary.map_node_count,
            }
        snapshot_started = time.monotonic()
        search_summary = build_trace_search_snapshot(progress=reporter)
        snapshot_build_duration_ms = round((time.monotonic() - snapshot_started) * 1000, 2)
    finally:
        reporter.done()
    payload = {
        "status": "ok",
        "search_snapshot": search_summary.as_dict(),
        "keep_warm": {
            "ok": True,
            "synced": False,
            "changed": 0,
            "deleted": 0,
            "maintenance_required": [],
            "skipped": True,
            "reason": "snapshot_rebuild_reads_trace_records_directly",
        },
        "legacy_index": {
            "path": str(default_index_path()),
            "healed": healed_legacy_index,
            "forced": legacy_rebuild_forced,
            "missing": legacy_index_missing,
            "summary": legacy_rebuild_summary,
            "advice": (
                "opentraces trace index rebuild --legacy"
                if legacy_index_missing and not rebuild_legacy
                else None
            ),
        },
        "telemetry": {
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "keep_warm_duration_ms": 0,
            "snapshot_build_duration_ms": snapshot_build_duration_ms,
            "legacy_bootstrap_duration_ms": legacy_bootstrap_duration_ms,
            # Additive per-stage progress telemetry (issue #88). Nested under the
            # existing top-level "telemetry" key, so the json-surface sweep
            # (top-level-keys-only) is unaffected.
            "stages": reporter.telemetry(),
        },
    }
    if as_json:
        click.echo(_dump_json(payload))
        return

    click.echo(f"Search snapshot rebuilt: {search_summary.path}")
    click.echo(f"  traces:    {search_summary.trace_count}")
    if legacy_index_missing and not rebuild_legacy:
        click.echo(
            "Legacy Trace Index is not built. map/get/slice serve from the bucket "
            "and do not need it; build it with `opentraces trace index rebuild "
            "--legacy` only for the optional trail-enriched legacy index.",
            err=True,
        )


@trace_index_group.command("refresh", cls=OpentracesCommand)
@click.option(
    "--source",
    "query_source",
    type=click.Choice(["index", "projection", "both"]),
    default="both",
    show_default=True,
    help="Which warm cache to keep up to date.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_index_refresh_cmd(query_source: str, as_json: bool) -> None:
    """Keep the warm Trace Index + search projection up to date (cheap sync).

    The explicit companion to the best-effort keep-warm hooks: runs the
    digest-gated incremental sync so freshly captured traces become queryable
    WITHOUT a full ``trace index rebuild``. Steady state (no bucket change since
    the last sync) is a sub-2s no-op.
    """
    from ..core.trace_index import keep_index_warm

    sources = ("index", "projection") if query_source == "both" else (query_source,)
    result = keep_index_warm(query_sources=sources)
    status = "ok" if result.ok else "error"
    if result.maintenance_required:
        status = "maintenance_required"
    payload = {
        "status": status,
        "synced": result.synced,
        "changed_trace_ids": result.changed_trace_ids,
        "deleted_trace_ids": result.deleted_trace_ids,
        "sources": list(sources),
    }
    if result.maintenance_required:
        payload["maintenance_required"] = result.maintenance_required
        payload["refused_full_rebuild"] = True
        payload["advice"] = "opentraces trace index rebuild --legacy"
    if result.error:
        payload["error"] = result.error
    if as_json:
        click.echo(_dump_json(payload))
        if result.maintenance_required:
            sys.exit(2)
        return

    if not result.ok:
        click.echo(f"Keep-warm failed (best-effort): {result.error}")
        return
    if result.maintenance_required:
        click.echo(
            "Search caches need explicit Trace Index maintenance; "
            "refresh refused to run a full rebuild implicitly."
        )
        click.echo("  next: opentraces trace index rebuild --legacy")
        sys.exit(2)
    if result.synced:
        click.echo("Search caches refreshed (incremental sync):")
        click.echo(f"  changed: {len(result.changed_trace_ids)}")
        click.echo(f"  deleted: {len(result.deleted_trace_ids)}")
    else:
        click.echo("Search caches already fresh (no change since last sync).")


@trace_index_group.command("status", cls=OpentracesCommand)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
@click.option(
    "--verbose",
    is_flag=True,
    help="Include the full per-table byte breakdown in --json output.",
)
def trace_index_status_cmd(as_json: bool, verbose: bool) -> None:
    """Show local trace search snapshot status."""
    from ..core.trace_index import default_index_path
    from ..core.trace_search_snapshot import default_snapshot_path, snapshot_status

    search_snapshot = snapshot_status()
    # Issue #27 item A: per-table byte breakdown for BOTH on-disk SQLite DBs so
    # the multi-GB bloat (issue #22 hypothesis 3) is measurable. Read-only.
    db_sizes = {
        "legacy_index": _db_table_sizes(default_index_path()),
        "search_snapshot": _db_table_sizes(default_snapshot_path()),
    }
    payload = {
        "status": "ok",
        "search_snapshot": search_snapshot,
        # Agent-facing default is aggregate scalars only (envelope budget);
        # --verbose restores the per-table map, and the text render below
        # always shows it.
        "db_sizes": (
            db_sizes
            if verbose
            else {label: _compact_db_entry(entry) for label, entry in db_sizes.items()}
        ),
    }
    if as_json:
        click.echo(_dump_json(payload))
        return

    click.echo(f"Search snapshot: {search_snapshot.get('state')}")
    click.echo(f"  path:   {search_snapshot.get('path')}")
    if search_snapshot.get("trace_count") is not None:
        click.echo(f"  traces: {search_snapshot.get('trace_count')}")
    click.echo(f"  dirty:  {search_snapshot.get('dirty')}")
    click.echo(f"  wal:    {search_snapshot.get('wal_exists')}")
    click.echo(f"  shm:    {search_snapshot.get('shm_exists')}")
    for label, entry in db_sizes.items():
        if not entry.get("exists"):
            continue
        size_bytes = entry.get("size_bytes")
        click.echo(f"{label}: {entry.get('path')}")
        if size_bytes is not None:
            click.echo(f"  size:   {size_bytes} bytes")
        tables = entry.get("tables")
        if tables is None:
            click.echo(f"  tables: unavailable ({entry.get('tables_error', 'n/a')})")
        else:
            for name, stat in tables.items():
                click.echo(f"  table {name}: {stat['bytes']} bytes ({stat['pages']} pages)")


@trace_index_group.command("compact", cls=OpentracesCommand)
@click.option(
    "--vacuum",
    is_flag=True,
    help=(
        "Run a one-time SQLite VACUUM to reclaim freelist space to the OS. "
        "VACUUM rewrites the whole DB into a temp copy, so it needs free disk "
        ">= the current DB file size and can take minutes on a multi-GB file. "
        "Refused (with a reason in the report) when free disk is insufficient."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_index_compact_cmd(vacuum: bool, as_json: bool) -> None:
    """Reclaim legacy Trace Index space (issue #40).

    Deletes superseded-generation unit bodies in short incremental
    transactions (safe to run while live capture mutates the DB), folds the
    WAL back into the main file, and — only with ``--vacuum`` and sufficient
    free disk — runs a VACUUM to return freelist pages to the OS. Reports the
    per-table ``db_sizes`` breakdown before and after.
    """
    from ..core.trace_index import compact_index, default_index_path

    db_path = default_index_path()
    sizes_before = _db_table_sizes(db_path)
    try:
        summary = compact_index(db_path, vacuum=vacuum)
    except Exception as exc:  # IndexLockedError or sqlite error → typed exit.
        click.echo(f"Trace index compact failed: {exc}", err=True)
        sys.exit(3)
    sizes_after = _db_table_sizes(db_path)
    payload = {
        "status": "ok",
        "compact": summary.as_dict(),
        "db_sizes_before": sizes_before,
        "db_sizes_after": sizes_after,
    }
    if as_json:
        click.echo(_dump_json(payload))
        return

    data = summary.as_dict()
    click.echo(f"Trace index compact: {data['index_path']}")
    click.echo(f"  purged units:   {data['purged_units']}")
    before = data["size_bytes_before"]
    after = data["size_bytes_after"]
    if before is not None and after is not None:
        reclaimed = before - after
        click.echo(f"  size:           {before} -> {after} bytes ({reclaimed:+d})")
    click.echo(
        f"  freelist:       {data['freelist_before']} -> {data['freelist_after']}"
    )
    if data["vacuumed"]:
        click.echo("  vacuum:         ran")
    elif data["vacuum_skipped_reason"]:
        click.echo(f"  vacuum:         skipped ({data['vacuum_skipped_reason']})")
    elif not vacuum:
        click.echo(
            "  vacuum:         not requested "
            "(pass --vacuum to return freelist pages to the OS)"
        )


@trace_group.command("map", cls=OpentracesCommand)
@click.argument("target")
@click.option("--candidate", default=None, help="Candidate unit or map node to expand around.")
@click.option("--around", default=None, help="Map node or unit to show a local neighborhood around.")
@click.option("--depth", type=int, default=2, show_default=True, help="Neighborhood depth for --around.")
@click.option("--from-node", default=None, help="Map node or unit where a directional walk starts.")
@click.option("--walk", type=click.Choice(["back", "forward"]), default=None, help="Walk direction for --from-node.")
@click.option("--until", "until_actions", multiple=True, help="Action type that stops --walk.")
@click.option("--max-steps", type=int, default=40, show_default=True, help="Maximum nodes in candidate slice.")
@click.option(
    "--actions",
    "actions_filter",
    default=None,
    help=(
        "Comma-separated action types to keep (compactness filter). "
        "Canonical lineage subset: "
        "user_instruction,file_edit,patch_created,git_anchor,test_run,error_signal,final_response."
    ),
)
@click.option(
    "--bursts",
    "as_bursts",
    is_flag=True,
    help="Project the map as `change_burst` aggregate nodes (one per cluster).",
)
@click.option(
    "--burst-gap",
    "burst_gap",
    type=int,
    default=None,
    help="Step-index gap between adjacent edits within a burst (default 35).",
)
@click.option(
    "--no-commit-lookup",
    "no_commit_lookup",
    is_flag=True,
    help=(
        "Skip the per-burst `git log` lookup (commit subject + body). "
        "Useful for offline runs and hot CLI paths that don't need the prose."
    ),
)
@click.option(
    "--waste",
    "as_waste",
    is_flag=True,
    help="Emit the context-waste findings for the trace (opentraces.context_waste.v2).",
)
@click.option(
    "--large-output-chars",
    type=int,
    default=None,
    help="With --waste: override the large-output threshold (default 12000).",
)
@click.option(
    "--file-read-window-min",
    type=int,
    default=None,
    help="With --waste: override the repeated-file-read window in minutes (default 20).",
)
@click.option(
    "--search-window-min",
    type=int,
    default=None,
    help="With --waste: override the repeated-search window in minutes (default 10).",
)
@click.option(
    "--run-intel",
    "as_run_intel",
    is_flag=True,
    help="Emit deterministic resteer/recovery/loop signals for the trace (opentraces.run_intel.v1).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_map_cmd(
    target: str,
    candidate: str | None,
    around: str | None,
    depth: int,
    from_node: str | None,
    walk: str | None,
    until_actions: tuple[str, ...],
    max_steps: int,
    actions_filter: str | None,
    as_bursts: bool,
    burst_gap: int | None,
    no_commit_lookup: bool,
    as_waste: bool,
    large_output_chars: int | None,
    file_read_window_min: int | None,
    search_window_min: int | None,
    as_run_intel: bool,
    as_json: bool,
) -> None:
    """Show a deterministic Trace Map or bounded candidate slice."""
    from ..core.bursts import DEFAULT_BURST_GAP, bursts_to_trace_map, detect_bursts
    from ..core.trace_index import get_trace_map
    from ..core.trace_map import (
        filter_trace_map_actions,
        slice_trace_map_for_candidate,
        trace_map_around,
        walk_trace_map,
    )

    if sum(bool(v) for v in (as_bursts, as_waste, as_run_intel)) > 1:
        click.echo("Use only one of --bursts, --waste, or --run-intel.", err=True)
        sys.exit(2)

    # --waste / --run-intel route through the same one-shot impl helpers as
    # `trace get`, so the two surfaces emit byte-identical payloads.
    if as_waste:
        _trace_get_waste_impl(
            target,
            as_json,
            large_output_chars=large_output_chars,
            file_read_window_min=file_read_window_min,
            search_window_min=search_window_min,
        )
        return
    if as_run_intel:
        _trace_get_run_intel_impl(target, as_json)
        return

    trace_id = _trace_id_from_ref(target)
    trace_map = get_trace_map(trace_id)
    if trace_map is None:
        click.echo(f"Trace Map not found: {target}", err=True)
        sys.exit(6)

    selected = trace_map
    candidate_node_id = None
    if sum(bool(value) for value in (candidate, around, from_node)) > 1:
        click.echo("Use only one of --candidate, --around, or --from-node.", err=True)
        sys.exit(2)
    if candidate:
        candidate_node_id = _candidate_node_id(trace_map, candidate)
        if candidate_node_id is None:
            click.echo(f"Candidate not found in Trace Map: {candidate}", err=True)
            sys.exit(6)
        selected = slice_trace_map_for_candidate(
            trace_map,
            candidate_node_id,
            max_steps=max_steps,
        )
    elif around:
        candidate_node_id = _candidate_node_id(trace_map, around)
        if candidate_node_id is None:
            click.echo(f"Node not found in Trace Map: {around}", err=True)
            sys.exit(6)
        selected = trace_map_around(trace_map, candidate_node_id, depth=depth)
    elif from_node:
        if walk is None:
            click.echo("--from-node requires --walk back|forward.", err=True)
            sys.exit(2)
        candidate_node_id = _candidate_node_id(trace_map, from_node)
        if candidate_node_id is None:
            click.echo(f"Node not found in Trace Map: {from_node}", err=True)
            sys.exit(6)
        selected = walk_trace_map(
            trace_map,
            candidate_node_id,
            direction=walk,
            until_action_types=set(until_actions),
            max_steps=max_steps,
        )
    elif walk:
        click.echo("--walk requires --from-node.", err=True)
        sys.exit(2)

    if as_bursts:
        gap = burst_gap if burst_gap is not None else DEFAULT_BURST_GAP
        # Best-effort: load the underlying TraceRecord so the burst pass
        # can mine the hook trail for git commit transitions and pull
        # the full user-instruction text (the trace map's text_preview
        # is truncated for compact listing). Failure is non-fatal —
        # we fall back to record=None which still produces correct
        # bursts, just without intent.commit_subject / commit_body.
        record = _try_load_trace_record(trace_id)
        bursts = detect_bursts(
            selected,
            gap=gap,
            trace_record=record,
            commit_lookup=not no_commit_lookup,
        )
        selected = bursts_to_trace_map(selected, bursts)
    elif actions_filter:
        keep = {part.strip() for part in actions_filter.split(",") if part.strip()}
        if keep:
            selected = filter_trace_map_actions(selected, keep)

    selector_active = bool(candidate or around or from_node or as_bursts or actions_filter)
    if selector_active:
        # A selector narrowed the map — the sub-map is already bounded by the
        # selector; emit it verbatim, wrapped in the L5 envelope, and still end
        # in a runnable slice handoff for the narrowed span.
        handoff = _map_handoff(selected, trace_id)
        payload = _envelope(
            "opentraces.trace.map.v1",
            trace_id=trace_id,
            candidate_node_id=candidate_node_id,
            map=selected.model_dump(mode="json"),
            handoff=handoff,
        )
        if as_json:
            click.echo(_dump_json(payload))
            return
        for node in selected.nodes:
            click.echo(f"{node.node_id}  {node.action_type}  {node.text_preview or ''}")
        click.echo(f"next: opentraces {handoff['command']}")
        return

    # Default whole-trace browse — bounded faceted view (v7 V1: never O(nodes)).
    record = _try_load_trace_record(trace_id)
    view = _bounded_map_view(selected, trace_id=trace_id, record=record)
    payload = _envelope("opentraces.trace.map.v1", trace_id=trace_id, **view)
    if as_json:
        click.echo(_dump_json(payload))
        return
    span = view["scope"]["span"]
    click.echo(
        f"trace {trace_id}  steps {span['from_step']}..{span['to_step']}  "
        f"nodes {view['scope']['node_count']}"
    )
    click.echo("TYPE:")
    for atype, count in view["facets"]["type"].items():
        click.echo(f"  {count:>4}  {atype}")
    if view["facets"]["file"]:
        click.echo("FILE:")
        for fpath, count in view["facets"]["file"].items():
            click.echo(f"  {count:>4}  {fpath}")
    click.echo(f"SECTIONS ({view['section_total']}):")
    for sec in view["sections"]:
        click.echo(f"  @{sec['step']}  {sec['preview']}")
    if view["section_total"] > len(view["sections"]):
        click.echo(f"  … +{view['section_total'] - len(view['sections'])} more")
    click.echo(f"LANDMARKS ({view['landmark_total']}, relevance-capped):")
    for lm in view["landmarks"]:
        click.echo(f"  @{lm['step']}  {lm['type']}  {lm['preview']}")
    click.echo(f"next: opentraces {view['handoff']['command']}")


@trace_group.command("slice", cls=OpentracesCommand)
@click.argument("target")
@click.option(
    "--by",
    "slicer_by",
    type=click.Choice(["user-turn", "change-burst", "milestone", "subgoal"]),
    default=None,
    help=(
        "Tile the WHOLE trace into a Trajectory array (opentraces.slicing.v1): "
        "user-turn/change-burst (deterministic); milestone/subgoal (cheap-LLM). "
        "Absorbs the former `trace partition`."
    ),
)
@click.option(
    "--answers",
    "answers_file",
    default=None,
    help="With --by milestone/subgoal: JSON file of cheap-LLM JudgmentAnswers.",
)
@click.option(
    "--judge",
    type=click.Choice(["deterministic", "agent", "provider", "human"]),
    default="agent",
    show_default=True,
    help="With --by milestone/subgoal: judge backend. 'agent' uses the rc=10 handshake.",
)
@click.option("--from-step", "from_step", type=int, default=None, help="First step index in a manual slice.")
@click.option("--to-step", "to_step", type=int, default=None, help="Last step index in a manual slice.")
@click.option("--around-step", "around_step", type=int, default=None, help="Create a slice around one step.")
@click.option("--around-patch", "around_patch", default=None, help="Create a slice around a patch id, map node, or trace-patch id.")
@click.option("--radius", type=int, default=3, show_default=True, help="Step radius for --around-step/--around-patch.")
@click.option(
    "--template",
    type=click.Choice(["bursts"], case_sensitive=False),
    default=None,
    help="Built-in deterministic slicing strategy.",
)
@click.option(
    "--burst-gap",
    "burst_gap",
    type=int,
    default=None,
    help="With --template bursts: step-index gap between adjacent edits (default 35).",
)
@click.option(
    "--no-commit-lookup",
    "no_commit_lookup",
    is_flag=True,
    help="With --template bursts: skip the per-burst `git log` lookup.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_slice_cmd(
    target: str,
    slicer_by: str | None,
    answers_file: str | None,
    judge: str,
    from_step: int | None,
    to_step: int | None,
    around_step: int | None,
    around_patch: str | None,
    radius: int,
    template: str | None,
    burst_gap: int | None,
    no_commit_lookup: bool,
    as_json: bool,
) -> None:
    """Extract deterministic Trace Slices, or tile the whole trace with --by."""
    from ..core.bursts import DEFAULT_BURST_GAP
    from ..core.trace_index import get_trace_map
    from ..core.trace_slices import (
        slice_around_patch,
        slice_around_step,
        slice_by_steps,
        slices_from_bursts,
    )

    manual_range = from_step is not None or to_step is not None

    # --by tiles the WHOLE trace via the unified slicer engine (absorbs the
    # former `trace partition`); it is mutually exclusive with the windowing
    # modes and emits the FROZEN opentraces.slicing.v1 envelope untouched.
    if slicer_by is not None:
        if manual_range or around_step is not None or around_patch or template:
            click.echo(
                "--by tiles the whole trace and cannot be combined with "
                "--template/--from-step/--to-step/--around-step/--around-patch.",
                err=True,
            )
            sys.exit(2)
        _run_slicing_partition(
            target,
            _SLICE_BY_TO_SLICER[slicer_by],
            answers_file=answers_file,
            judge=judge,
            remote=None,
            as_json=as_json,
        )
        return

    if manual_range and (from_step is None or to_step is None):
        click.echo("Use --from-step and --to-step together.", err=True)
        sys.exit(2)
    mode_count = sum(bool(value) for value in (manual_range, around_step is not None, around_patch, template))
    if mode_count != 1:
        click.echo(
            "Choose exactly one slice mode: --by, --template, --from-step/--to-step, "
            "--around-step, or --around-patch.",
            err=True,
        )
        sys.exit(2)

    trace_id = _trace_id_from_ref(target)
    trace_map = get_trace_map(trace_id)
    if trace_map is None:
        click.echo(f"Trace Map not found: {target}", err=True)
        sys.exit(6)
    record = _try_load_trace_record(trace_id)

    try:
        if template:
            gap = burst_gap if burst_gap is not None else DEFAULT_BURST_GAP
            slices = slices_from_bursts(
                trace_map,
                record,
                gap=gap,
                commit_lookup=not no_commit_lookup,
            )
            payload = {
                "status": "ok",
                "trace_id": trace_id,
                "mode": "template",
                "template": template,
                "burst_gap": gap,
                "slices": slices,
            }
        elif around_step is not None:
            payload = {
                "status": "ok",
                "trace_id": trace_id,
                "mode": "around_step",
                "slices": [
                    slice_around_step(
                        trace_map,
                        record,
                        step_index=around_step,
                        radius=radius,
                    )
                ],
            }
        elif around_patch:
            payload = {
                "status": "ok",
                "trace_id": trace_id,
                "mode": "around_patch",
                "slices": [
                    slice_around_patch(
                        trace_map,
                        record,
                        patch_ref=around_patch,
                        radius=radius,
                    )
                ],
            }
        else:
            assert from_step is not None
            assert to_step is not None
            payload = {
                "status": "ok",
                "trace_id": trace_id,
                "mode": "manual_step_range",
                "slices": [
                    slice_by_steps(
                        trace_map,
                        record,
                        start_step_index=from_step,
                        end_step_index=to_step,
                    )
                ],
            }
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)

    payload = _envelope(
        "opentraces.trace.slice.v1",
        **{k: v for k, v in payload.items() if k != "status"},
    )
    if as_json:
        click.echo(_dump_json(payload))
        return
    for item in payload["slices"]:
        click.echo(
            f"{item['slice_id']}  steps {item['start_step_index']}..{item['end_step_index']}  "
            f"nodes={len(item['map']['nodes'])}  patches={len(item['trace_patch_refs'])}"
        )


@trace_group.command("partition", cls=OpentracesCommand, hidden=True)  # v7 7->4 collapse: absorbed by `trace slice --by`. Callable (slicer-conformance journey), off --help.
@click.argument("ref")
@click.option(
    "--by",
    "slicer",
    type=click.Choice(["s1", "s2", "s3", "s4"]),
    required=True,
    help="Slicer: s1 user-turn, s2 change-burst (deterministic); s3 milestone, s4 subgoal (cheap-LLM).",
)
@click.option(
    "--answers",
    "answers_file",
    default=None,
    help="JSON file of cheap-LLM JudgmentAnswers ({\"answers\": [{id, decision, confidence}]}).",
)
@click.option(
    "--judge",
    type=click.Choice(["deterministic", "agent", "provider", "human"]),
    default="agent",
    show_default=True,
    help="Judge backend for the cheap-LLM step (S3/S4). 'agent' uses the rc=10 handshake.",
)
@click.option(
    "--remote",
    "remote",
    default=None,
    help="Read the trace from a HuggingFace bucket repo instead of the local bucket.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the opentraces.slicing.v1 envelope as JSON.")
def trace_partition_cmd(
    ref: str,
    slicer: str,
    answers_file: str | None,
    judge: str,
    remote: str | None,
    as_json: bool,
) -> None:
    """Partition a trace into a tiling array of Trajectories (opentraces.slicing.v1).

    ``slice(trace, slicer) -> Trajectory[]`` — the array tiles the whole trace.
    For the cheap-LLM slicers (s3/s4) with ``--judge agent`` (the default), if
    judgments are needed and none are supplied the command prints the
    JudgmentRequests + an instruction and exits ``rc=10``; answer them, write an
    ``--answers`` file, and re-run for the final tiled result at ``rc=0``.
    """
    _run_slicing_partition(
        ref,
        slicer,
        answers_file=answers_file,
        judge=judge,
        remote=remote,
        as_json=as_json,
    )


# Map `trace slice --by <name>` to the frozen slicer ids (v7: slice absorbs
# partition; the envelope SHAPE — opentraces.slicing.v1 — is what's frozen, not
# the verb name, so the rename is safe).
_SLICE_BY_TO_SLICER = {
    "user-turn": "s1",
    "change-burst": "s2",
    "milestone": "s3",
    "subgoal": "s4",
}


def _run_slicing_partition(
    ref: str,
    slicer: str,
    *,
    answers_file: str | None,
    judge: str,
    remote: str | None,
    as_json: bool,
) -> None:
    """Shared slicer engine for `trace partition` and `trace slice --by`.

    Emits the FROZEN ``opentraces.slicing.v1`` envelope and honors the
    ``rc=10 needs-judgment -> rc=0`` agent-loop handshake. This body is byte
    -identical between the two entry points (the slicer-conformance SENTINEL
    rides ``trace partition``; ``trace slice --by`` reuses the same engine).
    """
    from ..core import slicing

    trace_id = _trace_id_from_ref(ref)
    try:
        if remote:
            record = _read_trace_record_via_backend(trace_id, remote)
        else:
            record = _try_load_trace_record(trace_id)
    except _BackendUnavailable as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)
    if record is None:
        click.echo(f"Trace not found: {ref}", err=True)
        sys.exit(6)

    answers = None
    if answers_file:
        try:
            answers = slicing.load_answers(answers_file)
        except (OSError, ValueError, KeyError) as exc:
            click.echo(f"Could not read --answers file: {exc}", err=True)
            sys.exit(2)

    from ..core.slicing.labeler import provider_labeler

    rc, env = slicing.partition_trace(
        trace_id=record.trace_id,
        slicer_name=slicer,
        steps=record.steps,
        judge=judge,
        answers=answers,
        labeler=provider_labeler(),  # model-authored S3/S4 labels when a model is reachable
    )

    if as_json:
        click.echo(_dump_json(env))
        sys.exit(rc)

    if rc == slicing.RC_NEEDS_JUDGMENT:
        click.echo(
            f"needs-judgment: {slicer} needs {len(env['judgment_requests'])} cheap-LLM "
            f"judgment(s) to finalize boundaries (trace {short_trace_id(record.trace_id)}).",
            err=True,
        )
        for r in env["judgment_requests"]:
            click.echo(f"  [{r['id']}] {r['kind']} @step {r['candidate_step']}: {r['prompt']}", err=True)
        click.echo("\n" + env["instruction"], err=True)
        sys.exit(rc)

    t = env["tiling"]
    click.echo(
        f"{slicer} ({env['tier']}): {len(env['trajectories'])} trajectories over "
        f"{env['total_steps']} steps — coverage {t['coverage']} gaps {t['gaps']} overlaps {t['overlaps']}"
    )
    for traj in env["trajectories"]:
        click.echo(f"  [{traj['start']:>4}..{traj['end']:<4}] {traj['kind']:12} {traj['label']}")
    sys.exit(rc)


@trace_group.command("get", cls=OpentracesCommand)
@click.argument("ref")
@click.option(
    "--resume",
    "resume",
    is_flag=True,
    help="Hand control back to the native/upstream agent runtime for this trace.",
)
@click.option(
    "--at-step",
    "at_step",
    default=None,
    help="With --resume: fork from a specific step id; currently Claude Code only.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="With --resume: print the resume command instead of exec'ing it.",
)
@click.option(
    "--bursts",
    "as_bursts",
    is_flag=True,
    help="Return only the change-burst summary list for this trace (no map skeleton).",
)
@click.option(
    "--card",
    "as_card",
    is_flag=True,
    help="Return a bounded progressive-discovery card for this trace.",
)
@click.option(
    "--burst-gap",
    "burst_gap",
    type=int,
    default=None,
    help="Step-index gap between adjacent edits within a burst (default 35).",
)
@click.option(
    "--no-commit-lookup",
    "no_commit_lookup",
    is_flag=True,
    help=(
        "With --bursts: skip the per-burst `git log` lookup "
        "(commit subject + body)."
    ),
)
@click.option(
    "--remote-bucket",
    is_flag=True,
    hidden=True,
    help="Deprecated: pull the configured private bucket remote before resolving the trace (still works).",
)
@click.option(
    "--force-remote-bucket",
    is_flag=True,
    hidden=True,
    help="Allow the remote-bucket pull to overwrite a local-ahead or diverged bucket.",
)
@click.option(
    "--remote",
    "remote",
    default=None,
    help=(
        "Read the trace.json from a remote HF dataset bucket "
        "(plan 080 §7 — symmetric local/remote read). Format: 'user/repo'."
    ),
)
@click.option(
    "--waste",
    "as_waste",
    is_flag=True,
    help="Emit the context-waste findings for the trace (opentraces.context_waste.v2).",
)
@click.option(
    "--large-output-chars",
    type=int,
    default=None,
    help="With --waste: override the large-output threshold (default 12000).",
)
@click.option(
    "--file-read-window-min",
    type=int,
    default=None,
    help="With --waste: override the repeated-file-read window in minutes (default 20).",
)
@click.option(
    "--search-window-min",
    type=int,
    default=None,
    help="With --waste: override the repeated-search window in minutes (default 10).",
)
@click.option(
    "--run-intel",
    "as_run_intel",
    is_flag=True,
    help="Emit deterministic resteer/recovery/loop signals for the trace (opentraces.run_intel.v1).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_get(
    ref: str,
    resume: bool,
    at_step: str | None,
    dry_run: bool,
    as_bursts: bool,
    as_card: bool,
    burst_gap: int | None,
    no_commit_lookup: bool,
    as_waste: bool,
    large_output_chars: int | None,
    file_read_window_min: int | None,
    search_window_min: int | None,
    as_run_intel: bool,
    remote_bucket: bool,
    force_remote_bucket: bool,
    remote: str | None,
    as_json: bool,
) -> None:
    """Resolve a trace, trace unit, map node, or ot:// Trail resource.

    REF accepts the universal trace address: ``<trace>``,
    ``<trace>:<step>``, ``<trace>:last``, or ``<trace>:<A>-<B>`` — the same
    token ``ctx`` and ``trail`` resolve. ``<trace>:<step>`` returns a bounded
    step card (the action lens, with the ``context_node_id`` join key);
    ``<trace>:<A>-<B>`` returns the same slice object as
    ``trace slice --from-step A --to-step B``.

    Pass ``--resume`` to hand control back to the native/upstream agent
    runtime instead of printing the trace details (``<trace>:<N> --resume``
    forks from step N). ``--at-step`` currently
    requires a Claude Code trace. Pass
    ``--bursts`` to return the change-burst summary for the trace
    without re-walking the full Trace Map. Pass ``--card`` to return a bounded
    progressive-discovery card.
    """
    if remote_bucket and resume:
        click.echo("--remote-bucket cannot be combined with --resume.", err=True)
        sys.exit(2)
    if remote and resume:
        click.echo("--remote cannot be combined with --resume.", err=True)
        sys.exit(2)
    if as_card and remote:
        click.echo("--card cannot be combined with --remote yet.", err=True)
        sys.exit(2)

    # ------------------------------------------------------------------
    # v7 F1 — the universal trace:step address, resolved on trace get.
    #
    # Parse ORDER matters: refs with a known scheme prefix (ot://, tu:,
    # tmn:, t:) take the EXISTING dispatch paths byte-unchanged — their
    # embedded colons are scheme separators, never step selectors. Only a
    # bare ref containing ':' is run through the SHARED step-address
    # grammar (core.trails.lineage.parse_trail_ref — the single grammar
    # oracle, pinned by tests/cli/test_trail_lineage_world.py). This block
    # runs BEFORE the --remote branch below so `trace get <T>:<N> --remote`
    # returns the record-only step card, not the whole-trace overview.
    # ------------------------------------------------------------------
    addr_trace: str | None = None
    step_sel: int | None = None
    last_sel = False
    span_sel: tuple[int, int] | None = None
    if ":" in ref and not ref.startswith(("ot://", "tu:", "tmn:", "t:")):
        from ..core.trails.lineage import parse_trail_ref

        addr_trace, parsed_step, parsed_span, reserved = parse_trail_ref(ref)
        if reserved == "last":
            last_sel = True
        elif reserved == "span":
            span_sel = parsed_span
        elif reserved == "origin":
            click.echo(
                f"':origin' is reserved and not resolvable on trace get yet; "
                f"use {addr_trace}, {addr_trace}:<N>, {addr_trace}:last, or "
                f"{addr_trace}:<A>-<B>.",
                err=True,
            )
            sys.exit(2)
        elif reserved is not None:
            click.echo(
                f"Unparseable step address: {ref} "
                f"(use <trace>, <trace>:<N>, <trace>:last, or <trace>:<A>-<B>).",
                err=True,
            )
            sys.exit(2)
        else:
            step_sel = parsed_step
    has_addr = step_sel is not None or last_sel or span_sel is not None

    if has_addr:
        for flag_name, flag_val in (
            ("--bursts", as_bursts),
            ("--card", as_card),
            ("--waste", as_waste),
            ("--run-intel", as_run_intel),
        ):
            if flag_val:
                click.echo(
                    f"{flag_name} operates on the whole trace; use the bare trace ref.",
                    err=True,
                )
                sys.exit(2)
        if span_sel is not None and resume:
            click.echo(
                "--resume needs a single step; use <trace>:<N> or <trace>:last.",
                err=True,
            )
            sys.exit(2)
        if span_sel is not None and remote:
            click.echo(
                "Span addresses need the local Trace Map; "
                "--remote cannot serve <trace>:<A>-<B>.",
                err=True,
            )
            sys.exit(2)
        if resume and at_step:
            click.echo("Pass either <trace>:<step> or --at-step, not both.", err=True)
            sys.exit(2)

    remote_bucket_payload = None
    if remote_bucket:
        try:
            from ._remote_bucket import pull_remote_bucket_for_trace

            remote_bucket_payload = pull_remote_bucket_for_trace(
                force=force_remote_bucket,
            )
        except Exception as exc:
            click.echo(f"Unable to read remote bucket: {exc}", err=True)
            sys.exit(3)

    if sum(bool(v) for v in (as_bursts, as_waste, as_run_intel, resume)) > 1:
        click.echo("Use only one of --bursts, --waste, --run-intel, or --resume.", err=True)
        sys.exit(2)

    if resume:
        if has_addr:
            # <trace>:<N> --resume closes the resume triple: the same token
            # that names the step in ctx/trail is the fork point. The
            # at_step contract is exactly 's<int>'
            # (capture/claude_code/resume.py::_find_step).
            assert addr_trace is not None
            fork_step = step_sel
            if last_sel:
                from ..core.trace_index import get_trace_path as _get_trace_path

                trace_path = _get_trace_path(_trace_id_from_ref(addr_trace))
                if trace_path is None or not trace_path.exists():
                    click.echo(f"Trace not found: {ref}", err=True)
                    sys.exit(6)
                record = _read_trace_record_from_path(trace_path)
                indices = [s.step_index for s in record.steps]
                if not indices:
                    click.echo(f"Step not found: {ref} (trace has no steps)", err=True)
                    sys.exit(6)
                fork_step = max(indices)
            _resume_trace_impl(addr_trace, f"s{fork_step}", dry_run, as_json)
            return
        _resume_trace_impl(ref, at_step, dry_run, as_json)
        return

    if as_bursts:
        _trace_get_bursts_impl(ref, burst_gap, as_json, commit_lookup=not no_commit_lookup)
        return
    if as_card:
        from ..core.discovery import candidate_card

        try:
            card = candidate_card(ref)
        except FileNotFoundError:
            click.echo(f"Trace not found: {ref}", err=True)
            sys.exit(6)
        payload = _envelope("opentraces.trace.get.v1", card=card.model_dump(mode="json"))
        if remote_bucket_payload is not None:
            payload["remote_bucket"] = remote_bucket_payload
        if as_json:
            click.echo(_dump_json(payload))
            return
        click.echo(f"{card.trace_id}  {card.headline}")
        return

    if as_waste:
        _trace_get_waste_impl(
            ref,
            as_json,
            large_output_chars=large_output_chars,
            file_read_window_min=file_read_window_min,
            search_window_min=search_window_min,
        )
        return

    if as_run_intel:
        _trace_get_run_intel_impl(ref, as_json)
        return

    # --remote: route the trace.json read through the D1 backend
    # abstraction. The backend factory returns a LocalBucketBackend when
    # remote=None and a RemoteHubBackend(repo_id=remote) otherwise. The
    # CLI verb's logic doesn't change — only the data source.
    if remote:
        from ..core.bucket_remote import BucketRemoteError
        from ..core.bucket_store import BucketLayoutError

        trace_id = _trace_id_from_ref(addr_trace if has_addr else ref)
        try:
            record = _read_trace_record_via_backend(trace_id, remote)
        except _BackendUnavailable as exc:
            click.echo(str(exc), err=True)
            sys.exit(3)
        except (BucketRemoteError, BucketLayoutError) as exc:
            click.echo(f"Remote read failed: {exc}", err=True)
            sys.exit(3)
        except FileNotFoundError:
            click.echo(f"Trace not found on remote {remote}: {ref}", err=True)
            sys.exit(6)
        if step_sel is not None or last_sel:
            # Record-only step card: the Trace Map lives in the LOCAL index,
            # so map enrichment is silently skipped on --remote reads.
            step = _select_step(record, step_sel, last_sel, ref)
            payload = _envelope(
                "opentraces.trace.get.v1",
                trace_id=record.trace_id,
                step=_step_card(record, step, trace_map=None),
                next_steps=_step_next_steps(record.trace_id, step.step_index),
            )
            if remote_bucket_payload is not None:
                payload["remote_bucket"] = remote_bucket_payload
            if as_json:
                click.echo(_dump_json(payload))
                return
            _echo_step_card_human(payload)
            return
        # v7: bounded overview by default, symmetric with the local read path.
        payload = _envelope("opentraces.trace.get.v1", trace=_trace_overview(record))
        if remote_bucket_payload is not None:
            payload["remote_bucket"] = remote_bucket_payload
        if as_json:
            click.echo(_dump_json(payload))
            return
        click.echo(f"{payload['trace']['trace_id']}  {payload['trace']['title']}")
        return

    from ..core.trace_index import get_map_node, get_trace_path, get_unit

    if ref.startswith("ot://"):
        from ..core.trails import resolve_resource

        try:
            resource = resolve_resource(Path.cwd(), ref)
        except ValueError as exc:
            click.echo(f"Trace resource not found: {ref}: {exc}", err=True)
            sys.exit(6)
        payload = _envelope("opentraces.trace.get.v1", resource=resource)
    elif ref.startswith("tu:"):
        unit = get_unit(ref)
        if unit is None:
            click.echo(f"Trace unit not found: {ref}", err=True)
            sys.exit(6)
        payload = _envelope("opentraces.trace.get.v1", unit=unit.model_dump(mode="json"))
    elif ref.startswith("tmn:"):
        node = get_map_node(ref)
        if node is None:
            click.echo(f"Trace Map node not found: {ref}", err=True)
            sys.exit(6)
        payload = _envelope("opentraces.trace.get.v1", map_node=node.model_dump(mode="json"))
    elif span_sel is not None:
        # SPAN lens — one primitive, two addresses: `trace get <T>:<A>-<B>`
        # delegates to the exact engine behind `trace slice --from-step A
        # --to-step B`, embedding the slice object byte-identically.
        from ..core.trace_index import get_trace_map
        from ..core.trace_slices import slice_by_steps

        assert addr_trace is not None
        span_trace_id = _trace_id_from_ref(addr_trace)
        trace_map = get_trace_map(span_trace_id)
        if trace_map is None:
            click.echo(f"Trace Map not found: {ref}", err=True)
            sys.exit(6)
        record = _try_load_trace_record(span_trace_id)
        try:
            slice_obj = slice_by_steps(
                trace_map,
                record,
                start_step_index=span_sel[0],
                end_step_index=span_sel[1],
            )
        except ValueError as exc:
            click.echo(str(exc), err=True)
            sys.exit(2)
        payload = _envelope(
            "opentraces.trace.get.v1",
            trace_id=trace_map.trace_id,
            span={
                "from_step": slice_obj["start_step_index"],
                "to_step": slice_obj["end_step_index"],
            },
            slice=slice_obj,
        )
    elif step_sel is not None or last_sel:
        # STEP lens — the bounded, constant-size action card. Same loader +
        # same rc=6 not-found contract as the bare form.
        assert addr_trace is not None
        trace_path = get_trace_path(_trace_id_from_ref(addr_trace))
        if trace_path is None or not trace_path.exists():
            click.echo(f"Trace not found: {ref}", err=True)
            sys.exit(6)
        record = _read_trace_record_from_path(trace_path)
        step = _select_step(record, step_sel, last_sel, ref)
        # Best-effort local-map enrichment; silently omitted when the Trace
        # Index is absent.
        try:
            from ..core.trace_index import get_trace_map

            trace_map = get_trace_map(record.trace_id)
        except Exception:
            trace_map = None
        payload = _envelope(
            "opentraces.trace.get.v1",
            trace_id=record.trace_id,
            step=_step_card(record, step, trace_map=trace_map),
            next_steps=_step_next_steps(record.trace_id, step.step_index),
        )
    else:
        trace_path = get_trace_path(_trace_id_from_ref(ref))
        if trace_path is None or not trace_path.exists():
            click.echo(f"Trace not found: {ref}", err=True)
            sys.exit(6)
        record = _read_trace_record_from_path(trace_path)
        # v7 audit-P2 cure: bounded overview by default, NEVER a 1000-step dump.
        payload = _envelope("opentraces.trace.get.v1", trace=_trace_overview(record))

    if remote_bucket_payload is not None:
        payload["remote_bucket"] = remote_bucket_payload
    if as_json:
        click.echo(_dump_json(payload))
        return
    if "trace" in payload:
        overview = payload["trace"]
        click.echo(f"{overview['trace_id']}  {overview['title']}")
        click.echo(f"  summary:  {overview['summary']}")
        click.echo(f"  steps:    {overview['step_count']}")
        outcome = overview.get("outcome") or {}
        click.echo(
            f"  outcome:  success={outcome.get('success')} "
            f"committed={overview.get('committed')}"
        )
        if overview.get("duration_seconds") is not None:
            click.echo(f"  duration: {overview['duration_seconds']}s")
        files = overview.get("files_touched") or []
        total_files = overview.get("files_touched_count", len(files))
        if files:
            shown = ", ".join(files)
            more = "" if total_files <= len(files) else f" (+{total_files - len(files)} more)"
            click.echo(f"  files:    {shown}{more}")
    elif "step" in payload:
        _echo_step_card_human(payload)
    elif "slice" in payload:
        # Same one-line-per-slice summary as `trace slice`.
        item = payload["slice"]
        click.echo(
            f"{item['slice_id']}  steps {item['start_step_index']}..{item['end_step_index']}  "
            f"nodes={len(item['map']['nodes'])}  patches={len(item['trace_patch_refs'])}"
        )
    elif "unit" in payload:
        click.echo(payload["unit"]["unit_id"])
    elif "map_node" in payload:
        click.echo(payload["map_node"]["node_id"])
    else:
        click.echo(payload["resource"].get("resource_type", ref))


@trace_group.command("compare", cls=OpentracesCommand, hidden=True)  # v7 7->4 collapse: not one of the four job verbs; callable, off --help.
@click.argument("trace_a")
@click.argument("trace_b")
@click.option("--no-quality", is_flag=True, help="Skip the deterministic quality persona delta.")
@click.option(
    "--burst-gap",
    "burst_gap",
    type=int,
    default=None,
    help="Burst gap applied to BOTH traces for comparable burst deltas (default 35).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def trace_compare_cmd(
    trace_a: str,
    trace_b: str,
    no_quality: bool,
    burst_gap: int | None,
    as_json: bool,
) -> None:
    """Compare two traces: metrics, quality, burst/error signals, and security deltas."""
    from ..core.bursts import DEFAULT_BURST_GAP
    from ..core.trace_compare import compare_traces

    def _load(ref: str):
        from ..core.trace_index import get_trace_path

        trace_path = get_trace_path(_trace_id_from_ref(ref))
        if trace_path is None or not trace_path.exists():
            click.echo(f"Trace not found: {ref}", err=True)
            sys.exit(6)
        return _read_trace_record_from_path(trace_path)

    record_a = _load(trace_a)
    record_b = _load(trace_b)
    gap = burst_gap if burst_gap is not None else DEFAULT_BURST_GAP
    payload = compare_traces(
        record_a,
        record_b,
        include_quality=not no_quality,
        burst_gap=gap,
    )
    if as_json:
        click.echo(_dump_json(payload))
        return
    m = payload["delta"]["metrics"]
    click.echo(f"compare {payload['trace_a']} -> {payload['trace_b']}")
    for field in ("total_steps", "total_input_tokens", "estimated_cost_usd"):
        t = m[field]
        click.echo(f"  {field}: {t['a']} -> {t['b']}  (delta {t['delta']})")


def _trace_get_bursts_impl(
    ref: str,
    burst_gap: int | None,
    as_json: bool,
    *,
    commit_lookup: bool = True,
) -> None:
    """Convenience: emit the bursts list for ``ref`` directly.

    Same algorithm as ``trace map --bursts``, just trimmed for one-shot
    consumers — no map skeleton, just the burst metadata array.
    """
    from ..core.bursts import DEFAULT_BURST_GAP, detect_bursts
    from ..core.trace_index import get_trace_map

    trace_id = _trace_id_from_ref(ref)
    trace_map = get_trace_map(trace_id)
    if trace_map is None:
        click.echo(f"Trace Map not found: {ref}", err=True)
        sys.exit(6)
    gap = burst_gap if burst_gap is not None else DEFAULT_BURST_GAP
    record = _try_load_trace_record(trace_id)
    bursts = detect_bursts(
        trace_map,
        gap=gap,
        trace_record=record,
        commit_lookup=commit_lookup,
    )
    payload = _envelope(
        "opentraces.trace.get.v1",
        trace_id=trace_id,
        burst_gap=gap,
        bursts=[b.to_metadata() for b in bursts],
    )
    if as_json:
        click.echo(_dump_json(payload))
        return
    for index, b in enumerate(bursts, 1):
        files = sum(b.unique_files.values())
        anchors = len(b.unique_git_anchors)
        click.echo(
            f"#{index} steps {b.step_range[0]}..{b.step_range[1]}  "
            f"files={files}  patches={len(b.patches)}  anchors={anchors}"
        )


def _trace_get_waste_impl(
    ref: str,
    as_json: bool,
    *,
    large_output_chars: int | None = None,
    file_read_window_min: int | None = None,
    search_window_min: int | None = None,
) -> None:
    """Emit the context-waste findings for ``ref`` (opentraces.context_waste.v2)."""
    from ..core.context_waste import detect_context_waste

    trace_id = _trace_id_from_ref(ref)
    record = _try_load_trace_record(trace_id)
    if record is None:
        click.echo(f"Trace record not found: {ref}", err=True)
        sys.exit(6)
    kwargs: dict[str, int] = {}
    if large_output_chars is not None:
        kwargs["large_output_chars"] = large_output_chars
    if file_read_window_min is not None:
        kwargs["file_read_window_minutes"] = file_read_window_min
    if search_window_min is not None:
        kwargs["search_window_minutes"] = search_window_min
    report = detect_context_waste(record, **kwargs)
    payload = report.to_envelope()
    if as_json:
        click.echo(_dump_json(payload))
        return
    for f in report.findings:
        click.echo(f"{f.pattern}  step {f.step_index}  {f.reason}")


def _trace_get_run_intel_impl(ref: str, as_json: bool) -> None:
    """Emit deterministic resteer/recovery/loop annotations for ``ref``."""
    from ..core.run_intel import detect_run_signals

    trace_id = _trace_id_from_ref(ref)
    record = _try_load_trace_record(trace_id)
    if record is None:
        click.echo(f"Trace record not found: {ref}", err=True)
        sys.exit(6)
    report = detect_run_signals(record)
    payload = report.to_envelope()
    if as_json:
        click.echo(_dump_json(payload))
        return
    for s in report.signals:
        click.echo(f"{s.kind}  step {s.step_index}  {s.reason}")


def _trace_overview(record) -> dict:
    """Build a bounded, readable overview of a trace (v7 audit-P2 cure).

    The bare ``trace get <id>`` default must be readable but NEVER an
    O(steps) dump: title/summary, step count, outcome, files touched, and
    duration — a constant-size card regardless of how long the session ran.
    """
    from ..core.boilerplate import headline_from_summary, summary_for_record

    summary = summary_for_record(record)
    task_desc = getattr(getattr(record, "task", None), "description", None)
    title = headline_from_summary(summary or task_desc or record.trace_id)

    # Files touched: from patches (one entry per Edit/Write hunk), collapsed to
    # unique relative paths and CAPPED so the overview stays constant-size.
    max_files = 12
    seen: list[str] = []
    for patch in getattr(record, "patches", None) or []:
        fp = getattr(patch, "file_path", None)
        if fp and fp not in seen:
            seen.append(fp)
    files_touched = seen[:max_files]

    outcome = record.outcome
    outcome_dict = (
        outcome.model_dump(mode="json") if hasattr(outcome, "model_dump") else dict(outcome or {})
    )

    metrics = getattr(record, "metrics", None)
    duration = getattr(metrics, "total_duration_s", None) if metrics is not None else None
    if duration is None:
        start = getattr(record, "timestamp_start", None)
        end = getattr(record, "timestamp_end", None)
        if start is not None and end is not None:
            try:
                duration = max(0.0, (end - start).total_seconds())
            except (TypeError, AttributeError):
                duration = None

    agent = getattr(record, "agent", None)
    return {
        "trace_id": record.trace_id,
        "title": title or record.trace_id,
        "summary": summary or title or record.trace_id,
        "agent": {
            "name": getattr(agent, "name", None),
            "version": getattr(agent, "version", None),
            "model": getattr(agent, "model", None),
        },
        "step_count": len(record.steps),
        "outcome": outcome_dict,
        "files_touched": files_touched,
        "files_touched_count": len(seen),
        "duration_seconds": duration,
        "committed": getattr(outcome, "committed", None) if outcome is not None else None,
        "commit_sha": getattr(outcome, "commit_sha", None) if outcome is not None else None,
        "refs": {
            "trace": record.trace_id,
            "map": f"ot://trace/{record.trace_id}/map",
            "card": f"ot://trace/{record.trace_id}/card",
        },
    }


_STEP_CARD_CONTENT_PREVIEW_CHARS = 240
_STEP_CARD_INPUT_PREVIEW_CHARS = 120
_STEP_CARD_MAX_TOOL_CALLS = 8


def _select_step(record, step_sel: int | None, last_sel: bool, ref: str):
    """Resolve a step selector against a loaded TraceRecord, or exit 6.

    Selection is an INDEX-FIELD match (``step.step_index == N``, mirroring
    capture/claude_code/resume.py::_find_step), never a list position;
    ``:last`` is the max step_index — the final action. Missing step is rc=6
    for the whole address family (deliberate divergence from ctx's rc=3).
    """
    indices = [s.step_index for s in record.steps]
    if not indices:
        click.echo(f"Step not found: {ref} (trace has no steps)", err=True)
        sys.exit(6)
    wanted = max(indices) if last_sel else step_sel
    step = next((s for s in record.steps if s.step_index == wanted), None)
    if step is None:
        click.echo(
            f"Step not found: {ref} (trace has steps {min(indices)}..{max(indices)})",
            err=True,
        )
        sys.exit(6)
    return step


def _step_card(record, step, *, trace_map=None) -> dict:
    """Build the bounded, constant-size STEP CARD (the action lens).

    Derived ONLY from the Step model (schema untouched) plus best-effort
    local-map enrichment. Same audit-P2 discipline as ``_trace_overview``:
    previews are truncated, tool_calls capped, observations summarized —
    NEVER an O(step-content) dump.
    """
    content = step.content or ""
    tool_calls = []
    for tc in step.tool_calls[:_STEP_CARD_MAX_TOOL_CALLS]:
        try:
            input_preview = json.dumps(tc.input, default=str, sort_keys=True)
        except (TypeError, ValueError):
            input_preview = str(tc.input)
        tool_calls.append(
            {
                "tool_name": tc.tool_name,
                "tool_call_id": tc.tool_call_id,
                "input_preview": input_preview[:_STEP_CARD_INPUT_PREVIEW_CHARS],
            }
        )
    observations = {
        "count": len(step.observations),
        "total_chars": sum(len(o.content or "") for o in step.observations),
        "first_error": next((o.error for o in step.observations if o.error), None),
    }
    token_usage = step.token_usage
    card = {
        "step_index": step.step_index,
        "role": step.role,
        "call_type": step.call_type,
        "agent_role": step.agent_role,
        "model": step.model,
        "timestamp": step.timestamp,
        "content_preview": content[:_STEP_CARD_CONTENT_PREVIEW_CHARS],
        "content_chars": len(content),
        "reasoning_present": bool(step.reasoning_content),
        "tool_calls": tool_calls,
        "tool_call_count": len(step.tool_calls),
        "observations": observations,
        "token_usage": (
            token_usage.model_dump(mode="json")
            if hasattr(token_usage, "model_dump")
            else token_usage
        ),
        "context_node_id": step.context_node_id,
    }
    if trace_map is not None:
        # A step usually projects to SEVERAL map nodes (agent_message +
        # file_edit + tool_result ...). map_node_id is the first node covering
        # the step; the file lists aggregate across all of them, deduped.
        step_nodes = [n for n in trace_map.nodes if n.step_index == step.step_index]
        if step_nodes:
            card["map_node_id"] = step_nodes[0].node_id
            files_modified: list[str] = []
            files_read: list[str] = []
            for node in step_nodes:
                for fp in node.files_modified:
                    if fp not in files_modified:
                        files_modified.append(fp)
                for fp in node.files_read:
                    if fp not in files_read:
                        files_read.append(fp)
            card["files_modified"] = files_modified
            card["files_read"] = files_read
    return card


def _step_next_steps(trace_id: str, step_index: int) -> list[str]:
    """The closed cross-substrate triple for a resolved step address."""
    return [
        f"ctx {trace_id}:{step_index}",
        f"trail {trace_id}:{step_index}",
        f"trace slice {trace_id} --around-step {step_index} --radius 5",
    ]


def _echo_step_card_human(payload: dict) -> None:
    """Compact human rendering of the step card envelope."""
    card = payload["step"]
    click.echo(
        f"{payload['trace_id']}:{card['step_index']}  role={card['role']}  "
        f"model={card.get('model') or '-'}"
    )
    if card.get("tool_calls"):
        names = ", ".join(tc["tool_name"] for tc in card["tool_calls"])
        suffix = ""
        if card.get("tool_call_count", 0) > len(card["tool_calls"]):
            suffix = f" (+{card['tool_call_count'] - len(card['tool_calls'])} more)"
        click.echo(f"  tools:    {names}{suffix}")
    if card.get("content_preview"):
        click.echo(f"  content:  {card['content_preview']}")
    obs = card.get("observations") or {}
    if obs.get("count"):
        line = f"  obs:      count={obs['count']} chars={obs['total_chars']}"
        if obs.get("first_error"):
            line += f" first_error={obs['first_error']}"
        click.echo(line)
    if card.get("context_node_id"):
        click.echo(f"  ctx node: {card['context_node_id']}")
    for hint in payload.get("next_steps", []):
        click.echo(f"  next:     {hint}")


def _map_span(trace_map) -> tuple[int, int]:
    """First/last step index across a (sub)map's nodes (0,0 when empty)."""
    steps = [n.step_index for n in trace_map.nodes if n.step_index is not None]
    if not steps:
        return 0, 0
    return min(steps), max(steps)


def _map_handoff(trace_map, trace_id: str) -> dict:
    """The always-available `trace slice` handoff for the current span.

    v7 map contract: every ``map`` call ends in a runnable
    ``trace slice --from-step L --to-step H`` for the browsed span, with no
    node-count cutoff — ``slice`` is the expensive pull, ``map`` the cheap browse.
    """
    lo, hi = _map_span(trace_map)
    return {
        "command": f"trace slice {trace_id} --from-step {lo} --to-step {hi}",
        "from_step": lo,
        "to_step": hi,
    }


# Landmark node types, in RELEVANCE priority order (v7 map ablation: capping by
# raw position hid 23/25 errors, so the cap must keep the highest-signal types).
_MAP_LANDMARK_PRIORITY = {
    "error_signal": 0,
    "subagent_call": 1,
    "git_anchor": 2,
    "final_response": 3,
    "user_instruction": 4,
}
_MAP_MAX_LANDMARKS = 24
_MAP_MAX_SECTIONS = 24
_MAP_MAX_FILES = 20
_MAP_PREVIEW_CHARS = 72


def _bounded_map_view(trace_map, *, trace_id: str, record=None) -> dict:
    """Bounded, roughly constant-size faceted browse of a whole trace (v7 V1).

    NEVER O(nodes): TYPE + FILE facet counts (file paths collapsed across
    abs/worktree/dir spellings), a collapsed SECTION breadcrumb, a
    relevance-capped landmark set, and the always-available ``trace slice``
    handoff for the current span.
    """
    from ..core.bursts import _normalise_path, _resolve_repo_root_for_paths

    repo_root = None
    try:
        repo_root = _resolve_repo_root_for_paths(record, None)
    except Exception:
        repo_root = None

    nodes = trace_map.nodes

    type_counts: dict[str, int] = {}
    for n in nodes:
        type_counts[n.action_type] = type_counts.get(n.action_type, 0) + 1

    file_counts: dict[str, int] = {}
    for n in nodes:
        for fp in list(n.files_modified) + list(n.files_read):
            norm = _normalise_path(fp, repo_root)
            if norm:
                file_counts[norm] = file_counts.get(norm, 0) + 1
    top_files = sorted(file_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_MAP_MAX_FILES]

    sections = [
        {"step": n.step_index, "preview": (n.text_preview or "")[:_MAP_PREVIEW_CHARS]}
        for n in nodes
        if n.action_type == "user_instruction"
    ]
    section_total = len(sections)
    sections = sections[:_MAP_MAX_SECTIONS]

    raw_landmarks = [
        {
            "step": n.step_index if n.step_index is not None else -1,
            "type": n.action_type,
            "node_id": n.node_id,
            "preview": (n.text_preview or "")[:_MAP_PREVIEW_CHARS],
        }
        for n in nodes
        if n.action_type in _MAP_LANDMARK_PRIORITY
    ]
    # Relevance cap: keep the highest-signal landmarks (errors before filler),
    # THEN restore chronological order for display.
    raw_landmarks.sort(key=lambda l: (_MAP_LANDMARK_PRIORITY.get(l["type"], 9), l["step"]))
    landmark_total = len(raw_landmarks)
    landmarks = sorted(raw_landmarks[:_MAP_MAX_LANDMARKS], key=lambda l: l["step"])

    lo, hi = _map_span(trace_map)
    return {
        "scope": {"filters": {}, "node_count": len(nodes), "span": {"from_step": lo, "to_step": hi}},
        "facets": {
            "type": dict(sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "file": dict(top_files),
        },
        "sections": sections,
        "section_total": section_total,
        "landmarks": landmarks,
        "landmark_total": landmark_total,
        "handoff": {
            "command": f"trace slice {trace_id} --from-step {lo} --to-step {hi}",
            "from_step": lo,
            "to_step": hi,
        },
    }


def _try_load_trace_record(trace_id: str):
    """Best-effort load of the staging TraceRecord by ``trace_id``.

    Used by the burst projection to pull the full step content (the
    Trace Map's per-node text_preview is truncated for display) and
    the hook trail (post-tool ``git_head`` transitions). Returns
    ``None`` on any failure — callers must handle that gracefully.
    """
    try:
        from ..core.trace_index import get_trace_path

        trace_path = get_trace_path(trace_id)
        if trace_path is None or not trace_path.exists():
            return None
        return _read_trace_record_from_path(trace_path)
    except Exception:
        return None


def _read_trace_record_from_path(trace_path: Path):
    """Load a TraceRecord from a legacy JSONL shard or bucket object."""

    from opentraces_schema import load_record_json

    from ..core.bucket_store import read_trace_record_object

    bucket_obj = read_trace_record_object(trace_path)
    if bucket_obj is not None:
        return bucket_obj.record
    first_line = next(
        (line for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()),
        "",
    )
    # Legacy 0.3.x shards carry outcome.patch; route through the migration-aware
    # loader so patches[] + metadata.legacy.patch survive the read (no-op on 0.4+).
    return load_record_json(first_line)


class _BackendUnavailable(RuntimeError):
    """Raised when the D1 backend abstraction isn't available yet."""


def _read_trace_record_via_backend(trace_id: str, remote: str):
    """Load a TraceRecord through the D1 backend abstraction.

    Plan 080 §7 — every read verb gains ``--remote <hf-repo>`` and routes
    its single data fetch through ``get_backend(remote)``. The verb
    logic is unchanged; only the data source swaps.
    """

    from opentraces_schema import load_record_dict

    try:
        from ..core.bucket_backend import get_backend
    except ImportError as exc:
        raise _BackendUnavailable(
            f"--remote requires the bucket backend module (in flight as Track D1): {exc}"
        ) from exc

    backend = get_backend(remote)
    payload = backend.get_trace_json(trace_id)
    return load_record_dict(payload)


def _trace_id_from_ref(ref: str) -> str:
    if ref.startswith("ot://trace/"):
        path = ref.removeprefix("ot://trace/")
        if path.endswith("/map"):
            return path.removesuffix("/map")
        return path.split("/", 1)[0]
    if ref.startswith("tmn:"):
        from ..core.trace_index import get_map_node

        node = get_map_node(ref)
        if node is not None:
            return node.trace_id
    if ref.startswith("t:"):
        return ref[2:]
    if ref.startswith("tu:"):
        parts = ref.split(":")
        if len(parts) >= 3:
            return parts[1]
    return ref


def _candidate_node_id(trace_map, candidate: str) -> str | None:
    for node in trace_map.nodes:
        if candidate in {node.node_id, node.unit_id}:
            return node.node_id
    trace_unit_id = f"tu:{trace_map.trace_id}:trace"
    signal_prefix = f"tu:{trace_map.trace_id}:signal:"
    if candidate == trace_unit_id or candidate.startswith(signal_prefix):
        for action_type in ("file_edit", "test_run", "agent_plan", "user_instruction"):
            node = next((n for n in trace_map.nodes if n.action_type == action_type), None)
            if node:
                return node.node_id
    return None


def _load_project_state():
    """Shared helper: load project-local StateManager and staging dir."""
    from ..core.config import get_project_traces_dir, get_project_state_path, project_is_opted_in
    from ..core.state import StateManager

    project_dir = Path.cwd()
    if not project_is_opted_in(project_dir):
        click.echo("Not an opentraces project. Run 'opentraces init' first.")
        sys.exit(3)

    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)
    staging_dir = get_project_traces_dir(project_dir)
    return state, staging_dir


# ---------------------------------------------------------------------------
# ``ot trace teleport`` — portable trace workspaces.
#
# The export side takes a trace_id and produces a directory containing a
# Git bundle (the trail event log), the trace JSONL, and a manifest. The
# open side takes such a directory and reconstitutes a fresh project.
# Both sides operate on a trace handle, which is why the verbs live under
# ``trace`` rather than ``trail`` (the trail evidence is bundled along).
# ---------------------------------------------------------------------------


@trace_group.group("teleport", cls=OpentracesGroup, hidden=True)
def teleport_group() -> None:
    """Move a trace and its retained Git evidence between workspaces."""


@teleport_group.command(
    "export",
    cls=OpentracesCommand,
    examples=[
        "opentraces trace teleport export tr1 --output ./tr1.trace-workspace",
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
        "opentraces trace teleport open ./tr1.trace-workspace --project ./blank --json",
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


def _load_trace_record(staging_dir: Path, trace_id: str):
    """Load a TraceRecord from staging by trace_id.

    Accepts the full ``<agent>_<uuid>`` id or a unique prefix of either
    the full id or its session-uuid portion (>=2 chars). Also strips the
    ``t:`` CLI-ish form. Ambiguous or unknown prefixes return
    ``(None, None)``.
    """
    from opentraces_schema import load_record_json

    # Strip the `t:` decorative prefix from graph output.
    probe = trace_id[2:] if trace_id[:2].lower() == "t:" else trace_id

    # Exact file first (fast path for full ids).
    staging_file = staging_dir / f"{probe}.jsonl"
    if not staging_file.exists():
        if len(probe) < 2:
            return None, staging_file
        # Match either a left-anchored prefix (full `<agent>_<uuid>` form)
        # or anywhere-inside match that catches bare session-uuid prefixes
        # like ``b0ea2e9e`` against files named ``claude-code_b0ea2e9e-*.jsonl``.
        matches = sorted({*staging_dir.glob(f"{probe}*.jsonl"),
                          *staging_dir.glob(f"*_{probe}*.jsonl")})
        if not matches:
            return None, staging_file
        if len(matches) > 1:
            return None, staging_file
        staging_file = matches[0]

    data = staging_file.read_text().strip()
    if not data:
        return None, staging_file
    record = load_record_json(data.splitlines()[0])
    return record, staging_file


@click.command("list", cls=OpentracesCommand)
@click.option("--stage", type=click.Choice(["inbox", "staged", "pushed", "rejected"]), default=None, help="Filter by stage")
@click.option("--model", type=str, default=None, help="Filter by model name (substring)")
@click.option("--agent", type=str, default=None, help="Filter by agent name")
@click.option("--limit", type=int, default=50, help="Max traces to return")
@click.option("--by-commit", is_flag=True, help="Group traces by git_links[].revision")
def trace_list(stage: str | None, model: str | None, agent: str | None, limit: int, by_commit: bool) -> None:
    """List staged traces with optional filters."""
    import time as _time
    from opentraces_schema import TraceRecord, load_record_json

    state, staging_dir = _load_project_state()
    staged_files = list(staging_dir.glob("*.jsonl")) if staging_dir.exists() else []

    now = _time.time()

    def _ts_epoch(record) -> float:
        if not record.timestamp_end:
            return 0.0
        try:
            from datetime import datetime
            if hasattr(record.timestamp_end, "timestamp"):
                return record.timestamp_end.timestamp()
            return datetime.fromisoformat(
                str(record.timestamp_end).replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0.0

    # Load all, sort by record timestamp_end desc (actual age, not mtime).
    parsed: list[tuple[TraceRecord, float]] = []
    for sf in staged_files:
        try:
            data = sf.read_text().strip()
            record = load_record_json(data.splitlines()[0])
            parsed.append((record, _ts_epoch(record)))
        except Exception:
            continue
    parsed.sort(key=lambda p: p[1], reverse=True)

    traces: list[dict] = []
    for record, ts_epoch in parsed:
        entry = state.get_trace(record.trace_id)
        visible_stage = resolve_visible_stage(entry.status if entry else None)

        if stage and visible_stage != stage:
            continue
        if agent and record.agent.name != agent:
            continue
        if model and (not record.agent.model or model.lower() not in record.agent.model.lower()):
            continue

        rel_time = "unknown"
        if ts_epoch:
            diff_seconds = now - ts_epoch
            if diff_seconds < 3600:
                rel_time = f"{int(diff_seconds / 60)}m ago"
            elif diff_seconds < 86400:
                rel_time = f"{int(diff_seconds / 3600)}h ago"
            elif diff_seconds < 172800:
                rel_time = "yesterday"
            else:
                rel_time = f"{int(diff_seconds / 86400)}d ago"

        traces.append({
            "trace_id": record.trace_id,
            "task": (record.task.description or "untitled")[:80],
            "agent": record.agent.name,
            "model": record.agent.model or "unknown",
            "stage": visible_stage,
            "step_count": len(record.steps),
            "tool_count": sum(len(s.tool_calls) for s in record.steps),
            "flag_count": record.security.flags_reviewed or 0,
            "timestamp": str(record.timestamp_end) if record.timestamp_end else None,
            "relative_time": rel_time,
            "git_links": [
                {"revision": link.revision, "tier": link.tier}
                for link in record.git_links
            ],
            "lifecycle": record.lifecycle,
        })

        if len(traces) >= limit:
            break

    from rich.console import Console as _Console
    from rich.table import Table as _Table
    from rich import box as _box

    console = _Console()

    def _build_table():
        t = _Table(box=_box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), header_style="dim")
        t.add_column("ID", no_wrap=True)
        t.add_column("Age", no_wrap=True, justify="right")
        t.add_column("Task", overflow="ellipsis", no_wrap=True)
        return t

    def _row_task(s):
        task = s["task"] or "untitled"
        if len(task) > 80:
            task = task[:79] + "…"
        return (
            short_trace_id(s['trace_id']),
            f"[dim]{s['relative_time']}[/]",
            task,
        )

    console.print()
    if by_commit:
        # Plan 041 R29: group by git_links[].revision; unlinked bucket last.
        groups: dict[str, list[dict]] = {}
        for s in traces:
            keys = [gl["revision"] for gl in s.get("git_links") or []] or ["(unlinked)"]
            for k in keys:
                groups.setdefault(k, []).append(s)
        for rev in sorted(groups, key=lambda r: (r == "(unlinked)", r)):
            rev_label = rev if rev == "(unlinked)" else rev[:10]
            console.print(f"[bold]git {rev_label}[/]  [dim]({len(groups[rev])})[/]")
            t = _build_table()
            for s in groups[rev]:
                t.add_row(*_row_task(s))
            console.print(t)
            console.print()
    else:
        t = _build_table()
        for s in traces:
            t.add_row(*_row_task(s))
        console.print(t)

    console.print(
        f"[dim]{len(traces)} trace{'s' if len(traces) != 1 else ''}  "
        f"· copy an ID to continue (e.g. `ot show <id>` or paste into your next prompt)[/]",
        highlight=False,
    )

    emit_json({
        "status": "ok",
        "traces": traces,
        "total": len(traces),
        "by_commit": by_commit,
    })




def _trace_commit_impl(trace_id: str) -> None:
    """Commit a single trace for push."""
    state, staging_dir = _load_project_state()
    entry = state.get_trace(trace_id)
    if entry is None:
        click.echo(f"Trace not found: {trace_id}")
        emit_json(error_response("NOT_FOUND", "trace", f"No trace entry for {trace_id}"))
        sys.exit(6)

    # Build a commit message from the trace task description
    message = short_trace_id(trace_id, 12)
    try:
        if entry.file_path:
            from opentraces_schema import load_record_json
            record = load_record_json(Path(entry.file_path).read_text().strip())
            task_desc = (record.task or {}).get("description", "") if isinstance(record.task, dict) else (getattr(record.task, "description", "") if record.task else "")
            if task_desc:
                message = task_desc[:80]
    except Exception:
        pass

    from ..core.review import commit_single
    commit_id = commit_single(state, trace_id, message)
    human_echo(f"Committed: {short_trace_id(trace_id)} (commit {commit_id})")

    emit_json({
        "status": "ok",
        "trace_id": trace_id,
        "commit_id": commit_id,
        "stage": "staged",
        "next_steps": ["Run 'opentraces push' to upload"],
        "next_command": "opentraces push",
    })




# ---------------------------------------------------------------------------
# ``ot trail resume`` — hand control back to the upstream agent.
#
# Registered resumers can hand control back to a native agent REPL.
# Agents without a resumer fall back to printing a generic hint.
# ---------------------------------------------------------------------------


def _resume_trace_impl(
    trace_id: str,
    at_step: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Hand control back to the upstream agent for a given trace.

    Shared between the canonical ``ot trace get <id> --resume`` flag form
    and the deprecated ``ot trail resume <id>`` alias kept for backwards
    compatibility with existing scripts and tests.
    """
    from ..core.trace_meta import (
        resolve_trace_id_prefix,
        AmbiguousPrefixError,
    )
    from ..capture import get_resumer
    from ..core.agent_resume import print_generic_hint
    from ..core.trails import snapshot_resume_packet

    state, staging_dir = _load_project_state()
    project_dir = Path.cwd()

    # Resolve the prefix to a full id. The resolver accepts ``t:`` form.
    try:
        full_id = resolve_trace_id_prefix(project_dir, trace_id)
    except AmbiguousPrefixError as e:
        click.echo(f"Ambiguous trace prefix {trace_id!r}:", err=True)
        for cand in e.candidates[:10]:
            click.echo(f"  {cand[:12]}...", err=True)
        sys.exit(2)
    except ValueError as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    if not full_id:
        click.echo(f"No trace matches {trace_id!r}", err=True)
        sys.exit(6)

    record, _staging_file = _load_trace_record(staging_dir, full_id)
    if record is None:
        # Filename is historically the session_id for Claude Code captures,
        # not the trace_id. Fall back to scanning all JSONL files for a
        # matching trace_id or session_id.
        from opentraces_schema import load_record_json as _load_record_json
        for p in staging_dir.glob("*.jsonl"):
            try:
                line = p.read_text().strip().splitlines()[0]
                rec = _load_record_json(line)
            except Exception:
                continue
            if rec.trace_id == full_id or rec.session_id == full_id:
                record = rec
                break
    if record is None:
        click.echo(f"Trace file unreadable: {full_id}", err=True)
        sys.exit(6)

    agent_name = (getattr(record.agent, "name", "") or "").lower()
    session_id = record.session_id or ""
    if not session_id:
        click.echo(
            f"Trace {full_id[:8]} has no session_id; cannot resume.", err=True
        )
        sys.exit(6)

    resumer_cls = get_resumer(agent_name)
    resumer = resumer_cls() if resumer_cls is not None else None

    if resumer is not None:
        if at_step:
            if not getattr(resumer, "supports_at_step", False):
                message = f"--at-step resume is not supported for {agent_name} traces."
                if as_json:
                    click.echo(_dump_json(error_response(
                        "UNSUPPORTED_AT_STEP_AGENT",
                        "resume",
                        message,
                    )))
                else:
                    click.echo(message, err=True)
                sys.exit(2)

            if agent_name in ("claude-code", "claude_code", "claude"):
                try:
                    snapshot_packet = snapshot_resume_packet(
                        project_dir,
                        record,
                        at_step,
                        state=state,
                        dry_run=dry_run,
                    )
                except ValueError as exc:
                    if as_json:
                        click.echo(_dump_json(error_response("INVALID_STEP", "resume", str(exc))))
                    else:
                        click.echo(str(exc), err=True)
                    sys.exit(2)
                if as_json:
                    click.echo(_dump_json(snapshot_packet))
                    sys.exit(0)
                if snapshot_packet.get("resume_mode") == "snapshot_backed":
                    argv = snapshot_packet.get("launch", {}).get("argv") or []
                    new_session_id = snapshot_packet.get("session", {}).get("new_session_id")
                    materialization = snapshot_packet.get("materialization") or {}
                    if dry_run:
                        click.echo(" ".join(argv))
                        click.echo(
                            "would materialize snapshot "
                            f"{snapshot_packet.get('snapshot', {}).get('snapshot_id')} "
                            f"at {materialization.get('path')}"
                        )
                        sys.exit(0)
                    rc = resumer.resume_session(
                        new_session_id,
                        project_cwd=Path(materialization.get("path")),
                        dry_run=False,
                    )
                    sys.exit(rc)

            try:
                target = resumer.resolve_at_step(
                    full_id,
                    at_step,
                    staging_dir,
                    project_cwd=project_dir,
                    state=state,
                    materialize=not dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                click.echo(getattr(exc, "message", str(exc)), err=True)
                sys.exit(6)

            if dry_run:
                click.echo(" ".join(target.argv))
                click.echo(
                    f"would truncate {target.truncated_at_line} lines -> new session {target.new_session_id}"
                )
                sys.exit(0)

            rc = resumer.resume_session(
                target.new_session_id,
                project_cwd=project_dir,
                dry_run=False,
            )
            sys.exit(rc)

        rc = resumer.resume_session(
            session_id,
            project_cwd=project_dir,
            dry_run=dry_run,
        )
        sys.exit(rc)

    if at_step:
        message = "--at-step resume is currently supported only for claude-code traces."
        if as_json:
            click.echo(
                json.dumps(
                    error_response(
                        "UNSUPPORTED_AT_STEP_AGENT",
                        "resume",
                        message,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            click.echo(message, err=True)
        sys.exit(2)

    # Non-claude-code: print the native resume hint and exit 0.
    print_generic_hint(agent_name, session_id)


@click.command(
    "resume",
    cls=OpentracesCommand,
    hidden=True,
    examples=[
        "opentraces trace get abc12 --resume",
        "opentraces trace get abc12 --resume --dry-run",
    ],
    see_also=[
        ("opentraces trace get", "inspect the trace before resuming."),
        ("opentraces trace query", "browse trace ids."),
    ],
)
@click.argument("trace_id")
@click.option(
    "--at-step",
    "at_step",
    help="Fork from a specific step id; currently Claude Code only.",
)
@click.option("--dry-run", "dry_run", is_flag=True,
              help="Print the resume command instead of exec'ing it.")
@click.option("--json", "as_json", is_flag=True, help="Emit a structured resume packet.")
def trace_resume(
    trace_id: str,
    at_step: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Deprecated alias for ``ot trace get <id> --resume``."""
    _resume_trace_impl(trace_id, at_step, dry_run, as_json)
