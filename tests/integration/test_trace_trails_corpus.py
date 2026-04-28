"""Trace Trails deterministic corpus generator coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.harness.trace_trails_corpus import (
    CORPUS_VERSION,
    CorpusMismatchError,
    check_corpus,
    update_corpus,
)


def test_trace_trails_corpus_fixture_is_current() -> None:
    result = check_corpus()

    assert result["status"] == "ok"
    assert result["file_count"] > 0


def test_trace_trails_corpus_update_writes_versioned_layout(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "trace_trails_corpus" / CORPUS_VERSION

    update_result = update_corpus(fixture_dir)
    check_result = check_corpus(fixture_dir)

    manifest = json.loads((fixture_dir / "manifest.json").read_text())
    events = (fixture_dir / "trail" / "events.jsonl").read_text().splitlines()

    assert update_result["status"] == "updated"
    assert check_result["status"] == "ok"
    assert manifest["corpus_version"] == CORPUS_VERSION
    assert manifest["scenarios"][0]["name"] == "full_stack_demo"
    assert (fixture_dir / "commands" / "full_stack_demo" / "summary.json").exists()
    assert (fixture_dir / "api" / "full_stack_demo" / "summary.json").exists()
    assert (fixture_dir / "workspace" / "manifest.json").exists()
    assert (fixture_dir / "dataset" / "dataset_infos.json").exists()
    assert (fixture_dir / "dataset" / "data" / "traces.jsonl").exists()
    assert (fixture_dir / "summaries" / "full_stack_demo.json").exists()
    assert events


def test_trace_trails_corpus_check_reports_fixture_mismatch(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "trace_trails_corpus" / CORPUS_VERSION
    update_corpus(fixture_dir)
    (fixture_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(CorpusMismatchError, match="manifest.json"):
        check_corpus(fixture_dir)
