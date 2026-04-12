"""Phase 4 correlator tests: divergence and overlapping tiers."""

from __future__ import annotations

from opentraces.enrichment.git.correlator import correlate
from opentraces_schema import Agent, Step, TokenUsage, ToolCall, TraceRecord


def _trace(*edits: tuple[str, str, str]) -> TraceRecord:
    steps = []
    for i, (fp, old, new) in enumerate(edits):
        steps.append(Step(
            step_index=i, role="agent",
            tool_calls=[ToolCall(
                tool_call_id=f"tc{i}", tool_name="Edit",
                input={"file_path": fp, "old_string": old, "new_string": new},
            )],
            observations=[], token_usage=TokenUsage(),
        ))
    return TraceRecord(
        trace_id="t", session_id="s", agent=Agent(name="claude-code"),
        steps=steps,
    )


class TestDivergenceTier:
    def test_file_overlap_no_hash_match_is_divergence(self):
        # Agent edited src/app.py writing FOO_BAR. The committed bytes
        # are BAZ_QUX — path overlap but no content match (formatter
        # or human rewrote the agent's output). Result: divergence.
        trace = _trace(("src/app.py", "old", "FOO_BAR"))
        diff = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-old
+BAZ_QUX
"""
        links = correlate(trace, "abc", diff)
        assert len(links) == 1
        assert links[0].tier == "tool_emitted_with_divergence"

    def test_match_wins_over_divergence(self):
        # Edit 1 matches the commit's content; Edit 2 is to a different
        # file whose commit content diverges. Tool_emitted wins.
        trace = _trace(
            ("src/app.py", "o", "GOOD_LINE"),
            ("src/other.py", "o", "WHAT_AGENT_WROTE"),
        )
        diff = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-o
+GOOD_LINE
diff --git a/src/other.py b/src/other.py
--- a/src/other.py
+++ b/src/other.py
@@ -1,1 +1,1 @@
-o
+FORMATTER_OUTPUT
"""
        links = correlate(trace, "abc", diff)
        assert links[0].tier == "tool_emitted"


class TestOverlappingTier:
    def test_trace_with_edits_but_no_file_overlap_is_overlapping(self):
        # Agent edited src/a.py but commit touched src/b.py — the time
        # window said these could be related, but nothing connects
        # them. Overlapping is the honest tier (not orphan, because
        # the trace did touch the repo; not divergence, because no
        # file overlap).
        trace = _trace(("src/a.py", "o", "n"))
        diff = """\
diff --git a/src/b.py b/src/b.py
--- a/src/b.py
+++ b/src/b.py
@@ -1,1 +1,1 @@
-o
+n
"""
        links = correlate(trace, "abc", diff)
        assert links[0].tier == "overlapping"

    def test_empty_trace_is_orphan(self):
        trace = TraceRecord(
            trace_id="t", session_id="s", agent=Agent(name="claude-code"), steps=[],
        )
        diff = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,1 @@
-o
+n
"""
        links = correlate(trace, "abc", diff)
        assert links[0].tier == "orphan"
