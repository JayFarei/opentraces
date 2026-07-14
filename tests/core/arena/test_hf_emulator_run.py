from __future__ import annotations

import hashlib
import inspect
import json
import re
from pathlib import Path

import pytest

from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.contract import result_exit_code
from opentraces.core.arena.emulate.huggingface.runtime import (
    EmulatorReadinessError,
    PROVENANCE_SCHEMA,
    app_state_digest,
    emulator_provenance_path,
    emulator_binary_pin,
    start_huggingface_emulator,
    verified_emulator_binary_pin,
)
from opentraces.core.arena.engine import Bench, ScenarioSource
from opentraces.core.arena.page import render_evidence_page
from opentraces.core.arena.run_store import RunStore
from tests.arena.scenarios import test_publish_reaches_hf_remote as scenario_2


_BASE_APP_STATE = {
    "name": "install-only",
    "digest": "sha256:installed-product",
    "provides": ["cli", "script"],
}
_COMMIT_ROW = {
    "method": "POST",
    "operation_id": "commit",
    "path": "/api/datasets/bench/scenario-2/commit/main",
    "request": {"repo_id": "bench/scenario-2", "revision": "main"},
    "response": {"commit_oid": "a" * 40, "status": 200},
}
_MANIFEST = {
    "id": "huggingface",
    "specs": [
        {
            "operations": [
                {"operationId": "commit", "status": "hand-authored"},
                {"operationId": "listDatasets", "status": "partial"},
                {"operationId": "xetUpload", "status": "unsupported"},
            ]
        }
    ],
}


def _write_test_provenance(binary: Path) -> None:
    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    pin = emulator_binary_pin(binary)
    emulator_provenance_path(binary).write_text(
        json.dumps(
            {
                "schema_version": PROVENANCE_SCHEMA,
                **hf_runtime._build_inputs(),
                "build_inputs_sha256": hf_runtime._build_inputs_sha256(),
                "binary_sha256": pin.sha256,
                "size_bytes": pin.size_bytes,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _trust_test_binary(binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    _write_test_provenance(binary)
    provenance = json.loads(hf_runtime.emulator_provenance_path(binary).read_text())
    trusted = binary.parent / "trusted-build.json"
    trusted.write_text(
        json.dumps(
            {
                "schema_version": hf_runtime.TRUSTED_BUILD_SCHEMA,
                "binary_sha256": provenance["binary_sha256"],
                "size_bytes": provenance["size_bytes"],
                "source_sha256": provenance["source_sha256"],
                "build_inputs_sha256": provenance["build_inputs_sha256"],
                "bun_version": provenance["bun_version"],
                "target": provenance["target"],
                "contract_version": provenance["contract_version"],
                "evidence": "test fixture",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(hf_runtime, "TRUSTED_BUILD_MANIFEST", trusted)


class WorldRuntime:
    """Small black-box runtime double for the one supported world sidecar."""

    crabbox_version = "0.38.0"

    def __init__(self, raw_ledger: bytes = b"") -> None:
        self.raw_ledger = raw_ledger
        self.live = False
        self.process_alive = True
        self.manifest_override: dict | None = None
        self.bound_manifest: dict | None = None
        self.events: list[str] = []
        self.copied: tuple[Path, str] | None = None
        self.stop_command: str | None = None

    def lease(self) -> Box:
        return Box(
            id="box-1",
            slug="box-1",
            provider="local-container",
            sandbox_tier="container",
            ssh_host="127.0.0.1",
            ssh_user="crabbox",
            ssh_port="22",
            ssh_key="/tmp/key",
            image="ubuntu:24.04",
        )

    def materialize(self, box: Box, app_state: str, *, repository: Path) -> dict:
        assert app_state == "install-only"
        return {**_BASE_APP_STATE, "provides": list(_BASE_APP_STATE["provides"])}

    def copy_into_box(
        self,
        box: Box,
        source: Path,
        destination: str,
        *,
        timeout: float = 120,
    ) -> str:
        self.events.append("copy")
        self.copied = (Path(source), destination)
        return destination

    def exec(self, box: Box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        rendered = " ".join(map(str, argv))
        if "/proc/$pid/exe" in rendered:
            self.events.append("process-binding")
            stdout, returncode = (
                (json.dumps({"pid": 4242, "user": "opentraces-hf"}) + "\n", 0)
                if self.process_alive
                else ("", 1)
            )
        elif "sha256sum" in rendered:
            assert self.copied is not None
            self.events.append("sha256")
            digest = hashlib.sha256(self.copied[0].read_bytes()).hexdigest()
            stdout, returncode = f"{digest}  {self.copied[1]}\n", 0
        elif "test ! -s" in rendered:
            self.events.append("custody-unchanged")
            stdout, returncode = "", 0 if not self.raw_ledger else 1
        elif "_emulate/manifest" in rendered:
            self.events.append("readiness")
            manifest = self.manifest_override or self.bound_manifest or _MANIFEST
            stdout, returncode = (json.dumps(manifest) + "\n", 0) if self.live else ("", 7)
        elif "_emulate/ledger" in rendered:
            self.events.append("snapshot")
            stdout, returncode = (self.raw_ledger.decode(), 0) if self.live else ("", 7)
        elif "setsid" in rendered or "hf-emulator" in rendered and "kill" not in rendered:
            self.live = True
            self.events.append("start")
            fields = {
                name: match.group(1)
                for name in (
                    "OPENTRACES_HF_LAUNCH_NONCE",
                    "OPENTRACES_HF_CONTRACT_VERSION",
                    "OPENTRACES_HF_SOURCE_SHA256",
                    "OPENTRACES_HF_BUILD_INPUTS_SHA256",
                    "OPENTRACES_HF_BINARY_SHA256",
                )
                if (match := re.search(rf"{name}=([^ ]+)", rendered)) is not None
            }
            if len(fields) == 5:
                self.bound_manifest = {
                    **_MANIFEST,
                    "launch": {
                        "nonce": fields["OPENTRACES_HF_LAUNCH_NONCE"],
                        "pid": 4242,
                        "contract_version": fields["OPENTRACES_HF_CONTRACT_VERSION"],
                        "source_sha256": fields["OPENTRACES_HF_SOURCE_SHA256"],
                        "build_inputs_sha256": fields["OPENTRACES_HF_BUILD_INPUTS_SHA256"],
                        "binary_sha256": fields["OPENTRACES_HF_BINARY_SHA256"],
                    },
                }
            stdout, returncode = "", 0
        elif "kill" in rendered:
            self.events.append("stop")
            self.stop_command = rendered
            # Model the exact defect class from the A2 review: a best-effort
            # signal with no wait/death proof may leave the sidecar serving.
            # Only the strict stop contract makes the world unavailable and
            # takes its final ledger snapshot after that fact is established.
            if "kill -0" in rendered:
                self.live = False
                self.events.append("snapshot")
                stdout, returncode = self.raw_ledger.decode(), 0
            else:
                stdout, returncode = "", 0
        elif "dataset publish" in rendered:
            if self.live:
                self.raw_ledger = (json.dumps(_COMMIT_ROW, separators=(",", ":")) + "\n").encode()
                stdout, returncode = '{"published":true}\n', 0
            else:
                stdout, returncode = "", 1
        elif "CUSTODY_PROBE" in rendered:
            self.events.append("custody-probe")
            stdout, returncode = "", 0
        elif "FORGED_LEDGER_ROW" in rendered:
            self.events.append("product-ledger-mutation")
            stdout, returncode = "", 1
        else:
            stdout, returncode = "{}\n", 0
        return BoxCommandResult(
            argv=list(map(str, argv)),
            returncode=returncode,
            stdout=stdout,
            stderr="emulator unavailable\n" if returncode else "",
            timing={"schemaVersion": 1, "timing": {"exitCode": returncode}},
        )

    def exec_product(self, box: Box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        rendered = " ".join(map(str, argv))
        self.events.append("product-exec")
        if "CUSTODY_PROBE" in rendered:
            self.events.append("product-custody-probe")
            return BoxCommandResult(
                argv=list(map(str, argv)),
                returncode=0,
                stdout="",
                stderr="",
                timing={"schemaVersion": 1, "timing": {"exitCode": 0}},
            )
        if "/var/lib/opentraces-bench/huggingface.jsonl" in rendered and (
            "sudo" in rendered or "su " in rendered or "FORGED_LEDGER_ROW" in rendered
        ):
            self.events.append("product-ledger-escalation-refused")
            return BoxCommandResult(
                argv=list(map(str, argv)),
                returncode=1,
                stdout="",
                stderr="permission denied\n",
                timing={"schemaVersion": 1, "timing": {"exitCode": 1}},
            )
        return self.exec(
            box,
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            timing_path=timing_path,
        )

    def release(self, box: Box) -> None:
        self.events.append("release")


def _source() -> ScenarioSource:
    path = Path(inspect.getsourcefile(scenario_2.test_publish_reaches_hf_remote) or "")
    return ScenarioSource(
        nodeid=f"{path.as_posix()}::test_publish_reaches_hf_remote",
        claim="Publishing a dataset reaches the configured Hugging Face remote.",
        source_path=path,
        scenario_path="tests/arena/scenarios/test_publish_reaches_hf_remote.py",
        repository="JayFarei/opentraces",
        commit="product-commit",
        dirty_diff_digest=None,
    )


def _bench(tmp_path: Path, runtime: WorldRuntime) -> Bench:
    return Bench(
        source=_source(),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=Path(__file__).resolve().parents[3],
    )


def test_scenario_2_source_freezes_the_claim_public_journey_and_down_control() -> None:
    function = scenario_2.test_publish_reaches_hf_remote
    assert inspect.getdoc(function).split("\n\n", 1)[0] == (
        "Publishing a dataset reaches the configured Hugging Face remote."
    )
    source = inspect.getsource(function)
    for public_call in (
        'run.emulate("huggingface")',
        '"dataset",\n            "new"',
        '"dataset",\n            "review",\n            "approve"',
        '"dataset",\n            "remote",\n            "create"',
        '"dataset",\n            "publish"',
        "env=hf.env",
        'os.environ.get("OT_BENCH_HF_DOWN_CONTROL")',
        "hf.stop()",
    ):
        assert public_call in source


def test_run_emulate_pins_collects_and_projects_the_huggingface_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = WorldRuntime()
    bench = _bench(tmp_path, runtime)

    with bench.run(app_state="install-only") as run:
        hf = run.emulate("huggingface")
        assert hf.env == {
            "HF_ENDPOINT": "http://127.0.0.1:14318",
            "HF_TOKEN": hf.env["HF_TOKEN"],
        }
        assert hf.env["HF_TOKEN"].startswith("hf_")
        runtime.raw_ledger = (json.dumps(_COMMIT_ROW, separators=(",", ":")) + "\n").encode()
        forged = run.terminal.exec(
            "sh",
            "-c",
            "printf FORGED_LEDGER_ROW > /var/lib/opentraces-bench/huggingface.jsonl",
            env=hf.env,
        )
        assert forged.returncode != 0
        run.verify(scenario_2.publish_commit_is_witnessed, hf=hf)

    expected_binary_pin = verified_emulator_binary_pin(binary).to_dict()
    result = json.loads((run.final_path / "result.json").read_text(encoding="utf-8"))
    stored_ledger = run.final_path / "ledgers" / "huggingface.jsonl"
    assert stored_ledger.read_bytes() == runtime.raw_ledger
    assert result["verdict"] == "pass"
    assert result["verifiers"][0]["evidence_refs"] == ["ledgers/huggingface.jsonl"]
    emulator_pin = result["pins"]["emulators"]["huggingface"]
    assert {key: emulator_pin[key] for key in expected_binary_pin} == expected_binary_pin
    assert emulator_pin["evidence_ref"] == "world/huggingface.json"
    assert emulator_pin["setup"]["endpoint"] == "http://127.0.0.1:14318"
    assert emulator_pin["setup"]["baseline"]["identity"] == {
        "name": "bench",
        "type": "user",
    }
    assert emulator_pin["setup"]["ledger_custody"]["product_writable"] is False
    assert result["pins"]["app_state"] == {
        "name": "install-only",
        "digest": app_state_digest(
            _BASE_APP_STATE,
            hf_emulator=verified_emulator_binary_pin(binary),
        ),
        "provides": ["cli", "script", "hf-emulator"],
        "emulators": {"huggingface": expected_binary_pin},
        "recipe": {
            "base": _BASE_APP_STATE,
            "emulators": {"huggingface": expected_binary_pin},
        },
    }
    assert runtime.copied == (binary, "/opt/bench/emulators/opentraces-hf-emulator")
    assert runtime.events.index("copy") < runtime.events.index("sha256")
    assert runtime.events.index("sha256") < runtime.events.index("start")
    assert runtime.events.index("start") < runtime.events.index("readiness")
    assert runtime.events.index("snapshot") < runtime.events.index("release")
    assert runtime.events.index("stop") < runtime.events.index("release")
    assert max(
        index for index, event in enumerate(runtime.events) if event == "snapshot"
    ) > runtime.events.index("stop")
    assert runtime.stop_command is not None
    assert "kill -0" in runtime.stop_command
    assert runtime.events.count("snapshot") == 2
    assert runtime.events.count("stop") == 1
    assert runtime.events.count("product-ledger-mutation") == 0
    assert runtime.events.count("product-ledger-escalation-refused") == 1
    assert runtime.events.count("custody-probe") == 0
    assert runtime.events.count("product-custody-probe") == 1
    assert runtime.events.count("custody-unchanged") == 1
    assert runtime.events.count("process-binding") == 1
    assert b"FORGED_LEDGER_ROW" not in stored_ledger.read_bytes()

    page = render_evidence_page(run.final_path)
    frozen_page = page.read_text(encoding="utf-8")
    assert "World" in frozen_page
    assert "huggingface" in frozen_page
    assert expected_binary_pin["sha256"] in frozen_page
    assert "ledgers/huggingface.jsonl" in frozen_page
    assert "http://127.0.0.1:14318" in frozen_page
    assert "baseline identity: bench" in frozen_page
    assert "hand-authored" in frozen_page
    assert "unsupported" in frozen_page
    assert "world/huggingface.json" in frozen_page


def test_startup_rejects_stale_fixed_port_listener_when_new_child_is_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = WorldRuntime()
    runtime.process_alive = False
    runtime.manifest_override = {
        **_MANIFEST,
        "launch": {
            "nonce": "stale-launch",
            "pid": 111,
            "contract_version": "huggingface.v1",
            "source_sha256": "stale-source",
            "build_inputs_sha256": "stale-inputs",
            "binary_sha256": "stale-binary",
        },
    }

    with pytest.raises(EmulatorReadinessError, match="launch identity"):
        start_huggingface_emulator(
            runtime=runtime,
            box=runtime.lease(),
            repository=tmp_path,
            run_path=tmp_path / "run",
        )


def test_startup_rejects_dead_recorded_process_even_with_current_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = WorldRuntime()
    runtime.process_alive = False

    with pytest.raises(EmulatorReadinessError, match="running process"):
        start_huggingface_emulator(
            runtime=runtime,
            box=runtime.lease(),
            repository=tmp_path,
            run_path=tmp_path / "run",
        )


def test_startup_liveness_probe_does_not_signal_differently_owned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Crabbox login user may inspect, but cannot signal, the sidecar user."""

    class DifferentlyOwnedProcessRuntime(WorldRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.binding_commands: list[str] = []

        def exec(self, box: Box, argv, *, cwd=None, env=None, timeout=60, timing_path):
            rendered = " ".join(map(str, argv))
            if "/proc/$pid/exe" in rendered:
                self.binding_commands.append(rendered)
                if "kill -0" in rendered:
                    return BoxCommandResult(
                        argv=list(map(str, argv)),
                        returncode=1,
                        stdout="",
                        stderr="sh: 1: kill: Operation not permitted\n",
                        timing={"schemaVersion": 1, "timing": {"exitCode": 1}},
                    )
                privileged_reads = (
                    'sudo -u opentraces-hf test -r "/proc/$pid/stat"',
                    "state=$(sudo -u opentraces-hf sed",
                    "owner_uid=$(sudo -u opentraces-hf awk",
                    "executable=$(sudo -u opentraces-hf readlink",
                    "digest=$(sudo -u opentraces-hf sha256sum",
                )
                if any(fragment not in rendered for fragment in privileged_reads):
                    return BoxCommandResult(
                        argv=list(map(str, argv)),
                        returncode=1,
                        stdout="",
                        stderr="readlink: /proc/4242/exe: Permission denied\n",
                        timing={"schemaVersion": 1, "timing": {"exitCode": 1}},
                    )
            return super().exec(
                box,
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
                timing_path=timing_path,
            )

    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = DifferentlyOwnedProcessRuntime()

    handle = start_huggingface_emulator(
        runtime=runtime,
        box=runtime.lease(),
        repository=tmp_path,
        run_path=tmp_path / "run",
    )

    assert handle.world_setup["readiness"]["process_binding"]["pid"] == 4242
    assert len(runtime.binding_commands) == 1
    assert "kill -0" not in runtime.binding_commands[0]
    assert "/proc/$pid/stat" in runtime.binding_commands[0]


def test_configured_binary_requires_matching_build_provenance(
    tmp_path: Path,
) -> None:
    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    binary = tmp_path / "untrusted-emulator"
    binary.write_bytes(b"not a verified Bun build")

    with pytest.raises(RuntimeError, match="provenance"):
        hf_runtime.verified_emulator_binary_pin(binary)


def test_synthesized_sibling_cannot_attest_arbitrary_binary(tmp_path: Path) -> None:
    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    binary = tmp_path / "arbitrary-emulator"
    binary.write_bytes(b"not compiled from the repository server source")
    _write_test_provenance(binary)

    with pytest.raises(RuntimeError, match="trusted build"):
        hf_runtime.verified_emulator_binary_pin(binary)


def test_final_ledger_refresh_includes_events_after_verifier_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = WorldRuntime()
    bench = _bench(tmp_path, runtime)
    first = {**_COMMIT_ROW, "request_id": "req_1"}
    later = {
        "request_id": "req_2",
        "method": "GET",
        "operation_id": "datasetInfo",
        "path": "/api/datasets/bench/scenario-2",
        "request": None,
        "response": {"status": 200},
    }

    with bench.run(app_state="install-only") as run:
        hf = run.emulate("huggingface")
        runtime.raw_ledger = (json.dumps(first) + "\n").encode()
        assert run.verify(scenario_2.publish_commit_is_witnessed, hf=hf)["status"] == "pass"
        runtime.raw_ledger = (json.dumps(first) + "\n" + json.dumps(later) + "\n").encode()

    stored = (run.final_path / "ledgers" / "huggingface.jsonl").read_text()
    assert [json.loads(line)["request_id"] for line in stored.splitlines()] == [
        "req_1",
        "req_2",
    ]
    assert runtime.events.count("snapshot") == 2


def test_terminal_drive_cannot_escalate_into_independent_ledger_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = WorldRuntime()
    bench = _bench(tmp_path, runtime)
    ledger = "/var/lib/opentraces-bench/huggingface.jsonl"

    with bench.run(app_state="install-only") as run:
        run.emulate("huggingface")
        attacks = [
            ("sh", "-c", f"printf FORGED_LEDGER_ROW >> {ledger}"),
            ("sudo", "-u", "opentraces-hf", "sh", "-c", f"printf forged >> {ledger}"),
            ("sudo", "sh", "-c", f": > {ledger}"),
            ("su", "-s", "/bin/sh", "opentraces-hf", "-c", f"printf forged >> {ledger}"),
        ]
        assert all(run.terminal.exec(*attack).returncode != 0 for attack in attacks)
        assert run.terminal.exec("opentraces", "doctor", "--json").returncode == 0
        run.verify(lambda _run: {"evidence_refs": []})

    assert runtime.events.count("product-ledger-escalation-refused") == 4


def test_run_emulate_refuses_every_unregistered_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"binary")
    _write_test_provenance(binary)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = WorldRuntime()
    bench = _bench(tmp_path, runtime)

    with bench.run(app_state="install-only") as run:
        with pytest.raises(ValueError, match="unknown emulator.*github"):
            run.emulate("github")

    assert "start" not in runtime.events
    assert "copy" not in runtime.events


def test_scenario_2_down_control_uses_the_same_source_and_cannot_pass_vacuously(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    monkeypatch.setenv("OT_BENCH_HF_DOWN_CONTROL", "1")
    runtime = WorldRuntime()
    bench = _bench(tmp_path, runtime)

    scenario_2.test_publish_reaches_hf_remote(bench)

    run_path = next(path for path in bench.store.root.glob("run_*") if path.is_dir())
    result = json.loads((run_path / "result.json").read_text(encoding="utf-8"))
    ledger_rows = [
        json.loads(line)
        for line in (run_path / "ledgers/huggingface.jsonl").read_text().splitlines()
    ]
    assert result["scenario"]["source_ref"] == "source/scenario.py"
    assert result["scenario"]["claim"] == (
        "Publishing a dataset reaches the configured Hugging Face remote."
    )
    assert result["execution_status"] == "complete"
    assert result["verdict"] == "fail"
    assert result["reason"]["code"] == "assertion_failed"
    assert "0x" not in result["reason"]["message"]
    assert result_exit_code(result) == 1
    assert not any(row.get("operation_id") == "commit" for row in ledger_rows)
    assert result["verifiers"][0]["evidence_refs"] == ["ledgers/huggingface.jsonl"]
    assert result["evidence"]["requirements"][0]["evidence_refs"] == ["ledgers/huggingface.jsonl"]
    assert runtime.events.count("start") == 1
    assert runtime.events.count("release") == 1
    assert runtime.events.count("product-exec") == 6
    assert runtime.stop_command is not None
    assert "kill -0" in runtime.stop_command
