from __future__ import annotations

import base64
import contextlib
import json
import os
import shlex
import socket
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping
from urllib.request import Request, urlopen

import pytest

from opentraces.core.arena import engine as arena_engine
from opentraces.core.arena.agent import AgentDrive
from opentraces.core.arena.drives.actions import RunActionSequence
from opentraces.core.arena.drives.agent import AgentTerminalObservation
from opentraces.core.arena.drives.browser_mcp import BrowserMcpBridge
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.test_engine import FakeBoxRuntime, _scenario
from tests.core.arena.test_browser_drive import PublicBrowserSession


def verify_passes(_run) -> dict[str, list[str]]:
    return {"evidence_refs": []}


def verify_returns_evidence_ref(_run, *, evidence_ref: str) -> dict[str, list[str]]:
    return {"evidence_refs": [evidence_ref]}


class CompletingHarnessSession:
    """One real-session boundary double with a retained recording."""

    def __init__(self) -> None:
        self.started_argv: list[str] | None = None
        self.started_env: dict[str, str] = {}
        self.prompts: list[str] = []
        self.start_count = 0
        self.stop_count = 0

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.start_count += 1
        self.started_argv = list(argv)
        self.started_env = dict(env or {})
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_bytes(b"agent recording")

    def send(self, text: str) -> None:
        self.prompts.append(text)

    def observe(self) -> AgentTerminalObservation:
        return AgentTerminalObservation(
            state="running",
            screen=("OPENTRACES_HARNESS_VERSION=2.1.210\nOPENTRACES_AGENT_ATTEMPT_COMPLETE"),
            logs="",
        )

    def stop(self) -> None:
        self.stop_count += 1

    def recording_complete(self, recording_path: Path) -> bool:
        return recording_path.is_file() and recording_path.stat().st_size > 0


class EchoingCredentialHarnessSession(CompletingHarnessSession):
    """Model a live harness that writes its process-only credential by mistake."""

    def __init__(self, sentinel: str) -> None:
        super().__init__()
        self.sentinel = sentinel

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().start(
            argv,
            recording_path=recording_path,
            cols=cols,
            rows=rows,
            env=env,
        )
        (recording_path.parent / "credential-link").symlink_to(self.sentinel)

    def observe(self) -> AgentTerminalObservation:
        return AgentTerminalObservation(
            state="running",
            screen=(
                "OPENTRACES_HARNESS_VERSION=2.1.210\n"
                f"{self.sentinel}\n"
                "OPENTRACES_AGENT_ATTEMPT_COMPLETE"
            ),
            logs="",
        )


class CredentialNamedPathHarnessSession(CompletingHarnessSession):
    """Model a live harness that accidentally uses its credential as a filename."""

    def __init__(self, sentinel: str) -> None:
        super().__init__()
        self.sentinel = sentinel

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().start(
            argv,
            recording_path=recording_path,
            cols=cols,
            rows=rows,
            env=env,
        )
        (recording_path.parent / self.sentinel).write_text(
            "credential appeared in this path\n", encoding="utf-8"
        )


class UndeletableCredentialHarnessSession(CompletingHarnessSession):
    """Leak a credential into a directory whose first deletion is denied."""

    def __init__(self, sentinel: str) -> None:
        super().__init__()
        self.sentinel = sentinel
        self.locked_dir: Path | None = None

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().start(
            argv,
            recording_path=recording_path,
            cols=cols,
            rows=rows,
            env=env,
        )
        locked = recording_path.parent / "locked"
        locked.mkdir(parents=True, exist_ok=True)
        (locked / "leaked.txt").write_text(
            f"{self.sentinel}\n", encoding="utf-8"
        )
        # Deny write on the directory so its entry cannot be unlinked: the first
        # rmtree of the contaminated staging tree fails until permissions are
        # repaired.
        os.chmod(locked, 0o500)
        self.locked_dir = locked

    def observe(self) -> AgentTerminalObservation:
        return AgentTerminalObservation(
            state="running",
            screen=(
                "OPENTRACES_HARNESS_VERSION=2.1.210\n"
                f"{self.sentinel}\n"
                "OPENTRACES_AGENT_ATTEMPT_COMPLETE"
            ),
            logs="",
        )


class SpecialNodeHarnessSession(CompletingHarnessSession):
    """Model a harness that leaves a non-regular node in the evidence tree."""

    def __init__(self) -> None:
        super().__init__()
        self.bound_socket: socket.socket | None = None

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().start(
            argv,
            recording_path=recording_path,
            cols=cols,
            rows=rows,
            env=env,
        )
        self.bound_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bind_path = Path("/tmp") / f"ot-{os.getpid()}-{id(self):x}.sock"
        self.bound_socket.bind(str(bind_path))
        os.link(bind_path, recording_path.parent / "credential-socket")
        bind_path.unlink()

    def stop(self) -> None:
        super().stop()


def _harness_argv(argv: list[str]) -> list[str]:
    serialized = argv[argv.index("--") + 1 :]
    assert len(serialized) == 1
    remote = shlex.split(serialized[0])
    assert remote[:5] == [
        "/bin/sh",
        "-c",
        'cd -- "$1" && shift && exec "$@"',
        "opentraces-agent-workspace",
        "/work/crabbox/fake-1/opentraces",
    ]
    return remote[5:]


def _closed_mcp_config(argv: list[str]) -> dict[str, object]:
    remote = _harness_argv(argv)
    assert "--strict-mcp-config" in remote
    assert "--mcp-config" in remote
    marker = remote.index("opentraces-agent")
    encoded = remote[marker + 3]
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


class BrowserAttemptingHarnessSession(CompletingHarnessSession):
    """Exercise the closed MCP authority decision at the harness boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.browser_refused = False

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().start(
            argv,
            recording_path=recording_path,
            cols=cols,
            rows=rows,
            env=env,
        )
        config = _closed_mcp_config(argv)
        self.browser_refused = config == {"mcpServers": {}}

    def observe(self) -> AgentTerminalObservation:
        browser_result = (
            "BROWSER_TOOL_REFUSED: no browser MCP server is configured"
            if self.browser_refused
            else "BROWSER_TOOL_SUCCEEDED"
        )
        return AgentTerminalObservation(
            state="running",
            screen=(
                "OPENTRACES_HARNESS_VERSION=2.1.210\n"
                f"{browser_result}\n"
                "OPENTRACES_AGENT_ATTEMPT_COMPLETE"
            ),
            logs="",
        )


class BrowserCallingHarnessSession(CompletingHarnessSession):
    """Call the exact run-owned browser through the SSH-forwarded MCP bridge."""

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().start(
            argv,
            recording_path=recording_path,
            cols=cols,
            rows=rows,
            env=env,
        )
        config = _closed_mcp_config(argv)
        server = config["mcpServers"]["opentraces_browser"]
        assert server["type"] == "http"
        reverse = argv[argv.index("-R") + 1]
        _remote_host, _remote_port, local_host, local_port = reverse.split(":")
        assert local_host == "127.0.0.1"
        request = Request(
            f"http://127.0.0.1:{local_port}/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "browser_navigate",
                        "arguments": {"url": "http://127.0.0.1:8080/authorize"},
                    },
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload["result"]["isError"] is False


class DisconnectedRecordedHarnessSession(CompletingHarnessSession):
    def observe(self) -> AgentTerminalObservation:
        return AgentTerminalObservation(state="disconnected", screen="", logs="")

    def recording_complete(self, recording_path: Path) -> bool:
        return False


class WrongVersionHarnessSession(CompletingHarnessSession):
    def observe(self) -> AgentTerminalObservation:
        return AgentTerminalObservation(
            state="running",
            screen=("OPENTRACES_HARNESS_VERSION=2.1.209\nOPENTRACES_AGENT_ATTEMPT_COMPLETE"),
            logs="",
        )


class StaleMarkerTerminalStateHarnessSession(CompletingHarnessSession):
    def __init__(self, state: str) -> None:
        super().__init__()
        self.state = state

    def observe(self) -> AgentTerminalObservation:
        return AgentTerminalObservation(
            state=self.state,
            screen=("OPENTRACES_HARNESS_VERSION=2.1.210\nOPENTRACES_AGENT_ATTEMPT_COMPLETE"),
            logs="",
        )


class InterruptedCaptureRuntime(FakeBoxRuntime):
    def collect(self, box, globs, *, destination, repository):
        assert len(globs) == 1
        capture_root = destination / "files" / ".opentraces" / "bench-capture"
        capture_root.mkdir(parents=True)
        report = capture_root / "finalizers" / "git.report.json"
        report.parent.mkdir()
        report.write_text('{"interrupted":true}\n', encoding="utf-8")
        result_path = capture_root / "capture_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "opentraces.capture.result.v1",
                    "module_version": "0.4.8",
                    "placement": "leased",
                    "completeness": "partial",
                    "observer_version": "0.4.8",
                    "product_under_test_version": "0.4.8",
                    "sources": [
                        {
                            "name": "git",
                            "view": "world_effects",
                            "requested": True,
                            "required": True,
                            "status": "unavailable",
                            "completeness": "missing",
                            "evidence_refs": ["finalizers/git.report.json"],
                            "limitations": ["source process exited after explicit interruption"],
                            "duration_ms": 1,
                            "details": {},
                        }
                    ],
                    "views": [],
                    "limitations": ["git: source process exited after explicit interruption"],
                    "trace_refs": [],
                    "security": {},
                    "result_path": ".opentraces/bench-capture/capture_result.json",
                    "provenance": {},
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return {"capture_result.json": result_path, "git.report.json": report}


class CompleteCaptureRuntime(FakeBoxRuntime):
    def collect(self, box, globs, *, destination, repository):
        assert len(globs) == 1
        capture_root = destination / "files" / ".opentraces" / "bench-capture"
        finalizers = capture_root / "finalizers"
        finalizers.mkdir(parents=True)
        source_specs = [
            ("session_jsonl", "harness"),
            ("telemetry", "model_boundary"),
            ("git", "world_effects"),
            ("bucket", "storage"),
        ]
        sources = []
        for source, view in source_specs:
            reference = f"finalizers/{source}.report.json"
            (capture_root / reference).write_text(
                json.dumps({"source": source}) + "\n", encoding="utf-8"
            )
            sources.append(
                {
                    "name": source,
                    "view": view,
                    "requested": True,
                    "required": True,
                    "status": "finalized",
                    "completeness": "full",
                    "evidence_refs": [reference],
                    "limitations": [],
                    "duration_ms": 1,
                    "details": {},
                }
            )
        result_path = capture_root / "capture_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": "opentraces.capture.result.v1",
                    "module_version": "0.4.8",
                    "placement": "leased",
                    "completeness": "complete",
                    "observer_version": "0.4.8",
                    "product_under_test_version": "0.4.8",
                    "sources": sources,
                    "views": [],
                    "limitations": [],
                    "trace_refs": ["trace-scenario-4"],
                    "security": {},
                    "result_path": ".opentraces/bench-capture/capture_result.json",
                    "provenance": {},
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return {path.name: path for path in capture_root.rglob("*") if path.is_file()}


def _bench(tmp_path: Path, session: CompletingHarnessSession) -> Bench:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
        agent_session_factory=lambda _name: session,
        agent_poll_interval=0,
    )


@pytest.fixture(autouse=True)
def _dedicated_live_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-live-key-not-a-real-credential")


@pytest.mark.parametrize(
    ("access", "message"),
    [
        ([], "at least one run-owned surface"),
        (["terminal"], "run-owned terminal/browser objects"),
    ],
)
def test_agent_access_refuses_non_capabilities_before_harness_spawn(
    tmp_path: Path,
    access: list[object],
    message: str,
) -> None:
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        with pytest.raises(ValueError, match=message):
            run.agent.attempt(
                harness="claude",
                task="Use the browser.",
                access=access,
                inference="live",
            )
        assert not (run.draft.path / "actions").exists()
        run.verify(verify_passes)

    assert session.start_count == 0


def test_agent_access_rejects_foreign_and_duplicate_drive_objects_before_spawn(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    first = _bench(tmp_path / "first", session)
    second = _bench(tmp_path / "second", session)

    with first.run(app_state="install-only", execution_mode="agent_live") as first_run:
        with second.run(app_state="install-only", execution_mode="agent_live") as second_run:
            for access, message in (
                ([second_run.browser], "run-owned terminal/browser objects"),
                ([first_run.terminal, first_run.terminal], "duplicate access surface"),
            ):
                with pytest.raises(ValueError, match=message):
                    first_run.agent.attempt(
                        harness="claude",
                        task="Use only the declared surfaces.",
                        access=access,
                        inference="live",
                    )
            first_run.verify(verify_passes)
            second_run.verify(verify_passes)

    assert session.start_count == 0


@pytest.mark.parametrize(
    ("execution_mode", "inference", "message"),
    [
        ("direct", "live", "requires agent_live or agent_replay"),
        ("agent_replay", "live", "agent_replay requires a model-wire inference"),
    ],
)
def test_agent_refuses_mode_inference_mismatch_before_spawn(
    tmp_path: Path,
    execution_mode: str,
    inference: object,
    message: str,
) -> None:
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode=execution_mode) as run:
        with pytest.raises(ValueError, match=message):
            run.agent.attempt(
                harness="claude",
                task="Complete one task.",
                access=[run.terminal],
                inference=inference,
            )
        run.verify(verify_passes)

    assert session.start_count == 0


def test_terminal_only_agent_attempt_is_one_product_user_action_with_stored_grants(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Open the browser and report its title.",
            access=[run.terminal],
            inference="live",
        )
        assert attempt.completed is True
        assert attempt.failure is None
        with pytest.raises(RuntimeError, match="one agent attempt"):
            run.agent.attempt(
                harness="claude",
                task="Try again.",
                access=[run.terminal],
                inference="live",
            )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert session.start_count == 1
    assert session.started_argv is not None
    remote = _harness_argv(session.started_argv)
    assert "/usr/bin/sudo" in remote
    sudo = remote.index("/usr/bin/sudo")
    assert remote[sudo : sudo + 7] == [
        "/usr/bin/sudo",
        "-H",
        "-n",
        "-u",
        "opentraces-product",
        "--preserve-env=ANTHROPIC_API_KEY",
        "--",
    ]
    assert "--allowedTools" in remote
    assert "Bash" in remote
    assert "--disallowedTools" in remote
    assert "mcp__opentraces_browser__*" in remote

    attempt_record = json.loads(
        (run.final_path / "artifacts/agent-attempt.json").read_text(encoding="utf-8")
    )
    assert attempt_record == {
        "schema_version": "opentraces.bench.agent-attempt.v0",
        "task": "Open the browser and report its title.",
        "granted_surfaces": ["terminal"],
        "harness": {
            "name": "claude",
            "executable": "/home/opentraces-product/.local/bin/claude",
            "version": "2.1.210",
        },
        "inference": {"mode": "live"},
        "action_refs": ["actions/0001"],
        "recording_refs": ["recordings/agent-0001.termctrl"],
        "completed": True,
        "failure": None,
    }
    actions = sorted((run.final_path / "actions").iterdir())
    assert [path.name for path in actions] == ["0001"]
    invocation = json.loads((actions[0] / "invocation.json").read_text(encoding="utf-8"))
    assert invocation["surface"] == "agent"
    assert not any(path.name.startswith("browser") for path in actions)
    assert run.result["pins"]["harness"] == attempt_record["harness"]
    assert run.result["pins"]["model_wire"] == {"mode": "live"}
    assert run.result["recordings"]["timeline"]["complete"] is True
    assert any(
        channel
        == {
            "kind": "agent_terminal",
            "complete": True,
            "path": "recordings/agent-0001.termctrl",
            "reason": None,
        }
        for channel in run.result["recordings"]["channels"]
    )


def test_terminal_only_agent_browser_attempt_is_refused_by_closed_mcp_config(
    tmp_path: Path,
) -> None:
    session = BrowserAttemptingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Use the browser to open the authorization page.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert attempt.completed is True
    assert session.browser_refused is True
    assert session.started_argv is not None
    assert _closed_mcp_config(session.started_argv) == {"mcpServers": {}}
    transcript = (run.final_path / "actions/0001/transcript.txt").read_text(encoding="utf-8")
    assert "BROWSER_TOOL_REFUSED" in transcript
    assert [path.name for path in (run.final_path / "actions").iterdir()] == ["0001"]
    assert not (run.final_path / "recordings/browser").exists()


def test_browser_grant_routes_mcp_call_into_exact_run_drive_and_shared_timeline(
    tmp_path: Path,
) -> None:
    session = BrowserCallingHarnessSession()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=PublicBrowserSession,
        agent_session_factory=lambda _name: session,
        agent_poll_interval=0,
    )

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Use the browser to open the authorization page.",
            access=[run.terminal, run.browser],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert attempt.completed is True
    assert session.started_argv is not None
    config = _closed_mcp_config(session.started_argv)
    assert list(config["mcpServers"]) == ["opentraces_browser"]
    invocations = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run.final_path / "actions").glob("*/invocation.json"))
    ]
    assert [(item["ordinal"], item.get("surface"), item["kind"]) for item in invocations] == [
        (1, "agent", "interactive_prompt"),
        (2, None, "navigate"),
    ]
    assert invocations[1]["url"] == "http://127.0.0.1:8080/authorize"
    assert run.result["recordings"]["timeline"]["complete"] is True
    assert {channel["kind"] for channel in run.result["recordings"]["channels"]} >= {
        "agent_terminal",
        "browser_video",
        "playwright_trace",
        "browser_screenshots",
    }


def test_unfinalized_agent_recording_is_not_reported_rewatchable(
    tmp_path: Path,
) -> None:
    session = DisconnectedRecordedHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Inspect the terminal.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert attempt.completed is False
    agent_channel = next(
        channel
        for channel in run.result["recordings"]["channels"]
        if channel["kind"] == "agent_terminal"
    )
    assert agent_channel == {
        "kind": "agent_terminal",
        "complete": False,
        "path": None,
        "reason": "agent terminal recording did not finalize cleanly",
    }
    assert run.result["recordings"]["rewatchable"] is False
    assert session.stop_count == 1


def test_failed_agent_attempt_overrides_passing_verifier_and_incompletes_evidence(
    tmp_path: Path,
) -> None:
    session = DisconnectedRecordedHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Inspect the terminal.",
            access=[run.terminal],
            inference="live",
        )
        assert attempt.completed is False
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert run.result["execution_status"] == "complete"
    assert run.result["verdict"] == "fail"
    assert run.result["reason"] == {
        "code": "agent_drive_disconnected",
        "message": "agent terminal disconnected during actions/0001",
    }
    assert run.result["evidence"]["complete"] is False
    agent_requirement = next(
        requirement
        for requirement in run.result["evidence"]["requirements"]
        if requirement["name"] == "agent.attempt"
    )
    assert agent_requirement == {
        "name": "agent.attempt",
        "complete": False,
        "evidence_refs": [attempt.artifact_ref],
    }


@pytest.mark.parametrize(
    ("terminal_state", "reason_code", "reason_message"),
    [
        (
            "disconnected",
            "agent_drive_disconnected",
            "agent terminal disconnected during actions/0001",
        ),
        (
            "exited",
            "agent_drive_exited_before_expectation",
            "agent terminal exited before expectation during actions/0001",
        ),
    ],
)
def test_stale_completion_marker_cannot_green_failed_agent_run(
    tmp_path: Path,
    terminal_state: str,
    reason_code: str,
    reason_message: str,
) -> None:
    session = StaleMarkerTerminalStateHarnessSession(terminal_state)
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Inspect the terminal.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert attempt.completed is False
    assert attempt.failure == {"code": reason_code, "message": reason_message}
    assert run.result["verdict"] == "fail"
    assert run.result["reason"] == attempt.failure
    assert run.result["evidence"]["complete"] is False
    assert next(
        requirement
        for requirement in run.result["evidence"]["requirements"]
        if requirement["name"] == "agent.attempt"
    ) == {
        "name": "agent.attempt",
        "complete": False,
        "evidence_refs": [attempt.artifact_ref],
    }


def test_interrupted_required_capture_fails_even_when_agent_attempt_completes(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=InterruptedCaptureRuntime(),
        repository_path=tmp_path,
        agent_session_factory=lambda _name: session,
        agent_poll_interval=0,
    )

    with bench.run(
        app_state="install-only",
        execution_mode="agent_live",
        capture_required=["git"],
    ) as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make and commit the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert attempt.completed is True
    assert run.result["execution_status"] == "complete"
    assert run.result["verdict"] == "fail"
    assert run.result["reason"] == {
        "code": "required_capture_missing",
        "message": "required capture evidence is unavailable: git",
    }
    assert run.result["capture"]["completeness"] == "partial"
    assert run.result["capture"]["sources"][0]["limitations"] == [
        "source process exited after explicit interruption"
    ]
    assert {
        "name": "capture.git",
        "complete": False,
        "evidence_refs": ["capture/finalizers/git.report.json"],
    } in run.result["evidence"]["requirements"]
    assert (run.final_path / "capture/finalizers/git.report.json").is_file()
    custody = json.loads(
        (run.final_path / "artifacts/live-key-absence.json").read_text(encoding="utf-8")
    )
    assert custody["secret_names"] == ["ANTHROPIC_API_KEY"]
    assert custody["capture_files_checked"] == 2
    assert custody["matches"] == []
    assert custody["absent"] is True


def test_live_key_reaches_agent_process_but_no_finalized_run_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "sk-ant-bench-sentinel-9ac22d0f"
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert session.started_env == {"ANTHROPIC_API_KEY": sentinel}
    assert session.started_argv is not None
    assert "SendEnv=ANTHROPIC_API_KEY" in session.started_argv
    remote = _harness_argv(session.started_argv)
    assert "--preserve-env=ANTHROPIC_API_KEY" in remote
    assert not any(sentinel in argument for argument in session.started_argv)
    custody = json.loads(
        (run.final_path / "artifacts/live-key-absence.json").read_text(encoding="utf-8")
    )
    assert custody == {
        "schema_version": "opentraces.bench.secret-absence.v0",
        "secret_names": ["ANTHROPIC_API_KEY"],
        "files_checked": custody["files_checked"],
        "bytes_checked": custody["bytes_checked"],
        "capture_files_checked": 0,
        "candidate_result_checked": True,
        "matches": [],
        "absent": True,
    }
    assert custody["files_checked"] > 0
    assert custody["bytes_checked"] > 0
    assert {
        "name": "agent.live_key_absence",
        "complete": True,
        "evidence_refs": ["artifacts/live-key-absence.json"],
    } in run.result["evidence"]["requirements"]
    for stored_path in run.final_path.rglob("*"):
        if stored_path.is_file():
            assert sentinel.encode() not in stored_path.read_bytes(), stored_path


def test_oauth_only_live_agent_reaches_harness_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "oauth-bench-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert session.started_env == {"CLAUDE_CODE_OAUTH_TOKEN": sentinel}
    assert run.result["verdict"] == "pass"


def test_oauth_leak_fails_without_persisting_secret_bytes_or_symlink_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "oauth-bench-leak-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = EchoingCredentialHarnessSession(sentinel)
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert run.result["verdict"] == "fail"
    assert run.result["reason"] == {
        "code": "live_key_stored",
        "message": "the live inference key appeared in stored run evidence",
    }
    assert run.result["verifiers"] == []
    assert run.result["capture"] is None
    assert run.result["recordings"] == {"rewatchable": False, "channels": []}
    assert run.result["artifacts"] == [
        {
            "path": "artifacts/live-key-absence.json",
            "media_type": "application/json",
            "kind": "secret_absence",
        }
    ]
    assert run.result["evidence"] == {
        "complete": False,
        "requirements": [
            {
                "name": "agent.live_key_absence",
                "complete": False,
                "evidence_refs": ["artifacts/live-key-absence.json"],
            }
        ],
    }
    assert bench.store.verify(run.final_path) is True
    custody = json.loads(
        (run.final_path / "artifacts/live-key-absence.json").read_text(encoding="utf-8")
    )
    assert custody["absent"] is False
    assert custody["matches"] == [
        "actions/0001/transcript.txt",
        "recordings/credential-link [symlink not frozen]",
    ]
    for stored_path in run.final_path.rglob("*"):
        assert not stored_path.is_symlink(), stored_path
        if stored_path.is_file():
            assert sentinel.encode() not in stored_path.read_bytes(), stored_path


def test_oauth_leak_in_filename_is_quarantined_without_repeating_the_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "oauth-bench-filename-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = CredentialNamedPathHarnessSession(sentinel)
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert run.result["verdict"] == "fail"
    custody_path = run.final_path / "artifacts/live-key-absence.json"
    custody_bytes = custody_path.read_bytes()
    assert sentinel.encode() not in custody_bytes
    custody = json.loads(custody_bytes)
    assert custody["matches"] == [
        "sha256:b2f1a090fec75d21b3c0f533a3232537f46985e9559a8a3bc3dc5ec9e1202be1"
        " [path contains live credential]"
    ]
    for stored_path in run.final_path.rglob("*"):
        relative = stored_path.relative_to(run.final_path).as_posix().encode()
        assert sentinel.encode() not in relative, stored_path


def test_live_agent_special_evidence_node_is_classified_and_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-special-node-not-a-real-credential")
    session = SpecialNodeHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)
    assert session.bound_socket is not None
    session.bound_socket.close()

    assert run.result["verdict"] == "fail"
    custody = json.loads(
        (run.final_path / "artifacts/live-key-absence.json").read_text(encoding="utf-8")
    )
    assert custody["matches"] == ["recordings/credential-socket [special node not frozen]"]
    assert not any(
        stat.S_ISSOCK(stored_path.lstat().st_mode) for stored_path in run.final_path.rglob("*")
    )


def test_oauth_precedes_api_key_without_freezing_either_live_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth = "oauth-bench-selected-sentinel-not-a-real-credential"
    api_key = "sk-ant-bench-unselected-sentinel-not-a-real-credential"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", oauth)
    monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert session.started_env == {"CLAUDE_CODE_OAUTH_TOKEN": oauth}
    assert session.started_argv is not None
    assert "SendEnv=CLAUDE_CODE_OAUTH_TOKEN" in session.started_argv
    assert "SendEnv=ANTHROPIC_API_KEY" not in session.started_argv
    remote = _harness_argv(session.started_argv)
    assert "--preserve-env=CLAUDE_CODE_OAUTH_TOKEN" in remote
    assert not any(
        value in argument for value in (oauth, api_key) for argument in session.started_argv
    )

    custody = json.loads(
        (run.final_path / "artifacts/live-key-absence.json").read_text(encoding="utf-8")
    )
    assert custody["secret_names"] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert custody["matches"] == []
    assert custody["absent"] is True
    for stored_path in run.final_path.rglob("*"):
        if stored_path.is_file():
            frozen = stored_path.read_bytes()
            assert oauth.encode() not in frozen, stored_path
            assert api_key.encode() not in frozen, stored_path


def test_missing_live_key_is_a_named_skip_before_agent_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )

    assert run.result["execution_status"] == "complete"
    assert run.result["verdict"] == "skip"
    assert run.result["reason"] == {
        "code": "anthropic_credential_missing",
        "message": ("CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY is required for agent_live"),
    }
    assert run.result["evidence"] == {
        "complete": False,
        "requirements": [
            {
                "name": "anthropic_credential_missing",
                "complete": False,
                "evidence_refs": [],
            }
        ],
    }
    assert session.start_count == 0
    assert not (run.final_path / "actions/0001").exists()


def test_dangling_symlink_cannot_escape_live_key_absence_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "sk-ant-bench-sentinel-broken-symlink"
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)
        assert run.draft is not None
        (run.draft.path / "artifacts/leak-link").symlink_to(sentinel)

    custody = json.loads(
        (run.final_path / "artifacts/live-key-absence.json").read_text(encoding="utf-8")
    )
    assert custody["absent"] is False
    assert custody["matches"] == ["artifacts/leak-link [symlink not frozen]"]
    assert run.result["verdict"] == "fail"
    assert run.result["reason"] == {
        "code": "live_key_stored",
        "message": "the live inference key appeared in stored run evidence",
    }


def test_required_capture_wraps_the_real_claude_child_with_exact_session_id(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(
        app_state="install-only",
        execution_mode="agent_live",
        capture_required=["session_jsonl", "git"],
    ) as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make and commit the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert session.started_argv is not None
    remote = _harness_argv(session.started_argv)
    sudo = remote.index("/usr/bin/sudo")
    product_command = remote[remote.index("--", sudo) + 1 :]
    assert attempt.capture_session_id is not None
    result_dir = f".opentraces/bench-capture/{attempt.capture_session_id}"
    assert product_command[:10] == [
        "/usr/bin/python3",
        "-m",
        "opentraces.capture._agent_harness",
        "--session-id",
        attempt.capture_session_id,
        "--result-dir",
        result_dir,
        "--required-source",
        "session_jsonl",
        "--required-source",
    ]
    assert product_command[10:12] == ["git", "--"]
    claude_command = product_command[12:]
    assert claude_command[:2] == ["/bin/sh", "-c"]
    assert claude_command[-2:] == ["--session-id", attempt.capture_session_id]

    attempt_record = json.loads(
        (run.final_path / "artifacts/agent-attempt.json").read_text(encoding="utf-8")
    )
    assert attempt_record["capture"] == {
        "session_id": attempt.capture_session_id,
        "artifact_glob": f"{result_dir}/**/*",
        "required_sources": ["session_jsonl", "git"],
    }


def test_semantic_capture_requirements_grade_each_source_independently(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "runs" / "v1"),
        box_runtime=CompleteCaptureRuntime(),
        repository_path=tmp_path,
        agent_session_factory=lambda _name: session,
        agent_poll_interval=0,
    )

    with bench.run(
        app_state="agent-ready",
        execution_mode="agent_live",
        capture_required=["trace", "context", "trail", "storage"],
    ) as run:
        attempt = run.agent.attempt(
            harness="claude",
            task="Make and commit the requested world-state change.",
            access=[run.terminal],
            inference="live",
        )
        run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert run.result["verdict"] == "pass"
    requirements = {
        requirement["name"]: requirement for requirement in run.result["evidence"]["requirements"]
    }
    assert requirements["capture.trace"] == {
        "name": "capture.trace",
        "complete": True,
        "evidence_refs": ["capture/finalizers/session_jsonl.report.json"],
    }
    assert requirements["capture.context"]["complete"] is True
    assert requirements["capture.trail"]["complete"] is True
    assert requirements["capture.storage"]["complete"] is True
    assert requirements["capture.lifecycle"] == {
        "name": "capture.lifecycle",
        "complete": True,
        "evidence_refs": ["capture/capture_result.json"],
    }
    assert session.started_argv is not None
    remote = _harness_argv(session.started_argv)
    assert "trace" not in remote
    assert "context" not in remote
    assert "trail" not in remote
    for source in ("session_jsonl", "telemetry", "git", "bucket"):
        assert source in remote


def test_wrong_claude_version_is_a_named_failed_attempt(tmp_path: Path) -> None:
    session = WrongVersionHarnessSession()
    draft = RunStore(tmp_path / "runs" / "v1").begin()
    terminal = object()
    registered = SimpleNamespace(
        pin={"kind": "anthropic-scripted", "script_sha256": "sha256:registered"},
        env={},
    )
    drive = AgentDrive(
        box=FakeBoxRuntime().lease(),
        draft=draft,
        actions=RunActionSequence(draft=draft, run_started_monotonic=time.monotonic()),
        terminal=terminal,
        browser=object(),
        execution_mode="agent_replay",
        product_environment=lambda: {},
        replay_inference=lambda: registered,
        session_factory=lambda _name: session,
        poll_interval=0,
    )

    attempt = drive.attempt(
        harness="claude",
        task="Complete the replay.",
        access=[terminal],
        inference=registered,
    )

    assert attempt.completed is False
    assert attempt.failure == {
        "code": "agent_harness_version_mismatch",
        "message": "expected Claude Code 2.1.210, observed 2.1.209",
    }


def test_replay_refuses_foreign_or_duck_typed_inference_before_spawn(tmp_path: Path) -> None:
    session = CompletingHarnessSession()
    draft = RunStore(tmp_path / "runs" / "v1").begin()
    terminal = object()
    registered = SimpleNamespace(
        pin={"kind": "anthropic-scripted", "script_sha256": "sha256:registered"},
        env={"ANTHROPIC_API_KEY": "registered-replay-key"},
    )
    foreign = SimpleNamespace(
        pin={"kind": "forged-wire", "script_sha256": "sha256:not-real"},
        env={"ANTHROPIC_API_KEY": "foreign-key"},
    )
    drive = AgentDrive(
        box=FakeBoxRuntime().lease(),
        draft=draft,
        actions=RunActionSequence(draft=draft, run_started_monotonic=time.monotonic()),
        terminal=terminal,
        browser=object(),
        execution_mode="agent_replay",
        product_environment=lambda: {},
        replay_inference=lambda: registered,
        session_factory=lambda _name: session,
        poll_interval=0,
    )

    with pytest.raises(ValueError, match="exact run-owned Anthropic replay emulator"):
        drive.attempt(
            harness="claude",
            task="Do not accept a forged model wire.",
            access=[terminal],
            inference=foreign,
        )

    assert session.start_count == 0
    assert not any((draft.path / "actions").iterdir())


def test_browser_mcp_partial_body_cannot_block_bounded_shutdown() -> None:
    class Browser:
        def finalize_recordings(self) -> None:
            return None

    bridge = BrowserMcpBridge(Browser())  # type: ignore[arg-type]
    bridge.start()
    client = socket.create_connection(("127.0.0.1", bridge.local_port), timeout=1)
    closed = threading.Event()
    shutdown = threading.Thread(target=lambda: (bridge.close(), closed.set()), daemon=True)
    try:
        client.sendall(
            b"POST /mcp HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1000000\r\n\r\n{}"
        )
        time.sleep(0.05)
        shutdown.start()
        assert closed.wait(1.0), "partial MCP request blocked bridge shutdown"
    finally:
        client.close()
        shutdown.join(timeout=2)


def test_agent_authority_refuses_controller_bearer_before_any_action(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    terminal = object()
    browser = object()
    drive = AgentDrive(
        box=FakeBoxRuntime().lease(),
        draft=draft,
        actions=RunActionSequence(draft=draft, run_started_monotonic=time.monotonic()),
        terminal=terminal,
        browser=browser,
        execution_mode="agent_live",
        product_environment=lambda: {
            "HF_ENDPOINT": "http://127.0.0.1:8765",
            "OPENTRACES_HF_CONTROL_TOKEN": "controller-secret",
        },
        session_factory=lambda _name: session,
        poll_interval=0,
    )

    with pytest.raises(ValueError, match="controller-only environment"):
        drive.attempt(
            harness="claude",
            task="Do one thing.",
            access=[terminal],
            inference="live",
        )

    assert session.start_count == 0
    assert not any((draft.path / "actions").iterdir())
    retained = b"".join(path.read_bytes() for path in draft.path.rglob("*") if path.is_file())
    assert b"controller-secret" not in retained


def test_agent_receives_only_product_facing_emulator_environment(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    draft = RunStore(tmp_path / "runs" / "v1").begin()
    terminal = object()
    drive = AgentDrive(
        box=FakeBoxRuntime().lease(),
        draft=draft,
        actions=RunActionSequence(draft=draft, run_started_monotonic=time.monotonic()),
        terminal=terminal,
        browser=object(),
        execution_mode="agent_live",
        product_environment=lambda: {
            "HF_ENDPOINT": "http://127.0.0.1:8765",
            "HF_TOKEN": "product-credential",
        },
        session_factory=lambda _name: session,
        poll_interval=0,
    )

    attempt = drive.attempt(
        harness="claude",
        task="Inspect the configured Hub identity.",
        access=[terminal],
        inference="live",
    )

    assert attempt.completed is True
    assert session.started_argv is not None
    assert session.started_env == {
        "ANTHROPIC_API_KEY": "unit-live-key-not-a-real-credential",
        "HF_ENDPOINT": "http://127.0.0.1:8765",
        "HF_TOKEN": "product-credential",
    }
    assert not any("product-credential" in argument for argument in session.started_argv)
    remote = _harness_argv(session.started_argv)
    assert remote[0] == "/usr/bin/sudo"
    assert "--preserve-env=ANTHROPIC_API_KEY,HF_ENDPOINT,HF_TOKEN" in remote
    invocation = (draft.path / "actions/0001/invocation.json").read_text(encoding="utf-8")
    artifact = (draft.path / attempt.artifact_ref).read_text(encoding="utf-8")
    assert "product-credential" not in invocation
    assert "product-credential" not in artifact
    assert "OPENTRACES_HF_CONTROL_TOKEN" not in invocation
    assert "OPENTRACES_HF_CONTROL_TOKEN" not in artifact


def test_unknown_harness_is_refused_by_the_closed_registry_before_spawn(
    tmp_path: Path,
) -> None:
    session = CompletingHarnessSession()
    bench = _bench(tmp_path, session)

    with bench.run(app_state="install-only", execution_mode="agent_live") as run:
        with pytest.raises(ValueError, match="unknown agent harness.*codex"):
            run.agent.attempt(
                harness="codex",
                task="Complete one task.",
                access=[run.terminal],
                inference="live",
            )
        run.verify(verify_passes)

    assert session.start_count == 0


def test_scan_reads_evidence_in_bounded_chunks_not_one_whole_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Peak scan read size must be bounded independently of file size (#334)."""

    root = tmp_path / "run"
    (root / "artifacts").mkdir(parents=True)
    size = (1 << 20) * 2 + 4096  # ~2 MiB regular evidence file
    (root / "artifacts" / "large-capture.bin").write_bytes(b"\x00" * size)

    max_single_read = {"n": 0}
    real_fdopen = arena_engine.os.fdopen

    class _ReadSizeSpy:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        def read(self, amount: int = -1) -> bytes:
            data = self._handle.read(amount)
            max_single_read["n"] = max(max_single_read["n"], len(data))
            return data

        def __enter__(self) -> "_ReadSizeSpy":
            return self

        def __exit__(self, *exc: object) -> object:
            return self._handle.__exit__(*exc)

    monkeypatch.setattr(
        arena_engine.os,
        "fdopen",
        lambda *args, **kwargs: _ReadSizeSpy(real_fdopen(*args, **kwargs)),
    )

    matches, files_checked, bytes_checked, _ = arena_engine._scan_sensitive_tree(
        root, (b"absent-live-credential-not-a-real-secret",)
    )

    assert matches == []
    assert files_checked == 1
    assert bytes_checked == size
    # Whole-file read returns the entire 2 MiB in one call; the bounded scanner
    # never reads more than one chunk (1 MiB) at a time.
    assert max_single_read["n"] <= 1 << 20


_STRADDLE_SECRET = b"straddle-live-credential-not-a-real-secret"


@pytest.mark.parametrize(
    "bytes_in_previous_chunk",
    [
        # One byte before the boundary (rest of the secret in the next chunk).
        1,
        # Mid split.
        10,
        # All but ONE byte before the boundary: only a full credential_length-1
        # overlap retains the secret's first byte in the carry, so this exact
        # split kills the off-by-one (L-1 -> L-2) overlap mutant.
        len(_STRADDLE_SECRET) - 1,
    ],
    ids=["one-byte-before", "mid-split", "one-byte-after"],
)
def test_scan_detects_credential_straddling_a_chunk_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bytes_in_previous_chunk: int,
) -> None:
    """A credential split across two scan chunks must still be caught (#334)."""

    root = tmp_path / "run"
    (root / "artifacts").mkdir(parents=True)
    secret = _STRADDLE_SECRET
    monkeypatch.setattr(arena_engine, "_SCAN_CHUNK_SIZE", 64, raising=False)
    boundary = 64
    prefix = b"A" * (boundary - bytes_in_previous_chunk)
    body = prefix + secret + b"B" * 200
    (root / "artifacts" / "evidence.bin").write_bytes(body)

    matches, files_checked, bytes_checked, _ = arena_engine._scan_sensitive_tree(
        root, (secret,)
    )

    assert matches == ["artifacts/evidence.bin"]
    assert files_checked == 1
    assert bytes_checked == len(body)


def test_contaminated_staging_is_purged_when_first_deletion_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recoverable first-deletion failure must still purge the leaked bytes (#335)."""

    sentinel = "purge-retry-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = UndeletableCredentialHarnessSession(sentinel)
    bench = _bench(tmp_path, session)
    runs_root = tmp_path / "runs"

    try:
        with contextlib.suppress(Exception):
            with bench.run(app_state="install-only", execution_mode="agent_live") as run:
                attempt = run.agent.attempt(
                    harness="claude",
                    task="Make the requested world-state change.",
                    access=[run.terminal],
                    inference="live",
                )
                run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

        remaining = []
        for path in runs_root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if sentinel.encode() in path.read_bytes():
                    remaining.append(path.relative_to(runs_root).as_posix())
            except OSError:
                continue
        assert remaining == []
    finally:
        # Restore traversal so pytest can tear the tmp tree down regardless of outcome.
        if session.locked_dir is not None and session.locked_dir.exists():
            os.chmod(session.locked_dir, 0o700)


def test_unpurgeable_contaminated_staging_is_named_without_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecoverable purge is named for an operator, never published (#335)."""

    sentinel = "unpurgeable-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = EchoingCredentialHarnessSession(sentinel)
    bench = _bench(tmp_path, session)
    runs_root = tmp_path / "runs" / "v1"

    def _always_fail(_self: object) -> None:
        raise OSError("injected: staging deletion denied")

    monkeypatch.setattr(arena_engine.RunDraft, "rebuild_empty", _always_fail)

    with pytest.raises(OSError, match="injected"):
        with bench.run(app_state="install-only", execution_mode="agent_live") as run:
            attempt = run.agent.attempt(
                harness="claude",
                task="Make the requested world-state change.",
                access=[run.terminal],
                inference="live",
            )
            run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    markers = list((runs_root / ".cleanup-required").glob("*.json"))
    assert len(markers) == 1
    marker_bytes = markers[0].read_bytes()
    assert sentinel.encode() not in marker_bytes
    marker = json.loads(marker_bytes)
    assert marker["reason"] == "contaminated_staging_purge_failed"
    assert marker["run_id"] in marker["contaminated_staging_path"]
    assert ".staging" in marker["contaminated_staging_path"]
    # The contaminated draft is never finalized or transaction-indexed.
    assert not list(runs_root.glob("run_*/result.json"))
    assert not list((runs_root / ".index").glob("*.json"))
    assert not list((runs_root / ".transactions").glob("*"))


def test_permission_repair_never_chmods_through_a_swapped_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding 2 (#335): the repair fallback must never follow a symlink.

    A generic OSError from the no-follow chmod must not route into a plain
    (link-following) chmod: if the entry was swapped for a symlink after the
    lstat, the fallback would chmod the link TARGET.
    """

    victim = tmp_path / "victim"
    victim.write_text("outside the repair tree\n", encoding="utf-8")
    os.chmod(victim, 0o400)
    root = tmp_path / "repair-root"
    root.mkdir()
    entry = root / "entry"
    entry.write_text("swap me\n", encoding="utf-8")
    os.chmod(entry, 0o644)

    real_chmod = os.chmod

    def swapping_chmod(path, mode, *args, **kwargs):
        if kwargs.get("follow_symlinks", True) is False and Path(path) == entry:
            # Simulate the entry being swapped for a symlink after the lstat,
            # with the no-follow chmod denied by a generic OSError.
            entry.unlink()
            entry.symlink_to(victim)
            raise OSError("injected: no-follow chmod denied")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(arena_engine.os, "chmod", swapping_chmod)

    arena_engine._repair_tree_permissions_no_follow(root)

    assert stat.S_IMODE(victim.stat().st_mode) == 0o400, (
        "permission repair chmod-ed through a swapped symlink"
    )


def test_permission_repair_enqueue_is_bounded_by_the_entry_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding 3 (#335): one scandir loop must not enqueue past the budget."""

    monkeypatch.setattr(arena_engine, "_MAX_PURGE_REPAIR_ENTRIES", 2)
    root = tmp_path / "repair-root"
    root.mkdir()
    for index in range(50):
        (root / f"child-{index:03d}").write_text("x\n", encoding="utf-8")

    consumed = {"n": 0}
    real_scandir = os.scandir

    class _CountingScandir:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __enter__(self) -> "_CountingScandir":
            self._iterator = self._inner.__enter__()
            return self

        def __exit__(self, *exc: object) -> object:
            return self._inner.__exit__(*exc)

        def __iter__(self):
            for item in self._iterator:
                consumed["n"] += 1
                yield item

    monkeypatch.setattr(
        arena_engine.os,
        "scandir",
        lambda path: _CountingScandir(real_scandir(path)),
    )

    arena_engine._repair_tree_permissions_no_follow(root)

    # With a budget of 2, the walk may enqueue at most up to the budget (plus the
    # one entry that observes the exhausted budget); it must not consume all 50.
    assert consumed["n"] <= 3, f"unbounded enqueue: consumed {consumed['n']} entries"


def test_cleanup_marker_is_credential_free_even_when_store_path_carries_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding 4 (#335): the marker must be unconditionally credential-free."""

    sentinel = "marker-path-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = EchoingCredentialHarnessSession(sentinel)
    store_root = tmp_path / f"team-{sentinel}" / "runs" / "v1"
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(store_root),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
        agent_session_factory=lambda _name: session,
        agent_poll_interval=0,
    )

    def _always_fail(_self: object) -> None:
        raise OSError("injected: staging deletion denied")

    monkeypatch.setattr(arena_engine.RunDraft, "rebuild_empty", _always_fail)

    with pytest.raises(OSError, match="injected"):
        with bench.run(app_state="install-only", execution_mode="agent_live") as run:
            attempt = run.agent.attempt(
                harness="claude",
                task="Make the requested world-state change.",
                access=[run.terminal],
                inference="live",
            )
            run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    markers = list((store_root / ".cleanup-required").glob("*.json"))
    assert len(markers) == 1
    marker_bytes = markers[0].read_bytes()
    assert sentinel.encode() not in marker_bytes, (
        "cleanup-required marker carried live credential bytes"
    )
    marker = json.loads(marker_bytes)
    assert marker["reason"] == "contaminated_staging_purge_failed"


def test_purge_reraises_the_original_deletion_error_not_the_final_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding 5 (#335): the FIRST deletion exception is the primary error."""

    sentinel = "original-error-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = EchoingCredentialHarnessSession(sentinel)
    bench = _bench(tmp_path, session)

    calls = {"n": 0}

    def _fail_distinct(_self: object) -> None:
        calls["n"] += 1
        raise OSError(f"denial-{calls['n']}")

    monkeypatch.setattr(arena_engine.RunDraft, "rebuild_empty", _fail_distinct)

    with pytest.raises(OSError, match="denial-1"):
        with bench.run(app_state="install-only", execution_mode="agent_live") as run:
            attempt = run.agent.attempt(
                harness="claude",
                task="Make the requested world-state change.",
                access=[run.terminal],
                inference="live",
            )
            run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)

    assert calls["n"] >= 2, "the purge did not retry the deletion"


def test_marker_write_failure_never_masks_the_primary_purge_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding 5 (#335): marker IO failure must not replace the purge error."""

    sentinel = "marker-masking-sentinel-not-a-real-credential"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    session = EchoingCredentialHarnessSession(sentinel)
    bench = _bench(tmp_path, session)
    runs_root = tmp_path / "runs" / "v1"
    runs_root.mkdir(parents=True, exist_ok=True)
    # Occupy the marker directory path with a FILE so the marker write fails.
    (runs_root / ".cleanup-required").write_text("occupied\n", encoding="utf-8")

    def _always_fail(_self: object) -> None:
        raise OSError("primary-denial")

    monkeypatch.setattr(arena_engine.RunDraft, "rebuild_empty", _always_fail)

    with pytest.raises(OSError, match="primary-denial"):
        with bench.run(app_state="install-only", execution_mode="agent_live") as run:
            attempt = run.agent.attempt(
                harness="claude",
                task="Make the requested world-state change.",
                access=[run.terminal],
                inference="live",
            )
            run.verify(verify_returns_evidence_ref, evidence_ref=attempt.artifact_ref)
