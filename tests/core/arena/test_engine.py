from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from opentraces import __version__
from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.engine import Bench, ScenarioSource, extract_claim
from opentraces.core.arena.page import render_evidence_page
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
            image="ubuntu:24.04",
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

    def exec_product(self, box: Box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        return self.exec(
            box,
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            timing_path=timing_path,
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


class SensitiveReleaseRuntime(FakeBoxRuntime):
    def release(self, box: Box) -> None:
        raise RuntimeError("cleanup token=release-token at /home/private/lease.json")


class SensitiveSetupRuntime(FakeBoxRuntime):
    def lease(self) -> Box:
        raise RuntimeError(
            '{"credential":"setup-credential","path":"/Users/private/setup.json",'
            '"operation":"warmup"}'
        )


_CREDENTIAL_SHAPES = (
    "private_key=private-value "
    "ssh_key: ssh-value "
    "api_token=api-value "
    "OPENAI_API_KEY=openai-value "
    "SERVICE_OPENAI_API_KEY=prefixed-value "
    "Authorization: Bearer bearer-value; "
    "tokenization completed; secret scanner healthy; credential policy loaded"
)


class CredentialShapeRuntime(FakeBoxRuntime):
    def diagnostic_records(self) -> list[dict]:
        return [{"operation": "inspect", "stderr": _CREDENTIAL_SHAPES}]

    def release(self, box: Box) -> None:
        raise RuntimeError(_CREDENTIAL_SHAPES)


class RealShellRecordingRuntime(FakeBoxRuntime):
    def exec(self, box: Box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env={**os.environ, **dict(env or {})},
            timeout=timeout,
            text=True,
            capture_output=True,
            check=False,
        )
        return BoxCommandResult(
            argv=list(argv),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timing={"schemaVersion": 1, "timing": {"exitCode": completed.returncode}},
        )

    def collect(self, box, globs, *, destination, repository):
        files = destination / "files"
        files.mkdir(parents=True)
        collected = {}
        for pattern in globs:
            source = repository / pattern
            if source.is_file():
                target = files / source.name
                shutil.copy2(source, target)
                collected[target.name] = target
        return collected


class DiagnosticRuntime(FakeBoxRuntime):
    # Requested configuration is not an observation. The result must use the
    # image returned by inspect through Box.image, not this value.
    image = "requested-but-not-observed:latest"
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


def test_terminal_timeout_persists_complete_failure_exhaust_and_page_before_propagating(
    tmp_path: Path,
) -> None:
    class TimingOutRuntime(FakeBoxRuntime):
        def exec(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
            timing_path.parent.mkdir(parents=True, exist_ok=True)
            timing_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "timing": {"commandMs": 250, "exitCode": None},
                    }
                ),
                encoding="utf-8",
            )
            raise subprocess.TimeoutExpired(
                cmd=list(argv),
                timeout=timeout,
                output="partial stdout before timeout\n",
                stderr="partial stderr before timeout\n",
            )

    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=TimingOutRuntime(),
        repository_path=tmp_path,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        with bench.run(app_state="install-only") as run:
            run.terminal.exec("slow-command", timeout=0.25)

    final_path = next(bench.store.root.glob("run_*"))
    action = final_path / "actions" / "0001"
    assert (action / "stdout").read_text(encoding="utf-8") == ("partial stdout before timeout\n")
    assert (action / "stderr").read_text(encoding="utf-8") == ("partial stderr before timeout\n")
    assert json.loads((action / "timing.json").read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "timing": {"commandMs": 250, "exitCode": None},
    }
    action_result = json.loads((action / "result.json").read_text(encoding="utf-8"))
    assert action_result == {
        "execution_status": "error",
        "returncode": None,
        "duration_ms": action_result["duration_ms"],
        "stdout_ref": "actions/0001/stdout",
        "stderr_ref": "actions/0001/stderr",
        "timing_ref": "actions/0001/timing.json",
        "reason": {
            "code": "terminal_timeout",
            "message": "terminal command exceeded its 0.25 second timeout",
        },
    }
    assert action_result["duration_ms"] >= 0
    result = json.loads((final_path / "result.json").read_text(encoding="utf-8"))
    assert result["execution_status"] == "error"
    assert result["verdict"] is None
    page = render_evidence_page(final_path)
    page_html = page.read_text(encoding="utf-8")
    assert "rc=None" in page_html
    for relative in (
        "actions/0001/result.json",
        "actions/0001/stdout",
        "actions/0001/stderr",
        "actions/0001/timing.json",
    ):
        assert relative in page_html


def test_terminal_recording_survives_a_real_shell_with_a_non_default_remote_cwd(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    script = fake_bin / "script"
    script.write_text(
        """#!/bin/sh
set -eu
while [ "$#" -gt 0 ]; do
  case "$1" in
    --log-timing) timing=$2; shift 2 ;;
    --log-out) typescript=$2; shift 2 ;;
    --command) command=$2; shift 2 ;;
    *) shift ;;
  esac
done
sh -c "$command" >"$typescript"
bytes=$(wc -c <"$typescript" | tr -d ' ')
printf '0.010 %s\\n' "$bytes" >"$timing"
cat "$typescript"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    remote_cwd = tmp_path / "elsewhere"
    remote_cwd.mkdir()
    runtime = RealShellRecordingRuntime()
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    def command_runs_in_requested_directory(run):
        observed = run.terminal.exec(
            "pwd",
            cwd=str(remote_cwd),
            env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )
        assert observed.stdout.strip() == str(remote_cwd)

    with bench.run(app_state="install-only") as run:
        run.verify(command_runs_in_requested_directory)

    assert run.result["recordings"]["rewatchable"] is True
    assert (run.final_path / "recordings/terminal-0001.cast").is_file()
    assert not (remote_cwd / "bench-recordings").exists()


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


@pytest.mark.parametrize(
    ("outcome", "execution_status", "verdict", "reason_code"),
    [
        ("fail", "complete", "fail", "assertion_failed"),
        ("skip", "complete", "skip", "absent_prerequisite"),
        ("error", "error", None, "machinery_error"),
    ],
)
def test_release_failure_is_always_diagnostic_without_rewriting_primary_outcome(
    tmp_path: Path,
    outcome: str,
    execution_status: str,
    verdict: str | None,
    reason_code: str,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=ReleaseFailingRuntime(),
        repository_path=tmp_path,
    )

    def execute():
        with bench.run(app_state="install-only") as run:
            if outcome == "fail":
                assert False, "functional red"
            if outcome == "skip":
                run.skip("absent_prerequisite", "not installed")
            raise RuntimeError("machinery red")
        return run

    if outcome == "error":
        with pytest.raises(RuntimeError, match="machinery red"):
            execute()
        final_path = next(bench.store.root.glob("run_*"))
    else:
        final_path = execute().final_path

    result = json.loads((final_path / "result.json").read_text(encoding="utf-8"))
    assert result["execution_status"] == execution_status
    assert result["verdict"] == verdict
    assert result["reason"]["code"] == reason_code
    diagnostic = next(
        item for item in result["artifacts"] if item["kind"] == "lifecycle_diagnostics"
    )
    payload = json.loads((final_path / diagnostic["path"]).read_text(encoding="utf-8"))
    assert payload["events"][-1]["code"] == "release_failed"


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


def test_setup_failure_reason_sanitizes_structured_credentials_and_host_paths(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=SensitiveSetupRuntime(),
        repository_path=tmp_path,
    )

    with pytest.raises(RuntimeError, match="setup-credential"):
        with bench.run(app_state="install-only"):
            pass

    final_path = next(bench.store.root.glob("run_*"))
    serialized = (final_path / "result.json").read_text(encoding="utf-8")
    assert "setup-credential" not in serialized
    assert "/Users/private/setup.json" not in serialized
    assert "[redacted]" in serialized
    assert "[host-path]" in serialized
    assert "warmup" in serialized


def test_verifier_and_release_diagnostics_share_secret_and_path_sanitization(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=SensitiveReleaseRuntime(),
        repository_path=tmp_path,
    )

    def verifier_crashes(run):
        raise RuntimeError("verifier secret=verifier-secret at /tmp/private/verifier.json")

    with bench.run(app_state="install-only") as run:
        run.verify(verifier_crashes)

    result_text = (run.final_path / "result.json").read_text(encoding="utf-8")
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (run.final_path / "artifacts").rglob("*")
        if path.is_file()
    )
    serialized = result_text + artifact_text
    assert "verifier-secret" not in serialized
    assert "release-token" not in serialized
    assert "/tmp/private/verifier.json" not in serialized
    assert "/home/private/lease.json" not in serialized
    assert "[redacted]" in serialized
    assert "[host-path]" in serialized
    assert "verifier" in serialized
    assert "cleanup" in serialized


def test_sensitive_assignment_shapes_are_redacted_without_erasing_ordinary_words(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=CredentialShapeRuntime(),
        repository_path=tmp_path,
    )

    def verifier_crashes(run):
        raise RuntimeError(_CREDENTIAL_SHAPES)

    with bench.run(app_state="install-only") as run:
        run.verify(verifier_crashes)

    result_text = (run.final_path / "result.json").read_text(encoding="utf-8")
    diagnostic = next(
        item for item in run.result["artifacts"] if item["kind"] == "lifecycle_diagnostics"
    )
    artifact_text = (run.final_path / diagnostic["path"]).read_text(encoding="utf-8")
    for persisted in (result_text, artifact_text):
        for secret in (
            "private-value",
            "ssh-value",
            "api-value",
            "openai-value",
            "prefixed-value",
            "bearer-value",
        ):
            assert secret not in persisted
        assert "private_key=[redacted]" in persisted
        assert "ssh_key: [redacted]" in persisted
        assert "api_token=[redacted]" in persisted
        assert "OPENAI_API_KEY=[redacted]" in persisted
        assert "SERVICE_OPENAI_API_KEY=[redacted]" in persisted
        assert "Authorization: Bearer [redacted]" in persisted
        assert "tokenization completed" in persisted
        assert "secret scanner healthy" in persisted
        assert "credential policy loaded" in persisted


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
        assert False, (
            "the product condition was false with token=assertion-secret "
            "at /Users/private/assertion.py"
        )

    result = json.loads((run.final_path / "result.json").read_text())
    assert result["execution_status"] == "complete"
    assert result["verdict"] == "fail"
    assert result["reason"]["code"] == "assertion_failed"
    assert result["verifiers"] == []
    artifact = next(item for item in result["artifacts"] if item["kind"] == "assertion_failure")
    observed = json.loads((run.final_path / artifact["path"]).read_text())
    assert observed["type"] == "AssertionError"
    assert observed["location"]["path"]
    assert observed["location"]["line"] > 0
    assert "the product condition was false" in observed["message"]
    assert "AssertionError" in observed["traceback"]
    serialized = json.dumps({"result": result, "artifact": observed})
    assert "assertion-secret" not in serialized
    assert "/Users/private/assertion.py" not in serialized
    assert "[redacted]" in serialized
    assert "[host-path]" in serialized
    assert result["evidence"] == {
        "complete": True,
        "requirements": [
            {
                "name": "scenario.assertion",
                "complete": True,
                "evidence_refs": [artifact["path"]],
            }
        ],
    }


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


def test_named_prerequisite_skip_is_named_in_incomplete_evidence(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    with bench.run(app_state="install-only") as run:
        run.skip("absent_prerequisite", "the named prerequisite is absent")

    assert run.result["execution_status"] == "complete"
    assert run.result["verdict"] == "skip"
    assert run.result["evidence"] == {
        "complete": False,
        "requirements": [
            {
                "name": "absent_prerequisite",
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
