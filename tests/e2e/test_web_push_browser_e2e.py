"""Browser-driven end-to-end test for the web push flow.

Pins the regression the user hit with the old "(upload module not
available)" fail-open: the ✓ banner appeared while nothing actually
went to HuggingFace, and the trace was silently marked ``UPLOADED`` on
disk. This test drives the real web viewer with ``agent-browser``,
clicks the push button, picks "Skip review and push", forces the CLI
subprocess to fail, and asserts:

1. The viewer surfaces the failure (no ✓ / no "trace(s) pushed") and
2. The local state still has the trace in ``COMMITTED`` (i.e. the
   silent fail-open is gone — the UI does not optimistically flip
   state when the subprocess did not transition it).

The test is opt-in. It requires the ``agent-browser`` CLI and is
gated on ``OT_BROWSER_E2E=1`` so it does not run in hermetic CI
suites that lack a headless Chrome.
"""

from __future__ import annotations

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

from opentraces.core.state import StateManager, TraceStatus


AB_SESSION = "otwpe"
TRACE_ID = "trace-e2e-push-001"
SESSION_ID = "sess-e2e-push-001"


def _require_browser_e2e_env() -> None:
    if os.environ.get("OT_BROWSER_E2E") != "1":
        pytest.skip("set OT_BROWSER_E2E=1 to enable browser-driven tests")
    if not shutil.which("agent-browser"):
        pytest.skip("agent-browser CLI not on PATH")
    # The subprocess resolves the CLI via ``Path(sys.executable).parent``,
    # so the script must exist next to the interpreter running the test.
    if not (Path(sys.executable).parent / "opentraces").exists():
        pytest.skip("'opentraces' console script not next to sys.executable")


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
            "--mode", "review",
            "--remote", "opentraces-e2e-nonexistent/repo",
            "--no-hook",
        ],
        cwd=str(project_dir),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

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


def test_browser_push_failure_keeps_trace_committed(tmp_path, ab_browser):
    """Drive the real viewer: a failing push must show ✕ and leave state alone.

    This is the end-to-end regression pin for the silent fail-open the
    user caught (`✓ Pushed 1 trace — (upload module not available)`).
    Under the pre-fix code a click on "Skip review and push" flipped
    the trace to ``UPLOADED`` locally without publishing anywhere.
    """
    home = tmp_path / "home"
    home.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    state_path, _traces_dir = _seed_committed_project(project_dir, home)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("HF_TOKEN", None)
    env.pop("HUGGINGFACE_TOKEN", None)
    # Belt-and-braces: the huggingface_hub library also reads its own
    # cached-token file. Disabling that keeps the push subprocess on
    # the "no token → CLI exits non-zero" path.
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

    port = _free_port()
    web_log_path = tmp_path / "web.log"
    web_log_fh = web_log_path.open("wb")
    web_proc = subprocess.Popen(
        ["opentraces", "web", "--port", str(port), "--no-open"],
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
        # The SPA fetches /api/traces and /api/context after load — wait
        # for the push button to reflect the seeded committed trace.
        _ab("wait", "--text", "push \u2192 1", timeout=20.0)

        # The push button is inside a React button with no stable id —
        # locate it by visible text.
        _ab("find", "text", "push \u2192 1", "click")

        # Modal title confirms the push modal is open.
        _ab("wait", "--text", "Push 1 staged trace", timeout=10.0)

        # Trigger the skip-review path via keyboard hotkey.
        _ab("press", "s")

        # The subprocess runs — wait for either the ✓ (must NOT happen)
        # or the ✕ we expect. The failure banner says "✕ ...".
        _ab("wait", "--text", "\u2715", timeout=60.0)

        # Negative assertion: the green ✓ success banner must NOT be
        # rendered. We read the DOM via ``eval`` instead of snapshot
        # so we do not depend on brittle ref ordering.
        doc_text = _ab_eval("document.body.innerText").strip()
        assert "\u2713 Pushed" not in doc_text, (
            f"green ✓ banner rendered on failure; silent fail-open regressed.\n"
            f"doc_text (trimmed):\n{doc_text[:2000]}"
        )

        # The on-disk state must still have the trace in COMMITTED —
        # the whole point of the fix. ``UPLOADED`` here would mean we
        # are back to the old silent fail-open.
        entry = StateManager(state_path=state_path).get_trace(TRACE_ID)
        assert entry is not None, "trace entry vanished from state"
        assert entry.status == TraceStatus.COMMITTED.value, (
            f"trace was silently flipped to {entry.status} on push failure; "
            "fail-open regression."
        )
    finally:
        web_proc.terminate()
        try:
            web_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            web_proc.kill()
            web_proc.wait(timeout=5)
