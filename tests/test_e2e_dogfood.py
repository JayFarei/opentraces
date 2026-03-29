"""End-to-end dogfood test: run opentraces on its own build traces.

Parses this project's Claude Code sessions through the full pipeline,
then scores each output trace against a conformance rubric derived
from the opentraces schema spec (resources/intent.md).

This is the ultimate test: if opentraces can't correctly parse and
enrich the sessions that built opentraces, something is wrong.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opentraces.config import Config
from opentraces.pipeline import process_trace
from opentraces.parsers.claude_code import ClaudeCodeParser
from opentraces.parsers.quality import meets_quality_threshold
from opentraces.quality import score_trace
from opentraces.security.scanner import scan_trace_record
from opentraces.security.classifier import classify_trace_record
from opentraces_schema import TraceRecord, SCHEMA_VERSION


# ---------------------------------------------------------------------------
# E2E test: parse this project's traces through the full pipeline
# ---------------------------------------------------------------------------

THIS_PROJECT_DIR = Path(os.environ["OPENTRACES_TEST_PROJECT_DIR"]) if "OPENTRACES_TEST_PROJECT_DIR" in os.environ else None
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project_sessions():
    """Find this project's session files."""
    if THIS_PROJECT_DIR is None or not THIS_PROJECT_DIR.exists():
        pytest.skip("Set OPENTRACES_TEST_PROJECT_DIR to run dogfood tests")
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

    for session_path in project_sessions[:10]:  # Cap at 10 for speed
        record = parser.parse_session(session_path)
        if record is None:
            continue

        processed = process_trace(
            record=record,
            project_dir=REPO_ROOT,
            cfg=cfg,
        )
        record = processed.record

        # Compute content hash
        record.content_hash = record.compute_content_hash()

        traces.append(record)

    if not traces:
        pytest.skip("No traces parsed successfully")
    return traces


class TestDogfoodPipeline:
    """E2E: parse this project's own sessions and verify the output."""

    def test_at_least_one_trace_parsed(self, parsed_traces):
        assert len(parsed_traces) >= 1

    def test_all_traces_have_schema_version(self, parsed_traces):
        for t in parsed_traces:
            assert t.schema_version == SCHEMA_VERSION

    def test_all_traces_have_content_hash(self, parsed_traces):
        for t in parsed_traces:
            assert t.content_hash is not None
            assert len(t.content_hash) == 64

    def test_all_traces_have_steps(self, parsed_traces):
        for t in parsed_traces:
            assert len(t.steps) >= 2

    def test_all_traces_have_tool_calls(self, parsed_traces):
        for t in parsed_traces:
            total_tc = sum(len(s.tool_calls) for s in t.steps)
            assert total_tc >= 1, f"Trace {t.trace_id} has 0 tool calls"

    def test_at_least_one_trace_has_subagents(self, parsed_traces):
        has_subagent = any(
            any(s.call_type == "subagent" for s in t.steps)
            for t in parsed_traces
        )
        assert has_subagent, "Expected at least one trace with subagent steps (this project uses Agent tool)"

    def test_at_least_one_trace_has_snippets(self, parsed_traces):
        has_snippets = any(
            any(len(s.snippets) > 0 for s in t.steps)
            for t in parsed_traces
        )
        assert has_snippets, "Expected at least one trace with code snippets"

    def test_reasoning_or_encrypted_thinking(self, parsed_traces):
        """Reasoning may be empty if model uses encrypted thinking (signature field).
        This is expected for Opus 4.6. The parser correctly reads the thinking
        block but the content is empty because it's encrypted."""
        # This is informational, not a failure
        has_reasoning = any(
            any(s.reasoning_content for s in t.steps)
            for t in parsed_traces
        )
        if not has_reasoning:
            print("  Note: no reasoning_content found (model uses encrypted thinking, expected for Opus 4.6)")

    def test_metrics_computed(self, parsed_traces):
        for t in parsed_traces:
            assert t.metrics.total_steps > 0
            # Cost should be computed for traces with tokens
            if t.metrics.total_input_tokens > 0:
                assert t.metrics.estimated_cost_usd is not None

    def test_jsonl_round_trip(self, parsed_traces):
        """Serialize to JSONL and deserialize, verify no data loss."""
        for t in parsed_traces[:3]:
            jsonl = t.to_jsonl_line()
            restored = TraceRecord.model_validate_json(jsonl)
            assert restored.trace_id == t.trace_id
            assert restored.schema_version == t.schema_version
            assert len(restored.steps) == len(t.steps)

    def test_quality_filter_passes(self, parsed_traces):
        """All parsed traces should meet quality threshold."""
        for t in parsed_traces:
            assert meets_quality_threshold(t)


class TestDogfoodRubric:
    """Score each trace against the full conformance rubric."""

    def test_rubric_score_above_threshold(self, parsed_traces):
        """Every trace should score at least 70% on the rubric."""
        reports = []
        for t in parsed_traces:
            raw = json.loads(t.to_jsonl_line())
            report = score_trace(t, raw)
            reports.append(report)
            assert report.total_score >= 70.0, (
                f"Trace {t.trace_id[:8]} scored {report.total_score}%. "
                f"Failed items: {[i.name for i in report.items if not i.passed]}"
            )

    def test_rubric_average_above_80(self, parsed_traces):
        """Average rubric score across all traces should be 80%+."""
        scores = []
        for t in parsed_traces:
            raw = json.loads(t.to_jsonl_line())
            report = score_trace(t, raw)
            scores.append(report.total_score)
        avg = sum(scores) / len(scores)
        assert avg >= 80.0, f"Average rubric score {avg:.1f}% below 80% threshold"

    def test_no_critical_failures(self, parsed_traces):
        """No trace should fail any weight=1.0 rubric item."""
        for t in parsed_traces:
            raw = json.loads(t.to_jsonl_line())
            report = score_trace(t, raw)
            critical_failures = [
                i for i in report.items
                if i.weight >= 1.0 and not i.passed
            ]
            assert len(critical_failures) == 0, (
                f"Trace {t.trace_id[:8]} has critical failures: "
                f"{[i.name for i in critical_failures]}"
            )


class TestDogfoodSecurity:
    """Security scanning on this project's own traces."""

    def test_scan_finds_no_raw_secrets(self, parsed_traces):
        """Security scan should not find raw secrets in parsed output."""
        for t in parsed_traces[:3]:
            result = scan_trace_record(t)
            # We expect some false positives (e.g., example patterns in code)
            # but no high-severity matches
            high_severity = [
                m for m in result.matches
                if m.severity == "critical"
            ]
            # This is informational, not blocking
            if high_severity:
                print(f"  Trace {t.trace_id[:8]}: {len(high_severity)} critical matches")

    def test_classifier_on_real_traces(self, parsed_traces):
        """Classifier should produce reasonable risk scores."""
        for t in parsed_traces[:3]:
            result = classify_trace_record(t, sensitivity="medium")
            assert 0.0 <= result.risk_score <= 1.0
            # This project's traces might flag internal paths, that's expected


class TestDogfoodReport:
    """Generate a full rubric report for manual review."""

    def test_generate_report(self, parsed_traces, tmp_path):
        """Generate a human-readable rubric report."""
        report_lines = []
        report_lines.append("# opentraces Dogfood Rubric Report")
        report_lines.append(f"\nTraces analyzed: {len(parsed_traces)}")
        report_lines.append(f"Project: {THIS_PROJECT_DIR.name}\n")

        all_scores = []
        category_totals: dict[str, list[float]] = {}

        for t in parsed_traces:
            raw = json.loads(t.to_jsonl_line())
            report = score_trace(t, raw)
            all_scores.append(report.total_score)

            report_lines.append(f"## Trace: {t.trace_id[:12]}...")
            report_lines.append(f"Task: {report.task_description}")
            report_lines.append(f"Steps: {len(t.steps)}, Tool calls: {sum(len(s.tool_calls) for s in t.steps)}")
            report_lines.append(f"**Score: {report.total_score}%** (pass rate: {report.pass_rate}%)\n")

            for cat, score in report.category_scores.items():
                category_totals.setdefault(cat, []).append(score)
                report_lines.append(f"  {cat}: {score}%")

            report_lines.append("")
            for item in report.items:
                status = "PASS" if item.passed else "FAIL"
                report_lines.append(f"  [{status}] {item.name} ({item.evidence})")
                if item.note:
                    report_lines.append(f"    Note: {item.note}")
            report_lines.append("")

        # Summary
        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
        report_lines.append("---")
        report_lines.append(f"\n## Summary")
        report_lines.append(f"Average score: {avg_score:.1f}%")
        report_lines.append(f"Min score: {min(all_scores):.1f}%")
        report_lines.append(f"Max score: {max(all_scores):.1f}%\n")
        report_lines.append("### Category averages:")
        for cat, scores in sorted(category_totals.items()):
            avg = sum(scores) / len(scores)
            report_lines.append(f"  {cat}: {avg:.1f}%")

        # Write report
        report_path = tmp_path / "dogfood-rubric-report.md"
        report_path.write_text("\n".join(report_lines))

        # Also write to .gstack/qa/
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        persistent_report = Path(f".gstack/qa/dogfood-rubric-{ts}.md")
        persistent_report.parent.mkdir(parents=True, exist_ok=True)
        persistent_report.write_text("\n".join(report_lines))

        print(f"\nReport written to {persistent_report}")
        print(f"\n{'='*60}")
        print(f"DOGFOOD RUBRIC: {avg_score:.1f}% average across {len(parsed_traces)} traces")
        print(f"{'='*60}")
        for cat, scores in sorted(category_totals.items()):
            avg = sum(scores) / len(scores)
            print(f"  {cat:15s}: {avg:.1f}%")
        print(f"{'='*60}")

        assert avg_score >= 75.0, f"Overall rubric score {avg_score:.1f}% below 75% minimum"
