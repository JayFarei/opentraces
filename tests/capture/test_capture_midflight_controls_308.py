"""Real mid-flight deadline controls for portable capture finalizers (#308)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from opentraces.capture import Capture, CapturePlan


def _git_project(root: Path) -> Path:
    root.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "capture-test@opentraces.local"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "capture-test"],
        cwd=root,
        check=True,
    )
    (root / "README.md").write_text("capture fixture\n", encoding="utf-8")
    (root / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": "capture-midflight-308",
                "review_policy": "review",
                "push_policy": "manual",
                "remotes": {"origin": {"url": "test/test", "visibility": "private"}},
                "active_remote": "origin",
                "agents": ["claude-code"],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=root, check=True)
    return root


def _write_session(project: Path, session_id: str) -> Path:
    source = project / f"{session_id}.jsonl"
    rows = [
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": "2026-07-14T10:00:00Z",
            "message": {"role": "user", "content": "Read README.md."},
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "timestamp": "2026-07-14T10:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-read-1",
                        "name": "Read",
                        "input": {"file_path": str(project / "README.md")},
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        },
        {
            "type": "user",
            "sessionId": session_id,
            "timestamp": "2026-07-14T10:00:02Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-read-1",
                        "content": "capture fixture",
                    }
                ],
            },
        },
    ]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return source


def _install_blocking_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    match: str,
) -> Path:
    real_git = shutil.which("git")
    assert real_git is not None
    marker = tmp_path / "blocking-git-started.json"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys, time\n"
        f"real_git = {real_git!r}\n"
        "args = sys.argv[1:]\n"
        "if os.environ['OT_MIDFLIGHT_MATCH_308'] in ' '.join(args):\n"
        "    marker = os.environ['OT_MIDFLIGHT_MARKER_308']\n"
        "    with open(marker, 'w', encoding='utf-8') as fh:\n"
        "        json.dump({'pid': os.getpid(), 'args': args}, fh)\n"
        "    while True:\n"
        "        time.sleep(30)\n"
        "os.execv(real_git, [real_git, *args])\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("OT_MIDFLIGHT_MATCH_308", match)
    monkeypatch.setenv("OT_MIDFLIGHT_MARKER_308", str(marker))
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    return marker


def _assert_midflight_timeout(
    capture,
    *,
    source: str,
    marker: Path,
    expected_argv: str,
) -> None:
    started = time.monotonic()
    result = capture.finish(deadline=started + 2.0)
    elapsed = time.monotonic() - started
    blocked = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None
    try:
        assert elapsed < 3.0
        assert blocked is not None, "the real blocking operation was never entered"
        assert expected_argv in " ".join(blocked["args"])

        observed = result.source(source)
        assert observed.status in {"partial", "unavailable", "timed_out"}
        assert observed.completeness != "full"

        pid = int(blocked["pid"])
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and _pid_exists(pid):
            time.sleep(0.02)
        assert not _pid_exists(pid), f"{source} finalizer left worker pid {pid} alive"
        assert any(source in limitation.lower() for limitation in observed.limitations)
    finally:
        if blocked is not None:
            pid = int(blocked["pid"])
            if _pid_exists(pid):
                os.kill(pid, signal.SIGKILL)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_watcher_midflight_git_scan_is_killed_at_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _git_project(tmp_path / "project")
    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("watcher",),
            required_sources=("watcher",),
            result_dir=tmp_path / "result",
        )
    )
    marker = _install_blocking_git(tmp_path, monkeypatch, match="ls-files")

    _assert_midflight_timeout(
        capture,
        source="watcher",
        marker=marker,
        expected_argv="ls-files",
    )


def test_git_midflight_maturation_probe_is_killed_at_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _git_project(tmp_path / "project")
    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("git",),
            required_sources=("git",),
            result_dir=tmp_path / "result",
        )
    )
    marker = _install_blocking_git(tmp_path, monkeypatch, match="rev-list")

    _assert_midflight_timeout(
        capture,
        source="git",
        marker=marker,
        expected_argv="rev-list",
    )


def test_bucket_midflight_event_mirror_probe_is_killed_at_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _git_project(tmp_path / "project")
    result_dir = tmp_path / "result"
    source = _write_session(project, "bucket-midflight")
    initial = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("session_jsonl", "bucket"),
            required_sources=("session_jsonl", "bucket"),
            session_id="bucket-midflight",
            session_path=source,
            result_dir=result_dir,
        )
    ).finish(deadline=time.monotonic() + 10.0)
    trace_id = initial.trace_refs[0]
    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=("bucket",),
            required_sources=("bucket",),
            trace_id=trace_id,
            result_dir=result_dir,
        )
    )
    marker = _install_blocking_git(
        tmp_path,
        monkeypatch,
        match="rev-parse --verify refs/opentraces/local/events/v1",
    )

    _assert_midflight_timeout(
        capture,
        source="bucket",
        marker=marker,
        expected_argv="refs/opentraces/local/events/v1",
    )
