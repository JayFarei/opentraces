"""Stop hook spawns `_ingest-session` subprocess (Phase 2).

The Stop hook's primary job is still to append the opentraces_hook
metadata line (git state at turn-end). Phase 2 adds a fire-and-forget
``opentraces _ingest-session <transcript>`` call so a fresh turn's
    content lands in the inbox in ~seconds rather than ~minutes. The
    subprocess is detached (start_new_session) so Claude Code never waits
    on it; failures are swallowed so ingestion issues can't break the
    user's session.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


HOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "opentraces" / "capture" / "claude_code" / "hooks" / "on_stop.py"
)


def _run_hook_with_payload(payload: dict) -> subprocess.CompletedProcess:
    """Invoke the hook script with ``payload`` on stdin."""
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
    )


class TestStopHookSpawnsIngest:
    def test_hook_exits_zero_when_transcript_missing_ignore_prior(
        self, tmp_path
    ) -> None:
        """Pre-existing contract: no transcript_path → exit 0 cleanly."""
        result = _run_hook_with_payload({})
        assert result.returncode == 0

    def test_hook_writes_metadata_line_and_spawns_ingest(
        self, tmp_path, monkeypatch
    ) -> None:
        """After writing the Stop metadata line, the hook must fire
        ``opentraces _ingest-session <path>`` as a detached subprocess.

        We verify by intercepting ``subprocess.Popen`` at the hook's import
        scope — inspecting the command it would have run. The real Popen is
        never invoked.
        """
        transcript = tmp_path / "sess.jsonl"
        transcript.write_text("")  # start empty

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "on_stop_under_test", HOOK_PATH
        )
        mod = importlib.util.module_from_spec(spec)

        popen_calls: list[list[str]] = []

        class _FakePopen:
            def __init__(self, argv, **kwargs):
                popen_calls.append(list(argv))

        monkeypatch.setattr("subprocess.Popen", _FakePopen)

        spec.loader.exec_module(mod)

        payload = {
            "transcript_path": str(transcript),
            "session_id": "abc",
            "cwd": str(tmp_path),
        }
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

        try:
            mod.main()
        except SystemExit as e:
            assert (e.code or 0) == 0

        # Metadata line appended to transcript.
        written = transcript.read_text().strip().splitlines()
        assert len(written) == 1
        line = json.loads(written[0])
        assert line["event"] == "Stop"

        # Subprocess spawn was attempted. Filter out the inner git calls
        # _git_info makes (it uses subprocess.check_output, which goes
        # through Popen too).
        ingest_calls = [
            c for c in popen_calls
            if "-m" in c and "opentraces" in c and "_ingest-session" in c
        ]
        assert len(ingest_calls) == 1, (
            f"expected one Popen for _ingest-session, got {popen_calls}"
        )
        argv = ingest_calls[0]
        assert argv[:4] == [sys.executable, "-m", "opentraces", "_ingest-session"]
        assert str(transcript) in argv

    def test_hook_tolerates_ingest_spawn_failure(
        self, tmp_path, monkeypatch
    ) -> None:
        """When ingestion cannot spawn, the hook must still write the
        metadata line and exit 0 without raising."""
        transcript = tmp_path / "sess.jsonl"
        transcript.write_text("")

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "on_stop_under_test2", HOOK_PATH
        )
        mod = importlib.util.module_from_spec(spec)

        real_popen = subprocess.Popen

        class _TrackedPopen(real_popen):
            def __init__(self, argv, **kwargs):
                normalized = list(argv) if not isinstance(argv, str) else [argv]
                if "-m" in normalized and "opentraces" in normalized:
                    raise OSError("simulated ingest spawn failure")
                super().__init__(argv, **kwargs)

        monkeypatch.setattr("subprocess.Popen", _TrackedPopen)

        spec.loader.exec_module(mod)

        payload = {"transcript_path": str(transcript),
                   "session_id": "abc", "cwd": str(tmp_path)}
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

        try:
            mod.main()
        except SystemExit as e:
            assert (e.code or 0) == 0

        written = transcript.read_text().strip().splitlines()
        assert len(written) == 1, "metadata line must still be written"
