"""Tests for QualitySummary, build_summary, and dataset card quality integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from opentraces.quality.summary import (
    PersonaScoreSummary,
    QualitySummary,
    build_summary,
)
from opentraces.quality.engine import (
    BatchAssessment,
    PersonaScore,
    TraceAssessment,
)
from opentraces.quality.gates import GateResult, check_gate
from opentraces.upload.dataset_card import generate_dataset_card
from opentraces.upload.hf_hub import HFUploader
from opentraces_schema.models import Agent, Metrics, Step, TokenUsage, TraceRecord
from opentraces_schema.version import SCHEMA_VERSION


# --- Fixtures ---


def _make_trace(trace_id: str = "test-1") -> TraceRecord:
    """Minimal TraceRecord for testing."""
    return TraceRecord(
        schema_version=SCHEMA_VERSION,
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code"),
        steps=[
            Step(step_index=0, role="user", content="hello"),
            Step(step_index=1, role="agent", content="hi"),
        ],
        metrics=Metrics(total_steps=2),
    )


def _make_batch(
    persona_averages: dict[str, float] | None = None,
    num_traces: int = 3,
) -> BatchAssessment:
    """Create a BatchAssessment with specified persona averages."""
    if persona_averages is None:
        persona_averages = {
            "conformance": 85.0,
            "training": 60.0,
            "rl": 50.0,
            "analytics": 72.0,
            "domain": 58.0,
        }

    assessments = []
    for i in range(num_traces):
        scores = {}
        for name, avg in persona_averages.items():
            # Spread scores around the average
            spread = 10.0
            score = avg + (i - num_traces // 2) * spread / num_traces
            scores[name] = PersonaScore(
                persona_name=name,
                total_score=max(0.0, min(100.0, score)),
                pass_rate=80.0,
            )
        assessments.append(TraceAssessment(
            trace_id=f"trace-{i}",
            session_id=f"session-{i}",
            task_description=f"Test task {i}",
            persona_scores=scores,
            overall_utility=sum(s.total_score for s in scores.values()) / len(scores),
        ))

    return BatchAssessment(
        assessments=assessments,
        persona_averages=persona_averages,
    )


# --- QualitySummary Tests ---


class TestQualitySummary:
    def test_to_dict_basic(self):
        """to_dict produces expected keys and values."""
        summary = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=10,
            persona_scores={
                "training": PersonaScoreSummary(average=68.4, min=42.1, max=89.2),
            },
            overall_utility=68.4,
            gate_passed=True,
        )
        d = summary.to_dict()

        assert d["scorer_version"] == "0.2.0"
        assert d["scoring_mode"] == "deterministic"
        assert d["trace_count"] == 10
        assert d["gate_status"] == "passing"
        assert d["gate_failures"] == []
        assert d["persona_scores"]["training"]["average"] == 68.4
        assert d["persona_scores"]["training"]["min"] == 42.1
        assert d["overall_utility"] == 68.4
        assert "judge_model" not in d  # None judge_model is omitted

    def test_to_dict_with_judge(self):
        """to_dict includes judge_model when present."""
        summary = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="hybrid",
            judge_model="haiku",
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=5,
            overall_utility=70.0,
            gate_passed=True,
        )
        d = summary.to_dict()
        assert d["scoring_mode"] == "hybrid"
        assert d["judge_model"] == "haiku"

    def test_to_dict_failing_gate(self):
        """to_dict shows gate_status=failing with failure list."""
        summary = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=5,
            overall_utility=30.0,
            gate_passed=False,
            gate_failures=["training average 30.0% below 45.0% minimum"],
        )
        d = summary.to_dict()
        assert d["gate_status"] == "failing"
        assert len(d["gate_failures"]) == 1

    def test_to_dict_with_preservation(self):
        """to_dict includes preservation_average when set."""
        summary = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=5,
            overall_utility=70.0,
            gate_passed=True,
            preservation_average=0.92,
        )
        d = summary.to_dict()
        assert d["preservation_average"] == 0.92

    def test_round_trip_serialization(self):
        """from_dict(to_dict()) preserves all fields."""
        original = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="hybrid",
            judge_model="sonnet",
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=42,
            persona_scores={
                "conformance": PersonaScoreSummary(average=87.2, min=71.0, max=95.3),
                "training": PersonaScoreSummary(average=68.4, min=42.1, max=89.2),
                "rl": PersonaScoreSummary(average=55.7, min=38.0, max=72.1),
            },
            overall_utility=70.4,
            gate_passed=False,
            gate_failures=["rl average too low"],
            preservation_average=0.88,
        )

        d = original.to_dict()
        restored = QualitySummary.from_dict(d)

        assert restored.scorer_version == original.scorer_version
        assert restored.scoring_mode == original.scoring_mode
        assert restored.judge_model == original.judge_model
        assert restored.assessed_at == original.assessed_at
        assert restored.trace_count == original.trace_count
        assert restored.overall_utility == original.overall_utility
        assert restored.gate_passed == original.gate_passed
        assert len(restored.gate_failures) == 1
        assert restored.preservation_average == original.preservation_average

        for name in original.persona_scores:
            assert name in restored.persona_scores
            assert restored.persona_scores[name].average == original.persona_scores[name].average
            assert restored.persona_scores[name].min == original.persona_scores[name].min
            assert restored.persona_scores[name].max == original.persona_scores[name].max

    def test_json_round_trip(self):
        """Serialization through JSON preserves data."""
        original = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=10,
            persona_scores={
                "training": PersonaScoreSummary(average=68.4, min=42.1, max=89.2),
            },
            overall_utility=68.4,
            gate_passed=True,
        )

        json_str = json.dumps(original.to_dict())
        restored = QualitySummary.from_dict(json.loads(json_str))
        assert restored.persona_scores["training"].average == 68.4

    def test_from_dict_defaults(self):
        """from_dict handles missing keys with sensible defaults."""
        summary = QualitySummary.from_dict({})
        assert summary.scorer_version == "unknown"
        assert summary.scoring_mode == "deterministic"
        assert summary.judge_model is None
        assert summary.trace_count == 0
        assert summary.gate_passed is True
        assert summary.persona_scores == {}


class TestQualitySummaryYamlFrontmatter:
    def test_flat_keys_for_hf_search(self):
        """to_yaml_frontmatter includes flat top-level keys for HF search."""
        summary = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=10,
            persona_scores={
                "training": PersonaScoreSummary(average=68.4, min=42.1, max=89.2),
                "conformance": PersonaScoreSummary(average=87.2, min=71.0, max=95.3),
            },
            overall_utility=77.8,
            gate_passed=True,
        )
        fm = summary.to_yaml_frontmatter()

        # Flat keys
        assert fm["training_score"] == 68.4
        assert fm["conformance_score"] == 87.2
        assert fm["overall_quality"] == 77.8

        # Nested block
        assert "opentraces_quality" in fm
        assert fm["opentraces_quality"]["scorer_version"] == "0.2.0"


# --- build_summary Tests ---


class TestBuildSummary:
    def test_basic_build(self):
        """build_summary produces correct QualitySummary from BatchAssessment."""
        batch = _make_batch()
        gate = check_gate(batch)
        summary = build_summary(batch, gate)

        assert summary.scorer_version == SCHEMA_VERSION
        assert summary.scoring_mode == "deterministic"
        assert summary.judge_model is None
        assert summary.trace_count == 3
        assert "conformance" in summary.persona_scores
        assert "training" in summary.persona_scores
        assert summary.persona_scores["conformance"].average == 85.0
        assert summary.overall_utility > 0

    def test_build_with_judge(self):
        """build_summary records hybrid mode when judge is used."""
        batch = _make_batch()
        gate = check_gate(batch)
        summary = build_summary(batch, gate, mode="hybrid", judge_model="haiku")

        assert summary.scoring_mode == "hybrid"
        assert summary.judge_model == "haiku"

    def test_build_records_gate_result(self):
        """build_summary correctly propagates gate pass/fail."""
        batch = _make_batch(persona_averages={"conformance": 50.0})
        gate = check_gate(batch)

        assert not gate.passed
        summary = build_summary(batch, gate)
        assert not summary.gate_passed
        assert len(summary.gate_failures) > 0

    def test_build_min_max(self):
        """build_summary computes correct min/max from individual trace scores."""
        batch = _make_batch(num_traces=5)
        gate = GateResult(passed=True)
        summary = build_summary(batch, gate)

        for name, ps in summary.persona_scores.items():
            assert ps.min <= ps.average <= ps.max

    def test_empty_batch(self):
        """build_summary handles an empty batch."""
        batch = BatchAssessment()
        gate = GateResult(passed=True)
        summary = build_summary(batch, gate)

        assert summary.trace_count == 0
        assert summary.overall_utility == 0.0
        assert summary.persona_scores == {}


# --- Dataset Card Quality Integration Tests ---


class TestDatasetCardQuality:
    def test_fresh_card_with_quality(self):
        """Fresh dataset card includes quality scores in frontmatter and stats."""
        traces = [_make_trace("t1"), _make_trace("t2")]
        quality = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=2,
            persona_scores={
                "training": PersonaScoreSummary(average=68.4, min=42.1, max=89.2),
            },
            overall_utility=68.4,
            gate_passed=True,
        ).to_dict()

        card = generate_dataset_card("user/test", traces, quality_summary=quality)

        # Frontmatter has flat score key
        assert "training_score: 68.4" in card
        assert "overall_quality: 68.4" in card

        # Stats section has quality table
        assert "opentraces Scorecard" in card
        assert "training" in card
        assert "68.4%" in card
        assert "PASSING" in card

    def test_fresh_card_without_quality(self):
        """Fresh dataset card works without quality data (backwards compat)."""
        traces = [_make_trace("t1")]
        card = generate_dataset_card("user/test", traces)

        assert "training_score" not in card
        assert "opentraces Scorecard" not in card
        assert "opentraces" in card  # basic tags still present

    def test_update_card_preserves_user_content(self):
        """Updating an existing card preserves non-machine-managed content."""
        traces = [_make_trace("t1")]
        existing = generate_dataset_card("user/test", traces)

        # Add user content after the auto section
        user_content = "\n\n## My Custom Section\n\nThis should be preserved.\n"
        existing_with_user = existing + user_content

        # Update with quality
        quality = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=1,
            persona_scores={
                "conformance": PersonaScoreSummary(average=90.0, min=90.0, max=90.0),
            },
            overall_utility=90.0,
            gate_passed=True,
        ).to_dict()

        updated = generate_dataset_card(
            "user/test", traces,
            existing_card=existing_with_user,
            quality_summary=quality,
        )

        assert "My Custom Section" in updated
        assert "This should be preserved." in updated
        assert "opentraces Scorecard" in updated
        assert "conformance_score: 90.0" in updated

    def test_card_failing_gate(self):
        """Dataset card shows FAILING gate status."""
        traces = [_make_trace("t1")]
        quality = QualitySummary(
            scorer_version="0.2.0",
            scoring_mode="deterministic",
            judge_model=None,
            assessed_at="2026-03-31T14:00:00Z",
            trace_count=1,
            persona_scores={
                "training": PersonaScoreSummary(average=20.0, min=20.0, max=20.0),
            },
            overall_utility=20.0,
            gate_passed=False,
            gate_failures=["training too low"],
        ).to_dict()

        card = generate_dataset_card("user/test", traces, quality_summary=quality)
        assert "FAILING" in card


# --- HFUploader quality.json Tests ---


class TestHFUploaderQuality:
    def _make_uploader(self, mock_api: MagicMock) -> HFUploader:
        """Create an HFUploader with a mocked HfApi instance."""
        uploader = HFUploader.__new__(HFUploader)
        uploader.token = "fake"
        uploader.repo_id = "user/test"
        uploader.api = mock_api
        return uploader

    def test_upload_quality_json(self):
        """upload_quality_json calls upload_file with correct args."""
        mock_api = MagicMock()
        uploader = self._make_uploader(mock_api)

        summary_dict = {"scorer_version": "0.2.0", "gate_status": "passing"}
        result = uploader.upload_quality_json(summary_dict)

        assert result is True
        mock_api.upload_file.assert_called_once()
        call_kwargs = mock_api.upload_file.call_args
        assert call_kwargs.kwargs["path_in_repo"] == "quality.json"
        assert call_kwargs.kwargs["repo_id"] == "user/test"
        assert call_kwargs.kwargs["repo_type"] == "dataset"

    def test_upload_quality_json_failure(self):
        """upload_quality_json returns False on failure."""
        mock_api = MagicMock()
        mock_api.upload_file.side_effect = Exception("network error")
        uploader = self._make_uploader(mock_api)

        result = uploader.upload_quality_json({"test": True})
        assert result is False

    def test_fetch_quality_json(self):
        """fetch_quality_json downloads and parses quality.json."""
        import tempfile

        mock_api = MagicMock()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"scorer_version": "0.2.0", "gate_status": "passing"}, f)
            temp_path = f.name

        mock_api.hf_hub_download.return_value = temp_path
        uploader = self._make_uploader(mock_api)

        result = uploader.fetch_quality_json()
        assert result is not None
        assert result["scorer_version"] == "0.2.0"

    def test_fetch_quality_json_missing(self):
        """fetch_quality_json returns None when file doesn't exist."""
        mock_api = MagicMock()
        mock_api.hf_hub_download.side_effect = Exception("Not found")
        uploader = self._make_uploader(mock_api)

        result = uploader.fetch_quality_json()
        assert result is None


# --- CLI assess command Tests ---


class TestAssessCLI:
    def test_assess_help_visible(self):
        """assess command is visible (not hidden)."""
        from opentraces.cli import main
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "assess" in result.output

    def test_assess_has_dataset_option(self):
        """assess command has --dataset option."""
        from opentraces.cli import main
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(main, ["assess", "--help"])
        assert "--dataset" in result.output
        assert "--compare-remote" in result.output
        assert "--all-staged" in result.output

    def test_assess_no_traces(self):
        """assess with no staged traces shows helpful message."""
        from opentraces.cli import main
        from click.testing import CliRunner

        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["assess"])
            # Should handle missing staging gracefully
            assert result.exit_code == 0

    def test_assess_remote_no_hfmount(self):
        """_assess-remote fails gracefully when hf-mount not installed."""
        from opentraces.cli import main
        from click.testing import CliRunner

        runner = CliRunner()
        with runner.isolated_filesystem():
            with patch("shutil.which", return_value=None):
                result = runner.invoke(main, ["_assess-remote", "--repo", "user/test"])
                assert "hf-mount" in result.output
