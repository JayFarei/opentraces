"""Tests for HF Hub upload and dataset card generation."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opentraces_schema.models import Agent, Metrics, Step, TokenUsage, TraceRecord
from opentraces_schema.version import SCHEMA_VERSION

from opentraces.upload.hf_hub import HFUploader, UploadResult
from opentraces.upload.dataset_card import (
    AUTO_END,
    AUTO_START,
    generate_dataset_card,
)

# --- Fixtures ---


def _make_trace(
    trace_id: str = "test-trace-1",
    agent_name: str = "claude-code",
    model: str = "anthropic/claude-sonnet-4-20250514",
    num_steps: int = 3,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    timestamp_start: str = "2026-03-01T00:00:00Z",
    timestamp_end: str = "2026-03-01T00:05:00Z",
) -> TraceRecord:
    """Create a sample TraceRecord for testing."""
    steps = [
        Step(
            step_index=i,
            role="agent" if i % 2 else "user",
            content=f"Step {i} content",
            token_usage=TokenUsage(
                input_tokens=input_tokens // num_steps,
                output_tokens=output_tokens // num_steps,
            ),
        )
        for i in range(num_steps)
    ]
    return TraceRecord(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name=agent_name, model=model),
        steps=steps,
        metrics=Metrics(
            total_steps=num_steps,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
        ),
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
    )


@pytest.fixture
def sample_traces() -> list[TraceRecord]:
    return [
        _make_trace(trace_id="t1", agent_name="claude-code", model="anthropic/claude-sonnet-4-20250514"),
        _make_trace(trace_id="t2", agent_name="cursor", model="anthropic/claude-sonnet-4-20250514"),
        _make_trace(trace_id="t3", agent_name="claude-code", model="openai/gpt-4o"),
    ]


# --- HFUploader Tests ---


class TestShardNaming:
    def test_shard_name_format(self):
        """Shard names follow traces_{timestamp}_{uuid}.jsonl pattern."""
        with patch("opentraces.upload.hf_hub.HfApi"):
            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            name = uploader._generate_shard_name()

        assert name.startswith("traces_")
        assert name.endswith(".jsonl")
        # Pattern: traces_YYYYMMDDTHHMMSSz_8hexchars.jsonl
        pattern = r"^traces_\d{8}T\d{6}Z_[0-9a-f]{8}\.jsonl$"
        assert re.match(pattern, name), f"Shard name '{name}' does not match expected pattern"

    def test_shard_names_are_unique(self):
        """Each call generates a unique shard name."""
        with patch("opentraces.upload.hf_hub.HfApi"):
            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            names = {uploader._generate_shard_name() for _ in range(100)}
        assert len(names) == 100


class TestUploadTraces:
    def test_upload_produces_valid_jsonl(self, sample_traces):
        """Upload serializes traces as valid JSONL."""
        with patch("opentraces.upload.hf_hub.HfApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.upload_file = MagicMock()

            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            uploader.api = mock_api

            result = uploader.upload_traces(sample_traces)

        assert result.success is True
        assert result.trace_count == 3

        # Verify the uploaded content is valid JSONL
        call_args = mock_api.upload_file.call_args
        fileobj = call_args.kwargs["path_or_fileobj"]
        content = fileobj.read().decode("utf-8")
        lines = [l for l in content.strip().split("\n") if l]
        assert len(lines) == 3

        for line in lines:
            record = json.loads(line)
            assert "trace_id" in record
            assert "content_hash" in record
            assert record["schema_version"] == SCHEMA_VERSION

    def test_upload_empty_traces(self):
        """Upload with empty list returns failure."""
        with patch("opentraces.upload.hf_hub.HfApi"):
            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            result = uploader.upload_traces([])

        assert result.success is False
        assert "No traces" in result.error

    def test_upload_file_path_in_repo(self, sample_traces):
        """Uploaded file goes to data/ directory."""
        with patch("opentraces.upload.hf_hub.HfApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.upload_file = MagicMock()

            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            uploader.api = mock_api

            uploader.upload_traces(sample_traces)

        call_args = mock_api.upload_file.call_args
        path_in_repo = call_args.kwargs["path_in_repo"]
        assert path_in_repo.startswith("data/traces_")
        assert path_in_repo.endswith(".jsonl")


class TestRetryLogic:
    def test_retry_on_failure(self, sample_traces):
        """Upload retries on transient failures."""
        with patch("opentraces.upload.hf_hub.HfApi") as MockApi:
            mock_api = MockApi.return_value
            # Fail twice, succeed on third
            mock_api.upload_file = MagicMock(
                side_effect=[
                    ConnectionError("Server error"),
                    ConnectionError("Server error"),
                    None,
                ]
            )

            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            uploader.api = mock_api
            uploader.BASE_DELAY = 0.01  # Speed up test

            result = uploader.upload_traces(sample_traces)

        assert result.success is True
        assert mock_api.upload_file.call_count == 3

    def test_retry_exhaustion(self, sample_traces):
        """Upload fails after max retries."""
        with patch("opentraces.upload.hf_hub.HfApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.upload_file = MagicMock(
                side_effect=ConnectionError("Server error")
            )

            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            uploader.api = mock_api
            uploader.BASE_DELAY = 0.01

            result = uploader.upload_traces(sample_traces)

        assert result.success is False
        assert "3 retries" in result.error
        assert mock_api.upload_file.call_count == 3


class TestEnsureRepo:
    def test_ensure_repo_creates_dataset(self):
        """ensure_repo_exists calls create_repo with correct params."""
        with patch("opentraces.upload.hf_hub.HfApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.create_repo = MagicMock(
                return_value="https://huggingface.co/datasets/user/dataset"
            )
            mock_api.update_repo_settings = MagicMock()

            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            uploader.api = mock_api

            url = uploader.ensure_repo_exists()

        mock_api.create_repo.assert_called_once_with(
            repo_id="user/dataset",
            repo_type="dataset",
            exist_ok=True,
            private=False,
        )
        assert "user/dataset" in url


class TestGetExistingShards:
    def test_lists_shard_files(self):
        """get_existing_shards filters to trace shard files."""
        with patch("opentraces.upload.hf_hub.HfApi") as MockApi:
            mock_api = MockApi.return_value
            mock_api.list_repo_files = MagicMock(
                return_value=[
                    "README.md",
                    "data/traces_20260301T000000Z_abcd1234.jsonl",
                    "data/traces_20260302T000000Z_efgh5678.jsonl",
                    "data/other_file.parquet",
                ]
            )

            uploader = HFUploader(token="fake-token", repo_id="user/dataset")
            uploader.api = mock_api

            shards = uploader.get_existing_shards()

        assert len(shards) == 2
        assert all(s.endswith(".jsonl") for s in shards)


# --- Dataset Card Tests ---


class TestDatasetCardGeneration:
    def test_new_card_has_frontmatter(self, sample_traces):
        """Fresh card includes YAML frontmatter."""
        card = generate_dataset_card("user/my-traces", sample_traces)
        assert card.startswith("---")
        assert "license: cc-by-4.0" in card
        assert "opentraces" in card
        assert "agent-traces" in card

    def test_new_card_has_stats(self, sample_traces):
        """Fresh card includes auto-generated stats."""
        card = generate_dataset_card("user/my-traces", sample_traces)
        assert AUTO_START in card
        assert AUTO_END in card
        assert "Total traces" in card
        assert "3" in card  # 3 traces

    def test_new_card_has_load_snippet(self, sample_traces):
        """Fresh card includes dataset load snippet."""
        card = generate_dataset_card("user/my-traces", sample_traces)
        assert 'load_dataset("user/my-traces")' in card

    def test_new_card_has_model_distribution(self, sample_traces):
        """Fresh card shows model distribution."""
        card = generate_dataset_card("user/my-traces", sample_traces)
        assert "anthropic/claude-sonnet-4-20250514" in card
        assert "openai/gpt-4o" in card

    def test_new_card_has_agent_distribution(self, sample_traces):
        """Fresh card shows agent distribution."""
        card = generate_dataset_card("user/my-traces", sample_traces)
        assert "claude-code" in card
        assert "cursor" in card

    def test_new_card_has_schema_version(self, sample_traces):
        """Fresh card includes schema version."""
        card = generate_dataset_card("user/my-traces", sample_traces)
        assert SCHEMA_VERSION in card


class TestDatasetCardUpdate:
    def test_preserves_user_section(self, sample_traces):
        """Updating a card preserves user-written content."""
        existing = f"""---
license: cc-by-4.0
tags:
  - opentraces
  - agent-traces
---

# My Custom Title

This is my custom description that should be preserved.

{AUTO_START}
## Dataset Statistics

| Metric | Value |
|--------|-------|
| Total traces | 1 |
{AUTO_END}

## My Custom Section

This should also be preserved.
"""
        updated = generate_dataset_card("user/my-traces", sample_traces, existing_card=existing)

        # User content preserved
        assert "My Custom Title" in updated
        assert "my custom description that should be preserved" in updated
        assert "My Custom Section" in updated
        assert "This should also be preserved" in updated

        # Stats updated
        assert "Total traces" in updated
        # Old count replaced
        assert AUTO_START in updated
        assert AUTO_END in updated

    def test_update_without_markers_generates_fresh(self, sample_traces):
        """If existing card has no markers, generate a fresh card."""
        existing = "# Old card with no markers"
        updated = generate_dataset_card("user/my-traces", sample_traces, existing_card=existing)

        # Should be a fresh card since no markers found
        assert AUTO_START in updated
        assert AUTO_END in updated
        assert "license: cc-by-4.0" in updated
