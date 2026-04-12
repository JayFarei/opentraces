"""Tests for the on_stop → `opentraces _enrich-transcript` background trigger."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from opentraces.installers.claude_code_hooks import on_stop


def _make_executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _minimal_transcript_lines() -> list[dict]:
    """Quality-gate-passing session with a tool_use round-trip."""
    return [
        {
            "type": "user",
            "sessionId": "sess-trigger",
            "timestamp": "2026-04-12T10:00:00Z",
            "version": "1.0.83",
            "message": {"role": "user", "content": "Wire on_stop to intent enrichment."},
        },
        {
            "type": "assistant",
            "sessionId": "sess-trigger",
            "timestamp": "2026-04-12T10:00:01Z",
            "message": {
                "role": "assistant",
                "model": "anthropic/claude-sonnet-4-5",
                "content": [
                    {"type": "tool_use", "id": "tc1", "name": "Read", "input": {"file_path": "on_stop.py"}},
                ],
                "usage": {
                    "input_tokens": 50, "output_tokens": 20,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                },
            },
        },
        {
            "type": "user",
            "sessionId": "sess-trigger",
            "timestamp": "2026-04-12T10:00:02Z",
            "toolUseResult": {"stdout": "def main(): ..."},
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": "def main(): ..."}],
            },
        },
        {
            "type": "assistant",
            "sessionId": "sess-trigger",
            "timestamp": "2026-04-12T10:00:03Z",
            "message": {
                "role": "assistant",
                "model": "anthropic/claude-sonnet-4-5",
                "content": [{"type": "text", "text": "Added the Popen call."}],
                "usage": {
                    "input_tokens": 80, "output_tokens": 30,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                },
            },
        },
    ]


def _write_transcript(path: Path) -> None:
    with path.open("w") as f:
        for line in _minimal_transcript_lines():
            f.write(json.dumps(line) + "\n")


class TestOnStopSpawnsBackground:
    """Unit: on_stop triggers Popen with detached options and does not wait."""

    def test_spawn_uses_start_new_session(self, tmp_path, monkeypatch):
        transcript = tmp_path / "s.jsonl"
        transcript.write_text("{}\n")

        calls: list[dict] = []

        class FakePopen:
            def __init__(self, args, **kwargs):
                calls.append({"args": args, "kwargs": kwargs})

        monkeypatch.setattr("shutil.which", lambda _: "/fake/opentraces")
        with patch.object(on_stop.subprocess, "Popen", FakePopen):
            on_stop._spawn_intent_enrichment(str(transcript))

        assert len(calls) == 1
        assert calls[0]["args"] == ["/fake/opentraces", "_enrich-transcript", str(transcript)]
        assert calls[0]["kwargs"]["start_new_session"] is True
        assert calls[0]["kwargs"]["stdin"] == subprocess.DEVNULL
        assert calls[0]["kwargs"]["stdout"] == subprocess.DEVNULL
        assert calls[0]["kwargs"]["stderr"] == subprocess.DEVNULL

    def test_spawn_missing_binary_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        # Must not raise.
        on_stop._spawn_intent_enrichment(str(tmp_path / "x.jsonl"))

    def test_main_calls_spawn_after_writing_sentinel(self, tmp_path, monkeypatch):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text("")  # empty file to append to
        payload = {"transcript_path": str(transcript), "session_id": "s", "cwd": ""}

        spawned: list[str] = []
        monkeypatch.setattr(on_stop, "_spawn_intent_enrichment", lambda p: spawned.append(p))
        monkeypatch.setattr(sys, "stdin", _StdinShim(json.dumps(payload)))

        on_stop.main()

        # Sentinel line appended
        contents = transcript.read_text().strip().splitlines()
        assert len(contents) == 1
        sentinel = json.loads(contents[0])
        assert sentinel["type"] == "opentraces_hook"
        # Spawn invoked with the transcript path
        assert spawned == [str(transcript)]


class _StdinShim:
    def __init__(self, text: str):
        self._text = text

    def read(self):  # pragma: no cover - unused path
        return self._text


class TestEnrichTranscriptCli:
    """Integration: run `opentraces _enrich-transcript` as a subprocess
    against a real Claude Code transcript + a stub `claude` binary on PATH."""

    def test_end_to_end_produces_intent_cache_file(self, tmp_path, monkeypatch):
        # Isolate HOME so we don't touch ~/.opentraces.
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        transcript = tmp_path / "sess-trigger.jsonl"
        _write_transcript(transcript)

        # Stub `claude` CLI that returns a canned JSON response.
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        claude_stub = stub_dir / "claude"
        _make_executable(
            claude_stub,
            '#!/usr/bin/env python3\n'
            'import sys\n'
            'sys.stdout.write(\'{"title": "trigger wired", '
            '"summary": "on_stop now spawns intent enrichment in the background."}\')\n',
        )

        # Prepend stub dir to PATH so claude_cli_client finds it.
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = f"{stub_dir}{os.pathsep}" + env.get("PATH", "")

        result = subprocess.run(
            [sys.executable, "-m", "opentraces", "_enrich-transcript", str(transcript)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert result.returncode == 0, result.stderr

        cache_file = home / ".opentraces" / "intent-cache" / "sess-trigger.json"
        assert cache_file.exists(), f"expected cache file at {cache_file}"
        data = json.loads(cache_file.read_text())
        assert data["intent"]["title"] == "trigger wired"
        assert data["intent"]["source"] == "llm_hook"

    def test_missing_transcript_is_silent_noop(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)
        result = subprocess.run(
            [sys.executable, "-m", "opentraces", "_enrich-transcript",
             str(tmp_path / "nope.jsonl")],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert result.returncode == 0
        # No cache file created.
        cache_dir = home / ".opentraces" / "intent-cache"
        assert not cache_dir.exists() or not any(cache_dir.iterdir())
