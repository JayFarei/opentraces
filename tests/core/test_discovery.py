from __future__ import annotations

from opentraces.core import paths
from opentraces.core.discovery import _topic_page, discover
from opentraces.core.trace_search_snapshot import build_trace_search_snapshot
from opentraces_schema import Agent, Step, ToolCall, TraceRecord


def _write_trace(slug: str, record: TraceRecord) -> None:
    trace_dir = paths.PROJECTS_DIR / slug / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _trace(
    trace_id: str,
    text: str,
    *,
    day: str,
    hms: str,
    file_path: str | None = None,
) -> TraceRecord:
    ts = f"{day}T{hms}Z"
    steps = [Step(step_index=1, role="user", content=text)]
    if file_path is not None:
        steps.append(
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id=f"{trace_id}-tool",
                        tool_name="Edit",
                        input={
                            "file_path": file_path,
                            "old_string": "old",
                            "new_string": "new",
                        },
                    )
                ],
            )
        )
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="pi", model="anthropic/claude-opus-4-6"),
        task={"description": text},
        timestamp_start=ts,
        timestamp_end=ts,
        steps=steps,
        outcome={"success": True, "committed": True},
    )


def test_topic_page_orders_term_hits_newest_first() -> None:
    # Issue #27 item G: term queries must come back newest-first (recency parity
    # with the base ``trace query`` surface), not BM25-only ordering. All three
    # traces match the same term "site" but carry distinct task text (so the
    # snapshot's search_group_key dedupe keeps them as three separate groups).
    # The OLDEST trace deliberately repeats "site" the most, so a BM25-only
    # ordering would surface it FIRST — recency ordering must instead put it
    # LAST. This makes the assertion a genuine regression for the sort_order fix.
    _write_trace(
        "demo-project",
        _trace("trace-old", "Fix the site site site header on the site", day="2026-06-07", hms="09:00:00"),
    )
    _write_trace("demo-project", _trace("trace-mid", "Refactor the site search index", day="2026-06-08", hms="09:00:00"))
    _write_trace("demo-project", _trace("trace-new", "Tune the site landing page", day="2026-06-09", hms="09:00:00"))
    build_trace_search_snapshot()

    page = _topic_page(
        "site",
        limit=10,
        latest_generation=True,
        project=None,
        index_path=None,
    )

    ordered = [hit.trace_id for hit in page.hits]
    assert ordered == ["trace-new", "trace-mid", "trace-old"]


def test_discover_preserves_day_grouping_with_recency_order() -> None:
    # Newest-first ordering must not break the day-grouping behavior: groups are
    # still keyed by day (newest day first), and within a day the cards stay in
    # newest-first order.
    _write_trace("demo-project", _trace("trace-d1-early", "Fix the site header layout", day="2026-06-08", hms="08:00:00"))
    _write_trace("demo-project", _trace("trace-d1-late", "Refactor the site search index", day="2026-06-08", hms="18:00:00"))
    _write_trace("demo-project", _trace("trace-d2", "Tune the site landing page", day="2026-06-09", hms="09:00:00"))
    build_trace_search_snapshot()

    packet = discover("site", limit=10, per_group=5)

    group_keys = [group.key for group in packet.groups]
    assert group_keys == ["2026-06-09", "2026-06-08"]

    day1_cards = [
        card.trace_id
        for group in packet.groups
        if group.key == "2026-06-08"
        for card in group.cards
    ]
    assert day1_cards == ["trace-d1-late", "trace-d1-early"]


def test_topic_page_breaks_recency_ties_on_relevance() -> None:
    # Issue #27 item G regression: when several traces share the same timestamp,
    # a pure ``sort_order="recency"`` kernel query tie-breaks on ``trace_id`` and
    # discards relevance, so a trace that only matches the term via file-path
    # URL sub-tokenization (plan 088 U6a) can be surfaced AHEAD of genuine
    # description matches. Discovery must keep recency as the primary order but
    # fall back to relevance for equal timestamps, so the genuine match wins.
    #
    # All three traces share the SAME timestamp. trace-api matches "site" only
    # through its file path web/site/src/app.tsx; the other two match "site" in
    # their description (a stronger relevance signal).
    same_day, same_hms = "2026-06-09", "09:00:00"
    _write_trace(
        "demo-project",
        _trace(
            "trace-site-1",
            "Fix the site search ranking",
            day=same_day,
            hms=same_hms,
            file_path="web/site/src/app.tsx",
        ),
    )
    _write_trace(
        "demo-project",
        _trace(
            "trace-site-2",
            "Review the site search filters",
            day=same_day,
            hms=same_hms,
            file_path="web/site/src/app.tsx",
        ),
    )
    _write_trace(
        "demo-project",
        _trace(
            "trace-api",
            "Tune the API importer",
            day=same_day,
            hms=same_hms,
            file_path="web/site/src/app.tsx",
        ),
    )
    build_trace_search_snapshot()

    page = _topic_page(
        "site",
        limit=10,
        latest_generation=True,
        project="demo-project",
        index_path=None,
    )

    ordered = [hit.trace_id for hit in page.hits]
    # All three are same-timestamp, so relevance (the upstream order, preserved
    # by the stable recency re-sort) decides — the genuine description matches
    # must come before the file-path-only match.
    assert ordered[-1] == "trace-api"
    assert set(ordered[:2]) == {"trace-site-1", "trace-site-2"}

    # And the limit=1 hydration path (mirrors the adjacent snapshot test) must
    # pick a genuine match, never the file-path-only one.
    top_page = _topic_page(
        "site",
        limit=1,
        latest_generation=True,
        project="demo-project",
        index_path=None,
    )
    assert top_page.hits[0].trace_id in {"trace-site-1", "trace-site-2"}
