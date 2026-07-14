"""Regression control for bounded Git source finalization (#307)."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

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
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=root, check=True)
    return root


def _terminate_recorded_processes(pid_path: Path) -> None:
    if not pid_path.is_file():
        return
    values = dict(
        line.split("=", 1)
        for line in pid_path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    for key in ("parent_pid", "child_pid"):
        raw_pid = values.get(key)
        if raw_pid is None:
            continue
        try:
            os.kill(int(raw_pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass


def _recorded_processes(pid_path: Path) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in (
            line.split("=", 1)
            for line in pid_path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
    }


def test_finish_kills_timed_out_git_probe_process_group(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A bounded Git result must kill the rev-parse parent and descendant."""
    project = _git_project(tmp_path / "project")
    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("git",),
            required_sources=("git",),
            result_dir=tmp_path / "result",
        )
    )

    real_git = shutil.which("git")
    assert real_git is not None
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    marker_path = tmp_path / "rev-parse-survived"
    pid_path = tmp_path / "rev-parse-pids"
    child_info_path = tmp_path / "rev-parse-child-info"
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "rev-parse" ] && [ -n "$OT_OPENTRACES_DIR" ]; then\n'
        f'  "{sys.executable}" -c \'import os, pathlib, time; '
        'pathlib.Path(os.environ["OT_GIT_SHIM_CHILD_INFO"]).write_text('
        'f"child_pid={os.getpid()}\\nchild_pgid={os.getpgid(0)}\\n"); '
        'time.sleep(3.5); '
        'pathlib.Path(os.environ["OT_GIT_SHIM_MARKER"]).write_text("survived"); '
        "time.sleep(30)\' &\n"
        "  child_pid=$!\n"
        '  parent_pgid=$(ps -o pgid= -p "$$" | tr -d " ")\n'
        '  printf "parent_pid=%s\\nparent_pgid=%s\\nchild_pid=%s\\n" '
        '"$$" "$parent_pgid" "$child_pid" > "$OT_GIT_SHIM_PIDS"\n'
        '  while [ ! -s "$OT_GIT_SHIM_CHILD_INFO" ]; do sleep 0.01; done\n'
        '  cat "$OT_GIT_SHIM_CHILD_INFO" >> "$OT_GIT_SHIM_PIDS"\n'
        '  wait "$child_pid"\n'
        "fi\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("OT_GIT_SHIM_MARKER", str(marker_path))
    monkeypatch.setenv("OT_GIT_SHIM_PIDS", str(pid_path))
    monkeypatch.setenv("OT_GIT_SHIM_CHILD_INFO", str(child_info_path))

    started = time.monotonic()
    try:
        result = capture.finish(deadline=started + 3.0)
        elapsed = time.monotonic() - started

        source = result.source("git")
        assert elapsed < 3.5
        assert source.status in {"partial", "unavailable", "timed_out"}
        assert source.completeness != "full"
        assert any(
            token in limitation.lower()
            for limitation in source.limitations
            for token in ("deadline", "timed out", "timeout")
        )

        assert pid_path.is_file(), "rev-parse timeout control was not exercised"
        processes = _recorded_processes(pid_path)
        assert processes.keys() == {
            "parent_pid",
            "parent_pgid",
            "child_pid",
            "child_pgid",
        }
        assert processes["parent_pid"] != processes["child_pid"]
        assert processes["parent_pgid"] == processes["child_pgid"]
        time.sleep(3.7)
        assert not marker_path.exists(), "timed-out rev-parse survived its finalizer"
    finally:
        _terminate_recorded_processes(pid_path)
