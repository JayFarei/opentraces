"""Perf/CPU-burn regression tests for the OTLP RawBodyWatcher (issue #251).

The receiver daemon's raw-body watcher burned 70-90% CPU permanently because
``_scan_once`` re-examined every unpaired ``*.request.json`` on every 200ms
poll. On real Claude Code output the filename pairing never succeeds (plan-078
gap (i): requests are ``<uuid>.request.json`` but responses are
``req_<id>.response.json``), so no request_id ever entered ``_seen`` and the
scan set never shrank. Under ``delete`` retention the corpus also grew
unbounded (the sweep was an explicit no-op).

These tests pin the three fixes and are hermetic: no ~/.opentraces access, no
wall-clock sleeps, mtimes set explicitly via os.utime, ``now`` passed in.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from opentraces.capture.otlp.raw_body_watcher import RawBodyWatcher


def _noop_pair(request_id: str, request_body: dict, response_body: dict) -> None:  # pragma: no cover - never called for unpaired corpus
    raise AssertionError("callback must not fire for an unpaired request")


def _write_old(path: Path, age_s: float, *, now: float) -> None:
    """Write a JSON body file and backdate its mtime by ``age_s`` seconds."""
    path.write_text(json.dumps({"messages": []}), encoding="utf-8")
    stamp = now - age_s
    os.utime(path, (stamp, stamp))


# --------------------------------------------------------------------------- #
# Fix 2: negative-cache stale unpaired requests
# --------------------------------------------------------------------------- #


def test_stale_unpaired_request_is_negative_cached(tmp_path, monkeypatch):
    """A request whose response never arrives and that is older than the
    pairing deadline must leave the scan set after ONE scan, so subsequent
    scans never stat it again."""
    now = time.time()
    req = tmp_path / "abc-uuid.request.json"
    _write_old(req, age_s=7200.0, now=now)  # 2h old, well past the 1h deadline

    watcher = RawBodyWatcher(tmp_path, _noop_pair, retention="delete")

    # Count how often the response path for our stale request is stat-checked.
    exists_calls = {"n": 0}
    orig_exists = Path.exists

    def counting_exists(self):
        if self.name.endswith(".response.json"):
            exists_calls["n"] += 1
        return orig_exists(self)

    monkeypatch.setattr(Path, "exists", counting_exists)

    watcher._scan_once(now=now)
    # NEW: request is negative-cached and a skip counter is exposed.
    assert watcher.stale_skipped == 1
    assert "abc-uuid" in watcher._seen

    # Perturb the dir so the mtime short-circuit does NOT skip scan #2 — we
    # want to prove the negative cache (not the dir-mtime guard) shrank the set.
    marker = tmp_path / "touch.request.json"
    _write_old(marker, age_s=0.0, now=now)
    marker.unlink()

    watcher._scan_once(now=now + 1.0)
    # The stale request was stat-checked exactly once, on the first scan only.
    assert exists_calls["n"] == 1, exists_calls
    assert watcher.stale_skipped == 1  # not re-counted


# --------------------------------------------------------------------------- #
# Fix 1: dir-mtime short-circuit
# --------------------------------------------------------------------------- #


def test_unchanged_dir_skips_the_glob(tmp_path, monkeypatch):
    """A second scan over an unchanged directory must not glob at all."""
    now = time.time()
    _write_old(tmp_path / "frozen.request.json", age_s=10.0, now=now)

    watcher = RawBodyWatcher(tmp_path, _noop_pair, retention="delete")

    glob_calls = {"n": 0}
    orig_glob = Path.glob

    def counting_glob(self, pattern):
        if pattern.endswith(".request.json"):
            glob_calls["n"] += 1
        return orig_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", counting_glob)

    watcher._scan_once(now=now)  # first scan always runs
    assert glob_calls["n"] == 1
    watcher._scan_once(now=now + 0.2)  # dir unchanged -> short-circuit
    assert glob_calls["n"] == 1, "unchanged dir must skip the glob"


# --------------------------------------------------------------------------- #
# Fix 1 regression: the short-circuit must NOT strand an unstable pair
# --------------------------------------------------------------------------- #


def test_unstable_pair_still_fires_when_no_further_file_lands(tmp_path):
    """A matched pair present-but-unstable at scan time (fresh mtime) must
    still fire on a later poll even if NO other file touches the dir. The
    dir-mtime short-circuit must not swallow it: an instability deferral has
    to force a rescan, matching the old always-retry behaviour."""
    now = time.time()
    fired: list[str] = []

    def record(request_id, request_body, response_body):
        fired.append(request_id)

    # Pair written with CURRENT mtime => fails the 0.2s stability window.
    (tmp_path / "x.request.json").write_text(json.dumps({"messages": []}))
    (tmp_path / "x.response.json").write_text(json.dumps({"content": []}))

    watcher = RawBodyWatcher(tmp_path, record, retention="delete")
    watcher._scan_once(now=now)  # unstable -> deferred, must NOT fire yet
    assert fired == []

    # Dir untouched; only the clock advanced past the stability window.
    watcher._scan_once(now=now + 1.0)
    assert fired == ["x"], "unstable pair must fire on a later poll"


# --------------------------------------------------------------------------- #
# Fix 3: orphan TTL sweep under `delete`, config-gated, default OFF
# --------------------------------------------------------------------------- #


def test_delete_policy_orphan_ttl_sweeps_stale_orphans(tmp_path):
    """Under `delete` retention with an orphan TTL configured, the sweep
    removes orphan bodies older than the TTL."""
    now = time.time()
    _write_old(tmp_path / "old.request.json", age_s=10 * 86400, now=now)
    _write_old(tmp_path / "fresh.request.json", age_s=1 * 3600, now=now)

    watcher = RawBodyWatcher(
        tmp_path, _noop_pair, retention="delete", orphan_ttl_days=7
    )
    removed = watcher._sweep_retention(now=now)
    assert removed > 0
    assert not (tmp_path / "old.request.json").exists()
    assert (tmp_path / "fresh.request.json").exists()


def test_delete_policy_without_orphan_ttl_is_a_noop(tmp_path):
    """Data-safe default: `delete` with no orphan TTL removes nothing (the
    user may still want the corpus for retroactive `flush --from-raw-bodies`)."""
    now = time.time()
    _write_old(tmp_path / "old.request.json", age_s=30 * 86400, now=now)

    watcher = RawBodyWatcher(
        tmp_path, _noop_pair, retention="delete", orphan_ttl_days=None
    )
    removed = watcher._sweep_retention(now=now)
    assert removed == 0
    assert (tmp_path / "old.request.json").exists()
