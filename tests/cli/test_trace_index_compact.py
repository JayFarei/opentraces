"""Issue #40 — legacy ``index.db`` bloat: A1 de-normalization, A2 stale-gen
purge, and the ``trace index compact`` reclaim verb.

All write-path work runs on a synthetic isolated corpus under the conftest's
tmp HOME (the autouse ``_isolate_opentraces_global_state`` fixture redirects
``OPENTRACES_DIR``). The live ``~/.opentraces`` DB is never touched.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from click.testing import CliRunner
from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord

from opentraces.cli import main
from opentraces.core import config as cfg
from opentraces.core import trace_index as ti


# ---------------------------------------------------------------------------
# Synthetic corpus helpers
# ---------------------------------------------------------------------------


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> Path:
    traces_dir = cfg.get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"{record.trace_id}.jsonl"
    path.write_text(record.model_dump_json() + "\n")
    return path


def _many_node_trace(
    trace_id: str,
    *,
    generation_index: int = 0,
    session_id: str = "sess-compact",
    node_count: int = 60,
    intent_token: str = "ordinary",
) -> TraceRecord:
    """A trace that expands into ``node_count`` trace_map_node units."""
    steps = [
        Step(
            step_index=1,
            role="user",
            content=(
                f"Fix the parser bug and prove the failing test passes "
                f"with {intent_token}"
            ),
        )
    ]
    for i in range(2, node_count):
        steps.append(
            Step(
                step_index=i,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id=f"tc{i}",
                        tool_name="Edit",
                        input={
                            "file_path": f"src/file_{i}.py",
                            "old_string": "before",
                            "new_string": "after",
                        },
                    )
                ],
                observations=[
                    Observation(source_call_id=f"tc{i}", content=f"edited file_{i}")
                ],
            )
        )
    return TraceRecord(
        trace_id=trace_id,
        session_id=session_id,
        generation_index=generation_index,
        agent=Agent(name="claude-code"),
        task={"description": "Fix parser bug"},
        steps=steps,
        outcome={"success": True, "committed": True},
    )


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _unit_count(db: Path, trace_id: str, *, unit_type: str | None = None) -> int:
    with _connect_ro(db) as conn:
        if unit_type is None:
            row = conn.execute(
                "select count(*) from units where trace_id = ?", (trace_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "select count(*) from units where trace_id = ? and unit_type = ?",
                (trace_id, unit_type),
            ).fetchone()
    return int(row[0])


def _map_node_count(db: Path, trace_id: str) -> int:
    with _connect_ro(db) as conn:
        row = conn.execute(
            "select count(*) from trace_map_nodes where trace_id = ?", (trace_id,)
        ).fetchone()
    return int(row[0])


def _freelist(db: Path) -> int:
    with _connect_ro(db) as conn:
        return int(conn.execute("PRAGMA freelist_count").fetchone()[0])


def _project_slug_from_db(db: Path) -> str:
    with _connect_ro(db) as conn:
        row = conn.execute("select project_slug from traces limit 1").fetchone()
    return str(row["project_slug"])


# ---------------------------------------------------------------------------
# A1 — node units must not de-normalize trace-level intent/title/summary
# ---------------------------------------------------------------------------


def test_a1_node_units_drop_trace_level_fields(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project,
        _many_node_trace("t-a1", node_count=100, intent_token="zzqqxx_rareterm"),
    )

    summary = ti.rebuild_index()
    db = summary.index_path

    # Node units carry no trace-level title/intent and no summary metadata.
    with _connect_ro(db) as conn:
        node_rows = conn.execute(
            "select title_text, intent_text, metadata_json from units "
            "where trace_id = 't-a1' and unit_type = 'trace_map_node'"
        ).fetchall()
    assert node_rows, "expected node units to exist"
    summary_keys = set(ti._TRACE_LEVEL_SUMMARY_KEYS)
    for row in node_rows:
        assert row["title_text"] == ""
        assert row["intent_text"] == ""
        meta = json.loads(row["metadata_json"])
        assert summary_keys.isdisjoint(meta.keys()), (
            "node unit must not stamp trace-level summary metadata: "
            f"found {summary_keys & set(meta.keys())}"
        )

    # The single trace-level unit retains intent/title/summary.
    with _connect_ro(db) as conn:
        trace_row = conn.execute(
            "select title_text, intent_text, metadata_json from units "
            "where unit_id = 'tu:t-a1:trace'"
        ).fetchone()
    assert trace_row["title_text"]
    assert trace_row["intent_text"]
    assert "summary" in json.loads(trace_row["metadata_json"])


def test_a1_node_candidate_packet_joins_trace_level_fields(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project, _many_node_trace("t-a1b", node_count=80, intent_token="ordinary")
    )
    ti.rebuild_index()

    page = ti.query_index_page(candidate_kind="trace_map_node", limit=10)
    node_packets = [c for c in page.candidates if c.trace_id == "t-a1b"]
    assert node_packets, "expected node candidate packets"
    for packet in node_packets:
        # CandidatePacket shape unchanged: title + intent hydrated from the
        # sibling trace-level unit at packet-build time.
        assert packet.title == "Fix parser bug"
        assert packet.intent_preview
        assert "parser" in packet.intent_preview.lower()


def test_a1_fts_intent_only_term_still_returns_trace(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project,
        _many_node_trace("t-a1c", node_count=50, intent_token="zzqqxx_rareterm"),
    )
    ti.rebuild_index()

    hits = ti.query_index(lex="zzqqxx_rareterm")
    assert "t-a1c" in {c.trace_id for c in hits}, (
        "an intent-only term must still match via the retained trace-level unit"
    )


# ---------------------------------------------------------------------------
# A2 — refresh purges superseded generations' heavy units, retains map rows
# ---------------------------------------------------------------------------


def test_a2_refresh_purges_superseded_generation_units(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    for trace_id, gen in (("g0", 0), ("g1", 1), ("g2", 2)):
        _write_project_trace(
            project,
            _many_node_trace(trace_id, generation_index=gen, session_id="sess-A"),
        )

    summary = ti.rebuild_index()
    db = summary.index_path

    # Superseded generations (g0, g1) keep exactly their single trace-level
    # unit and zero node units, but retain their trace_map_nodes rows.
    for old in ("g0", "g1"):
        assert _unit_count(db, old, unit_type="trace_map_node") == 0
        assert _unit_count(db, old, unit_type="trace") == 1
        assert _map_node_count(db, old) > 0

    # The latest generation (g2) keeps its full unit expansion.
    assert _unit_count(db, "g2", unit_type="trace_map_node") > 0


def test_a2_trace_map_on_superseded_generation_exits_zero(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    for trace_id, gen in (("og0", 0), ("og1", 1)):
        _write_project_trace(
            project,
            _many_node_trace(trace_id, generation_index=gen, session_id="sess-B"),
        )
    ti.rebuild_index()

    # The map for the superseded generation is still resolvable.
    assert ti.get_trace_map("og0") is not None

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "map", "og0", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["trace_id"] == "og0"
    assert payload["map"]["nodes"]


def test_a2_include_superseded_returns_trace_level_units(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    for trace_id, gen in (("s0", 0), ("s1", 1), ("s2", 2)):
        _write_project_trace(
            project,
            _many_node_trace(trace_id, generation_index=gen, session_id="sess-C"),
        )
    ti.rebuild_index()

    # Default query (no latest_generation filter) still returns a trace-level
    # row for every generation including the superseded ones.
    packets = ti.query_index(lex="parser bug")
    trace_ids = {c.trace_id for c in packets if c.unit_type == "trace"}
    assert {"s0", "s1", "s2"}.issubset(trace_ids)


def test_a2_latest_generation_query_byte_identical_pre_post_purge(tmp_path):
    """The --latest-generation result must be unchanged by the purge.

    Build a session with a writer-declared supersession chain so
    ``latest_generation=True`` is meaningful, capture the result, then prove a
    second refresh (idempotent purge) returns a byte-identical page.
    """
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    g0 = _many_node_trace("p0", generation_index=0, session_id="sess-D")
    g1 = _many_node_trace("p1", generation_index=1, session_id="sess-D")
    g1.metadata["superseded_trace_ids"] = ["p0"]
    _write_project_trace(project, g0)
    _write_project_trace(project, g1)
    ti.rebuild_index()

    before = ti.query_index(lex="parser bug", latest_generation=True)
    before_json = [c.model_dump_json() for c in before]
    # A second refresh re-runs the idempotent purge.
    ti.refresh_index()
    after = ti.query_index(lex="parser bug", latest_generation=True)
    after_json = [c.model_dump_json() for c in after]
    assert before_json == after_json
    # The superseded generation is excluded by the writer-declared chain.
    assert "p0" not in {c.trace_id for c in after}


# ---------------------------------------------------------------------------
# B — ``trace index compact`` verb
# ---------------------------------------------------------------------------


def _insert_superseded_units_directly(db: Path, record: TraceRecord, project_slug: str):
    """Bypass the purge to simulate a pre-fix DB carrying superseded units.

    Inserts a full unit expansion + traces row for ``record`` as if it were an
    un-purged older generation, so ``compact_index`` has stale rows to reclaim.
    """
    from opentraces.core.trace_map import build_trace_map

    trace_map = build_trace_map(record)
    with ti._connect(db) as conn:
        ti._insert_trace(conn, record, project_slug, db, trace_map)
        for unit in ti._build_units(record, trace_map, project_slug):
            ti._insert_unit(conn, unit)
        conn.commit()


def test_compact_incremental_purge_reclaims_superseded_units(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    latest = _many_node_trace("c-latest", generation_index=5, session_id="sess-E")
    _write_project_trace(project, latest)
    summary = ti.rebuild_index()
    db = summary.index_path
    project_slug = _project_slug_from_db(db)

    # Simulate an un-purged older generation sitting in the live DB.
    stale = _many_node_trace("c-stale", generation_index=1, session_id="sess-E")
    _insert_superseded_units_directly(db, stale, project_slug)
    assert _unit_count(db, "c-stale", unit_type="trace_map_node") > 0

    s = ti.compact_index(db, vacuum=False)
    assert s.purged_units > 0
    # The stale generation's node units are gone; its trace + map rows stay.
    assert _unit_count(db, "c-stale", unit_type="trace_map_node") == 0
    assert _unit_count(db, "c-stale", unit_type="trace") == 1
    assert _map_node_count(db, "c-stale") > 0
    # The latest generation is untouched.
    assert _unit_count(db, "c-latest", unit_type="trace_map_node") > 0


def test_compact_vacuum_reclaims_freelist(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    record = _many_node_trace("c-vac", node_count=60, session_id="sess-F")
    trace_path = _write_project_trace(project, record)
    ti.rebuild_index()
    db = ti.default_index_path()

    # Re-ingest the same trace_path repeatedly: delete-before-reinsert keeps
    # one logical copy but grows the freelist with reclaimable pages.
    for n in range(1, 11):
        record = _many_node_trace(
            "c-vac", node_count=60, session_id="sess-F", intent_token=f"salt{n}"
        )
        trace_path.write_text(record.model_dump_json() + "\n")
        future = time.time() + n
        import os

        os.utime(trace_path, (future, future))
        ti.refresh_index()

    assert _freelist(db) > 0, "re-ingesting should accumulate freelist pages"

    s = ti.compact_index(db, vacuum=True)
    assert s.vacuumed is True
    assert s.vacuum_skipped_reason is None
    assert _freelist(db) == 0
    assert s.freelist_before > 0
    assert s.freelist_after == 0


def test_compact_vacuum_refused_when_disk_too_small(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _many_node_trace("c-disk", session_id="sess-G"))
    ti.rebuild_index()
    db = ti.default_index_path()

    # Simulate a filesystem with one free byte: VACUUM needs >= file size.
    monkeypatch.setattr(ti, "_free_disk_bytes", lambda p: 1)
    s = ti.compact_index(db, vacuum=True)
    assert s.vacuumed is False
    assert s.vacuum_skipped_reason is not None
    assert "insufficient free disk" in s.vacuum_skipped_reason


def test_compact_db_sizes_reflected_in_cli_json(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _many_node_trace("c-cli", session_id="sess-H"))
    ti.rebuild_index()

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "compact", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert set(payload) == {"status", "compact", "db_sizes_before", "db_sizes_after"}
    compact = payload["compact"]
    assert "purged_units" in compact
    assert "size_bytes_before" in compact
    assert "freelist_before" in compact
    # The db_sizes block reuses the PR #38 dbstat helper for both DBs.
    assert payload["db_sizes_before"]["exists"] is True
    assert payload["db_sizes_after"]["exists"] is True


def test_compact_concurrent_reader_succeeds_during_purge(tmp_path):
    """A concurrent reader must keep answering while compact purges in batches.

    The incremental purge uses short per-batch transactions specifically so a
    reader is never blocked for long. We hammer reads from a thread while a
    compact pass with many stale generations runs, and assert every read
    succeeds (no ``database is locked`` surfaced to the reader).
    """
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    latest = _many_node_trace("cc-latest", generation_index=20, session_id="sess-I")
    _write_project_trace(project, latest)
    summary = ti.rebuild_index()
    db = summary.index_path
    project_slug = _project_slug_from_db(db)

    # Seed many un-purged superseded generations so the batched purge runs
    # across several transactions.
    for gen in range(0, 8):
        stale = _many_node_trace(
            f"cc-stale-{gen}", generation_index=gen, session_id="sess-I"
        )
        _insert_superseded_units_directly(db, stale, project_slug)

    errors: list[str] = []
    stop = threading.Event()

    def _reader() -> None:
        while not stop.is_set():
            try:
                with _connect_ro(db) as conn:
                    conn.execute(
                        "select count(*) from units where unit_type = 'trace'"
                    ).fetchone()
            except sqlite3.OperationalError as exc:  # pragma: no cover
                errors.append(str(exc))
            time.sleep(0.001)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    try:
        s = ti.compact_index(db, vacuum=False)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert s.purged_units > 0
    assert not errors, f"concurrent reader hit lock errors: {errors[:3]}"
