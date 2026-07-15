from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces.core.arena.agent import AgentDrive
from opentraces.core.arena.drives.agent import AgentTerminalObservation
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.test_engine import FakeBoxRuntime, _scenario


class CompletingHarnessSession:
    """One real-session boundary double with a retained recording."""

    def __init__(self) -> None:
        self.started_argv: list[str] | None = None
        self.prompts: list[str] = []
        self.start_count = 0

    def start(
        self,
        argv: list[str],
        *,
        recording_path: Path,
        cols: int,
        rows: int,
    ) -> None:
        self.start_count += 1
        self.started_argv = list(argv)
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        recording_path.write_bytes(b"agent recording")

    def send(self, text: str) -> None:
        self.prompts.append(text)

    def observe(self) -> AgentTerminalObservation:
        return AgentTerminalObservation(
            state="running",
            screen=(
                "OPENTRACES_HARNESS_VERSION=2.1.143\n"
                "OPENTRACES_AGENT_ATTEMPT_COMPLETE"
            ),
            logs="",
        )

    def stop(self) -> None:
        return None


def _bench(tmp_path: Path, session: CompletingHarnessSession) -> Bench:
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
    assert remote[sudo : sudo + 5] == [
        "/usr/bin/sudo",
        "-n",
        "-u",
        "opentraces-product",
        "--",
    ]
    assert "--allowedTools" in remote
    assert "Bash" in remote
    assert "--disallowedTools" in remote
    assert "mcp__playwright__*" in remote

    attempt_record = json.loads(
        (run.final_path / "artifacts/agent-attempt.json").read_text(encoding="utf-8")
    )
    assert attempt_record == {
        "schema_version": "opentraces.bench.agent-attempt.v0",
        "task": "Open the browser and report its title.",
        "granted_surfaces": ["terminal"],
        "harness": {
            "name": "claude",
            "executable": "claude",
            "version": "2.1.143",
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
        channel == {
            "kind": "agent_terminal",
            "complete": True,
            "path": "recordings/agent-0001.termctrl",
            "reason": None,
        }
        for channel in run.result["recordings"]["channels"]
    )


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
        actions=None,
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
    assert "controller-secret" not in json.dumps(store.root.as_posix())


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
