"""Deadline-bounded Git subprocesses for capture finalization."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence


def run_git_until(
    args: Sequence[str],
    *,
    cwd: Path,
    deadline: float | None,
    input: str | None = None,
    env: dict[str, str] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> subprocess.CompletedProcess[str]:
    """Run Git inside its own process group and clean up the group on timeout."""
    command = ["git", *args]
    timeout = None if deadline is None else max(0.0, deadline - monotonic())
    if timeout is not None and timeout <= 0.0:
        raise subprocess.TimeoutExpired(command, timeout)

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def run_git_bytes_until(
    args: Sequence[str],
    *,
    cwd: Path,
    deadline: float | None,
    input: bytes | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> subprocess.CompletedProcess[bytes]:
    """Binary counterpart for deadline-bounded event-log blob reads."""
    command = ["git", *args]
    timeout = None if deadline is None else max(0.0, deadline - monotonic())
    if timeout is not None and timeout <= 0.0:
        raise subprocess.TimeoutExpired(command, timeout)

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def timeout_limitation(exc: subprocess.TimeoutExpired) -> str:
    command = " ".join(str(part) for part in exc.cmd)
    return f"Git subprocess timed out at the finalization deadline: {command}"


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    """Terminate descendants on POSIX, with a child-only portable fallback."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.02)
        except subprocess.TimeoutExpired:
            pass
        # Always sweep the group: its leader may exit while a descendant lives.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        # ``start_new_session``/``killpg`` are POSIX-only. Terminate, then kill,
        # the direct child as the explicit portable fallback.
        process.terminate()
        try:
            process.wait(timeout=0.02)
        except subprocess.TimeoutExpired:
            process.kill()

    try:
        process.wait(timeout=0.05)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
