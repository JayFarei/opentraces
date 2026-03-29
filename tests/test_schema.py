"""Tests for the opentraces-schema package."""

import json

from opentraces_schema import (
    SCHEMA_VERSION,
    Agent,
    Attribution,
    AttributionConversation,
    AttributionFile,
    AttributionRange,
    Environment,
    Metrics,
    Observation,
    Outcome,
    SecurityMetadata,
    Snippet,
    Step,
    Task,
    TokenUsage,
    ToolCall,
    TraceRecord,
    VCS,
)


class TestSchemaVersion:
    def test_schema_version_format(self):
        assert SCHEMA_VERSION == "0.1.0"

    def test_trace_record_has_schema_version(self):
        record = TraceRecord(
            trace_id="test-id",
            session_id="session-1",
            agent=Agent(name="claude-code"),
        )
        assert record.schema_version == "0.1.0"


class TestTraceRecordRoundTrip:
    def test_minimal_record(self):
        record = TraceRecord(
            trace_id="test-id",
            session_id="session-1",
            agent=Agent(name="claude-code", version="1.0.83", model="anthropic/claude-sonnet-4-20250514"),
        )
        json_str = record.model_dump_json()
        restored = TraceRecord.model_validate_json(json_str)
        assert restored.trace_id == "test-id"
        assert restored.agent.name == "claude-code"

    def test_full_record_roundtrip(self):
        record = TraceRecord(
            trace_id="abc-123",
            session_id="sess-456",
            timestamp_start="2026-03-27T10:00:00Z",
            timestamp_end="2026-03-27T10:15:00Z",
            task=Task(
                description="Fix failing test",
                source="user_prompt",
                repository="owner/repo",
                base_commit="abc123",
            ),
            agent=Agent(name="claude-code", version="1.0.83", model="anthropic/claude-sonnet-4-20250514"),
            environment=Environment(
                os="darwin",
                shell="zsh",
                vcs=VCS(type="git", base_commit="abc123", branch="main"),
                language_ecosystem=["typescript", "python"],
            ),
            system_prompts={"sp_hash1": "You are Claude Code..."},
            tool_definitions=[{"name": "bash", "description": "Execute shell commands"}],
            steps=[
                Step(
                    step_index=1,
                    role="user",
                    content="Fix the failing test",
                    timestamp="2026-03-27T10:00:00Z",
                ),
                Step(
                    step_index=2,
                    role="agent",
                    content="I'll investigate...",
                    reasoning_content="The user wants me to fix...",
                    model="anthropic/claude-sonnet-4-20250514",
                    system_prompt_hash="sp_hash1",
                    agent_role="main",
                    call_type="main",
                    tools_available=["bash", "read", "edit"],
                    tool_calls=[
                        ToolCall(
                            tool_call_id="tc_001",
                            tool_name="bash",
                            input={"command": "npm test"},
                            duration_ms=3400,
                        ),
                    ],
                    observations=[
                        Observation(
                            source_call_id="tc_001",
                            content="FAIL src/parser.test.ts",
                            output_summary="1 test failed",
                        ),
                    ],
                    snippets=[
                        Snippet(
                            file_path="src/parser.ts",
                            start_line=42,
                            end_line=55,
                            language="typescript",
                            text="function parseToken()...",
                            source_step=2,
                        ),
                    ],
                    token_usage=TokenUsage(
                        input_tokens=12400,
                        output_tokens=890,
                        cache_read_tokens=11200,
                    ),
                    timestamp="2026-03-27T10:01:00Z",
                ),
            ],
            outcome=Outcome(
                success=True,
                signal_source="deterministic",
                signal_confidence="derived",
                committed=True,
                commit_sha="def789",
                patch="--- a/parser.ts\n+++ b/parser.ts",
            ),
            dependencies=["typescript", "jest"],
            metrics=Metrics(
                total_steps=2,
                total_input_tokens=12400,
                total_output_tokens=890,
                total_duration_s=60.0,
                cache_hit_rate=0.9,
                estimated_cost_usd=0.05,
            ),
            security=SecurityMetadata(scanned=True, redactions_applied=2),
            attribution=Attribution(
                files=[
                    AttributionFile(
                        path="src/parser.ts",
                        conversations=[
                            AttributionConversation(
                                contributor={"type": "ai", "model_id": "anthropic/claude-sonnet-4-20250514"},
                                url="opentraces://abc-123/step_2",
                                ranges=[
                                    AttributionRange(
                                        start_line=42,
                                        end_line=55,
                                        content_hash="murmur3:9f2e8a1b",
                                        confidence="high",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        )

        json_str = record.model_dump_json()
        restored = TraceRecord.model_validate_json(json_str)

        assert restored.trace_id == "abc-123"
        assert len(restored.steps) == 2
        assert restored.steps[1].tool_calls[0].tool_name == "bash"
        assert restored.steps[1].observations[0].output_summary == "1 test failed"
        assert restored.steps[1].snippets[0].file_path == "src/parser.ts"
        assert restored.outcome.committed is True
        assert restored.attribution is not None
        assert restored.attribution.files[0].conversations[0].ranges[0].confidence == "high"
        assert restored.environment.vcs.type == "git"


class TestContentHash:
    def test_content_hash_deterministic(self):
        record = TraceRecord(
            trace_id="test-id",
            session_id="session-1",
            agent=Agent(name="claude-code"),
        )
        hash1 = record.compute_content_hash()
        hash2 = record.compute_content_hash()
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_different_content_different_hash(self):
        r1 = TraceRecord(trace_id="id-1", session_id="s-1", agent=Agent(name="claude-code"))
        r2 = TraceRecord(trace_id="id-1", session_id="s-2", agent=Agent(name="claude-code"))
        assert r1.compute_content_hash() != r2.compute_content_hash()

    def test_same_content_same_hash_regardless_of_trace_id(self):
        """content_hash excludes trace_id for stable deduplication."""
        r1 = TraceRecord(trace_id="id-1", session_id="s-1", agent=Agent(name="claude-code"))
        r2 = TraceRecord(trace_id="id-2", session_id="s-1", agent=Agent(name="claude-code"))
        assert r1.compute_content_hash() == r2.compute_content_hash()

    def test_to_jsonl_line_includes_hash(self):
        record = TraceRecord(
            trace_id="test-id",
            session_id="session-1",
            agent=Agent(name="claude-code"),
        )
        line = record.to_jsonl_line()
        parsed = json.loads(line)
        assert parsed["content_hash"] is not None
        assert len(parsed["content_hash"]) == 64


class TestStepTypes:
    def test_subagent_step(self):
        step = Step(
            step_index=5,
            role="agent",
            agent_role="explore",
            parent_step=3,
            call_type="subagent",
            content="Searching...",
            subagent_trajectory_ref="subagent-session-id",
        )
        assert step.call_type == "subagent"
        assert step.parent_step == 3

    def test_warmup_step(self):
        step = Step(
            step_index=0,
            role="agent",
            call_type="warmup",
        )
        assert step.call_type == "warmup"


class TestOutcomeSignals:
    def test_derived_confidence(self):
        outcome = Outcome(committed=True, commit_sha="abc123")
        assert outcome.signal_confidence == "derived"

    def test_annotated_confidence(self):
        outcome = Outcome(
            success=True,
            signal_source="user_annotation",
            signal_confidence="annotated",
        )
        assert outcome.signal_confidence == "annotated"

    def test_no_success_signal(self):
        outcome = Outcome()
        assert outcome.success is None
        assert outcome.committed is False


class TestVCSDiscriminator:
    def test_no_git(self):
        vcs = VCS(type="none")
        assert vcs.base_commit is None
        assert vcs.branch is None

    def test_git_vcs(self):
        vcs = VCS(type="git", base_commit="abc", branch="main")
        assert vcs.type == "git"


class TestAttributionExperimental:
    def test_attribution_marked_experimental(self):
        attr = Attribution()
        assert attr.experimental is True

    def test_null_attribution(self):
        record = TraceRecord(
            trace_id="test",
            session_id="sess",
            agent=Agent(name="claude-code"),
        )
        assert record.attribution is None


class TestSecurityMetadata:
    def test_default_scanned(self):
        sec = SecurityMetadata()
        assert sec.scanned is False

    def test_scanned_with_classifier(self):
        sec = SecurityMetadata(scanned=True, classifier_version="0.1.0", flags_reviewed=5)
        assert sec.classifier_version == "0.1.0"
