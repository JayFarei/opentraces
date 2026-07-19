"""Regression control for bounded Git source finalization (#307)."""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from opentraces.capture import Capture, CapturePlan
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails._git_subprocess import run_git_bytes_until, run_git_until
from opentraces.core.trails.models import TrailEventDraft, sha256_text


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


def _pid_alive(pid: int) -> bool:
    """True iff a process with ``pid`` still exists (a zombie counts as alive
    until reaped). ``signal 0`` performs the existence/permission check without
    delivering a signal."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists but is owned by another uid — still a survivor.
        return True
    return True


def _recorded_processes(pid_path: Path) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in (
            line.split("=", 1)
            for line in pid_path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
    }


def _seed_event_log(project: Path) -> None:
    authored = "capture fixture\n"
    append_event_batch(
        project,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="trace-test",
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": "tracepatch-sha256:deadline-control",
                    "file_path": "README.md",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(authored.rstrip("\n")),
                    "limitations": [],
                },
            )
        ],
        writer="test-307-deadline",
    )


def _git_capture(project: Path, result_dir: Path) -> Capture:
    return Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="persistent",
            requested_sources=("git",),
            required_sources=("git",),
            result_dir=result_dir,
        )
    )


def _install_git_hang(
    *,
    tmp_path: Path,
    monkeypatch,
    condition: str,
) -> Path:
    real_git = shutil.which("git")
    assert real_git is not None
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    pid_path = tmp_path / "hung-git-pid"
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        f"if {condition}; then\n"
        '  printf "%s\\n" "$$" > "$OT_GIT_HANG_PID"\n'
        "  exec sleep 3.7\n"
        "fi\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("OT_GIT_HANG_PID", str(pid_path))
    return pid_path


def _finish_with_deadline(capture: Capture) -> tuple[object, float]:
    started = time.monotonic()
    result = capture.finish(deadline=started + 3.0)
    return result.source("git"), time.monotonic() - started


def _terminate_pid_file(pid_path: Path) -> None:
    if not pid_path.is_file():
        return
    try:
        os.kill(int(pid_path.read_text(encoding="utf-8")), signal.SIGTERM)
    except (ProcessLookupError, ValueError):
        pass


def test_deadline_git_runners_preserve_stdin(tmp_path: Path) -> None:
    """Deadline wrappers must hash the supplied blob, never an empty stdin."""
    project = _git_project(tmp_path / "project")
    payload = "deadline runner stdin\n"
    expected = hashlib.sha1(
        f"blob {len(payload.encode())}\0{payload}".encode()
    ).hexdigest()
    deadline = time.monotonic() + 2.0

    text_result = run_git_until(
        ["hash-object", "--stdin"],
        cwd=project,
        deadline=deadline,
        input=payload,
    )
    binary_result = run_git_bytes_until(
        ["hash-object", "--stdin"],
        cwd=project,
        deadline=deadline,
        input=payload.encode(),
    )

    assert text_result.stdout.strip() == expected
    assert binary_result.stdout.strip().decode() == expected


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


def test_finish_bounds_transitive_scoped_event_git_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A scoped event-log Git hang must become a named partial AND leave no
    survivor: the blocked ``cat-file --batch`` process, and a descendant it
    forks into the same group, must both be gone once ``finish`` returns.

    #316: the pre-strengthening control proved only the named partial result;
    it did not assert its own recorded PID was actually reaped. This records
    the scoped shim's PID + process group and a delayed-descendant marker, then
    asserts the exact recorded PID is gone (primary oracle, no sleep) and that
    the descendant could not survive to write its marker — a child-only kill
    escape would flip either assertion RED.
    """
    project = _git_project(tmp_path / "project")
    _seed_event_log(project)
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
    pid_path = tmp_path / "cat-file-pids"
    child_info_path = tmp_path / "cat-file-child-info"
    marker_path = tmp_path / "cat-file-descendant-survived"
    shim = shim_dir / "git"
    # The shim IS the process the deadline runner launches for
    # ``cat-file --batch``. It forks a delayed descendant into its own process
    # group (which SIGKILL of the group must also reap) that would write a
    # survival marker if it escaped the kill, records parent+child PID/PGID,
    # then blocks on the descendant so the real cat-file read genuinely hangs.
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "cat-file" ] && [ "$2" = "--batch" ] '
        '&& [ -n "$OT_OPENTRACES_DIR" ]; then\n'
        f'  "{sys.executable}" -c \'import os, pathlib, time; '
        'pathlib.Path(os.environ["OT_CAT_FILE_CHILD_INFO"]).write_text('
        'f"child_pid={os.getpid()}\\nchild_pgid={os.getpgid(0)}\\n"); '
        'time.sleep(3.5); '
        'pathlib.Path(os.environ["OT_CAT_FILE_MARKER"]).write_text("survived"); '
        "time.sleep(30)' &\n"
        "  child_pid=$!\n"
        '  parent_pgid=$(ps -o pgid= -p "$$" | tr -d " ")\n'
        '  printf "parent_pid=%s\\nparent_pgid=%s\\nchild_pid=%s\\n" '
        '"$$" "$parent_pgid" "$child_pid" > "$OT_GIT_SHIM_CAT_FILE_PID"\n'
        '  while [ ! -s "$OT_CAT_FILE_CHILD_INFO" ]; do sleep 0.01; done\n'
        '  cat "$OT_CAT_FILE_CHILD_INFO" >> "$OT_GIT_SHIM_CAT_FILE_PID"\n'
        '  wait "$child_pid"\n'
        "fi\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("OT_GIT_SHIM_CAT_FILE_PID", str(pid_path))
    monkeypatch.setenv("OT_CAT_FILE_CHILD_INFO", str(child_info_path))
    monkeypatch.setenv("OT_CAT_FILE_MARKER", str(marker_path))

    started = time.monotonic()
    try:
        result = capture.finish(deadline=started + 3.0)
        elapsed = time.monotonic() - started

        source = result.source("git")
        assert elapsed < 3.5
        assert source.status == "partial"
        assert source.completeness == "partial"
        assert any(
            "cat-file --batch" in limitation and "timed out" in limitation
            for limitation in source.limitations
        )

        # The control observed entry into the real blocked cat-file --batch.
        assert pid_path.is_file(), "scoped event-reader control was not exercised"
        processes = _recorded_processes(pid_path)
        assert processes.keys() == {
            "parent_pid",
            "parent_pgid",
            "child_pid",
            "child_pgid",
        }
        assert processes["parent_pid"] != processes["child_pid"]
        # The descendant shares the scoped process group, so the same
        # group-scoped kill must reap it.
        assert processes["parent_pgid"] == processes["child_pgid"]

        # Primary oracle (no sleep): the exact recorded cat-file PID is gone the
        # instant finish returns — the deadline runner reaped it before return.
        assert not _pid_alive(processes["parent_pid"]), (
            "the scoped cat-file --batch PID survived Capture.finish's deadline"
        )

        # Child-only-kill detection: the descendant must also be gone. Its
        # survival marker fires 3.5s after fork; wait past that and assert it
        # never wrote it (a group leader killed without its descendants would
        # leave the marker), and that its exact PID has no survivor either.
        deadline = time.monotonic() + 4.0
        while _pid_alive(processes["child_pid"]) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(processes["child_pid"]), (
            "a descendant of the timed-out cat-file --batch survived the kill"
        )
        assert not marker_path.exists(), (
            "the timed-out cat-file --batch descendant survived to write its "
            "marker — the scoped kill did not reach the child"
        )
    finally:
        _terminate_recorded_processes(pid_path)


def test_finish_bounds_anchor_reconciliation_git_show(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Anchor reconciliation must share the finalizer's absolute deadline."""
    project = _git_project(tmp_path / "project")
    _seed_event_log(project)
    capture = _git_capture(project, tmp_path / "result")
    pid_path = _install_git_hang(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        condition='[ "$1" = "show" ] && [ -n "$OT_OPENTRACES_DIR" ]',
    )

    try:
        source, elapsed = _finish_with_deadline(capture)
        assert elapsed < 3.5
        assert source.status == "partial"
        assert source.completeness == "partial"
        assert any(
            "git show" in limitation and "timed out" in limitation
            for limitation in source.limitations
        )
        assert pid_path.is_file(), "anchor git-show control was not exercised"
    finally:
        _terminate_pid_file(pid_path)


def test_finish_bounds_maturation_event_append(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Completed maturation work must not append beyond the shared deadline."""
    project = _git_project(tmp_path / "project")
    _seed_event_log(project)
    capture = _git_capture(project, tmp_path / "result")
    pid_path = _install_git_hang(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        condition=(
            '[ "$1" = "hash-object" ] && [ "$2" = "-w" ] '
            '&& [ -n "$OT_OPENTRACES_DIR" ]'
        ),
    )

    try:
        source, elapsed = _finish_with_deadline(capture)
        assert elapsed < 3.5
        assert source.status == "partial"
        assert source.completeness == "partial"
        assert any(
            "git hash-object -w --stdin" in limitation and "timed out" in limitation
            for limitation in source.limitations
        )
        assert pid_path.is_file(), "maturation append control was not exercised"
    finally:
        _terminate_pid_file(pid_path)


def test_finish_bounds_final_event_evidence_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The non-truncated final evidence read must remain inside the deadline."""
    project = _git_project(tmp_path / "project")
    _seed_event_log(project)
    capture = _git_capture(project, tmp_path / "result")
    pid_path = _install_git_hang(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        condition=(
            '[ "$1" = "rev-list" ] && [ "$2" = "--reverse" ] '
            '&& [ -n "$OT_OPENTRACES_DIR" ]'
        ),
    )

    try:
        source, elapsed = _finish_with_deadline(capture)
        assert elapsed < 3.5
        assert source.status == "partial"
        assert source.completeness == "partial"
        assert any(
            "git rev-list --reverse" in limitation and "timed out" in limitation
            for limitation in source.limitations
        )
        assert pid_path.is_file(), "final event-read control was not exercised"
    finally:
        _terminate_pid_file(pid_path)


def test_finish_bounds_parent_git_report_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Parent-side evidence validation must not outlive Capture.finish's deadline."""
    project = _git_project(tmp_path / "project")
    capture = _git_capture(project, tmp_path / "result")
    pid_path = _install_git_hang(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        condition=(
            '[ "$1" = "rev-parse" ] && [ "$2" = "--verify" ] '
            '&& [ "$3" = "refs/opentraces/local/events/v1" ] '
            '&& [ -z "$OT_OPENTRACES_DIR" ]'
        ),
    )

    try:
        source, elapsed = _finish_with_deadline(capture)
        assert elapsed < 3.5
        assert source.status == "partial"
        assert source.completeness == "partial"
        assert any(
            "git rev-parse --verify refs/opentraces/local/events/v1" in limitation
            and "timed out" in limitation
            for limitation in source.limitations
        )
        assert pid_path.is_file(), "parent validation control was not exercised"
    finally:
        _terminate_pid_file(pid_path)
