"""Tests for plan 041 phase 2 PostToolUse hook (R6).

Fires after Edit/Write, reads file from disk, writes an opentraces_hook
PostToolUse event with exact post-edit line range + murmur3 hash. Target
latency <50ms.
"""

from __future__ import annotations

import json
import time
from io import StringIO
from pathlib import Path


def _invoke(module_main, payload: dict, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    module_main()


def _read_events(transcript: Path) -> list[dict]:
    return [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]


class TestPostToolUseEdit:
    def test_single_match_stamps_high_confidence(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        target = tmp_path / "app.py"
        target.write_text("alpha\nbeta\nGAMMA_NEW\ndelta\n")
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")

        payload = {
            "transcript_path": str(transcript),
            "session_id": "sess",
            "tool_name": "Edit",
            "tool_use_id": "toolu_1",
            "tool_input": {
                "file_path": str(target),
                "old_string": "gamma",
                "new_string": "GAMMA_NEW",
            },
        }
        _invoke(main, payload, monkeypatch)

        events = _read_events(transcript)
        assert len(events) == 1
        ev = events[0]
        assert ev["type"] == "opentraces_hook"
        assert ev["event"] == "PostToolUse"
        d = ev["data"]
        assert d["tool"] == "Edit"
        assert d["tool_use_id"] == "toolu_1"
        assert d["file_path"] == str(target)
        assert d["start_line"] == 3
        assert d["end_line"] == 3
        assert d["content_hash"].startswith("murmur3:")
        assert d["confidence"] == "high"

    def test_multi_match_disambiguated_medium(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        # new_string "X" appears twice; old_string proximity picks
        # the first one (line 2).
        target = tmp_path / "f.py"
        target.write_text("a\nX\nb\nX\nc\n")
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")

        payload = {
            "transcript_path": str(transcript),
            "tool_name": "Edit",
            "tool_use_id": "toolu_2",
            "tool_input": {
                "file_path": str(target),
                "old_string": "a",  # proximity anchor: just above the first X.
                "new_string": "X",
            },
        }
        _invoke(main, payload, monkeypatch)
        ev = _read_events(transcript)[0]
        assert ev["data"]["confidence"] == "medium"
        assert ev["data"]["start_line"] == 2

    def test_multiline_new_string_spans_range(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        target = tmp_path / "f.py"
        target.write_text("a\nNEW1\nNEW2\nNEW3\nb\n")
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")

        payload = {
            "transcript_path": str(transcript),
            "tool_name": "Edit",
            "tool_use_id": "toolu_3",
            "tool_input": {
                "file_path": str(target),
                "old_string": "OLD",
                "new_string": "NEW1\nNEW2\nNEW3",
            },
        }
        _invoke(main, payload, monkeypatch)
        d = _read_events(transcript)[0]["data"]
        assert d["start_line"] == 2
        assert d["end_line"] == 4


class TestPostToolUseWrite:
    def test_write_spans_full_file(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        target = tmp_path / "new.py"
        content = "l1\nl2\nl3\nl4\n"
        target.write_text(content)
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")

        payload = {
            "transcript_path": str(transcript),
            "tool_name": "Write",
            "tool_use_id": "toolu_w1",
            "tool_input": {
                "file_path": str(target),
                "content": content,
            },
        }
        _invoke(main, payload, monkeypatch)
        d = _read_events(transcript)[0]["data"]
        assert d["tool"] == "Write"
        assert d["start_line"] == 1
        assert d["end_line"] == 4
        assert d["confidence"] == "high"


class TestHookRobustness:
    def test_missing_transcript_path_exits_clean(self, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        import pytest
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps({})))
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_missing_file_on_disk_does_not_crash(self, tmp_path, monkeypatch):
        import pytest
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")
        payload = {
            "transcript_path": str(transcript),
            "tool_name": "Edit",
            "tool_use_id": "toolu_x",
            "tool_input": {
                "file_path": str(tmp_path / "does-not-exist.py"),
                "old_string": "a",
                "new_string": "b",
            },
        }
        # Hook exits cleanly; test tolerates either a clean return or
        # an exit(0).
        try:
            _invoke(main, payload, monkeypatch)
        except SystemExit as exc:
            assert exc.code == 0
        events = _read_events(transcript)
        if events:
            assert events[0]["data"].get("confidence") in ("low", "medium", "high")

    def test_non_edit_write_tool_is_ignored(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")
        payload = {
            "transcript_path": str(transcript),
            "tool_name": "Bash",
            "tool_use_id": "toolu_b",
            "tool_input": {"command": "ls"},
        }
        try:
            _invoke(main, payload, monkeypatch)
        except SystemExit as exc:
            assert exc.code == 0
        # Non-file-editing tools produce no transcript event.
        assert _read_events(transcript) == []

    def test_hook_runs_under_50ms_budget(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        # 500-line file; edit replaces a single interior line. Hook
        # reads the file, locates the line, writes the event.
        target = tmp_path / "big.py"
        target.write_text("\n".join(f"line_{i}" for i in range(500)) + "\n")
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")

        payload = {
            "transcript_path": str(transcript),
            "tool_name": "Edit",
            "tool_use_id": "toolu_perf",
            "tool_input": {
                "file_path": str(target),
                "old_string": "line_99",
                "new_string": "line_99",
            },
        }
        start = time.perf_counter()
        _invoke(main, payload, monkeypatch)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 50, f"hook took {elapsed_ms:.1f}ms, plan 041 R6 budget is <50ms"
