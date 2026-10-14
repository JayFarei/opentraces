"""Unit tests for lossless superseded TraceRecord packing."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from opentraces_schema import Agent, Step, TraceRecord

from opentraces.core import bucket_pack_codecs as codecs
from opentraces.core import bucket_trace_pack as btp
from opentraces.core.bucket_layout import trace_records_root
from opentraces.core.bucket_trace_records import (
    read_trace_record_object,
    trace_record_path,
    write_trace_record,
)

OLD = time.time() - 3 * 3600


@pytest.fixture(autouse=True, params=codecs.WRITE_PREFERENCE)
def codec(request, monkeypatch):
    """Run every test once per codec this interpreter has."""
    if request.param not in codecs.available_codecs():
        pytest.skip(f"codec {request.param} not installed")
    monkeypatch.setattr(codecs, "WRITE_PREFERENCE", (request.param,))
    _isolated_root()
    yield request.param
    _isolated_root()


def _grown(turns: int, trace_id: str = "t1") -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": "growing"},
        steps=[Step(step_index=i, role="user", content=f"step {i} " + "abc " * 300) for i in range(1, turns + 1)],
    )


def _isolated_root() -> Path:
    root = trace_records_root()
    assert "pytest-" in str(root), f"refusing to touch a non-test bucket: {root}"
    return root


def _capture(turns: range, trace_id: str = "t1", *, age: float | None = OLD) -> Path:
    _isolated_root()
    for n in turns:
        obj = write_trace_record(_grown(n, trace_id), project_slug="proj", source_layer="bucket")
        if age is not None:
            os.utime(obj.path, (age + n, age + n))
    return trace_records_root() / "proj" / trace_id


def _objects(trace_dir: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in trace_dir.iterdir()
        if btp._OBJECT_NAME.match(p.name)
    }


def _versions(trace_dir: Path) -> dict[str, str]:
    return {n: hashlib.sha256(d).hexdigest() for n, d, _ in btp.iter_trace_record_versions(trace_dir)}


def _current_name(trace_dir: Path) -> str:
    return json.loads((trace_dir / "current.json").read_text())["object_path"].rsplit("/", 1)[-1]


def test_round_trip_is_byte_exact_and_keeps_current_loose() -> None:
    trace_dir = _capture(range(1, 6))
    before = _objects(trace_dir)
    report = btp.pack_superseded_trace_records()
    assert report["ok"] and report["records_packed"] == 4 and report["packs_written"] == 1
    assert set(_objects(trace_dir)) == {_current_name(trace_dir)}
    assert _versions(trace_dir) == before
    assert report["loose_records_before"] == 5 and report["loose_records_after"] == 1
    assert report["bytes_after"] < report["bytes_before"]
    # The hot path is untouched: current.json still resolves to a loose object.
    assert read_trace_record_object(trace_record_path("proj", "t1")).record.steps[-1].step_index == 5


def test_repack_is_idempotent_and_merges_new_versions() -> None:
    trace_dir = _capture(range(1, 4))
    btp.pack_superseded_trace_records()
    pack_bytes = btp.pack_path(trace_dir).read_bytes()
    again = btp.pack_superseded_trace_records()
    assert again["packs_written"] == 0 and again["records_packed"] == 0
    assert btp.pack_path(trace_dir).read_bytes() == pack_bytes
    before = _versions(trace_dir)
    _capture(range(4, 7))
    before.update(_objects(trace_dir))
    merged = btp.pack_superseded_trace_records()
    assert merged["records_packed"] == 3 and merged["packs_written"] == 1
    assert _versions(trace_dir) == before
    assert len(btp.list_pack(btp.pack_path(trace_dir))) == 5
    # Stored order is mtime order (oldest first).
    mtimes = [m.mtime_ns for m in btp.iter_pack_members(btp.pack_path(trace_dir))]
    assert mtimes == sorted(mtimes)


def test_loose_copy_already_in_pack_is_removed() -> None:
    """Crash after the pack rename but before unlink: next run finishes it."""
    trace_dir = _capture(range(1, 4))
    stale = {n: (trace_dir / n).read_bytes() for n in _objects(trace_dir) if n != _current_name(trace_dir)}
    btp.pack_superseded_trace_records()
    for name, data in stale.items():
        (trace_dir / name).write_bytes(data)
        os.utime(trace_dir / name, (OLD, OLD))
    report = btp.pack_superseded_trace_records()
    assert report["records_packed"] == 2 and report["packs_written"] == 0
    assert set(_objects(trace_dir)) == {_current_name(trace_dir)}


def test_crash_during_write_leaves_loose_files_and_no_pack(monkeypatch) -> None:
    trace_dir = _capture(range(1, 4))
    before = _objects(trace_dir)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(btp.tarfile.TarFile, "addfile", boom)
    report = btp.pack_superseded_trace_records()
    assert not report["ok"] and "disk full" in report["errors"][0]["detail"]
    assert _objects(trace_dir) == before
    assert not btp.pack_path(trace_dir).exists()
    assert not list(trace_dir.glob(".*.tmp"))


def test_stale_tmp_is_ignored_by_readers_and_swept() -> None:
    trace_dir = _capture(range(1, 4))
    tmp = trace_dir / f"{btp._TMP_PREFIX}abc.tmp"
    tmp.write_bytes(b"partial")
    assert read_trace_record_object(trace_record_path("proj", "t1")) is not None
    os.utime(tmp, (OLD, OLD))
    report = btp.pack_superseded_trace_records()
    assert report["tmp_removed"] == 1 and not tmp.exists()


def test_young_and_current_records_are_skipped() -> None:
    trace_dir = _capture(range(1, 3))
    _capture(range(3, 5), age=None)  # written just now
    report = btp.pack_superseded_trace_records()
    assert report["skipped_current"] == 1
    assert report["skipped_young"] == 1  # version 3; version 4 is current
    assert report["records_packed"] == 2
    assert len(_objects(trace_dir)) == 2


def test_dry_run_writes_nothing() -> None:
    trace_dir = _capture(range(1, 4))
    before = _objects(trace_dir)
    report = btp.pack_superseded_trace_records(dry_run=True)
    assert report["records_packable"] == 2 and report["records_packed"] == 0
    assert report["bytes_after"] == report["bytes_before"]
    assert _objects(trace_dir) == before and not btp.pack_path(trace_dir).exists()


def test_unpack_restores_loose_files_byte_exact() -> None:
    trace_dir = _capture(range(1, 5))
    before = {n: ((trace_dir / n).read_bytes(), (trace_dir / n).stat().st_mtime_ns) for n in _objects(trace_dir)}
    btp.pack_superseded_trace_records()
    report = btp.unpack_superseded_trace_records()
    assert report["ok"] and report["records_restored"] == 3 and report["packs_removed"] == 1
    assert not btp.pack_path(trace_dir).exists()
    after = {n: ((trace_dir / n).read_bytes(), (trace_dir / n).stat().st_mtime_ns) for n in _objects(trace_dir)}
    assert after == before


def test_packed_object_is_readable_through_the_record_reader() -> None:
    trace_dir = _capture(range(1, 3))
    oldest = min(_objects(trace_dir), key=lambda n: (trace_dir / n).stat().st_mtime_ns)
    btp.pack_superseded_trace_records()
    assert not (trace_dir / oldest).exists()
    obj = read_trace_record_object(trace_dir / oldest)
    assert obj is not None and len(obj.record.steps) == 1


def test_verify_checks_pack_members_without_false_dangling(monkeypatch) -> None:
    from opentraces.core.bucket_store import bucket_verify

    trace_dir = _capture(range(1, 4))
    btp.pack_superseded_trace_records()
    result = bucket_verify(full=True)
    assert result["ok"], result["errors"]
    assert result["packs_checked"] == 1
    # Corrupt one member's stored bytes by rewriting the pack with a bad sha.
    members = list(btp.iter_pack_members(btp.pack_path(trace_dir)))
    bad = [btp.PackMember(m.name, m.data + b" ", m.mtime_ns, m.mode, m.sha256) for m in members]
    monkeypatch.setattr(btp, "_verify_pack", lambda *a, **k: None)  # let the bad pack land
    btp._write_pack(btp.pack_path(trace_dir), iter(bad), 20)
    kinds = {e["kind"] for e in bucket_verify(full=True)["errors"]}
    assert "pack_member_sha" in kinds


def test_verify_flags_missing_current_object() -> None:
    from opentraces.core.bucket_store import bucket_verify

    trace_dir = _capture(range(1, 3))
    btp.pack_superseded_trace_records()
    (trace_dir / _current_name(trace_dir)).unlink()
    kinds = {e["kind"] for e in bucket_verify(full=True)["errors"]}
    assert "pack_current_missing" in kinds


def test_pointerless_trace_is_left_alone() -> None:
    trace_dir = _capture(range(1, 3))
    (trace_dir / "current.json").unlink()
    report = btp.pack_superseded_trace_records()
    assert report["records_packed"] == 0 and len(_objects(trace_dir)) == 2


@pytest.mark.parametrize("verb", ["pack", "unpack"])
def test_cli_json_shape(verb: str) -> None:
    from opentraces.cli import main

    _capture(range(1, 3))
    result = CliRunner().invoke(main, ["bucket", verb, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["schema_version"] == f"opentraces.bucket.{verb}.v1"
    for key in ("dry_run", "bytes_before", "bytes_after", "loose_records_before", "loose_records_after", "errors", "ok"):
        assert key in payload


def test_pack_header_names_the_codec(codec: str) -> None:
    trace_dir = _capture(range(1, 3))
    report = btp.pack_superseded_trace_records()
    assert report["codec"] == codec
    raw = btp.pack_path(trace_dir).read_bytes()
    assert raw.startswith(codecs.MAGIC)
    assert codecs.pack_codec(btp.pack_path(trace_dir)) == codec


def test_pack_written_with_another_codec_stays_readable_and_merges(codec: str, monkeypatch) -> None:
    other = [c for c in codecs.available_codecs() if c != codec]
    if not other:
        pytest.skip("needs two codecs")
    trace_dir = _capture(range(1, 3))
    monkeypatch.setattr(codecs, "WRITE_PREFERENCE", (other[0],))
    btp.pack_superseded_trace_records()
    before = _versions(trace_dir)
    monkeypatch.setattr(codecs, "WRITE_PREFERENCE", (codec,))
    _capture(range(3, 5))
    before.update(_objects(trace_dir))
    btp.pack_superseded_trace_records()
    assert codecs.pack_codec(btp.pack_path(trace_dir)) == codec
    assert _versions(trace_dir) == before



def test_corrupt_repack_never_replaces_old_pack_or_unlinks(monkeypatch) -> None:
    """A re-pack that writes corrupt bytes must leave the old pack (the only
    copy of its members) byte-identical and every loose object in place."""
    trace_dir = _capture(range(1, 4))
    btp.pack_superseded_trace_records()
    old_pack = btp.pack_path(trace_dir).read_bytes()
    _capture(range(4, 7))
    loose_before = _objects(trace_dir)
    real_open_writer = btp.open_writer

    class _FlipByte:
        def __init__(self, inner):
            self.inner = inner

        def write(self, data):
            data = bytearray(data)
            if len(data) > 1024:
                data[len(data) // 2] ^= 0xFF  # silent corruption inside a member
            return self.inner.write(bytes(data))

        def __getattr__(self, name):
            return getattr(self.inner, name)

    from contextlib import contextmanager

    @contextmanager
    def corrupt_writer(fh, codec, wlog):
        with real_open_writer(fh, codec, wlog) as inner:
            yield _FlipByte(inner)

    monkeypatch.setattr(btp, "open_writer", corrupt_writer)
    report = btp.pack_superseded_trace_records()
    assert not report["ok"] and report["records_packed"] == 0
    assert btp.pack_path(trace_dir).read_bytes() == old_pack
    assert _objects(trace_dir) == loose_before
    assert not list(trace_dir.glob(".*.tmp"))


def test_same_record_rewritten_with_new_envelope_packs_both_versions(monkeypatch) -> None:
    """Real writer path: an older record hash re-captured after it was packed
    gets a new ``written_at``; the trace must still pack, keeping both copies."""
    from opentraces.core import bucket_trace_records as btr

    trace_dir = _capture(range(1, 3))  # v1, v2
    btp.pack_superseded_trace_records()  # v1 packed
    # monkeypatch.context(), never monkeypatch.undo(): undo() would also drop
    # conftest's bucket isolation and point the rest of the test at ~/.opentraces.
    with monkeypatch.context() as m:
        m.setattr(btr, "utc_now_str", lambda: "2099-01-01T00:00:00.000001Z")
        _capture(range(1, 2))  # v1 again: same record hash, new envelope, now current
    v1 = _current_name(trace_dir)
    _capture(range(3, 4))  # v3 current; the re-written v1 is superseded
    before = sorted(_versions_all(trace_dir))
    assert [n for n, _ in before].count(v1) == 2
    report = btp.pack_superseded_trace_records()
    assert report["ok"], report["errors"]
    assert sorted(_versions_all(trace_dir)) == before
    assert btp.read_packed_object(trace_dir / v1).startswith(b"{")
    # Unpack keeps both: the newest as <digest>.json, the other as a variant.
    unpacked = btp.unpack_superseded_trace_records()
    assert unpacked["ok"] and unpacked["records_restored"] == 3
    assert (trace_dir / v1).exists() and len(list(trace_dir.glob(f"{v1[:-5]}.*.json"))) == 1
    assert sorted(_versions_all(trace_dir)) == before
    # ...and a re-pack folds the variant file back in losslessly.
    assert btp.pack_superseded_trace_records()["ok"]
    assert sorted(_versions_all(trace_dir)) == before
    assert set(_objects(trace_dir)) == {_current_name(trace_dir)}


def _versions_all(trace_dir: Path) -> list[tuple[str, str]]:
    return [(n, hashlib.sha256(d).hexdigest()) for n, d, _ in btp.iter_trace_record_versions(trace_dir)]
