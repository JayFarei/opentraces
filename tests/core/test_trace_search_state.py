"""Regression tests for the read-only search snapshot dirty-marker state.

Focus: the atomic compare-and-delete in ``clear_dirty_marker_if_unchanged``
(issue #27 E). The naive read-then-unlink races a concurrent
``mark_search_snapshot_dirty`` (``os.replace``) landing between the read and the
unlink — deleting a fresh marker and losing a rebuild signal.
"""

from __future__ import annotations

from opentraces.core import dirty_marker as marker_mod
from opentraces.core.trace_search_state import (
    clear_dirty_marker_if_unchanged,
    current_dirty_token,
    dirty_marker_path,
    mark_search_snapshot_dirty,
)


def test_clear_removes_marker_when_token_matches() -> None:
    mark_search_snapshot_dirty("test", trace_id="t1")
    token = current_dirty_token()
    assert token is not None

    clear_dirty_marker_if_unchanged(token)

    assert current_dirty_token() is None
    assert not dirty_marker_path().exists()
    # No temp residue left behind.
    parent = dirty_marker_path().parent
    assert not list(parent.glob(dirty_marker_path().name + ".clearing.*"))


def test_clear_noop_when_no_marker() -> None:
    # Nothing to clear must not raise.
    clear_dirty_marker_if_unchanged("anything")
    assert current_dirty_token() is None


def test_clear_preserves_concurrently_rewritten_marker(monkeypatch) -> None:
    # Build's pre-build token, then a concurrent mark writes a NEW marker after
    # we captured the token but the clear must NOT delete that fresh signal.
    mark_search_snapshot_dirty("first", trace_id="t1")
    stale_token = current_dirty_token()
    assert stale_token is not None

    # Simulate the interleaving: between our os.rename grabbing the old marker
    # and the token check, a concurrent writer os.replace-es a fresh marker into
    # the original path. We inject that by re-marking right after the rename.
    # The race-safe rename now lives in the shared dirty_marker primitive.
    real_rename = marker_mod.os.rename
    fired = {"done": False}

    def racing_rename(src, dst):
        real_rename(src, dst)
        if not fired["done"]:
            fired["done"] = True
            # A concurrent dirty mark lands in the now-free original slot.
            mark_search_snapshot_dirty("second", trace_id="t2")

    monkeypatch.setattr(marker_mod.os, "rename", racing_rename)

    clear_dirty_marker_if_unchanged(stale_token)

    # The concurrent (newer) marker must survive — the rebuild signal is not lost.
    surviving = current_dirty_token()
    assert surviving is not None
    assert surviving != stale_token
    assert "second" in surviving
    # No temp residue.
    parent = dirty_marker_path().parent
    assert not list(parent.glob(dirty_marker_path().name + ".clearing.*"))


def test_clear_matching_token_wins_when_no_concurrent_writer(monkeypatch) -> None:
    # When the captured marker matches our token and nothing reclaims the slot,
    # the marker is deleted (the steady-state PR #24 read-only clear path).
    mark_search_snapshot_dirty("only", trace_id="t1")
    token = current_dirty_token()
    assert token is not None

    clear_dirty_marker_if_unchanged(token)

    assert current_dirty_token() is None
