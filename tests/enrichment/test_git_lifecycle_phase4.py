"""Phase 4: lifecycle promotion + Attribution.revision + mixed contributor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import subprocess
import pytest

from opentraces.capture.git import post_commit
from opentraces_schema import (
    Agent, Attribution, AttributionConversation, AttributionFile,
    AttributionRange, Step, TokenUsage, ToolCall, TraceRecord,
)


def _sh(cmd, cwd):
    subprocess.check_call(
        cmd, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def repo(tmp_path):
    _sh(["git", "init", "-q", "-b", "main"], tmp_path)
    _sh(["git", "config", "user.email", "t@t"], tmp_path)
    _sh(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "app.py").write_text("hi\n")
    _sh(["git", "add", "-A"], tmp_path)
    _sh(["git", "commit", "-qm", "init"], tmp_path)
    return tmp_path


def _trace_with_attr(trace_id: str, file_path: str, new_string: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id, session_id="s",
        agent=Agent(name="claude-code"),
        timestamp_end=datetime.now(timezone.utc).isoformat(),
        steps=[Step(
            step_index=0, role="agent",
            tool_calls=[ToolCall(
                tool_call_id="tc0", tool_name="Edit",
                input={"file_path": file_path, "old_string": "hi", "new_string": new_string},
            )],
            observations=[], token_usage=TokenUsage(),
        )],
        attribution=Attribution(
            experimental=False,
            files=[AttributionFile(
                path=file_path,
                conversations=[AttributionConversation(
                    contributor={"type": "ai"},
                    url=f"opentraces://{trace_id}/step_0",
                    ranges=[AttributionRange(
                        start_line=1, end_line=1,
                        content_hash="murmur3:placeholder",
                        confidence="high",
                    )],
                )],
            )],
        ),
    )


class TestLifecyclePromotion:
    def test_tool_emitted_promotes_to_final(self, repo):
        # Realistic agent-scale line — the correlator's specificity
        # threshold rejects sub-30-char single-line matches to avoid
        # phantom matches on common idioms. ``GREETING`` alone was
        # fine under the old loose rule; keep a 30+ char line so the
        # positive-case test still matches against the new threshold.
        added = "def configure_greeting_dispatcher(app):"
        (repo / "app.py").write_text(f"hi\n{added}\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c"], repo)

        trace = _trace_with_attr("t-final", "app.py", f"hi\n{added}")
        post_commit.run(repo, [trace])

        # Phase 4 R35: after correlation, lifecycle → final, revision
        # pinned, git_links populated.
        assert trace.lifecycle == "final"
        assert trace.attribution.revision is not None
        assert trace.attribution.revision["vcs_type"] == "git"
        assert trace.attribution.revision["revision"] == post_commit.head_sha(repo)
        assert len(trace.git_links) == 1
        assert trace.git_links[0].tier == "tool_emitted"

    def test_overlapping_still_provisional(self, repo):
        # Overlapping is evidence of coincidence, not contribution —
        # don't promote to final.
        (repo / "app.py").write_text("hi\nGREETING\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c"], repo)
        trace = _trace_with_attr("t-ol", "other.py", "whatever")

        post_commit.run(repo, [trace])
        assert trace.lifecycle == "provisional"
        assert trace.attribution.revision is None
        # But still stamped with the link for evidence.
        assert len(trace.git_links) == 1
        assert trace.git_links[0].tier == "overlapping"


class TestDivergenceContributorMixed:
    def test_divergence_stamps_mixed_and_original(self, repo):
        # Agent wrote "AGENT_LINE" but committed bytes are "FORMATTED_LINE".
        (repo / "app.py").write_text("hi\nFORMATTED_LINE\n")
        _sh(["git", "add", "-A"], repo)
        _sh(["git", "commit", "-qm", "c"], repo)

        trace = _trace_with_attr("t-div", "app.py", "hi\nAGENT_LINE")
        post_commit.run(repo, [trace])

        assert trace.git_links[0].tier == "tool_emitted_with_divergence"
        # Divergence still pins revision + promotes to final.
        assert trace.lifecycle == "final"
        # Per-range contributor stamped mixed; range.original populated.
        rng = trace.attribution.files[0].conversations[0].ranges[0]
        assert rng.contributor is not None
        assert rng.contributor.get("type") == "mixed"
        assert rng.original is not None
        assert rng.original.get("content_hash") == "murmur3:placeholder"
