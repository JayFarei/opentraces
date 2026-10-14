"""Superseded raw-blob GC: write-path drop + ``bucket prune --superseded-raw``."""

from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

from click.testing import CliRunner
from opentraces_schema import Agent, Step, TraceRecord


def _capture(tmp_path: Path, trace_id: str, content: str) -> dict:
    from opentraces.core.bucket_raw_sources import write_raw_source_artifact

    source = tmp_path / f"{trace_id}.jsonl"
    source.write_text(content)
    return write_raw_source_artifact(
        source,
        trace_id=trace_id,
        project_slug="demo",
        source_kind="claude-code-session-jsonl",
        parser="claude-code",
    )


def _bucket() -> Path:
    from opentraces.core import paths

    return paths.bucket_dir()


def _blob_files() -> list[str]:
    from opentraces.core.bucket_layout import raw_sources_root

    root = raw_sources_root() / "blobs"
    return sorted(p.relative_to(_bucket()).as_posix() for p in root.glob("*/*"))


def _age(path: Path, seconds: int = 2 * 60 * 60) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


def _orphan(name: str, body: bytes, *, aged: bool = True) -> Path:
    from opentraces.core.bucket_layout import raw_sources_root

    path = raw_sources_root() / "blobs" / name[:2] / f"{name * (64 // len(name))}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if aged:
        _age(path)
    return path


def test_identical_recapture_keeps_its_blob(tmp_path):
    first = _capture(tmp_path, "t1", "same\n")
    second = _capture(tmp_path, "t1", "same\n")

    assert first["blob_path"] == second["blob_path"]
    assert _blob_files() == [first["blob_path"]]


def test_shared_digest_blob_survives_until_last_link_moves(tmp_path):
    shared = _capture(tmp_path, "t1", "shared\n")
    assert _capture(tmp_path, "t2", "shared\n")["blob_path"] == shared["blob_path"]

    grown = _capture(tmp_path, "t1", "shared\nmore\n")
    assert shared["blob_path"] in _blob_files(), "t2 still names the shared blob"

    t2_grown = _capture(tmp_path, "t2", "shared\nother\n")
    assert _blob_files() == sorted([grown["blob_path"], t2_grown["blob_path"]])


def test_rewritten_not_grown_session_keeps_old_blob(tmp_path):
    first = _capture(tmp_path, "t1", "line1\nline2\n")
    second = _capture(tmp_path, "t1", "line1\nREWRITTEN\nline3\n")

    assert _blob_files() == sorted([first["blob_path"], second["blob_path"]])


def test_write_path_keeps_old_blob_when_a_link_is_unreadable(tmp_path):
    from opentraces.core.bucket_layout import raw_sources_root

    first = _capture(tmp_path, "t1", "v1\n")
    (raw_sources_root() / "sources" / "demo" / "broken.json").write_text("{not json")
    _capture(tmp_path, "t1", "v1\nv2\n")

    assert first["blob_path"] in _blob_files()


def _write_sources_sidecar(blob_rel: str) -> None:
    from opentraces.core.bucket_layout import trace_v1_sources_path

    sidecar = trace_v1_sources_path("demo", "t-other")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    entry = {"blob_path": blob_rel, "content_digest": "sha256:" + Path(blob_rel).name.split(".")[0]}
    sidecar.write_bytes(gzip.compress((json.dumps(entry) + "\n").encode()))


def test_current_sidecar_reference_protects_older_blob(tmp_path):
    from opentraces.core.bucket_superseded import sweep_superseded_raw

    old = _capture(tmp_path, "t1", "a\n")
    _write_sources_sidecar(old["blob_path"])
    _capture(tmp_path, "t1", "a\nb\n")
    assert old["blob_path"] in _blob_files(), "write path honours the sidecar"

    _age(_bucket() / old["blob_path"])
    result = sweep_superseded_raw(dry_run=False)
    assert result["raw_blobs"]["count"] == 0
    assert old["blob_path"] in _blob_files(), "sweep honours the sidecar"


def _seed_backlog(tmp_path: Path) -> dict[str, Path]:
    """Pre-fix on-disk state plus the crash windows."""

    big = "x" * 5000
    current = _bucket() / _capture(tmp_path, "t1", f"{big}\nturn2\nturn3\n")["blob_path"]
    _age(current)
    return {
        "current": current,
        # Predecessors the old writer left behind (also: crash after repoint).
        "prefix_large": _orphan("a", f"{big}\nturn2\n".encode()),
        "prefix_small": _orphan("b", b"x" * 100),
        # Unreferenced but not a prefix of anything referenced: kept.
        "not_prefix": _orphan("c", f"{big}\nDIVERGED\n".encode()),
        # Crash after writing a new blob, before repointing: young, kept.
        "young": _orphan("d", f"{big}\nturn2\nturn3\nturn4\n".encode(), aged=False),
    }


def test_sweep_dry_run_reports_counts_and_bytes_without_deleting(tmp_path):
    from opentraces.core.bucket_superseded import sweep_superseded_raw

    seeded = _seed_backlog(tmp_path)
    result = sweep_superseded_raw(dry_run=True)

    expected = [seeded["prefix_large"], seeded["prefix_small"]]
    assert result["raw_blobs"]["count"] == 2
    assert result["raw_blobs"]["bytes"] == sum(p.stat().st_size for p in expected)
    assert result["kept_not_prefix"]["count"] == 1
    assert result["skipped"]["recent"] == 1
    assert all(path.exists() for path in seeded.values())


def test_sweep_apply_removes_only_prefix_garbage(tmp_path):
    from opentraces.core.bucket_superseded import sweep_superseded_raw

    seeded = _seed_backlog(tmp_path)
    result = sweep_superseded_raw(dry_run=False)

    assert result["raw_blobs"]["count"] == 2
    assert not seeded["prefix_large"].exists() and not seeded["prefix_small"].exists()
    for key in ("current", "not_prefix", "young"):
        assert seeded[key].exists(), key


def test_sweep_leaves_raw_alone_when_any_link_is_unreadable(tmp_path):
    from opentraces.core.bucket_layout import raw_sources_root
    from opentraces.core.bucket_superseded import sweep_superseded_raw

    seeded = _seed_backlog(tmp_path)
    (raw_sources_root() / "sources" / "demo" / "broken.json").write_text("{not json")
    result = sweep_superseded_raw(dry_run=False)

    assert result["raw_blobs"]["count"] == 0
    assert result["skipped"]["unreadable_refs"] == 1
    assert seeded["prefix_large"].exists()


def test_trace_record_history_is_never_dropped(tmp_path):
    from opentraces.core.bucket_superseded import sweep_superseded_raw
    from opentraces.core.bucket_trace_records import write_trace_record

    def record(content: str) -> TraceRecord:
        return TraceRecord(
            trace_id="t1",
            session_id="s-t1",
            agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
            steps=[Step(step_index=1, role="user", content=content)],
        )

    old = write_trace_record(record("first"), project_slug="demo", source_layer="canonical")
    write_trace_record(record("first then more"), project_slug="demo", source_layer="canonical")
    _age(old.path)
    sweep_superseded_raw(dry_run=False)
    assert old.path.exists()


def test_bucket_prune_superseded_raw_is_opt_in(tmp_path):
    from opentraces.core.bucket_maintenance import bucket_prune

    seeded = _seed_backlog(tmp_path)
    plain = bucket_prune(dry_run=False)
    assert "superseded" not in plain
    assert seeded["prefix_large"].exists()

    dry = bucket_prune(dry_run=True, superseded_raw=True)
    assert dry["superseded"]["raw_blobs"]["count"] == 2
    assert dry["deleted"] == 0
    assert seeded["prefix_large"].exists()


def test_cli_prune_superseded_raw_json_keeps_envelope_shape(tmp_path):
    from opentraces.cli import main

    seeded = _seed_backlog(tmp_path)
    result = CliRunner().invoke(main, ["bucket", "prune", "--superseded-raw", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert sorted(payload) == ["deleted", "prune", "status", "would_delete"]
    assert payload["prune"]["superseded"]["raw_blobs"]["count"] == 2
    assert seeded["prefix_large"].exists()
