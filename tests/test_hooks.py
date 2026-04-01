"""Tests for Claude Code hook scripts (on_stop.py, on_compact.py).

Each hook reads a JSON payload from stdin and appends a single
opentraces_hook line to the transcript JSONL.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke_hook(module_main, payload: dict, monkeypatch, stdin_override=None):
    """Call a hook's main() with a patched stdin payload."""
    raw = stdin_override if stdin_override is not None else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", StringIO(raw))
    module_main()


def _read_appended_lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# on_stop
# ---------------------------------------------------------------------------

class TestOnStopHook:
    def test_appends_valid_hook_line(self, tmp_path, monkeypatch):
        from opentraces.hooks.on_stop import main

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")
        payload = {
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "session_id": "abc123",
            "agent_type": "main",
            "permission_mode": "default",
        }
        _invoke_hook(main, payload, monkeypatch)

        lines = _read_appended_lines(transcript)
        assert len(lines) == 1
        line = lines[0]
        assert line["type"] == "opentraces_hook"
        assert line["event"] == "Stop"
        assert "timestamp" in line
        assert line["data"]["session_id"] == "abc123"
        assert line["data"]["agent_type"] == "main"
        assert "git" in line["data"]

    def test_missing_transcript_path_exits_clean(self, monkeypatch):
        from opentraces.hooks.on_stop import main

        monkeypatch.setattr("sys.stdin", StringIO(json.dumps({})))
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_git_failure_still_writes_line(self, tmp_path, monkeypatch):
        """When git is unavailable the hook still writes a line with git: {}."""
        from opentraces.hooks import on_stop

        # Patch _git_info to simulate failure
        monkeypatch.setattr(on_stop, "_git_info", lambda cwd: {})

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")
        payload = {"transcript_path": str(transcript), "cwd": str(tmp_path)}
        _invoke_hook(on_stop.main, payload, monkeypatch)

        lines = _read_appended_lines(transcript)
        assert len(lines) == 1
        assert lines[0]["data"]["git"] == {}

    def test_invalid_stdin_json_exits_clean(self, monkeypatch):
        from opentraces.hooks.on_stop import main

        monkeypatch.setattr("sys.stdin", StringIO("not-json{{{"))
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_appends_to_existing_content(self, tmp_path, monkeypatch):
        """Hook appends, not overwrites."""
        from opentraces.hooks.on_stop import main

        existing = {"type": "user", "sessionId": "s"}
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(json.dumps(existing) + "\n")

        payload = {"transcript_path": str(transcript), "cwd": str(tmp_path)}
        _invoke_hook(main, payload, monkeypatch)

        lines = _read_appended_lines(transcript)
        assert len(lines) == 2
        assert lines[0]["type"] == "user"
        assert lines[1]["type"] == "opentraces_hook"

    def test_written_line_is_valid_json(self, tmp_path, monkeypatch):
        from opentraces.hooks.on_stop import main

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")
        payload = {"transcript_path": str(transcript), "cwd": str(tmp_path)}
        _invoke_hook(main, payload, monkeypatch)

        raw = transcript.read_text().strip()
        # Should parse without error
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# on_compact
# ---------------------------------------------------------------------------

class TestOnCompactHook:
    def test_appends_valid_hook_line(self, tmp_path, monkeypatch):
        from opentraces.hooks.on_compact import main

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")
        payload = {
            "transcript_path": str(transcript),
            "session_id": "xyz789",
            "messages_removed": 42,
            "messages_kept": 10,
            "summary": "Context compacted",
        }
        _invoke_hook(main, payload, monkeypatch)

        lines = _read_appended_lines(transcript)
        assert len(lines) == 1
        line = lines[0]
        assert line["type"] == "opentraces_hook"
        assert line["event"] == "PostCompact"
        assert line["data"]["messages_removed"] == 42
        assert line["data"]["messages_kept"] == 10
        assert line["data"]["summary"] == "Context compacted"

    def test_missing_transcript_path_exits_clean(self, monkeypatch):
        from opentraces.hooks.on_compact import main

        monkeypatch.setattr("sys.stdin", StringIO(json.dumps({})))
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_invalid_stdin_exits_clean(self, monkeypatch):
        from opentraces.hooks.on_compact import main

        monkeypatch.setattr("sys.stdin", StringIO("bad json"))
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_partial_payload_writes_safely(self, tmp_path, monkeypatch):
        """Hook handles missing optional fields without error."""
        from opentraces.hooks.on_compact import main

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")
        # Only required field present
        payload = {"transcript_path": str(transcript)}
        _invoke_hook(main, payload, monkeypatch)

        lines = _read_appended_lines(transcript)
        assert len(lines) == 1
        assert lines[0]["data"]["messages_removed"] is None
        assert lines[0]["data"]["messages_kept"] is None
