from __future__ import annotations

import json
from pathlib import Path

from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.engine import Bench, ScenarioSource, extract_claim
from opentraces.core.arena.run_store import RunStore


class FakeBoxRuntime:
    def __init__(self) -> None:
        self.released = False
        self.commands: list[list[str]] = []

    def lease(self) -> Box:
        return Box(
            id="fake-1",
            slug="fake",
            provider="local-container",
            sandbox_tier="container",
            ssh_host="127.0.0.1",
            ssh_user="crabbox",
            ssh_port="22",
            ssh_key="/tmp/fake",
        )

    def materialize(self, box: Box, app_state: str, *, repository: Path) -> dict:
        return {"name": app_state, "digest": "sha256:app-state", "provides": ["cli"]}

    def exec(self, box: Box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        self.commands.append(list(argv))
        return BoxCommandResult(
            argv=["crabbox", "run", *argv],
            returncode=0,
            stdout='{"healthy":true}\n',
            stderr="",
            timing={"schemaVersion": 1, "timing": {"exitCode": 0}},
        )

    def release(self, box: Box) -> None:
        self.released = True


def _scenario(tmp_path: Path) -> ScenarioSource:
    source = tmp_path / "test_install.py"
    source.write_text(
        'def test_install(bench):\n    """Install is healthy on a fresh box.\n\nDetails."""\n',
        encoding="utf-8",
    )
    return ScenarioSource(
        nodeid="test_install.py::test_install",
        claim="Install is healthy on a fresh box.",
        source_path=source,
        scenario_path="tests/arena/test_install.py",
        repository="JayFarei/opentraces",
        commit="abc123",
        dirty_diff_digest=None,
    )


def test_extract_claim_preserves_the_first_docstring_paragraph_byte_for_byte() -> None:
    def scenario():
        """A user's exact claim — punctuation included.

        More implementation detail follows.
        """

    assert extract_claim(scenario) == "A user's exact claim — punctuation included."


def test_complete_attempt_drives_cli_verifies_and_finalizes(tmp_path: Path) -> None:
    runtime = FakeBoxRuntime()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    def doctor_is_healthy(run):
        observed = run.terminal.exec("opentraces", "doctor", "--json")
        assert observed.json["healthy"] is True
        return {"evidence_refs": [observed.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(doctor_is_healthy)

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["scenario"]["claim"] == "Install is healthy on a fresh box."
    assert result["verdict"] == "pass"
    assert result["execution_status"] == "complete"
    assert result["pins"]["environment"]["sandbox_tier"] == "container"
    assert result["verifiers"][0]["status"] == "pass"
    assert result["verifiers"][0]["source_ref"]["digest"].startswith("sha256:")
    assert (run.final_path / "actions" / "0001" / "stdout").read_text() == '{"healthy":true}\n'
    assert runtime.released is True


def test_assertion_failure_is_a_functional_fail_not_machinery_error(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    with bench.run(app_state="install-only") as run:
        assert False, "the product condition was false"

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["execution_status"] == "complete"
    assert result["verdict"] == "fail"
    assert result["reason"]["code"] == "assertion_failed"
