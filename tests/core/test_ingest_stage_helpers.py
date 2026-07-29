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
from opentraces_schema import GitAnchor, Patch

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
    def test_emit_trail_events_returns_current_ingest_events(
        self,
        tmp_path,
        monkeypatch,
    ):
        current_event = object()
        monkeypatch.setattr(
            trails,
            "emit_step_window_events_from_record",
            lambda *a, **k: SimpleNamespace(emitted_events=[current_event]),
        )

        emitted = ingest._emit_trail_events(
            tmp_path,
            _fake_record(),
            "trace-xyz",
            reconcile_watcher=False,
            trace_record_only=False,
        )

        assert emitted == [current_event]

    def test_patch_backfill_receives_current_ingest_events(
        self,
        tmp_path,
        monkeypatch,
    ):
        current_event = object()
        projected_patch = SimpleNamespace(patch_id="patch-current", step_index=3)
        record = SimpleNamespace(
            trace_id="trace-xyz",
            generation_index=2,
            patches=[],
        )
        seen = []
        monkeypatch.setattr(
            ingest,
            "_load_prior_staged_patches",
            lambda _project_dir, _trace_id: [],
        )
        monkeypatch.setattr(
            ingest,
            "_backfill_patches_from_trail_events",
            lambda project_dir, trace_id, generation_index, *, events: (
                seen.append((project_dir, trace_id, generation_index, events))
                or [projected_patch]
            ),
        )

        ingest._backfill_patches_onto_record(
            tmp_path,
            record,
            "trace-xyz",
            trace_record_only=False,
            events=[current_event],
        )

        assert record.patches == [projected_patch]
        assert seen == [(tmp_path, "trace-xyz", 2, [current_event])]

    def test_patch_backfill_preserves_prior_patches_on_refresh(
        self,
        tmp_path,
        monkeypatch,
    ):
        prior_patch = SimpleNamespace(patch_id="patch-prior", step_index=1)
        current_patch = SimpleNamespace(patch_id="patch-current", step_index=2)
        record = SimpleNamespace(
            trace_id="trace-xyz",
            generation_index=2,
            patches=[],
        )
        monkeypatch.setattr(
            ingest,
            "_load_prior_staged_patches",
            lambda _project_dir, _trace_id: [prior_patch],
        )
        monkeypatch.setattr(
            ingest,
            "_backfill_patches_from_trail_events",
            lambda *_a, **_k: [current_patch],
        )

        ingest._backfill_patches_onto_record(
            tmp_path,
            record,
            "trace-xyz",
            trace_record_only=False,
            events=[],
        )

        assert record.patches == [prior_patch, current_patch]

    def test_patch_backfill_sorts_late_reconciler_patch_by_step(
        self,
        tmp_path,
        monkeypatch,
    ):
        prior_patch = SimpleNamespace(patch_id="patch-prior", step_index=10)
        reconciled_patch = SimpleNamespace(
            patch_id="patch-reconciled",
            step_index=2,
        )
        record = SimpleNamespace(
            trace_id="trace-xyz",
            generation_index=2,
            patches=[],
        )
        monkeypatch.setattr(
            ingest,
            "_load_prior_staged_patches",
            lambda _project_dir, _trace_id: [prior_patch],
        )
        monkeypatch.setattr(
            ingest,
            "_backfill_patches_from_trail_events",
            lambda *_a, **_k: [reconciled_patch],
        )

        ingest._backfill_patches_onto_record(
            tmp_path,
            record,
            "trace-xyz",
            trace_record_only=False,
            events=[],
        )

        assert record.patches == [reconciled_patch, prior_patch]

    def test_reconciler_patch_refresh_preserves_durable_attribution(
        self,
        tmp_path,
        monkeypatch,
    ):
        prior_patch = Patch(
            patch_id="patch-shared",
            file_path="old.py",
            step_index=8,
            capture_method=["hook_posttooluse"],
            anchor=GitAnchor(
                last_searched_at="2026-07-28T12:00:00+00:00",
                found=True,
                commit_sha="b" * 40,
                evidence_tier="exact_range_hash",
                evidence_firmness="firm_observed",
            ),
            superseded_by=["a" * 40],
        )
        reconciled_patch = Patch(
            patch_id="patch-shared",
            file_path="new.py",
            step_index=8,
            capture_method=["hook_posttooluse", "watcher_backstop"],
            snapshot_after_id="snapshot-current",
            limitations=["watcher_corroborated"],
        )
        record = SimpleNamespace(
            trace_id="trace-xyz",
            generation_index=2,
            patches=[],
            outcome=SimpleNamespace(committed=False, commit_sha=None),
            git_links=[],
        )
        monkeypatch.setattr(
            ingest,
            "_load_prior_staged_patches",
            lambda _project_dir, _trace_id: [prior_patch],
        )
        monkeypatch.setattr(
            ingest,
            "_backfill_patches_from_trail_events",
            lambda *_a, **_k: [reconciled_patch],
        )

        ingest._backfill_patches_onto_record(
            tmp_path,
            record,
            "trace-xyz",
            trace_record_only=False,
            events=[],
        )
        ingest._derive_outcome_from_patches(record, "trace-xyz")

        [merged] = record.patches
        assert merged.file_path == "new.py"
        assert merged.capture_method == [
            "hook_posttooluse",
            "watcher_backstop",
        ]
        assert merged.snapshot_after_id == "snapshot-current"
        assert merged.limitations == ["watcher_corroborated"]
        assert merged.anchor == prior_patch.anchor
        assert merged.superseded_by == ["a" * 40]
        assert record.outcome.committed is True
        assert record.outcome.commit_sha == "b" * 40
        assert {link.revision for link in record.git_links} == {
            "a" * 40,
            "b" * 40,
        }

    def test_emit_trail_events_includes_reconciler_patch_events(
        self,
        tmp_path,
        monkeypatch,
    ):
        reconciled_event = object()
        monkeypatch.setattr(
            trails,
            "emit_step_window_events_from_record",
            lambda *a, **k: SimpleNamespace(emitted_events=[]),
        )

        def _reconcile(*_args, event_sink, **_kwargs):
            event_sink(reconciled_event)

        monkeypatch.setattr(trails, "reconcile_watcher_observations", _reconcile)

        emitted = ingest._emit_trail_events(
            tmp_path,
            _fake_record(),
            "trace-xyz",
            reconcile_watcher=True,
            trace_record_only=False,
        )

        assert emitted == [reconciled_event]

    def test_emit_trail_events_combines_retry_slice_with_reconciler_delta(
        self,
        tmp_path,
        monkeypatch,
    ):
        existing_event = object()
        reconciled_event = object()
        monkeypatch.setattr(
            trails,
            "emit_step_window_events_from_record",
            lambda *a, **k: SimpleNamespace(
                emitted_events=[],
                projection_events=[existing_event],
            ),
        )

        def _reconcile(*_args, event_sink, **_kwargs):
            event_sink(reconciled_event)

        monkeypatch.setattr(trails, "reconcile_watcher_observations", _reconcile)

        owned = ingest._emit_trail_events(
            tmp_path,
            _fake_record(),
            "trace-xyz",
            reconcile_watcher=True,
            trace_record_only=False,
        )

        assert owned == [existing_event, reconciled_event]

    def test_emit_trail_events_retains_emission_when_reconcile_fails(
        self,
        tmp_path,
        monkeypatch,
    ):
        current_event = object()
        monkeypatch.setattr(
            trails,
            "emit_step_window_events_from_record",
            lambda *a, **k: SimpleNamespace(emitted_events=[current_event]),
        )

        def _boom(*_args, **_kwargs):
            raise RuntimeError("reconcile boom")

        monkeypatch.setattr(trails, "reconcile_watcher_observations", _boom)

        emitted = ingest._emit_trail_events(
            tmp_path,
            _fake_record(),
            "trace-xyz",
            reconcile_watcher=True,
            trace_record_only=False,
        )

        assert emitted == [current_event]

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
