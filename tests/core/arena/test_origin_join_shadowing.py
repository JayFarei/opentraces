from __future__ import annotations

import os
from pathlib import Path

import pytest
from opentraces.core import paths
from opentraces.core.arena.origin import OriginJoinError, attach_explicit_bench_labels
from opentraces.core.bucket_layout import trace_v1_json_path
from opentraces.core.bucket_trace_records import (
    read_bucket_record_for_trace,
    trace_record_path,
    write_trace_record,
)
from opentraces.core.trace_corpus import LAYER_PROJECT, resolve
from opentraces_schema import Step

from tests.core.arena.test_origin_join import (
    PROJECT_SLUG,
    RUN_ID,
    _finalized_run,
    _origin_record,
)


def test_corrupt_newer_canonical_candidate_never_falls_back_to_stale_additive_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _finalized_run(tmp_path, monkeypatch)
    bucket = tmp_path / "bucket"
    projects = tmp_path / "projects"
    monkeypatch.setattr(paths, "bucket_dir", lambda: bucket)
    monkeypatch.setattr(paths, "PROJECTS_DIR", projects)
    monkeypatch.setattr(paths, "STAGING_DIR", tmp_path / "staging")

    canonical = _origin_record()
    written = write_trace_record(
        canonical,
        project_slug=PROJECT_SLUG,
        source_layer="canonical",
        legacy_mirror=False,
    )
    assert trace_record_path(PROJECT_SLUG, canonical.trace_id).is_file()
    assert [step.step_index for step in written.record.steps] == [1, 2, 3]

    stale = canonical.model_copy(deep=True)
    stale.steps = [Step(step_index=99, role="agent", content="stale additive step")]
    additive_path = trace_v1_json_path(PROJECT_SLUG, canonical.trace_id)
    additive_path.parent.mkdir(parents=True)
    additive_path.write_text(stale.model_dump_json() + "\n", encoding="utf-8")

    corrupt = projects / PROJECT_SLUG / "traces" / f"{canonical.trace_id}.jsonl"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{not valid json\n", encoding="utf-8")
    newer_ns = written.path.stat().st_mtime_ns + 1_000_000_000
    os.utime(corrupt, ns=(newer_ns, newer_ns))

    candidate = resolve(canonical.trace_id)
    assert candidate is not None
    assert candidate.layer == LAYER_PROJECT
    assert candidate.path == corrupt
    assert read_bucket_record_for_trace(canonical.trace_id) is None

    with pytest.raises(OriginJoinError, match="stored trace"):
        attach_explicit_bench_labels(
            store.root / RUN_ID,
            address=f"{canonical.trace_id}:99",
            store=store,
        )
