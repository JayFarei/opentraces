"""Tree-SHA equivalence gate for the fast candidate-write path (#362).

The reclaim CANDIDATE WRITE forks git ~2-4x PER EVENT via
``StreamingChainWriter`` (hash-object, update-index --cacheinfo, cat-file -t,
mktree). On a ~964k-event project that is ~2-4M forks and dominates wall-clock
(~6h). ``FastChainWriter`` collapses those to O(1) persistent child processes
per finalize-segment by writing loose objects directly from Python and staging
the index via one streamed ``git update-index --index-info``.

The NON-NEGOTIABLE contract: for the SAME staged events + batch, the fast
writer's output TREE SHA must be byte-identical to ``StreamingChainWriter``'s.
If the tree SHA matches, everything downstream (reads, mirror, companions, CAS)
is unaffected. This module is that gate, authored RED against a not-yet-added
``FastChainWriter`` / ``make_chain_writer``.

Never touches ~/.opentraces — every test builds its own throwaway git repo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opentraces.core.trails.event_log import (
    StreamingChainWriter,
    _git,
)
from opentraces.core.trails.models import finalize_event

# The subjects under test — do not exist yet ⇒ this whole module starts RED.
from opentraces.core.trails.event_log import (  # noqa: E402
    FastChainWriter,
    make_chain_writer,
    write_loose,
)


# ---------------------------------------------------------------------------
# temp git repo + object helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    return repo


def _write_blob(repo: Path, text: str) -> str:
    """Write a real blob into the repo's object store; return its hex oid."""
    return subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=text.encode(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()


def _write_tree(repo: Path, entries: list[tuple[str, str]]) -> str:
    """Write a real tree (mode 100644 blob entries) into the store; return oid."""
    body = "".join(f"100644 blob {oid}\t{name}\n" for name, oid in entries)
    return subprocess.run(
        ["git", "mktree"],
        cwd=repo,
        input=body.encode(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()


def _oid(hex_: str) -> dict[str, str]:
    return {"algo": "sha1" if len(hex_) == 40 else "sha256", "hex": hex_}


def _make_event(seq: int, event_type: str, payload: dict, *, previous: str | None = None):
    return finalize_event(
        {
            "event_sequence": seq,
            "event_time": "2026-01-01T00:00:00Z",
            "previous_event_id": previous,
            "batch_id": "batch-fixture",
            "writer": "test",
            "capture_method": ["test"],
            "event_type": event_type,
            "payload": payload,
        }
    )


def _rich_fixture(repo: Path) -> list:
    """A deliberately adversarial staged-event stream exercising every branch
    of the tree-building logic."""
    # Real objects in the store.
    blob_a = _write_blob(repo, "retained blob A\n")
    blob_b = _write_blob(repo, "retained blob B — unicode ☃ \n")
    blob_dup = _write_blob(repo, "duplicated across events\n")
    tree_snap = _write_tree(repo, [("f.txt", blob_a)])
    tree_boundary = _write_tree(repo, [("g.txt", blob_b)])
    tree_shared = _write_tree(repo, [("h.txt", blob_dup)])
    missing_hex = "0" * 40  # valid shape, absent from store ⇒ must be skipped

    events: list = []
    prev: str | None = None

    def add(event_type: str, payload: dict) -> None:
        nonlocal prev
        ev = _make_event(len(events) + 1, event_type, payload, previous=prev)
        events.append(ev)
        prev = ev.event_id

    # 1. plain event, no oids.
    add("trace_patch_created", {"trace_patch_id": "p1", "trace_id": "t1"})
    # 2. embedded retained blob.
    add("git_anchor_recorded", {"blob_id": _oid(blob_a), "trace_id": "t1"})
    # 3. embedded non-blob oid (a TREE as blob_id) + a MISSING oid — both skipped
    #    from objects/blobs; the tree oid here is NOT under a tree_id key so it
    #    contributes no boundary entry either.
    add(
        "git_anchor_recorded",
        {"blob_id": _oid(tree_shared), "parent_id": _oid(missing_hex), "trace_id": "t1"},
    )
    # 4. duplicate embedded blob across two events ⇒ exactly one objects/blobs entry.
    add("git_anchor_recorded", {"blob_id": _oid(blob_dup), "trace_id": "t2"})
    add("git_anchor_recorded", {"blob_id": _oid(blob_dup), "trace_id": "t2"})
    # 5. trace_snapshot_created with tree_id + snapshot_id ⇒ snapshots/ + boundary trees/.
    add(
        "trace_snapshot_created",
        {"snapshot_id": "snap/one:after", "tree_id": _oid(tree_snap), "trace_id": "t1"},
    )
    # 6. boundary event with tree_id but NOT a snapshot ⇒ only a boundary trees/ entry.
    add(
        "trace_step_window_closed",
        {"tree_id": _oid(tree_boundary), "trace_id": "t1"},
    )
    # 7. second snapshot sharing the SAME tree oid ⇒ first-write-wins in snapshots/.
    add(
        "trace_snapshot_created",
        {"snapshot_id": "snap/two:after", "tree_id": _oid(tree_snap), "trace_id": "t3"},
    )
    # 8. embedded retained blob B + non-ASCII scalar payload.
    add("git_anchor_recorded", {"blob_id": _oid(blob_b), "note": "café ☕ 日本語", "trace_id": "t3"})
    return events


def _batch(event_count: int) -> dict:
    return {
        "batch_id": "batch-fixture",
        "writer": "test",
        "previous_event_log_head": None,
        "event_count": event_count,
        "imported": True,
    }


def _stream_into(writer, events, batch) -> str:
    for ev in events:
        writer.stage(ev)
    return writer.finalize(batch)


# ---------------------------------------------------------------------------
# THE gate
# ---------------------------------------------------------------------------


def test_rich_fixture_tree_sha_identical(tmp_path):
    repo = _init_repo(tmp_path)
    events = _rich_fixture(repo)
    batch = _batch(len(events))

    with StreamingChainWriter(repo) as ref:
        ref_tree = _stream_into(ref, events, batch)
    with FastChainWriter(repo) as fast:
        fast_tree = _stream_into(fast, events, batch)

    assert fast_tree == ref_tree


def test_multi_finalize_tree_sha_identical(tmp_path):
    """The scratch index is never reset — a second finalize on the SAME writer
    derives a tree over EVERYTHING staged so far, with batch.json overwritten by
    the new event_count. Both writers must agree at BOTH finalize points."""
    repo = _init_repo(tmp_path)
    events = _rich_fixture(repo)
    a, b = events[:4], events[4:]

    ref = StreamingChainWriter(repo)
    fast = FastChainWriter(repo)
    try:
        ref_t1 = _stream_into(ref, a, _batch(len(a)))
        fast_t1 = _stream_into(fast, a, _batch(len(a)))
        assert fast_t1 == ref_t1

        # stage the remainder on the SAME writers, finalize again.
        for ev in b:
            ref.stage(ev)
            fast.stage(ev)
        ref_t2 = ref.finalize(_batch(len(events)))
        fast_t2 = fast.finalize(_batch(len(events)))
        assert fast_t2 == ref_t2
        assert fast_t2 != fast_t1  # a real growth happened
    finally:
        ref.close()
        fast.close()


def test_zero_events_tree_sha_identical(tmp_path):
    """batch.json-only tree, no subtrees, early-return branch."""
    repo = _init_repo(tmp_path)
    with StreamingChainWriter(repo) as ref:
        ref_tree = ref.finalize(_batch(0))
    with FastChainWriter(repo) as fast:
        fast_tree = fast.finalize(_batch(0))
    assert fast_tree == ref_tree


def test_flat_tree_no_subtrees_identical(tmp_path):
    """Events with no tree_id / snapshot payloads ⇒ flat tree, snapshots// and
    trees/ absent (early-return branch of finalize)."""
    repo = _init_repo(tmp_path)
    blob = _write_blob(repo, "x\n")
    events = []
    prev = None
    for i in range(3):
        ev = _make_event(i + 1, "git_anchor_recorded", {"blob_id": _oid(blob)}, previous=prev)
        events.append(ev)
        prev = ev.event_id

    with StreamingChainWriter(repo) as ref:
        ref_tree = _stream_into(ref, events, _batch(len(events)))
    with FastChainWriter(repo) as fast:
        fast_tree = _stream_into(fast, events, _batch(len(events)))
    assert fast_tree == ref_tree

    # Confirm the flat shape: no snapshots// or trees/ entries at root.
    names = {
        line.split("\t", 1)[1]
        for line in _git(repo, ["ls-tree", fast_tree]).stdout.splitlines()
    }
    assert "snapshots" not in names
    assert "trees" not in names
    assert "events" in names and "objects" in names and "batch.json" in names


def test_missing_object_is_skipped_and_does_not_crash(tmp_path):
    repo = _init_repo(tmp_path)
    events = []
    ev = _make_event(1, "git_anchor_recorded", {"blob_id": _oid("0" * 40)})
    events.append(ev)
    with StreamingChainWriter(repo) as ref:
        ref_tree = _stream_into(ref, events, _batch(1))
    with FastChainWriter(repo) as fast:
        fast_tree = _stream_into(fast, events, _batch(1))
    assert fast_tree == ref_tree
    # objects/blobs must be absent (the only embedded oid was missing).
    names = {
        line.split("\t", 1)[1]
        for line in _git(repo, ["ls-tree", fast_tree]).stdout.splitlines()
    }
    assert "objects" not in names


# ---------------------------------------------------------------------------
# write_loose self-check — the one bit of novel Python
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "hello\n", "unicode ☃ café 日本語\n", "x" * 100_000],
)
def test_write_loose_matches_git_hash_object(tmp_path, text):
    repo = _init_repo(tmp_path)
    objects_dir = Path(
        _git(repo, ["rev-parse", "--git-path", "objects"]).stdout.strip()
    )
    if not objects_dir.is_absolute():
        objects_dir = (repo / objects_dir).resolve()

    oid = write_loose(repo, objects_dir, text)
    expected = subprocess.run(
        ["git", "hash-object", "--stdin"],
        cwd=repo,
        input=text.encode(),
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    assert oid == expected
    # git can now read it back as a blob with identical content.
    assert _git(repo, ["cat-file", "-t", oid]).stdout.strip() == "blob"
    got = subprocess.run(
        ["git", "cat-file", "blob", oid], cwd=repo, capture_output=True, check=True
    ).stdout
    assert got == text.encode()


def test_write_loose_is_idempotent(tmp_path):
    repo = _init_repo(tmp_path)
    objects_dir = Path(
        _git(repo, ["rev-parse", "--git-path", "objects"]).stdout.strip()
    )
    if not objects_dir.is_absolute():
        objects_dir = (repo / objects_dir).resolve()
    a = write_loose(repo, objects_dir, "same\n")
    b = write_loose(repo, objects_dir, "same\n")
    assert a == b


# ---------------------------------------------------------------------------
# factory / fallback
# ---------------------------------------------------------------------------


def test_make_chain_writer_returns_fast_by_default(tmp_path):
    repo = _init_repo(tmp_path)
    with make_chain_writer(repo) as writer:
        assert isinstance(writer, FastChainWriter)


def test_kill_switch_forces_reference(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("OT_TRAILS_FASTWRITER", "0")
    # The per-process probe cache must not mask the env — clear it if present.
    import opentraces.core.trails.event_log as el

    if hasattr(el, "_fast_writer_supported"):
        el._fast_writer_supported.cache_clear()
    with make_chain_writer(repo) as writer:
        assert isinstance(writer, StreamingChainWriter)
        assert not isinstance(writer, FastChainWriter)


def test_fallback_when_probe_unsupported(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    import opentraces.core.trails.event_log as el

    monkeypatch.setattr(el, "_fast_writer_supported", lambda cwd: False)
    with el.make_chain_writer(repo) as writer:
        assert isinstance(writer, StreamingChainWriter)
        assert not isinstance(writer, FastChainWriter)
    # And the reference path still produces the reference tree.
    events = _rich_fixture(repo)
    with el.make_chain_writer(repo) as writer:
        got = _stream_into(writer, events, _batch(len(events)))
    with StreamingChainWriter(repo) as ref:
        want = _stream_into(ref, events, _batch(len(events)))
    assert got == want


# ---------------------------------------------------------------------------
# fork-count sanity (non-gating perf lane) — proves the storm is gone
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_fast_writer_has_bounded_fork_count(tmp_path):
    repo = _init_repo(tmp_path)
    blob = _write_blob(repo, "b\n")
    events = []
    prev = None
    for i in range(200):
        ev = _make_event(i + 1, "git_anchor_recorded", {"blob_id": _oid(blob)}, previous=prev)
        events.append(ev)
        prev = ev.event_id

    def _count_git_forks(build) -> int:
        import opentraces.core.trails.event_log as el

        n = {"c": 0}
        real_run = subprocess.run
        real_popen = subprocess.Popen

        def run_spy(args, *a, **k):
            if args and args[0] == "git":
                n["c"] += 1
            return real_run(args, *a, **k)

        def popen_spy(args, *a, **k):
            if args and args[0] == "git":
                n["c"] += 1
            return real_popen(args, *a, **k)

        orig_run = el.subprocess.run
        orig_popen = el.subprocess.Popen
        el.subprocess.run = run_spy
        el.subprocess.Popen = popen_spy
        try:
            build()
        finally:
            el.subprocess.run = orig_run
            el.subprocess.Popen = orig_popen
        return n["c"]

    def build_ref():
        with StreamingChainWriter(repo) as w:
            _stream_into(w, events, _batch(len(events)))

    def build_fast():
        with FastChainWriter(repo) as w:
            _stream_into(w, events, _batch(len(events)))

    ref_forks = _count_git_forks(build_ref)
    fast_forks = _count_git_forks(build_fast)
    # Reference forks scale with events (>= ~200); fast must be a small constant.
    assert fast_forks < 20
    assert fast_forks * 5 < ref_forks


@pytest.mark.perf
def test_fast_writer_wall_clock_speedup(tmp_path):
    """Non-gating smoke: the fast writer must be an ORDER OF MAGNITUDE below the
    reference on the same staged set, with a byte-identical tree — no absolute
    CI number asserted (machine-dependent)."""
    import time

    repo = _init_repo(tmp_path)
    blob = _write_blob(repo, "shared\n")
    n = 2000
    events = []
    prev = None
    for i in range(n):
        ev = _make_event(
            i + 1,
            "git_anchor_recorded",
            {"trace_id": f"t{i % 7}", "blob_id": _oid(blob), "note": f"s{i}"},
            previous=prev,
        )
        events.append(ev)
        prev = ev.event_id
    batch = _batch(n)

    t = time.perf_counter()
    with StreamingChainWriter(repo) as w:
        ref_tree = _stream_into(w, events, batch)
    ref_dt = time.perf_counter() - t

    t = time.perf_counter()
    with FastChainWriter(repo) as w:
        fast_tree = _stream_into(w, events, batch)
    fast_dt = time.perf_counter() - t

    assert fast_tree == ref_tree
    assert fast_dt * 10 < ref_dt, f"fast={fast_dt:.3f}s ref={ref_dt:.3f}s"
