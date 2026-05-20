"""Opt-in agent-browser smoke test for the real web inbox."""

from __future__ import annotations

# ruff: noqa: E402

import os
import shutil
import subprocess
import uuid

import pytest

pytest.skip(
    "legacy web review client is decommissioned until the next dataset review UI "
    "iteration lands",
    allow_module_level=True,
)

from ._smoke_helpers import (
    free_port,
    isolated_runtime_env,
    require_opt_in,
    seed_opted_in_project,
    viewer_dist_path,
    wait_for_http,
)


def _ab_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("AGENT_BROWSER_SOCKET_DIR", "/tmp")
    return env


def _ab(session_name: str, *args: str, check: bool = True, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["agent-browser", "--session", session_name, *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_ab_env(),
    )


def _ab_batch(session_name: str, *commands: str, timeout: float = 45.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["agent-browser", "--session", session_name, "batch", *commands],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_ab_env(),
    )


def _agent_browser_is_usable() -> bool:
    probe_session = f"ot-web-smoke-probe-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        result = subprocess.run(
            [
                "agent-browser",
                "--session",
                probe_session,
                "batch",
                "open about:blank",
                "get title",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20.0,
            env=_ab_env(),
        )
        return result.returncode == 0
    finally:
        subprocess.run(
            ["agent-browser", "--session", probe_session, "close"],
            check=False,
            capture_output=True,
            env=_ab_env(),
        )


def _load_agent_browser_skill() -> None:
    result = subprocess.run(
        ["agent-browser", "skills", "get", "agent-browser"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
        env=_ab_env(),
    )
    if result.returncode != 0:
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if "Unknown command: skills" in combined:
            return
        pytest.skip(
            "agent-browser core skill could not be loaded\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


def _require_web_smoke_env() -> None:
    require_opt_in("OT_WEB_SMOKE", "the agent-browser web smoke test")
    if not shutil.which("agent-browser"):
        pytest.skip("agent-browser not on PATH")
    if viewer_dist_path() is None:
        pytest.skip("web viewer build output not found")
    _load_agent_browser_skill()
    if not _agent_browser_is_usable():
        pytest.skip("agent-browser could not open a browser session")


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5.0)


@pytest.fixture
def agent_browser_session() -> str:
    _require_web_smoke_env()
    session_name = f"ot-web-smoke-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    yield session_name
    subprocess.run(
        ["agent-browser", "--session", session_name, "close"],
        check=False,
        capture_output=True,
        env=_ab_env(),
    )


def test_web_viewer_loads_seeded_trace(tmp_path, agent_browser_session: str) -> None:
    seeded = seed_opted_in_project(
        tmp_path,
        task_description="web smoke trace",
        trace_id="trace-web-smoke-001",
        session_id="sess-web-smoke-001",
    )
    env = isolated_runtime_env(seeded.home)
    port = free_port()
    log_path = tmp_path / "web.log"

    with log_path.open("wb") as log_file:
        web_proc = subprocess.Popen(
            seeded.cli_cmd("web", "--port", str(port), "--no-open"),
            cwd=str(seeded.project_dir),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            try:
                wait_for_http(f"http://127.0.0.1:{port}/api/context", timeout_s=15.0)
            except TimeoutError:
                log_file.flush()
                pytest.fail(
                    f"web server never became reachable on :{port}\n"
                    f"--- web.log ---\n{log_path.read_text(errors='replace')}"
                )

            try:
                snapshot = _ab_batch(
                    agent_browser_session,
                    f"open http://127.0.0.1:{port}/",
                    'wait --text "web smoke trace"',
                    "snapshot -i",
                )
            except subprocess.CalledProcessError as exc:
                log_file.flush()
                pytest.fail(
                    "agent-browser could not load the web viewer\n"
                    f"stdout: {exc.stdout}\n"
                    f"stderr: {exc.stderr}\n"
                    f"--- web.log ---\n{log_path.read_text(errors='replace')}"
                )

            assert "opentraces viewer" in snapshot.stdout
            assert "web smoke trace" in snapshot.stdout

            title = _ab(agent_browser_session, "get", "title").stdout.strip()
            assert title == "opentraces viewer"

            current_url = _ab(agent_browser_session, "get", "url").stdout.strip()
            assert current_url.rstrip("/") == f"http://127.0.0.1:{port}".rstrip("/")
        finally:
            _stop_process(web_proc)
