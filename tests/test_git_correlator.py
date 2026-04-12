"""Plan 041 phase 3 correlator tests.

Pure function: given a trace + a commit's unified diff, return a list
of GitLinks with evidence-graded tiers. Phase 3 covers tool_emitted
and orphan; tool_emitted_with_divergence and overlapping land in
phase 4.
"""

from __future__ import annotations

from opentraces.git.correlator import correlate
from opentraces_schema import Agent, Step, TokenUsage, ToolCall, TraceRecord


def _trace(*edits: tuple[str, str, str]) -> TraceRecord:
    """Build a minimal trace whose steps contain the given Edits.

    Each edit: (file_path, old_string, new_string).
    """
    steps = []
    for i, (fp, old, new) in enumerate(edits):
        steps.append(Step(
            step_index=i, role="agent",
            tool_calls=[ToolCall(
                tool_call_id=f"tc{i}",
                tool_name="Edit",
                input={"file_path": fp, "old_string": old, "new_string": new},
            )],
            observations=[],
            token_usage=TokenUsage(),
        ))
    return TraceRecord(
        trace_id="t", session_id="s", agent=Agent(name="claude-code"),
        steps=steps,
    )


COMMIT_APP_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,2 +10,3 @@
 def handle():
-    return old
+    return new_value
+    # helper line
"""


class TestTierAssignment:
    def test_matching_edit_is_tool_emitted(self):
        trace = _trace(("src/app.py", "return old", "return new_value\n    # helper line"))
        links = correlate(trace, "abc123", COMMIT_APP_DIFF)
        assert len(links) == 1
        assert links[0].tier == "tool_emitted"
        assert links[0].revision == "abc123"
        assert links[0].vcs_type == "git"

    def test_no_matching_edit_is_orphan(self):
        trace = _trace(("src/other.py", "x", "y"))
        links = correlate(trace, "abc123", COMMIT_APP_DIFF)
        assert links[0].tier == "orphan"

    def test_absolute_edit_path_matches_repo_relative_hunk(self):
        trace = _trace((
            "/Users/x/proj/src/app.py",
            "return old",
            "return new_value\n    # helper line",
        ))
        links = correlate(trace, "abc123", COMMIT_APP_DIFF)
        assert links[0].tier == "tool_emitted"

    def test_no_edits_is_orphan(self):
        trace = TraceRecord(
            trace_id="t", session_id="s", agent=Agent(name="claude-code"),
            steps=[],
        )
        links = correlate(trace, "abc123", COMMIT_APP_DIFF)
        assert links[0].tier == "orphan"

    def test_repo_url_and_branch_forwarded(self):
        trace = _trace(("src/app.py", "return old", "return new_value"))
        links = correlate(
            trace, "abc123", COMMIT_APP_DIFF,
            repo_url="https://github.com/x/y", branch="main",
        )
        assert links[0].repo_url == "https://github.com/x/y"
        assert links[0].branch == "main"
