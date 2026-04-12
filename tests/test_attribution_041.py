"""Tests for plan 041 phase 1B attribution fidelity (R1, R2, R4, R19)."""

from __future__ import annotations

from opentraces.enrichment.attribution import _content_hash, build_attribution
from opentraces_schema import Observation, Step, TokenUsage, ToolCall


def _step(
    idx: int,
    *,
    tool_calls: list[ToolCall] | None = None,
    observations: list[Observation] | None = None,
    model: str | None = None,
) -> Step:
    return Step(
        step_index=idx,
        role="agent",
        tool_calls=tool_calls or [],
        observations=observations or [],
        token_usage=TokenUsage(),
        model=model,
    )


def _edit_tc(file_path: str, old: str, new: str, call_id: str = "tc1") -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        tool_name="Edit",
        input={"file_path": file_path, "old_string": old, "new_string": new},
    )


def _write_tc(file_path: str, content: str, call_id: str = "w1") -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        tool_name="Write",
        input={"file_path": file_path, "content": content},
    )


class TestMurmur3Hash:
    def test_hash_uses_murmur3_prefix(self):
        h = _content_hash("hello world")
        assert h.startswith("murmur3:"), f"expected murmur3: prefix, got {h!r}"

    def test_hash_is_stable(self):
        assert _content_hash("x") == _content_hash("x")
        assert _content_hash("x") != _content_hash("y")

    def test_hash_hex_body(self):
        h = _content_hash("hello")
        body = h.split(":", 1)[1]
        # mmh3.hash128 yields 32 hex chars (128 bits).
        assert len(body) == 32
        assert all(c in "0123456789abcdef" for c in body)

    def test_built_ranges_carry_murmur3_hash(self):
        steps = [_step(0, tool_calls=[_edit_tc("/a.py", "old", "new")], model="anthropic/claude-sonnet-4")]
        attr = build_attribution(steps)
        assert attr is not None
        r = attr.files[0].conversations[0].ranges[0]
        assert r.content_hash.startswith("murmur3:")


class TestModelIdPopulated:
    def test_model_id_from_step(self):
        steps = [_step(0, tool_calls=[_edit_tc("/a.py", "o", "n")], model="anthropic/claude-sonnet-4-20250514")]
        attr = build_attribution(steps)
        conv = attr.files[0].conversations[0]
        assert conv.contributor["type"] == "ai"
        assert conv.contributor["model_id"] == "anthropic/claude-sonnet-4-20250514"

    def test_model_id_omitted_when_step_has_none(self):
        steps = [_step(0, tool_calls=[_edit_tc("/a.py", "o", "n")], model=None)]
        attr = build_attribution(steps)
        conv = attr.files[0].conversations[0]
        assert conv.contributor["type"] == "ai"
        assert "model_id" not in conv.contributor


class TestConditionalExperimental:
    def test_clean_single_edit_not_experimental(self):
        # Read establishes file content (no edit entry), then one Edit —
        # one range with known lines → high confidence → experimental=False.
        read = ToolCall(
            tool_call_id="r1",
            tool_name="Read",
            input={"file_path": "/a.py"},
        )
        read_obs = Observation(source_call_id="r1", content="l1\nl2\nl3\nl4\nl5\n")
        edit = _edit_tc("/a.py", "l1", "L1", call_id="e1")
        steps = [
            _step(0, tool_calls=[read], observations=[read_obs]),
            _step(1, tool_calls=[edit]),
        ]
        attr = build_attribution(steps)
        assert attr.experimental is False
        assert attr.files[0].conversations[0].ranges[0].confidence == "high"

    def test_overlapping_edits_are_experimental(self):
        write = _write_tc("/a.py", "line1\nline2\n", call_id="w")
        e1 = _edit_tc("/a.py", "line1", "LINE1", call_id="e1")
        e2 = _edit_tc("/a.py", "LINE1", "LINE1v2", call_id="e2")
        steps = [_step(0, tool_calls=[write]), _step(1, tool_calls=[e1]), _step(2, tool_calls=[e2])]
        attr = build_attribution(steps)
        assert attr.experimental is True

    def test_fallback_line_resolution_is_experimental(self):
        # Edit with no prior Read/Write → line range falls back to (1, ...).
        # Fallback use → experimental=True.
        steps = [_step(0, tool_calls=[_edit_tc("/ghost.py", "old", "new")])]
        attr = build_attribution(steps)
        assert attr.experimental is True


class TestTraceIdInUrl:
    def test_url_includes_trace_id(self):
        steps = [_step(3, tool_calls=[_edit_tc("/a.py", "o", "n")])]
        attr = build_attribution(steps, trace_id="trace-xyz")
        url = attr.files[0].conversations[0].url
        assert url == "opentraces://trace-xyz/step_3"

    def test_url_trace_placeholder_when_no_trace_id(self):
        # Backward-compat: caller may omit trace_id.
        steps = [_step(2, tool_calls=[_edit_tc("/a.py", "o", "n")])]
        attr = build_attribution(steps)
        url = attr.files[0].conversations[0].url
        assert url.startswith("opentraces://")
        assert url.endswith("/step_2")
