from __future__ import annotations

import json
from pathlib import Path

from opentraces import __version__
from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.engine import Bench, ScenarioSource, extract_claim
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.fixtures.verifier_helper import assert_healthy_payload


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


class ReleaseFailingRuntime(FakeBoxRuntime):
    def release(self, box: Box) -> None:
        raise RuntimeError("release boom")


class DiagnosticRuntime(FakeBoxRuntime):
    image = "ubuntu:24.04"
    crabbox_version = "0.38.0"

    def diagnostic_records(self) -> list[dict]:
        return [
            {
                "operation": "warmup",
                "returncode": 0,
                "stdout": "ready\n",
                "stderr": "",
            }
        ]


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


def test_terminal_action_honors_the_remote_cwd_that_it_records(tmp_path: Path) -> None:
    runtime = FakeBoxRuntime()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    def command_runs_in_requested_directory(run):
        observed = run.terminal.exec("pwd", cwd="/tmp")
        assert observed.returncode == 0

    with bench.run(app_state="install-only") as run:
        run.verify(command_runs_in_requested_directory)

    invocation = json.loads((run.final_path / "actions/0001/invocation.json").read_text())
    assert invocation["cwd"] == "/tmp"
    assert "cd /tmp" in " ".join(runtime.commands[0])


def test_release_failure_is_diagnostic_and_does_not_hide_a_passing_verdict(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=ReleaseFailingRuntime(),
        repository_path=tmp_path,
    )

    def condition_holds(run):
        return {"evidence_refs": []}

    with bench.run(app_state="install-only") as run:
        run.verify(condition_holds)

    assert run.result["execution_status"] == "complete"
    assert run.result["verdict"] == "pass"
    diagnostic = next(
        item for item in run.result["artifacts"] if item["kind"] == "lifecycle_diagnostics"
    )
    payload = json.loads((run.final_path / diagnostic["path"]).read_text())
    assert payload["events"][-1]["code"] == "release_failed"
    assert "release boom" in payload["events"][-1]["message"]


def test_result_pins_observer_crabbox_image_and_product_state(tmp_path: Path) -> None:
    runtime = DiagnosticRuntime()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    def condition_holds(run):
        return {"evidence_refs": []}

    with bench.run(app_state="install-only") as run:
        run.verify(condition_holds)

    assert run.result["pins"]["observer"] == {
        "package": "opentraces",
        "version": __version__,
    }
    assert run.result["pins"]["environment"] == {
        "provider": "local-container",
        "image": "ubuntu:24.04",
        "sandbox_tier": "container",
        "runtime": {"name": "crabbox", "version": "0.38.0"},
    }
    assert run.result["pins"]["product"]["commit"] == "abc123"
    assert run.result["pins"]["app_state"]["digest"] == "sha256:app-state"


def test_box_lifecycle_diagnostics_are_part_of_the_finalized_exhaust(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=DiagnosticRuntime(),
        repository_path=tmp_path,
    )

    def condition_holds(run):
        return {"evidence_refs": []}

    with bench.run(app_state="install-only") as run:
        run.verify(condition_holds)

    artifact = next(
        item for item in run.result["artifacts"] if item["kind"] == "lifecycle_diagnostics"
    )
    assert artifact == {
        "path": "artifacts/box-lifecycle.json",
        "media_type": "application/json",
        "kind": "lifecycle_diagnostics",
    }
    payload = json.loads((run.final_path / artifact["path"]).read_text())
    assert payload["events"][0]["operation"] == "warmup"
    assert payload["events"][0]["stdout"] == "ready\n"


def test_verifier_source_manifest_records_a_direct_imported_helper(tmp_path: Path) -> None:
    runtime = FakeBoxRuntime()
    repository = Path(__file__).resolve().parents[3]
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=repository,
    )

    def doctor_is_healthy(run):
        observed = run.terminal.exec("opentraces", "doctor", "--json")
        assert_healthy_payload(observed.json)
        return {"evidence_refs": [observed.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(doctor_is_healthy)

    manifest = json.loads((run.final_path / "source" / "verifiers.json").read_text())
    sources = {item["path"]: item["digest"] for item in manifest["sources"]}
    assert set(sources) == {
        "tests/core/arena/test_engine.py",
        "tests/core/arena/fixtures/verifier_helper.py",
    }
    assert sources["tests/core/arena/fixtures/verifier_helper.py"] == (
        "sha256:dd2cbc2ba78532dc9258b96fb0aaebaa42e41650ff85c53077680f8184ce9a1e"
    )


def test_absolute_evidence_ref_inside_run_is_stored_as_a_persisted_relative_ref(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    def verifier(run):
        run.draft.write_text("artifacts/proof.txt", "persisted proof\n")
        return {"evidence_refs": [str(run.draft.path / "artifacts/proof.txt")]}

    with bench.run(app_state="install-only") as run:
        run.verify(verifier)

    assert run.result["verdict"] == "pass"
    assert run.result["verifiers"][0]["evidence_refs"] == ["artifacts/proof.txt"]
    assert (run.final_path / "artifacts/proof.txt").is_file()


def test_evidence_ref_outside_run_is_rejected_without_leaking_host_path(tmp_path: Path) -> None:
    host_file = tmp_path / "private" / "host-secret.txt"
    host_file.parent.mkdir()
    host_file.write_text("secret\n")
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    def verifier(run):
        return {"evidence_refs": [str(host_file)]}

    with bench.run(app_state="install-only") as run:
        run.verify(verifier)

    serialized = json.dumps(run.result)
    assert run.result["execution_status"] == "error"
    assert run.result["verdict"] is None
    assert run.result["verifiers"][0]["evidence_refs"] == []
    assert run.result["verifiers"][0]["reason"]["code"] == "invalid_evidence_ref"
    assert str(host_file) not in serialized


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


def test_attempt_without_a_called_verifier_cannot_claim_a_pass(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    with bench.run(app_state="install-only") as run:
        run.terminal.exec("opentraces", "doctor", "--json")

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["execution_status"] == "error"
    assert result["verdict"] is None
    assert result["reason"]["code"] == "no_verifiers_called"
    assert result["evidence"] == {
        "complete": False,
        "requirements": [
            {
                "name": "bench.adjudication",
                "complete": False,
                "evidence_refs": [],
            }
        ],
    }


def test_missing_cast_is_not_rewatchable_and_does_not_rewrite_pass(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    def command_succeeds(run):
        observed = run.terminal.exec("true")
        assert observed.returncode == 0

    with bench.run(app_state="install-only") as run:
        run.verify(command_succeeds)

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

    def command_succeeds(run):
        observed = run.terminal.exec("printf", "ok")
        assert observed.returncode == 0

    with bench.run(app_state="install-only") as run:
        run.verify(command_succeeds)

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
