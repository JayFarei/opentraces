"""Tests for plan 041 R5: diff-hunk-based line ranges.

When a session ended in a commit, the committed unified diff is the
authoritative source for what lines the agent's edits landed on. Diff
hunks should win over str.find fallback when both are available.
"""

from __future__ import annotations

from opentraces.enrichment.attribution import build_attribution
from opentraces_schema import Observation, Step, TokenUsage, ToolCall


def _step(idx: int, tool_calls=None, observations=None, model=None) -> Step:
    return Step(
        step_index=idx,
        role="agent",
        tool_calls=tool_calls or [],
        observations=observations or [],
        token_usage=TokenUsage(),
        model=model,
    )


def _edit(path, old, new, call_id="e"):
    return ToolCall(
        tool_call_id=call_id,
        tool_name="Edit",
        input={"file_path": path, "old_string": old, "new_string": new},
    )


PATCH_APP = """\
diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -42,3 +42,5 @@ class Handler:
     def handle(self):
-        return old_value
+        return new_value
+        # new comment line
+        # and another
"""


class TestDiffBasedRanges:
    def test_diff_hunk_overrides_fallback(self):
        # Edit with no prior Read would normally fall back to (1, N)
        # and be marked low-confidence. With a committed patch that
        # includes the edited hunk, the builder should prefer the
        # hunk's actual lines and stamp high confidence.
        tc = _edit(
            "src/app.py",
            "return old_value",
            "return new_value\n        # new comment line\n        # and another",
        )
        steps = [_step(0, tool_calls=[tc], model="anthropic/claude-sonnet-4")]
        attr = build_attribution(steps, outcome_patch=PATCH_APP)
        assert attr is not None

        # One file, one conversation, one range.
        files = {f.path: f for f in attr.files}
        assert "src/app.py" in files
        conv = files["src/app.py"].conversations[0]
        assert len(conv.ranges) == 1
        r = conv.ranges[0]

        # Hunk starts at line 42. The "+" lines are 3 consecutive.
        # Per the +42,5 header the hunk spans 5 new-file lines; the
        # hunk's + region is at the tail: lines 43..45 (start_line=43,
        # end_line=45) based on our hunk parsing + matching logic.
        assert r.start_line >= 42
        assert r.end_line >= r.start_line
        # Diff-sourced ranges are high-confidence and non-experimental.
        assert r.confidence == "high"
        assert attr.experimental is False

    def test_no_patch_preserves_fallback(self):
        # Without a patch, behavior matches Phase 1B (fallback → low).
        tc = _edit("src/app.py", "return old_value", "return new_value")
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(steps, outcome_patch=None)
        r = attr.files[0].conversations[0].ranges[0]
        assert r.confidence == "low"
        assert attr.experimental is True

    def test_patch_for_different_file_preserves_fallback(self):
        # Patch mentions src/app.py but the agent edited src/other.py.
        tc = _edit("src/other.py", "return old_value", "return new_value")
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(steps, outcome_patch=PATCH_APP)
        # Agent-edited file not in patch → fallback, not diff.
        other = [f for f in attr.files if f.path == "src/other.py"][0]
        assert other.conversations[0].ranges[0].confidence == "low"

    def test_patch_hunk_unrelated_content_does_not_claim_match(self):
        # The patch hunk adds lines that don't include the agent's
        # new_string at all → no diff match, keep fallback low.
        tc = _edit(
            "src/app.py",
            "completely unrelated old",
            "completely unrelated new content",
        )
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(steps, outcome_patch=PATCH_APP)
        r = attr.files[0].conversations[0].ranges[0]
        assert r.confidence == "low"
