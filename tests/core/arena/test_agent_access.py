from __future__ import annotations

import base64
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping
from urllib.request import Request, urlopen

import pytest

from opentraces.core.arena.agent import AgentDrive
from opentraces.core.arena.drives.actions import RunActionSequence
from opentraces.core.arena.drives.agent import AgentTerminalObservation
from opentraces.core.arena.drives.browser_mcp import BrowserMcpBridge
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.test_engine import FakeBoxRuntime, _scenario
from tests.core.arena.test_browser_drive import PublicBrowserSession


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


def _closed_mcp_config(argv: list[str]) -> dict[str, object]:
    remote = argv[argv.index("--") + 1 :]
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
        run.verify(lambda _run: {"evidence_refs": []})

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
            first_run.verify(lambda _run: {"evidence_refs": []})
            second_run.verify(lambda _run: {"evidence_refs": []})

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
        run.verify(lambda _run: {"evidence_refs": []})

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
        run.verify(lambda _run: {"evidence_refs": [attempt.artifact_ref]})

    assert session.start_count == 1
    assert session.started_argv is not None
    remote = session.started_argv[session.started_argv.index("--") + 1 :]
    assert "/usr/bin/sudo" in remote
    sudo = remote.index("/usr/bin/sudo")
    assert remote[sudo : sudo + 6] == [
        "/usr/bin/sudo",
        "-H",
        "-n",
        "-u",
        "opentraces-product",
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
        run.verify(lambda _run: {"evidence_refs": [attempt.artifact_ref]})

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
        run.verify(lambda _run: {"evidence_refs": [attempt.artifact_ref]})

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
        run.verify(lambda _run: {"evidence_refs": [attempt.artifact_ref]})

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
        run.verify(lambda _run: {"evidence_refs": [attempt.artifact_ref]})

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
        run.verify(lambda _run: {"evidence_refs": [attempt.artifact_ref]})

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
        "HF_ENDPOINT": "http://127.0.0.1:8765",
        "HF_TOKEN": "product-credential",
    }
    assert not any("product-credential" in argument for argument in session.started_argv)
    remote = session.started_argv[session.started_argv.index("--") + 1 :]
    assert remote[0] == "/usr/bin/sudo"
    assert "--preserve-env=HF_ENDPOINT,HF_TOKEN" in remote
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
        run.verify(lambda _run: {"evidence_refs": []})

    assert session.start_count == 0
