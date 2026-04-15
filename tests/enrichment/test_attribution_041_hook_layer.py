"""Plan 041 phase 2: builder prefers PostToolUse hook data (R7).

Three-layer priority: hook > diff > str.find fallback. When a hook
event exists for a given tool_use_id / content_hash, its line range
and confidence override both diff-matching and str.find.
"""

from __future__ import annotations

import mmh3

from opentraces.enrichment.attribution import build_attribution
from opentraces_schema import Step, TokenUsage, ToolCall


def _h(s: str) -> str:
    return f"murmur3:{mmh3.hash128(s.encode('utf-8'), signed=False):032x}"


def _step(idx, tool_calls=None, model=None):
    return Step(
        step_index=idx, role="agent",
        tool_calls=tool_calls or [], observations=[],
        token_usage=TokenUsage(), model=model,
    )


def _edit(path, old, new, call_id="e"):
    return ToolCall(
        tool_call_id=call_id, tool_name="Edit",
        input={"file_path": path, "old_string": old, "new_string": new},
    )


class TestHookLayerPriority:
    def test_hook_overrides_fallback(self):
        # No Read/Write prior, no patch → would normally fall back to
        # (1, N) low-confidence. Hook says (101, 103) high.
        tc = _edit("src/app.py", "old", "new block\nline 2\nline 3", call_id="tc1")
        steps = [_step(0, tool_calls=[tc], model="anthropic/claude-sonnet-4")]
        hook = {
            "tc1": {
                "tool": "Edit",
                "file_path": "src/app.py",
                "start_line": 101,
                "end_line": 103,
                "content_hash": _h("new block\nline 2\nline 3"),
                "confidence": "high",
            }
        }
        attr = build_attribution(steps, hook_post_tool_use=hook)
        r = attr.files[0].conversations[0].ranges[0]
        assert r.start_line == 101
        assert r.end_line == 103
        assert r.confidence == "high"
        # Hook-sourced ranges are authoritative: not experimental.
        assert attr.experimental is False

    def test_hook_overrides_diff(self):
        # Patch would place the edit at lines 42-42, hook says 200-200.
        # Hook wins.
        tc = _edit("src/app.py", "old_value", "new_value", call_id="tc2")
        steps = [_step(0, tool_calls=[tc])]
        patch = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -42,1 +42,1 @@
-old_value
+new_value
"""
        hook = {
            "tc2": {
                "tool": "Edit",
                "file_path": "src/app.py",
                "start_line": 200,
                "end_line": 200,
                "content_hash": _h("new_value"),
                "confidence": "high",
            }
        }
        attr = build_attribution(steps, outcome_patch=patch, hook_post_tool_use=hook)
        r = attr.files[0].conversations[0].ranges[0]
        assert r.start_line == 200
        assert r.confidence == "high"

    def test_hook_medium_confidence_preserved(self):
        tc = _edit("src/app.py", "old", "new", call_id="tc3")
        steps = [_step(0, tool_calls=[tc])]
        hook = {
            "tc3": {
                "tool": "Edit",
                "file_path": "src/app.py",
                "start_line": 5,
                "end_line": 5,
                "content_hash": _h("new"),
                "confidence": "medium",  # ambiguous match disambiguated
            }
        }
        attr = build_attribution(steps, hook_post_tool_use=hook)
        r = attr.files[0].conversations[0].ranges[0]
        assert r.confidence == "medium"
        # Medium is not low, so not experimental.
        assert attr.experimental is False

    def test_no_hook_preserves_phase1_behavior(self):
        tc = _edit("src/app.py", "old", "new", call_id="tc4")
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(steps, hook_post_tool_use=None)
        r = attr.files[0].conversations[0].ranges[0]
        # No hook, no patch, no prior read → low fallback.
        assert r.confidence == "low"

    def test_hook_without_matching_tool_use_id_falls_through(self):
        # Hook map present but doesn't contain this tc's id → still
        # falls through to diff/str.find/fallback.
        tc = _edit("src/app.py", "old", "new", call_id="different_id")
        steps = [_step(0, tool_calls=[tc])]
        hook = {
            "toolu_unrelated": {
                "tool": "Edit",
                "file_path": "src/other.py",
                "start_line": 1,
                "end_line": 1,
                "content_hash": _h("x"),
                "confidence": "high",
            }
        }
        attr = build_attribution(steps, hook_post_tool_use=hook)
        r = attr.files[0].conversations[0].ranges[0]
        assert r.confidence == "low"
