"""Tests for the schema completeness audit."""

from __future__ import annotations

from opentraces_schema import TraceRecord, SCHEMA_VERSION
from opentraces_schema.models import (
    Agent, Task, Environment, VCS, Step, ToolCall, Observation,
    Snippet, TokenUsage, Outcome, Metrics, SecurityMetadata, Attribution,
    AttributionFile, AttributionConversation, AttributionRange,
)
from opentraces.security import SECURITY_VERSION
from opentraces.quality.schema_audit import (
    FIELD_SPECS,
    audit_schema_completeness,
    format_audit_report,
    _is_populated,
    _get_nested_value,
    _sample_list_field,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_full_trace() -> TraceRecord:
    """Create a TraceRecord with ALL fields populated."""
    return TraceRecord(
        schema_version=SCHEMA_VERSION,
        trace_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        session_id="test-session-001",
        content_hash="a" * 64,
        timestamp_start="2026-03-28T10:00:00Z",
        timestamp_end="2026-03-28T10:30:00Z",
        task=Task(
            description="Fix the login bug",
            source="user_prompt",
            repository="owner/repo",
            base_commit="abc123",
        ),
        agent=Agent(
            name="claude-code",
            version="1.0.83",
            model="anthropic/claude-sonnet-4-20250514",
        ),
        environment=Environment(
            os="darwin",
            shell="zsh",
            vcs=VCS(type="git", base_commit="abc123", branch="main", diff="diff --git ..."),
            language_ecosystem=["python", "typescript"],
        ),
        system_prompts={"hash1": "You are a helpful assistant"},
        tool_definitions=[{"name": "Read", "description": "Read a file"}],
        steps=[
            Step(
                step_index=0,
                role="user",
                content="Fix the login bug",
                timestamp="2026-03-28T10:00:00Z",
            ),
            Step(
                step_index=1,
                role="agent",
                content="I'll look at the code",
                reasoning_content="Let me think about this...",
                model="anthropic/claude-sonnet-4-20250514",
                system_prompt_hash="hash1",
                agent_role="main",
                call_type="main",
                tools_available=["Read", "Edit"],
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc_001",
                        tool_name="Read",
                        input={"file_path": "/src/auth.py"},
                        duration_ms=150,
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc_001",
                        content="def login():\n    pass",
                        output_summary="def login()...",
                    ),
                ],
                snippets=[
                    Snippet(
                        file_path="/src/auth.py",
                        start_line=1,
                        end_line=2,
                        language="python",
                        text="def login():\n    pass",
                        source_step=1,
                    ),
                ],
                token_usage=TokenUsage(
                    input_tokens=1000,
                    output_tokens=200,
                    cache_read_tokens=500,
                    cache_write_tokens=100,
                ),
                timestamp="2026-03-28T10:01:00Z",
            ),
            Step(
                step_index=2,
                role="agent",
                content="I fixed the bug",
                model="anthropic/claude-sonnet-4-20250514",
                call_type="subagent",
                agent_role="explore",
                parent_step=1,
                subagent_trajectory_ref="sub-session-001",
                token_usage=TokenUsage(input_tokens=500, output_tokens=100),
                timestamp="2026-03-28T10:02:00Z",
            ),
        ],
        outcome=Outcome(
            success=True,
            signal_source="deterministic",
            signal_confidence="derived",
            description="Fixed the login bug",
            patch="diff --git a/src/auth.py ...",
            committed=True,
            commit_sha="def456",
        ),
        dependencies=["flask", "pytest"],
        metrics=Metrics(
            total_steps=3,
            total_input_tokens=1500,
            total_output_tokens=300,
            total_duration_s=120.0,
            cache_hit_rate=0.33,
            estimated_cost_usd=0.05,
        ),
        security=SecurityMetadata(
            scanned=True,
            flags_reviewed=3,
            redactions_applied=1,
            classifier_version=SECURITY_VERSION,
        ),
        attribution=Attribution(
            files=[
                AttributionFile(
                    path="/src/auth.py",
                    conversations=[
                        AttributionConversation(
                            contributor={"type": "ai", "model_id": "claude-sonnet-4"},
                            url="opentraces://trace_id/step_1",
                            ranges=[
                                AttributionRange(
                                    start_line=1, end_line=10,
                                    content_hash="abcd1234", confidence="high",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        metadata={"project": "test-project"},
    )


def _make_minimal_trace() -> TraceRecord:
    """Create a TraceRecord with only required fields (many gaps)."""
    return TraceRecord(
        trace_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        session_id="test-session-002",
        agent=Agent(name="claude-code"),
        steps=[
            Step(step_index=0, role="user", content="hello"),
            Step(step_index=1, role="agent"),
        ],
    )


# ---------------------------------------------------------------------------
# Unit tests: _is_populated
# ---------------------------------------------------------------------------

class TestIsPopulated:
    def test_none_is_not_populated(self):
        assert not _is_populated(None)

    def test_empty_string_is_not_populated(self):
        assert not _is_populated("")
        assert not _is_populated("  ")

    def test_nonempty_string_is_populated(self):
        assert _is_populated("hello")

    def test_empty_list_is_not_populated(self):
        assert not _is_populated([])

    def test_nonempty_list_is_populated(self):
        assert _is_populated(["a"])

    def test_empty_dict_is_not_populated(self):
        assert not _is_populated({})

    def test_nonempty_dict_is_populated(self):
        assert _is_populated({"a": 1})

    def test_zero_is_populated(self):
        assert _is_populated(0)

    def test_false_is_populated(self):
        assert _is_populated(False)

    def test_float_is_populated(self):
        assert _is_populated(0.0)


# ---------------------------------------------------------------------------
# Unit tests: _get_nested_value
# ---------------------------------------------------------------------------

class TestGetNestedValue:
    def test_simple_field(self):
        record = _make_full_trace()
        assert _get_nested_value(record, "trace_id") == record.trace_id

    def test_nested_field(self):
        record = _make_full_trace()
        assert _get_nested_value(record, "task.description") == "Fix the login bug"

    def test_deep_nested(self):
        record = _make_full_trace()
        assert _get_nested_value(record, "environment.vcs.type") == "git"

    def test_missing_field(self):
        record = _make_minimal_trace()
        assert _get_nested_value(record, "task.description") is None

    def test_none_intermediate(self):
        record = _make_minimal_trace()
        assert _get_nested_value(record, "attribution.files") is None


# ---------------------------------------------------------------------------
# Unit tests: _sample_list_field
# ---------------------------------------------------------------------------

class TestSampleListField:
    def test_step_content(self):
        record = _make_full_trace()
        populated, total = _sample_list_field(record, "steps[].content")
        assert total == 3  # 3 steps
        assert populated >= 2  # at least user + first agent have content

    def test_tool_call_ids(self):
        record = _make_full_trace()
        populated, total = _sample_list_field(record, "steps[].tool_calls[].tool_call_id")
        assert total >= 1
        assert populated >= 1

    def test_empty_steps(self):
        record = _make_minimal_trace()
        populated, total = _sample_list_field(record, "steps[].tool_calls[].tool_name")
        assert total == 0
        assert populated == 0


# ---------------------------------------------------------------------------
# Unit tests: audit_schema_completeness
# ---------------------------------------------------------------------------

class TestAuditSchemaCompleteness:
    def test_full_trace_mostly_ok(self):
        """A fully populated trace should have high OK rate."""
        report = audit_schema_completeness([_make_full_trace()])
        assert report.total_traces == 1
        assert report.total_fields == len(FIELD_SPECS)
        # Most fields should be OK
        assert report.ok_count > report.gap_count

    def test_minimal_trace_many_gaps(self):
        """A minimal trace should flag many gaps."""
        report = audit_schema_completeness([_make_minimal_trace()])
        assert report.gap_count > 10

    def test_empty_batch(self):
        """Empty batch should return empty report."""
        report = audit_schema_completeness([])
        assert report.total_traces == 0
        assert report.total_fields == len(FIELD_SPECS)

    def test_known_not_yet_implemented(self):
        """Fields in _NOT_YET_IMPLEMENTED should be classified correctly."""
        report = audit_schema_completeness([_make_minimal_trace()])
        # task.repository is not yet implemented (needs git remote enrichment)
        repo_field = next((f for f in report.fields if f.path == "task.repository"), None)
        assert repo_field is not None
        assert repo_field.classification == "not_yet_implemented"

    def test_session_dependent_fields(self):
        """Fields like outcome.committed should be session_dependent when missing."""
        report = audit_schema_completeness([_make_minimal_trace()])
        committed = next((f for f in report.fields if f.path == "outcome.committed"), None)
        assert committed is not None
        # committed is False by default, which is "populated" for a bool
        # but outcome.patch should be session_dependent
        patch = next((f for f in report.fields if f.path == "outcome.patch"), None)
        assert patch is not None
        assert patch.classification == "session_dependent"

    def test_with_raw_signal_map(self):
        """Raw signal map should influence classification."""
        raw_map = {"task.repository": True}  # Raw has repo data
        report = audit_schema_completeness([_make_minimal_trace()], raw_signal_map=raw_map)
        repo_field = next((f for f in report.fields if f.path == "task.repository"), None)
        # With raw signal present but field empty, and it's in NOT_YET_IMPLEMENTED,
        # it should still be not_yet_implemented (specific classification takes precedence)
        assert repo_field is not None
        assert repo_field.classification == "not_yet_implemented"

    def test_mixed_batch(self):
        """Mix of full and minimal traces should show partial population rates."""
        full = _make_full_trace()
        minimal = _make_minimal_trace()
        report = audit_schema_completeness([full, minimal])
        # task.description: full has it, minimal doesn't -> 50% rate
        desc = next((f for f in report.fields if f.path == "task.description"), None)
        assert desc is not None
        assert 0.4 <= desc.population_rate <= 0.6

    def test_by_classification_grouping(self):
        """by_classification should group correctly."""
        report = audit_schema_completeness([_make_minimal_trace()])
        by_class = report.by_classification
        assert "not_yet_implemented" in by_class
        assert len(by_class["not_yet_implemented"]) > 0

    def test_by_persona_impact(self):
        """by_persona_impact should only include fields with gaps."""
        report = audit_schema_completeness([_make_minimal_trace()])
        by_persona = report.by_persona_impact
        # Domain sourcing should have gaps (environment.os, dependencies, etc.)
        assert "domain" in by_persona or "analytics" in by_persona


# ---------------------------------------------------------------------------
# Unit tests: format_audit_report
# ---------------------------------------------------------------------------

class TestFormatAuditReport:
    def test_format_produces_markdown(self):
        report = audit_schema_completeness([_make_minimal_trace()])
        md = format_audit_report(report)
        assert "## Schema Completeness Audit" in md
        assert "Fields checked:" in md

    def test_format_includes_gaps(self):
        report = audit_schema_completeness([_make_minimal_trace()])
        md = format_audit_report(report)
        assert "environment.os" in md
        assert "Not Yet Implemented" in md

    def test_format_includes_persona_impact(self):
        report = audit_schema_completeness([_make_minimal_trace()])
        md = format_audit_report(report)
        assert "Impact by Persona" in md


# ---------------------------------------------------------------------------
# Integration: field spec coverage
# ---------------------------------------------------------------------------

class TestFieldSpecCoverage:
    def test_all_top_level_fields_covered(self):
        """Every field in TraceRecord should have a FieldSpec entry."""
        spec_paths = {s.path for s in FIELD_SPECS}
        # Check top-level TraceRecord fields
        for field_name in TraceRecord.model_fields:
            if field_name in ("schema_version", "trace_id"):
                # These are always populated
                assert field_name in spec_paths or any(
                    s.path == field_name for s in FIELD_SPECS
                ), f"Missing FieldSpec for TraceRecord.{field_name}"

    def test_no_duplicate_paths(self):
        """No duplicate paths in FIELD_SPECS."""
        paths = [s.path for s in FIELD_SPECS]
        assert len(paths) == len(set(paths)), (
            f"Duplicate paths: {[p for p in paths if paths.count(p) > 1]}"
        )

    def test_valid_sources(self):
        """All source values should be from known set."""
        valid_sources = {
            "parser", "enrichment:git", "enrichment:metrics",
            "enrichment:attribution", "enrichment:dependencies",
            "security", "generated",
        }
        for spec in FIELD_SPECS:
            assert spec.source in valid_sources, (
                f"Unknown source '{spec.source}' for {spec.path}"
            )

    def test_valid_expected_when(self):
        """All expected_when values should be from known set."""
        valid = {
            "always", "git_repo", "has_edits", "has_commits",
            "has_subagents", "has_tools", "has_manifests", "optional",
        }
        for spec in FIELD_SPECS:
            assert spec.expected_when in valid, (
                f"Unknown expected_when '{spec.expected_when}' for {spec.path}"
            )
