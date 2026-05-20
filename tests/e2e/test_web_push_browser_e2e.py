"""Browser-driven end-to-end test for the web publication guidance flow.

The legacy trace-push UI used to shell out to removed root commands.
This test drives the real web viewer with ``agent-browser``, opens the
dataset-publication guidance modal, presses the old push hotkeys, and
asserts:

1. The viewer shows the dataset run/review/publish path, not a push
   runner, and
2. The local state still has the trace in ``COMMITTED`` because the
   modal is informational and must not mutate trace state.

The test is opt-in. It requires the ``agent-browser`` CLI and is
gated on ``OT_BROWSER_E2E=1`` so it does not run in hermetic CI
suites that lack a headless Chrome.
"""

from __future__ import annotations

# ruff: noqa: E402

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.skip(
    "legacy web review client is decommissioned until the next dataset review UI "
    "iteration lands",
    allow_module_level=True,
)

from opentraces.core.state import StateManager, TraceStatus


AB_SESSION = "otwpe"
TRACE_ID = "trace-e2e-push-001"
SESSION_ID = "sess-e2e-push-001"


def _require_browser_e2e_env() -> None:
    if os.environ.get("OT_BROWSER_E2E") != "1":
        pytest.skip("set OT_BROWSER_E2E=1 to enable browser-driven tests")
    if not shutil.which("agent-browser"):
        pytest.skip("agent-browser CLI not on PATH")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, *, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError) as exc:
            last_err = exc
            time.sleep(0.25)
    raise TimeoutError(f"web server never answered at {url}: {last_err}")


def _ab_env() -> dict[str, str]:
    """Environment for agent-browser invocations.

    The default socket dir under macOS runtime is long enough that
    any ``--session <name>`` blows past the AF_UNIX 103-byte limit.
    Override to ``/tmp`` so the socket path stays under the cap.
    """
    env = os.environ.copy()
    env.setdefault("AGENT_BROWSER_SOCKET_DIR", "/tmp")
    return env


def _ab(*args: str, check: bool = True, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run agent-browser scoped to a named session so parallel tests do not collide."""
    return subprocess.run(
        ["agent-browser", "--session", AB_SESSION, *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_ab_env(),
    )


def _ab_eval(script: str) -> str:
    """Run JS via ``agent-browser eval --stdin`` and return stdout."""
    proc = subprocess.run(
        ["agent-browser", "--session", AB_SESSION, "eval", "--stdin"],
        input=script,
        check=True,
        capture_output=True,
        text=True,
        timeout=15.0,
        env=_ab_env(),
    )
    return proc.stdout


def _seed_committed_project(project_dir: Path, home: Path) -> tuple[Path, Path]:
    """Initialize an opted-in project with one COMMITTED trace.

    Returns ``(state_path, traces_dir)`` so the test can assert on-disk
    state transitions after the browser-driven push.
    """
    # ``opentraces init`` reads HOME; isolate it under tmp_path so we do
    # not touch the real ``~/.opentraces/``.
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("HF_TOKEN", None)
    env.pop("HUGGINGFACE_TOKEN", None)
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

    subprocess.run(
        [
            "opentraces", "init",
            "--start-fresh",
        ],
        cwd=str(project_dir),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    marker_path = project_dir / ".opentraces.json"
    marker = json.loads(marker_path.read_text())
    marker.update(
        {
            "review_policy": "review",
            "push_policy": "manual",
            "remotes": {
                "opentraces-e2e-nonexistent/repo": {
                    "url": "hf://opentraces-e2e-nonexistent/repo",
                    "visibility": "private",
                }
            },
            "active_remote": "opentraces-e2e-nonexistent/repo",
        }
    )
    marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n")

    # Resolve the per-project state/traces dirs under the isolated HOME.
    # We import with the env patched so the ``PROJECTS_DIR`` constant
    # picks up the tmp HOME.
    resolved = subprocess.run(
        [
            sys.executable, "-c",
            "import os, sys, json\n"
            "from pathlib import Path\n"
            "from opentraces.core.config import get_project_state_path, get_project_traces_dir\n"
            "p = Path(sys.argv[1])\n"
            "print(json.dumps({\n"
            "  'state_path': str(get_project_state_path(p)),\n"
            "  'traces_dir': str(get_project_traces_dir(p)),\n"
            "}))\n",
            str(project_dir),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = json.loads(resolved.stdout.strip().splitlines()[-1])
    state_path = Path(paths["state_path"])
    traces_dir = Path(paths["traces_dir"])
    traces_dir.mkdir(parents=True, exist_ok=True)

    trace = {
        "schema_version": "0.3.0",
        "trace_id": TRACE_ID,
        "session_id": SESSION_ID,
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": "e2e push trace"},
        "steps": [
            {
                "step_index": 1,
                "role": "user",
                "content": "hi",
                "timestamp": "2026-04-16T10:00:00Z",
            },
        ],
        "metrics": {},
    }
    (traces_dir / f"{TRACE_ID}.jsonl").write_text(json.dumps(trace) + "\n")

    state = StateManager(state_path=state_path)
    state.set_trace_status(
        TRACE_ID, TraceStatus.COMMITTED, session_id=SESSION_ID,
        file_path=str(traces_dir / f"{TRACE_ID}.jsonl"),
    )
    return state_path, traces_dir


@pytest.fixture
def ab_browser():
    """Guarantee the agent-browser session is torn down after the test."""
    _require_browser_e2e_env()
    yield
    subprocess.run(
        ["agent-browser", "--session", AB_SESSION, "close"],
        check=False, capture_output=True, env=_ab_env(),
    )


def test_browser_publication_guidance_keeps_trace_committed(tmp_path, ab_browser):
    """Drive the real viewer: publication guidance must leave state alone."""
    home = tmp_path / "home"
    home.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    state_path, _traces_dir = _seed_committed_project(project_dir, home)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("HF_TOKEN", None)
    env.pop("HUGGINGFACE_TOKEN", None)
    # Belt-and-braces: this scenario must not depend on any real HF auth.
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

    port = _free_port()
    web_log_path = tmp_path / "web.log"
    web_log_fh = web_log_path.open("wb")
    web_proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from opentraces.cli import _launch_web_ui\n"
                f"_launch_web_ui(port={port}, open_browser=False)\n"
            ),
        ],
        cwd=str(project_dir),
        env=env,
        stdout=web_log_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        try:
            _wait_for_http(f"http://127.0.0.1:{port}/api/context", timeout_s=15.0)
        except TimeoutError:
            web_log_fh.flush()
            pytest.fail(
                f"web server never became reachable on :{port}\n"
                f"--- web.log ---\n{web_log_path.read_text(errors='replace')}"
            )

        try:
            _ab("open", f"http://127.0.0.1:{port}/")
        except subprocess.CalledProcessError as exc:
            web_log_fh.flush()
            pytest.fail(
                "agent-browser could not open the viewer\n"
                f"stdout: {exc.stdout}\n"
                f"stderr: {exc.stderr}\n"
                f"--- web.log ---\n{web_log_path.read_text(errors='replace')}"
            )
        # The SPA fetches /api/traces and /api/context after load. Wait
        # for the dataset-publication button to reflect the seeded trace.
        _ab("wait", "--text", "dataset \u2192 1", timeout=20.0)

        # The publication button is inside a React button with no stable id;
        # locate it by visible text.
        _ab("find", "text", "dataset \u2192 1", "click")

        # Modal title confirms the dataset guidance modal is open.
        _ab("wait", "--text", "Dataset publication replaces trace push", timeout=10.0)
        _ab("wait", "--text", "opentraces dataset publish <name>", timeout=10.0)

        # Old push hotkeys must be inert in this informational modal.
        _ab("press", "s")
        _ab("press", "l")

        doc_text = _ab_eval("document.body.innerText").strip()
        assert "Running opentraces" not in doc_text, (
            "legacy push runner rendered after old hotkey press.\n"
            f"doc_text (trimmed):\n{doc_text[:2000]}"
        )
        assert "opentraces dataset run <name>" in doc_text
        assert "opentraces dataset review <name>" in doc_text
        assert "opentraces dataset publish <name>" in doc_text

        entry = StateManager(state_path=state_path).get_trace(TRACE_ID)
        assert entry is not None, "trace entry vanished from state"
        assert entry.status == TraceStatus.COMMITTED.value, (
            f"trace was silently flipped to {entry.status} by publication guidance"
        )
    finally:
        web_proc.terminate()
        try:
            web_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            web_proc.kill()
            web_proc.wait(timeout=5)
