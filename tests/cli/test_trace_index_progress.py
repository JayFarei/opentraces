"""CLI-side progress builder + ``trace index rebuild --progress`` contract (#88).

Two layers: a unit check that ``auto`` resolves to quiet on a non-TTY and to
``plain`` on a TTY (resolved at call time), and a subprocess e2e check that the
JSONL progress lands on stderr while stdout stays a single clean JSON object
carrying the additive ``telemetry.stages`` block.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from opentraces.cli import _progress as cli_progress
from opentraces.core.progress import NullProgress, ProgressReporter


def test_auto_mode_tty_vs_nontty(monkeypatch):
    # Non-TTY (CI / agent default) -> quiet NullProgress.
    monkeypatch.setattr(cli_progress, "_stderr_isatty", lambda: False)
    reporter = cli_progress.build_cli_progress("trace index rebuild", "auto")
    assert isinstance(reporter, NullProgress)

    # TTY -> a live plain reporter.
    monkeypatch.setattr(cli_progress, "_stderr_isatty", lambda: True)
    reporter = cli_progress.build_cli_progress("trace index rebuild", "auto")
    assert isinstance(reporter, ProgressReporter)

    # never -> always quiet; json -> always live.
    assert isinstance(
        cli_progress.build_cli_progress("c", "never"), NullProgress
    )
    assert isinstance(
        cli_progress.build_cli_progress("c", "json"), ProgressReporter
    )


def _run_rebuild(home, *progress_args):
    env = {
        "HOME": str(home),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "opentraces",
            "trace",
            "index",
            "rebuild",
            *progress_args,
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(home),
    )


def test_rebuild_progress_json(tmp_path):
    proc = _run_rebuild(tmp_path, "--progress", "json")
    assert proc.returncode == 0, f"stderr={proc.stderr}\nstdout={proc.stdout}"

    # stdout = one clean JSON object with the additive telemetry.stages block.
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert "stages" in payload["telemetry"]
    assert isinstance(payload["telemetry"]["stages"], list)
    assert payload["telemetry"]["stages"], "expected at least one stage record"

    # stderr carries >= 1 progress JSONL line of the frozen shape.
    progress_lines = []
    for raw in proc.stderr.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("event") == "progress":
            progress_lines.append(obj)
    assert progress_lines, f"no progress JSONL on stderr; stderr={proc.stderr!r}"
    assert all(p["command"] == "trace index rebuild" for p in progress_lines)


def test_rebuild_progress_never_is_quiet(tmp_path):
    proc = _run_rebuild(tmp_path, "--progress", "never")
    assert proc.returncode == 0, f"stderr={proc.stderr}\nstdout={proc.stdout}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # No progress events on stderr in 'never' mode.
    for raw in proc.stderr.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        assert not (isinstance(obj, dict) and obj.get("event") == "progress"), (
            f"unexpected progress line in never mode: {raw!r}"
        )
