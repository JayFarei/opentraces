"""Shared helpers for opt-in end-to-end smoke tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SeededProject:
    home: Path
    project_dir: Path
    python: Path
    state_dir: Path
    state_path: Path
    traces_dir: Path
    trace_path: Path
    trace_id: str

    def cli_cmd(self, *args: str) -> list[str]:
        return [
            str(self.python),
            "-c",
            "from opentraces.cli import main; main()",
            *args,
        ]


def require_opt_in(flag_name: str, reason: str) -> None:
    if os.environ.get(flag_name) != "1":
        pytest.skip(f"set {flag_name}=1 to enable {reason}")


def viewer_dist_path() -> Path | None:
    pkg_viewer = REPO_ROOT / "src" / "opentraces" / "static" / "viewer"
    if pkg_viewer.exists():
        return pkg_viewer
    repo_viewer = REPO_ROOT / "web" / "viewer" / "dist"
    if repo_viewer.exists():
        return repo_viewer
    return None


def isolated_runtime_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    env.pop("HF_TOKEN", None)
    env.pop("HUGGINGFACE_TOKEN", None)
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, *, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise TimeoutError(f"HTTP endpoint never became ready at {url}: {last_error}")


def seed_opted_in_project(
    tmp_path: Path,
    *,
    task_description: str,
    trace_id: str,
    session_id: str,
) -> SeededProject:
    home = tmp_path / "home"
    home.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    python = Path(sys.executable)
    env = isolated_runtime_env(home)
    subprocess.run(
        [
            str(python),
            "-c",
            "from opentraces.cli import main; main()",
            "init",
            "--mode",
            "review",
            "--remote",
            "opentraces-smoke/repo",
            "--no-hook",
        ],
        cwd=str(project_dir),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    state_dirs = sorted(
        path
        for path in (home / ".opentraces" / "projects").glob("*")
        if path.is_dir()
    )
    if len(state_dirs) != 1:
        raise AssertionError(
            f"expected exactly one opted-in project under {home}, found {len(state_dirs)}"
        )

    state_dir = state_dirs[0]
    state_path = state_dir / "state.json"
    traces_dir = state_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    trace = {
        "schema_version": "0.3.0",
        "trace_id": trace_id,
        "session_id": session_id,
        "timestamp_start": "2026-04-18T10:00:00Z",
        "timestamp_end": "2026-04-18T10:01:00Z",
        "agent": {
            "name": "claude-code",
            "model": "anthropic/claude-opus-4-1",
        },
        "task": {"description": task_description},
        "steps": [
            {
                "step_index": 1,
                "role": "user",
                "content": "hello",
                "timestamp": "2026-04-18T10:00:00Z",
            },
            {
                "step_index": 2,
                "role": "agent",
                "content": "world",
                "timestamp": "2026-04-18T10:01:00Z",
                "tool_calls": [],
                "observations": [],
                "snippets": [],
            },
        ],
        "metrics": {"total_steps": 2},
        "security": {
            "scanned": True,
            "flags_reviewed": 0,
            "redactions_applied": 0,
        },
    }
    trace_path = traces_dir / f"{trace_id}.jsonl"
    trace_path.write_text(json.dumps(trace) + "\n")

    return SeededProject(
        home=home,
        project_dir=project_dir,
        python=python,
        state_dir=state_dir,
        state_path=state_path,
        traces_dir=traces_dir,
        trace_path=trace_path,
        trace_id=trace_id,
    )
