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
import types

import pytest
from click.testing import CliRunner

from opentraces.cli import _progress as cli_progress
from opentraces.cli import main
from opentraces.core.progress import NullProgress, ProgressReporter


def _progress_events(stderr: str) -> list[dict]:
    """Parse the {"event":"progress",...} JSONL lines out of a stderr blob."""

    events: list[dict] = []
    for raw in stderr.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("event") == "progress":
            events.append(obj)
    return events


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


def test_clirunner_progress_routes_to_stderr_only():
    """Direct exercise of the click.echo(err=True) guarantee.

    CliRunner separates stdout/stderr (Click 8.2+), so this asserts the JSONL
    progress lands on result.stderr while result.stdout stays one clean JSON
    object — complementing the subprocess test, and the exact thing that breaks
    if the sink ever binds sys.stderr at import time (codex finding #1).
    """

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["trace", "index", "rebuild", "--progress", "json", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert isinstance(payload["telemetry"]["stages"], list)

    events = _progress_events(result.stderr)
    assert events, f"no progress JSONL on stderr; stderr={result.stderr!r}"
    assert all(e["command"] == "trace index rebuild" for e in events)


def test_legacy_rebuild_emits_progress_stage(monkeypatch):
    """`trace index rebuild --legacy --progress json` must beat through the
    long blocking legacy rebuild_index() — the command's slowest mode.

    rebuild_index is monkeypatched to a fast no-op so the test never runs a
    real multi-GB legacy rebuild; we assert a 'rebuilding_legacy_index' progress
    event reaches stderr and stdout stays a clean JSON object (codex CRITICAL).
    """

    import opentraces.core.trace_index as ti

    def _fake_rebuild_index(path):
        return types.SimpleNamespace(trace_count=0, unit_count=0, map_node_count=0)

    monkeypatch.setattr(ti, "rebuild_index", _fake_rebuild_index)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["trace", "index", "rebuild", "--legacy", "--progress", "json", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["legacy_index"]["forced"] is True

    events = _progress_events(result.stderr)
    stages = {e["stage"] for e in events}
    assert "rebuilding_legacy_index" in stages, (
        f"legacy stage absent from stderr progress; stages={stages}"
    )
    # The legacy stage is also recorded in the additive telemetry block.
    tele_stages = {s["stage"] for s in payload["telemetry"]["stages"]}
    assert "rebuilding_legacy_index" in tele_stages
