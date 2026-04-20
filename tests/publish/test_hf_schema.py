"""Regression tests for HuggingFace dataset_infos generation."""

from __future__ import annotations

import json
from pathlib import Path

from datasets import Features, load_dataset

from opentraces.publish.huggingface.schema import HF_FEATURES, build_dataset_infos
from opentraces_schema import Agent, TraceRecord
from opentraces_schema.version import SCHEMA_VERSION


FIXTURE = Path(__file__).parent.parent / "fixtures" / "trace_record_stability" / "v02_sample.jsonl"


def _load_with_generated_features(tmp_path: Path, payloads: list[dict]):
    data_file = tmp_path / "rows.jsonl"
    data_file.write_text("".join(json.dumps(payload) + "\n" for payload in payloads))
    features = Features.from_dict(build_dataset_infos("test-dataset")["default"]["features"])
    return load_dataset("json", data_files=str(data_file), split="train", features=features)


def test_dataset_infos_uses_current_schema_version():
    info = build_dataset_infos("test-dataset")["default"]
    assert info["version"]["version_str"] == SCHEMA_VERSION


def test_generated_features_include_recent_trace_fields():
    assert "repository_url" in HF_FEATURES["task"]
    assert "total_cache_read_tokens" in HF_FEATURES["metrics"]
    assert "total_cache_creation_tokens" in HF_FEATURES["metrics"]
    assert "generation_index" in HF_FEATURES
    assert HF_FEATURES["attribution"]["revision"] == {"_type": "Json"}
    assert HF_FEATURES["attribution"]["files"][0]["conversations"][0]["contributor"] == {"_type": "Json"}


def test_generated_features_accept_minimal_trace_record(tmp_path: Path):
    payload = TraceRecord(
        trace_id="trace-minimal",
        session_id="session-minimal",
        agent=Agent(name="claude-code"),
    ).model_dump(mode="json")

    dataset = _load_with_generated_features(tmp_path, [payload])

    assert dataset.num_rows == 1
    row = dataset[0]
    assert row["trace_id"] == "trace-minimal"
    assert row["task"]["repository_url"] is None
    assert row["attribution"] is None
    assert row["generation_index"] == 0


def test_generated_features_accept_rich_fixture_trace(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text().splitlines()[0])

    dataset = _load_with_generated_features(tmp_path, [payload])

    assert dataset.num_rows == 1
    row = dataset[0]
    assert "generation_index" in row
    assert "total_cache_creation_tokens" in row["metrics"]
    assert row["attribution"]["revision"] is None
    contributor = row["attribution"]["files"][0]["conversations"][0]["contributor"]
    assert contributor["type"] == "ai"
    assert contributor["model_id"]
