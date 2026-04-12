"""Plan 041 phase 7 tests: jj detection + dual-emit."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from opentraces.enrichment.git import jj_support


class TestJjSupport:
    def test_plain_git_repo_is_not_jj(self, tmp_path):
        import subprocess
        subprocess.check_call(
            ["git", "init", "-q", "-b", "main"], cwd=str(tmp_path),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        assert jj_support.is_jj_repo(tmp_path) is False
        assert jj_support.detect_vcs(tmp_path) == "git"

    def test_current_change_id_none_without_jj(self, tmp_path):
        assert jj_support.current_change_id(tmp_path) is None


class TestDualEmit:
    def test_hook_writes_agent_trace_line(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        target = tmp_path / "f.py"
        target.write_text("a\nHELLO_WORLD\n")
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")

        payload = {
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "session_id": "sess-1",
            "tool_name": "Edit",
            "tool_use_id": "toolu_x",
            "tool_input": {
                "file_path": str(target),
                "old_string": "OLD",
                "new_string": "HELLO_WORLD",
            },
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        main()

        dual_path = tmp_path / ".agent-trace" / "traces.jsonl"
        assert dual_path.exists()
        lines = [json.loads(l) for l in dual_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        line = lines[0]
        assert line["schema"] == "agent-trace/v0.1.0"
        assert line["session_id"] == "sess-1"
        assert line["tool_use_id"] == "toolu_x"
        assert line["tool"] == "Edit"
        assert line["content_hash"].startswith("murmur3:")
        assert line["start_line"] == 2

    def test_dual_emit_appends_not_overwrites(self, tmp_path, monkeypatch):
        from opentraces.capture.claude_code.hooks.on_tool_use import main

        target = tmp_path / "f.py"
        target.write_text("A\nB\n")
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("")

        for i, (old, new) in enumerate([("X1", "A"), ("X2", "B")]):
            payload = {
                "transcript_path": str(transcript),
                "cwd": str(tmp_path),
                "tool_name": "Edit",
                "tool_use_id": f"tc_{i}",
                "tool_input": {
                    "file_path": str(target),
                    "old_string": old,
                    "new_string": new,
                },
            }
            monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
            main()

        dual_path = tmp_path / ".agent-trace" / "traces.jsonl"
        lines = dual_path.read_text().splitlines()
        assert len(lines) == 2
