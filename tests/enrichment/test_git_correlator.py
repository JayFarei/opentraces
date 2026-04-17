"""Plan 041 phase 3 correlator tests.

Pure function: given a trace + a commit's unified diff, return a list
of GitLinks with evidence-graded tiers. Phase 3 covers tool_emitted
and orphan; tool_emitted_with_divergence and overlapping land in
phase 4.
"""

from __future__ import annotations

from opentraces.enrichment.git.correlator import correlate
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

    def test_no_matching_edit_is_overlapping(self):
        # Phase 4 taxonomy: agent touched the repo (edits exist) and
        # commit has hunks (on src/app.py), but no file overlap → the
        # honest tier is overlapping, not orphan. Orphan is reserved
        # for "no edits at all" or "no commit hunks at all."
        trace = _trace(("src/other.py", "x", "y"))
        links = correlate(trace, "abc123", COMMIT_APP_DIFF)
        assert links[0].tier == "overlapping"

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


class TestMatchThresholds:
    """Regression tests for the specificity thresholds that replaced
    the old "any single novel line substring-match" rule. The old rule
    produced phantom matches on common lines (``return None``,
    ``import json``) that happened to appear in unrelated commit diffs.
    """

    COMMON_LINE_DIFF = """\
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,1 +1,2 @@
 x = 1
+    return False
+    import json
"""

    def test_short_single_line_match_is_not_tool_emitted(self):
        # Agent wrote only ``return False`` (12 chars normalized). It
        # substring-matches the diff, but a 12-char common line is not
        # specific enough — the old rule would false-positive here.
        trace = _trace(("src/a.py", "old", "return False"))
        links = correlate(trace, "abc", self.COMMON_LINE_DIFF)
        assert links[0].tier == "tool_emitted_with_divergence", (
            "12-char common line must not single-handedly trigger tool_emitted"
        )

    def test_strong_single_line_match_is_tool_emitted(self):
        # One meaningful line (>= 30 chars normalized) is specific
        # enough that coincidental matches are unlikely.
        strong = "def handle_request(self, request, user_id):"  # 43 chars
        diff = f"""\
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,1 +1,2 @@
-old
+{strong}
+    return request.json
"""
        trace = _trace(("src/a.py", "old", f"{strong}\n    return request.json"))
        links = correlate(trace, "abc", diff)
        assert links[0].tier == "tool_emitted"

    def test_multi_short_matches_in_same_hunk_are_tool_emitted(self):
        # Two distinct novel lines, each >= 8 chars, both in the same
        # hunk — this clusters rather than scatters, so it clears the
        # false-positive bar.
        trace = _trace(("src/a.py", "old", "return False\n    import json"))
        links = correlate(trace, "abc", self.COMMON_LINE_DIFF)
        assert links[0].tier == "tool_emitted"

    def test_scattered_matches_across_hunks_do_not_combine(self):
        # The phantom-match shape: one common line matches hunk A,
        # another matches hunk B. Historically this was tool_emitted;
        # tightened to NOT cross hunks so the pattern stops scoring.
        diff = """\
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,1 +1,2 @@
 # region A
+    return False
@@ -20,1 +21,2 @@
 # region B
+    import json
"""
        trace = _trace(("src/a.py", "old", "return False\n    import json"))
        links = correlate(trace, "abc", diff)
        assert links[0].tier == "tool_emitted_with_divergence"

    def test_tiny_line_below_multi_threshold_doesnt_count(self):
        # Lines shorter than MULTI_MIN_LEN (e.g. ``pass``, ``}``)
        # don't count toward the multi rule — a session that happens
        # to include ``pass`` and ``}`` isn't claiming credit.
        diff = """\
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,1 +1,3 @@
-old
+pass
+}
+something_else
"""
        trace = _trace(("src/a.py", "old", "pass\n}"))
        links = correlate(trace, "abc", diff)
        assert links[0].tier == "tool_emitted_with_divergence"
