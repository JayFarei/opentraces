"""Integration tests: run the full quality assessment harness against real sessions.

This is the comprehensive test that validates the entire harness works
end-to-end against the developer's actual Claude Code sessions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opentraces.config import Config
from opentraces.pipeline import process_trace
from opentraces.parsers.claude_code import ClaudeCodeParser
from opentraces_schema import TraceRecord

from opentraces.quality import (
    assess_trace,
    assess_batch,
    generate_report,
    audit_schema_completeness,
    format_audit_report,
    score_trace,
    check_gate,
    DEFAULT_THRESHOLDS,
    PRESERVATION_THRESHOLD,
    RubricReport,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

THIS_PROJECT_DIR = Path(os.environ["OPENTRACES_TEST_PROJECT_DIR"]) if "OPENTRACES_TEST_PROJECT_DIR" in os.environ else None
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project_sessions():
    """Find this project's session files."""
    if THIS_PROJECT_DIR is None or not THIS_PROJECT_DIR.exists():
        pytest.skip("Set OPENTRACES_TEST_PROJECT_DIR to run harness tests")
    sessions = list(THIS_PROJECT_DIR.glob("*.jsonl"))
    if not sessions:
        pytest.skip("No session files found")
    return sessions


@pytest.fixture
def parsed_traces(project_sessions):
    """Parse all sessions through the full pipeline."""
    parser = ClaudeCodeParser()
    cfg = Config()
    traces = []

    for session_path in project_sessions[:10]:
        record = parser.parse_session(session_path)
        if record is None:
            continue

        processed = process_trace(
            record=record,
            project_dir=REPO_ROOT,
            cfg=cfg,
        )
        record = processed.record
        record.content_hash = record.compute_content_hash()

        traces.append(record)

    if not traces:
        pytest.skip("No traces parsed successfully")
    return traces


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Ensure the promoted score_trace() works identically."""

    def test_score_trace_returns_rubric_report(self, parsed_traces):
        """score_trace() should return a RubricReport."""
        t = parsed_traces[0]
        raw = json.loads(t.to_jsonl_line())
        report = score_trace(t, raw)
        assert isinstance(report, RubricReport)
        assert report.total_score > 0

    def test_score_trace_matches_old_thresholds(self, parsed_traces):
        """Every trace should still score >= 70%."""
        for t in parsed_traces:
            raw = json.loads(t.to_jsonl_line())
            report = score_trace(t, raw)
            assert report.total_score >= 70.0, (
                f"Trace {t.trace_id[:8]} scored {report.total_score}%"
            )


# ---------------------------------------------------------------------------
# Schema completeness audit
# ---------------------------------------------------------------------------

class TestSchemaAudit:
    """Schema completeness audit on real traces."""

    def test_audit_runs(self, parsed_traces):
        """Audit should complete without errors."""
        report = audit_schema_completeness(parsed_traces)
        assert report.total_traces == len(parsed_traces)
        assert report.total_fields > 50  # We have 70+ field specs

    def test_known_gaps_classified(self, parsed_traces):
        """Known missing fields should be classified correctly."""
        report = audit_schema_completeness(parsed_traces)

        # environment.os is now inferred from cwd path, should be populated
        os_field = next(
            (f for f in report.fields if f.path == "environment.os"), None
        )
        assert os_field is not None
        # After parser fix, OS is inferred from cwd. Check a still-missing field instead.
        repo_field = next(
            (f for f in report.fields if f.path == "task.repository"), None
        )
        assert repo_field is not None
        assert repo_field.classification in ("not_yet_implemented", "enrichment_gap")

    def test_always_populated_fields(self, parsed_traces):
        """Fields that should always be populated must have high rates."""
        report = audit_schema_completeness(parsed_traces)

        always_ok = ["schema_version", "trace_id", "session_id", "agent.name"]
        for path in always_ok:
            field_result = next(
                (f for f in report.fields if f.path == path), None
            )
            assert field_result is not None, f"Missing audit for {path}"
            assert field_result.population_rate >= 0.9, (
                f"{path} should be always populated but rate is "
                f"{field_result.population_rate:.0%}"
            )

    def test_report_format(self, parsed_traces):
        """Report should produce valid markdown."""
        report = audit_schema_completeness(parsed_traces)
        md = format_audit_report(report)
        assert "## Schema Completeness Audit" in md
        assert "Fields checked:" in md


# ---------------------------------------------------------------------------
# Full harness assessment
# ---------------------------------------------------------------------------

class TestPersonaAssessment:
    """Run the full multi-persona assessment."""

    def test_assess_trace_runs(self, parsed_traces):
        """assess_trace should produce scores for each persona."""
        t = parsed_traces[0]
        assessment = assess_trace(t)
        assert assessment.trace_id == t.trace_id
        assert len(assessment.persona_scores) >= 1  # At least conformance

    def test_assess_batch_runs(self, parsed_traces):
        """assess_batch should assess all traces."""
        batch = assess_batch(parsed_traces)
        assert len(batch.assessments) == len(parsed_traces)
        assert batch.schema_audit is not None

    def test_quality_gate_passes(self, parsed_traces):
        """All persona thresholds should pass via check_gate().

        Thresholds are defined in src/opentraces/quality/gates.py
        (DEFAULT_THRESHOLDS), not hardcoded in tests.
        """
        batch = assess_batch(parsed_traces)
        result = check_gate(batch)
        assert result.passed, (
            f"Quality gate failed with {len(result.failures)} failures:\n"
            + "\n".join(f"  - {f}" for f in result.failures)
        )


# ---------------------------------------------------------------------------
# Preservation with raw sessions
# ---------------------------------------------------------------------------

class TestPreservation:
    """Preservation comparison against raw session files."""

    def test_preservation_with_raw_sessions(self, parsed_traces, project_sessions):
        """Preservation should be >= 0.85 when raw sessions are available."""
        batch = assess_batch(
            parsed_traces,
            raw_session_dir=THIS_PROJECT_DIR,
        )
        pres_scores = [
            a.preservation.overall
            for a in batch.assessments
            if a.preservation is not None
        ]
        if pres_scores:
            avg = sum(pres_scores) / len(pres_scores)
            assert avg >= 0.85, f"Preservation average {avg:.2f} below 0.85"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestReportGeneration:
    """Generate the full assessment report."""

    def test_generate_full_report(self, parsed_traces, tmp_path):
        """Generate the comprehensive report."""
        batch = assess_batch(
            parsed_traces,
            raw_session_dir=THIS_PROJECT_DIR,
        )
        report = generate_report(batch)

        # Write to tmp
        report_path = tmp_path / "persona-rubric-report.md"
        report_path.write_text(report)

        # Also persist to .gstack/qa/
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        persistent_report = Path(f".gstack/qa/persona-rubric-{ts}.md")
        persistent_report.parent.mkdir(parents=True, exist_ok=True)
        persistent_report.write_text(report)

        # Verify content
        assert "# Trace Quality Assessment Report" in report
        assert "## Summary" in report
        assert "## Schema Completeness Audit" in report
        assert "## Recommendations" in report

        print(f"\nReport written to {persistent_report}")
        print(f"\n{'=' * 60}")
        print(f"QUALITY ASSESSMENT: {len(parsed_traces)} traces")
        print(f"{'=' * 60}")
        for name, avg in batch.persona_averages.items():
            print(f"  {name:15s}: {avg:.1f}%")
        if batch.preservation_average is not None:
            print(f"  {'preservation':15s}: {batch.preservation_average:.0%}")
        if batch.schema_audit:
            print(f"  Schema gaps: {batch.schema_audit.gap_count}")
        print(f"{'=' * 60}")
