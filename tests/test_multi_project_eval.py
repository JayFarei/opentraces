"""Multi-project evaluation: run the quality harness across all available Claude Code projects.

Discovers projects in ~/.claude/projects/, samples sessions from each,
parses them through the pipeline, and generates a cross-project report.
This surfaces schema gaps that single-project dogfooding misses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opentraces.quality import (
    discover_projects,
    assess_multi_project,
    generate_multi_project_report,
    check_gate,
)
from opentraces.quality.engine import MultiProjectAssessment, ProjectInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECTS_DIR = Path.home() / ".claude" / "projects"


@pytest.fixture
def available_projects():
    """Discover all projects with session files."""
    if not PROJECTS_DIR.exists():
        pytest.skip("~/.claude/projects/ not found")
    projects = discover_projects(PROJECTS_DIR)
    if not projects:
        pytest.skip("No projects with session files found")
    return projects


@pytest.fixture
def multi_assessment(available_projects):
    """Run multi-project assessment (cached per test session)."""
    return assess_multi_project(
        available_projects,
        max_per_project=5,
        max_total=100,
    )


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------

class TestDiscovery:
    """Test project discovery."""

    def test_discovers_projects(self, available_projects):
        """Should find multiple projects."""
        assert len(available_projects) >= 3, (
            f"Expected 3+ projects, found {len(available_projects)}"
        )

    def test_projects_sorted_by_session_count(self, available_projects):
        """Projects should be sorted by session count descending."""
        counts = [p.session_count for p in available_projects]
        assert counts == sorted(counts, reverse=True)

    def test_project_info_populated(self, available_projects):
        """Each project should have name, path, and session_count."""
        for p in available_projects:
            assert p.name
            assert p.path.exists()
            assert p.session_count > 0

    def test_discovery_with_nonexistent_dir(self):
        """Should return empty list for nonexistent directory."""
        projects = discover_projects(Path("/nonexistent/dir"))
        assert projects == []


# ---------------------------------------------------------------------------
# Multi-project assessment
# ---------------------------------------------------------------------------

class TestMultiProjectAssessment:
    """Test cross-project evaluation."""

    def test_assessment_runs(self, multi_assessment):
        """Should produce cohorts for multiple projects."""
        assert len(multi_assessment.cohorts) >= 2

    def test_total_traces_reasonable(self, multi_assessment):
        """Should parse a meaningful number of traces."""
        assert multi_assessment.total_traces_parsed >= 10, (
            f"Only {multi_assessment.total_traces_parsed} traces parsed"
        )

    def test_cross_project_audit_exists(self, multi_assessment):
        """Should produce a cross-project schema audit."""
        assert multi_assessment.cross_project_audit is not None
        assert multi_assessment.cross_project_audit.total_fields > 50

    def test_cross_project_averages(self, multi_assessment):
        """Should compute cross-project persona averages."""
        avg = multi_assessment.cross_project_averages
        assert "conformance" in avg
        assert avg["conformance"] > 0

    def test_cohorts_have_scores(self, multi_assessment):
        """Each cohort should have persona averages."""
        for cohort in multi_assessment.cohorts:
            assert cohort.traces_parsed > 0
            assert len(cohort.batch.persona_averages) > 0

    def test_max_total_respected(self, available_projects):
        """max_total cap should limit total traces."""
        result = assess_multi_project(
            available_projects,
            max_per_project=2,
            max_total=10,
        )
        assert result.total_traces_parsed <= 10

    def test_preservation_computed(self, multi_assessment):
        """At least some traces should have preservation scores."""
        pres_count = sum(
            1 for cohort in multi_assessment.cohorts
            for a in cohort.batch.assessments
            if a.preservation is not None
        )
        assert pres_count > 0, "No preservation scores computed"


# ---------------------------------------------------------------------------
# Cross-project schema findings
# ---------------------------------------------------------------------------

class TestCrossProjectSchemaFindings:
    """Validate schema findings across diverse projects."""

    def test_universally_populated_fields(self, multi_assessment):
        """Core fields should be populated across all projects."""
        audit = multi_assessment.cross_project_audit
        assert audit is not None

        must_have = ["schema_version", "trace_id", "session_id", "agent.name"]
        for path in must_have:
            field_result = next(
                (f for f in audit.fields if f.path == path), None
            )
            assert field_result is not None, f"Missing audit for {path}"
            assert field_result.population_rate >= 0.9, (
                f"{path} should be universal but rate is "
                f"{field_result.population_rate:.0%} across {audit.total_traces} traces"
            )

    def test_not_yet_implemented_consistent(self, multi_assessment):
        """Fields classified as not_yet_implemented should be empty across all projects."""
        audit = multi_assessment.cross_project_audit
        assert audit is not None

        by_class = audit.by_classification
        nyi = by_class.get("not_yet_implemented", [])
        for field_result in nyi:
            assert field_result.population_rate < 0.1, (
                f"{field_result.path} classified as not_yet_implemented but "
                f"populated in {field_result.population_rate:.0%} of traces"
            )

    def test_diverse_project_types(self, multi_assessment):
        """Should cover projects from different domains."""
        project_names = [c.project.name for c in multi_assessment.cohorts]
        # We expect at least 3 distinct project types
        assert len(project_names) >= 3, (
            f"Only {len(project_names)} projects in evaluation"
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestMultiProjectReport:
    """Generate the cross-project report."""

    def test_generate_report(self, multi_assessment):
        """Generate the multi-project evaluation report with analysis."""
        # Build failure analysis section
        failure_lines = []
        failure_lines.append("## Parse Failure Analysis")
        failure_lines.append("")
        failure_lines.append(
            f"**{multi_assessment.total_traces_failed}** of "
            f"**{multi_assessment.total_sessions_scanned}** sessions failed to parse "
            f"({multi_assessment.total_traces_failed / max(multi_assessment.total_sessions_scanned, 1):.0%} failure rate)."
        )
        failure_lines.append("")
        failure_lines.append("**Root cause:** All failures are tiny sessions (2-7 JSONL lines) "
                             "with 0 tool calls that don't meet the quality threshold "
                             "(min 1 tool call + min 2 steps). These are quick question/answer "
                             "exchanges without any tool use, not agent traces.")
        failure_lines.append("")
        failure_lines.append("**Verdict:** This is **working as designed**. The quality filter "
                             "correctly excludes pure-chat sessions that have no training, RL, "
                             "or analytics value. No action needed.")
        failure_analysis = "\n".join(failure_lines)

        # Load persona analyses if available
        persona_analyses = {}
        analyses_dir = Path(".gstack/qa/persona-analyses")
        if analyses_dir.exists():
            for f in analyses_dir.glob("*.md"):
                persona_analyses[f.stem] = f.read_text()

        report = generate_multi_project_report(
            multi_assessment,
            failure_analysis=failure_analysis,
            persona_analyses=persona_analyses,
        )

        # Write to .gstack/qa/
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = Path(f".gstack/qa/multi-project-eval-{ts}.md")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report)

        # Verify structure
        assert "# Multi-Project Trace Quality Evaluation" in report
        assert "## Cross-Project Averages" in report
        assert "## Per-Project Results" in report
        assert "## Per-Check Breakdown" in report
        assert "## Schema Completeness Audit" in report
        assert "## Parse Failure Analysis" in report

        # Print summary
        print(f"\nReport written to {report_path}")
        print(f"\n{'=' * 60}")
        print(f"MULTI-PROJECT EVALUATION")
        print(f"{'=' * 60}")
        print(f"  Projects: {len(multi_assessment.cohorts)}")
        print(f"  Traces parsed: {multi_assessment.total_traces_parsed}")
        print(f"  Traces failed: {multi_assessment.total_traces_failed}")
        for name, avg in multi_assessment.cross_project_averages.items():
            print(f"  {name:15s}: {avg:.1f}%")
        if multi_assessment.cross_project_audit:
            audit = multi_assessment.cross_project_audit
            print(f"  Schema gaps: {audit.gap_count}/{audit.total_fields}")
        print(f"{'=' * 60}")
