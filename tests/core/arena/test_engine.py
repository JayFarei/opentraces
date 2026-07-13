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


class RecordingBoxRuntime(FakeBoxRuntime):
    def collect(self, box, globs, *, destination, repository):
        files = destination / "files"
        files.mkdir(parents=True)
        timing = files / Path(globs[0]).name
        typescript = files / Path(globs[1]).name
        timing.write_text("0.010 4\n", encoding="utf-8")
        typescript.write_bytes(b"ok\r\n")
        return {timing.name: timing, typescript.name: typescript}


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
    external_source = result["verifiers"][0]["source_ref"]["path"]
    assert external_source.startswith("external/")
    assert not Path(external_source).is_absolute()
    assert not any(token in external_source.lower() for token in ("/users/", "/home/", "jayfarei"))
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


def test_missing_cast_is_not_rewatchable_and_does_not_rewrite_pass(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    with bench.run(app_state="install-only") as run:
        run.terminal.exec("true")

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["verdict"] == "pass"
    assert result["recordings"]["rewatchable"] is False
    assert result["recordings"]["channels"][0]["complete"] is False
    assert "cast" in result["recordings"]["channels"][0]["reason"]


def test_each_terminal_action_produces_an_asciicast_playlist_marker(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=RecordingBoxRuntime(),
        repository_path=tmp_path,
    )

    with bench.run(app_state="install-only") as run:
        run.terminal.exec("printf", "ok")

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["verdict"] == "pass"
    assert result["recordings"]["rewatchable"] is True
    marker = result["recordings"]["channels"][0]["casts"][0]
    assert marker["ordinal"] == 1
    assert marker["label"] == "printf ok"
    assert marker["cast_ref"] == "recordings/terminal-0001.cast"
    assert marker["duration_ms"] >= 0
    cast = run.final_path / "recordings" / "terminal-0001.cast"
    assert json.loads(cast.read_text().splitlines()[0])["version"] == 2
