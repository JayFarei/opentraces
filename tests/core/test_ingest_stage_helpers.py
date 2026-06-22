"""Unit tests for the ingest stage helpers (issue #117 decomposition).

``core/ingest.py::_ingest_locked`` was decomposed into named
single-responsibility stage helpers so each best-effort concern is
attributable to one call rather than to one of a stack of look-alike
try/except blocks. These tests pin the two load-bearing invariants of that
decomposition:

  * ``write_trace_to_bucket`` isolates each additive projection — one failing
    projection never blocks the rest — while the canonical ``write_trace_record``
    stays load-bearing and PROPAGATES (so a half-staged trace is reported as an
    error, not silently swallowed).
  * the best-effort emission helpers swallow failures and honour
    ``trace_record_only`` gating.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from opentraces.core import bucket_store, ingest, trails


def _fake_record(trace_id: str = "trace-xyz"):
    """Minimal stand-in — the helpers only read ``.trace_id`` once the
    bucket_store writes themselves are patched out."""
    return SimpleNamespace(trace_id=trace_id)


# --------------------------------------------------------------------------- #
# write_trace_to_bucket
# --------------------------------------------------------------------------- #

class TestWriteTraceToBucket:
    def _patch_all(self, monkeypatch, calls, *, fail=None):
        """Patch every bucket_store write to record its name; optionally make
        the write named ``fail`` raise."""
        fail = fail or set()

        # The writer derives the project slug via get_project_dir().name; stub
        # it so the test needs no opted-in project on disk.
        monkeypatch.setattr(ingest, "get_project_dir", lambda _p: SimpleNamespace(name="proj-slug"))

        def _mk(name):
            def _fn(*_a, **_kw):
                calls.append(name)
                if name in fail:
                    raise RuntimeError(f"boom:{name}")
            return _fn

        monkeypatch.setattr(bucket_store, "write_trace_record", _mk("write_trace_record"))
        monkeypatch.setattr(bucket_store, "write_raw_source_artifact", _mk("raw_source"))
        monkeypatch.setattr(bucket_store, "sync_trail_events_from_repo", _mk("trail_events"))
        monkeypatch.setattr(bucket_store, "project_context_tree_to_bucket", _mk("context_tree"))
        monkeypatch.setattr(bucket_store, "project_per_trace_exports", _mk("per_trace_exports"))
        monkeypatch.setattr(bucket_store, "upsert_manifest_trace_row", _mk("manifest_row"))

    def test_all_writes_run_and_succeed(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._patch_all(monkeypatch, calls)

        outcome = ingest.write_trace_to_bucket(
            _fake_record(), tmp_path,
            parser_name="claude-code",
            source_jsonl=tmp_path / "s.jsonl",
            trace_record_only=False,
        )

        # Canonical write first, then all five additive projections.
        assert calls[0] == "write_trace_record"
        assert set(calls) == {
            "write_trace_record", "raw_source", "trail_events",
            "context_tree", "per_trace_exports", "manifest_row",
        }
        assert outcome.failures == {}
        assert outcome.writes == {
            "raw_source": None, "trail_events": None, "context_tree": None,
            "per_trace_exports": None, "manifest_row": None,
        }

    def test_one_failing_projection_does_not_block_the_rest(self, tmp_path, monkeypatch):
        calls: list[str] = []
        # A mid-sequence projection blows up.
        self._patch_all(monkeypatch, calls, fail={"trail_events"})

        outcome = ingest.write_trace_to_bucket(
            _fake_record(), tmp_path,
            parser_name="claude-code",
            source_jsonl=tmp_path / "s.jsonl",
            trace_record_only=False,
        )

        # Every later write still ran despite the trail_events failure.
        assert "context_tree" in calls
        assert "per_trace_exports" in calls
        assert "manifest_row" in calls
        # The failure is attributable to exactly one named write.
        assert set(outcome.failures) == {"trail_events"}
        assert outcome.failures["trail_events"].startswith("RuntimeError: boom:trail_events")
        assert outcome.writes["context_tree"] is None

    def test_canonical_write_propagates(self, tmp_path, monkeypatch):
        calls: list[str] = []
        # The load-bearing canonical record write fails.
        self._patch_all(monkeypatch, calls, fail={"write_trace_record"})

        with pytest.raises(RuntimeError, match="boom:write_trace_record"):
            ingest.write_trace_to_bucket(
                _fake_record(), tmp_path,
                parser_name="claude-code",
                source_jsonl=tmp_path / "s.jsonl",
                trace_record_only=False,
            )

        # It raised before any additive projection ran.
        assert calls == ["write_trace_record"]

    def test_trace_record_only_skips_gated_projections(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._patch_all(monkeypatch, calls)

        outcome = ingest.write_trace_to_bucket(
            _fake_record(), tmp_path,
            parser_name="claude-code",
            source_jsonl=tmp_path / "s.jsonl",
            trace_record_only=True,
        )

        # Canonical write + the unconditional raw-source link only.
        assert calls == ["write_trace_record", "raw_source"]
        assert set(outcome.writes) == {"raw_source"}
        assert "trail_events" not in outcome.writes
        assert "manifest_row" not in outcome.writes


# --------------------------------------------------------------------------- #
# best-effort emission helpers
# --------------------------------------------------------------------------- #

class TestBestEffortHelpers:
    def test_emit_trail_events_swallows_failure(self, tmp_path, monkeypatch):
        def _boom(*_a, **_kw):
            raise RuntimeError("trail boom")

        monkeypatch.setattr(trails, "emit_step_window_events_from_record", _boom)
        # Must not raise — best-effort.
        ingest._emit_trail_events(
            tmp_path, _fake_record(), "trace-xyz",
            reconcile_watcher=False, trace_record_only=False,
        )

    def test_emit_trail_events_skipped_when_record_only(self, tmp_path, monkeypatch):
        called = []
        monkeypatch.setattr(
            trails, "emit_step_window_events_from_record",
            lambda *a, **k: called.append(1),
        )
        ingest._emit_trail_events(
            tmp_path, _fake_record(), "trace-xyz",
            reconcile_watcher=True, trace_record_only=True,
        )
        assert called == []

    def test_keep_index_warm_uses_projection_then_index_only(self, monkeypatch):
        seen: list[tuple] = []
        monkeypatch.setattr(
            ingest, "keep_index_warm",
            lambda *a, **k: seen.append(k.get("query_sources")),
        )

        ingest._keep_index_warm_after_ingest(
            _fake_record(), "trace-xyz", trace_record_only=False,
        )
        ingest._keep_index_warm_after_ingest(
            _fake_record(), "trace-xyz", trace_record_only=True,
        )

        assert seen == [("index", "projection"), ("index",)]

    def test_keep_index_warm_swallows_failure(self, monkeypatch):
        def _boom(*_a, **_kw):
            raise RuntimeError("warm boom")

        monkeypatch.setattr(ingest, "keep_index_warm", _boom)
        # Must not raise even though keep_index_warm blew up.
        ingest._keep_index_warm_after_ingest(
            _fake_record(), "trace-xyz", trace_record_only=False,
        )
