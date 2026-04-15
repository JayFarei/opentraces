"""Tests for the RemoteIndex cache used by push-time dedup + TUI lineage hints."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opentraces.publish.huggingface.remote_index import (
    CACHE_FILENAME,
    RemoteIndex,
    cache_path_for,
)


def test_empty_returns_fresh_index():
    idx = RemoteIndex.empty("user/dataset")
    assert idx.repo_id == "user/dataset"
    assert idx.content_hashes == set()
    assert idx.session_generations == {}
    assert not idx.is_stale(3600)


def test_round_trip_through_disk(tmp_path: Path):
    idx = RemoteIndex.empty("user/dataset")
    idx.content_hashes = {"aaa", "bbb"}
    idx.session_generations = {"sess-1": 2, "sess-2": 5}
    path = tmp_path / "remote_index.json"
    idx.save(path)

    loaded = RemoteIndex.load(path)
    assert loaded is not None
    assert loaded.repo_id == "user/dataset"
    assert loaded.content_hashes == {"aaa", "bbb"}
    assert loaded.session_generations == {"sess-1": 2, "sess-2": 5}


def test_load_missing_returns_none(tmp_path: Path):
    assert RemoteIndex.load(tmp_path / "nope.json") is None


def test_load_corrupt_returns_none(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json")
    assert RemoteIndex.load(path) is None


def test_is_stale_respects_ttl(tmp_path: Path):
    idx = RemoteIndex.empty("r")
    # Backdate fetched_at by 10 minutes.
    idx.fetched_at = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert idx.is_stale(60)  # ttl 1min → stale
    assert not idx.is_stale(3600)  # ttl 1h → fresh


def test_supersedes_remote():
    idx = RemoteIndex.empty("r")
    idx.session_generations = {"sess-a": 3}
    assert not idx.supersedes_remote("sess-a", 3)
    assert not idx.supersedes_remote("sess-a", 2)
    assert idx.supersedes_remote("sess-a", 4)
    assert not idx.supersedes_remote("sess-b", 1)  # session unknown


def test_cache_path_for_uses_sibling_of_state(tmp_path: Path):
    assert cache_path_for(tmp_path) == tmp_path / CACHE_FILENAME
