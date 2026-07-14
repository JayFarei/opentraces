from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from opentraces.core.arena.box import (
    PINNED_CRABBOX_VERSION,
    PINNED_LOCAL_IMAGE,
    Box,
    CrabboxRefusal,
    CrabboxRuntime,
    sandbox_tier_for_provider,
)


EXPECTED_INSTALL_LOCK = {
    "annotated-doc": "0.0.4",
    "annotated-types": "0.7.0",
    "anyio": "4.14.2",
    "certifi": "2026.6.17",
    "charset-normalizer": "3.4.9",
    "click": "8.4.2",
    "filelock": "3.29.7",
    "fsspec": "2026.6.0",
    "h11": "0.16.0",
    "hf-xet": "1.4.3",
    "httpcore": "1.0.9",
    "httpx": "0.28.1",
    "huggingface-hub": "1.10.2",
    "idna": "3.18",
    "markdown-it-py": "4.2.0",
    "mdurl": "0.1.2",
    "mmh3": "5.2.1",
    "packaging": "26.2",
    "pyclack-cli": "0.4.0",
    "pydantic": "2.13.4",
    "pydantic-core": "2.46.4",
    "pygments": "2.20.0",
    "pyyaml": "6.0.3",
    "readchar": "4.2.2",
    "requests": "2.34.2",
    "rich": "15.0.0",
    "shellingham": "1.5.4",
    "tqdm": "4.68.4",
    "typer": "0.26.8",
    "typing-extensions": "4.16.0",
    "typing-inspection": "0.4.2",
    "urllib3": "2.7.0",
}


class ScriptedRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[str], Path | None, dict[str, str], float]] = []

    def __call__(self, argv, *, cwd=None, env, timeout):
        self.calls.append((list(argv), cwd, env, timeout))
        return self.responses.pop(0)


def _completed(argv: list[str], rc: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(argv, rc, stdout, stderr)


def _inspect() -> str:
    return json.dumps(
        {
            "id": "cbx_abc123",
            "slug": "steady-crab",
            "provider": "local-container",
            "state": "leased",
            "ready": True,
            "sshHost": "127.0.0.1",
            "sshUser": "crabbox",
            "sshPort": "32222",
            "sshKey": "/tmp/key",
            "labels": {"image": PINNED_LOCAL_IMAGE},
        }
    )


def test_lease_pins_version_image_tmpdir_and_runs_both_preflights(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            _completed(["crabbox", "inspect"], stdout=_inspect()),
            _completed(["ssh"], stdout=""),
        ]
    )
    runtime = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=tmp_path / "missing")

    box = runtime.lease()

    warmup, inspect, ssh = (runner.calls[1][0], runner.calls[2][0], runner.calls[3][0])
    assert warmup == [
        "crabbox",
        "warmup",
        "--provider",
        "local-container",
        "--local-container-image",
        PINNED_LOCAL_IMAGE,
    ]
    assert runner.calls[1][2]["TMPDIR"] == str(tmp_path / "crabbox-tmp")
    assert inspect == [
        "crabbox",
        "inspect",
        "--id",
        "cbx_abc123",
        "--provider",
        "local-container",
        "--json",
    ]
    assert ssh[:3] == ["ssh", "-F", "/dev/null"]
    assert box.sandbox_tier == "container"
    assert box.provider == "local-container"
    assert box.image == PINNED_LOCAL_IMAGE


def test_failed_warmup_with_reported_lease_is_stopped_once_without_hiding_primary_failure(
    tmp_path: Path,
) -> None:
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(
                ["crabbox", "warmup"],
                rc=1,
                stdout="bootstrap failed after lease=cbx_abc123\n",
            ),
            _completed(["crabbox", "stop"], rc=1, stderr="cleanup also failed\n"),
        ]
    )
    runtime = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=tmp_path / "missing")

    with pytest.raises(CrabboxRefusal, match="lease_failed") as caught:
        runtime.lease()

    assert caught.value.code == "lease_failed"
    stop_calls = [call[0] for call in runner.calls if call[0][1:2] == ["stop"]]
    assert stop_calls == [
        [
            "crabbox",
            "stop",
            "--id",
            "cbx_abc123",
            "--provider",
            "local-container",
        ]
    ]
    assert runtime.diagnostic_records()[-1] == {
        "operation": "cleanup",
        "code": "release_failed",
        "message": "release_failed: cleanup also failed",
    }


@pytest.mark.parametrize(
    ("patch", "refusal_code"),
    [
        ({"provider": "firecracker"}, "lease_provider_mismatch"),
        ({"labels": {"image": "ubuntu:26.04"}}, "lease_image_mismatch"),
    ],
)
def test_lease_refuses_requested_vs_observed_provider_or_image_mismatch(
    tmp_path: Path,
    patch: dict,
    refusal_code: str,
) -> None:
    facts = json.loads(_inspect())
    facts.update(patch)
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            _completed(["crabbox", "inspect"], stdout=json.dumps(facts)),
            _completed(["crabbox", "stop"]),
        ]
    )

    with pytest.raises(CrabboxRefusal, match=refusal_code) as caught:
        CrabboxRuntime(
            runner=runner,
            home=tmp_path,
            ssh_config=tmp_path / "missing",
        ).lease()

    assert caught.value.code == refusal_code
    assert sum(call[0][1:2] == ["stop"] for call in runner.calls) == 1


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("local-container", "container"),
        ("cloudflare", "container"),
        ("firecracker", "microvm"),
        ("ssh", "none"),
        ("new-unreviewed-provider", "none"),
    ],
)
def test_sandbox_tier_uses_an_explicit_non_escalating_provider_map(
    provider: str, expected: str
) -> None:
    assert sandbox_tier_for_provider(provider) == expected


def test_version_drift_is_a_loud_named_refusal(tmp_path: Path) -> None:
    runner = ScriptedRunner([_completed(["crabbox", "--version"], stdout="crabbox 0.39.0\n")])

    with pytest.raises(CrabboxRefusal, match="crabbox_version_mismatch") as caught:
        CrabboxRuntime(runner=runner, home=tmp_path).lease()

    assert "re-audit" in str(caught.value)
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    ("inspect_result", "refusal_code"),
    [
        (_completed(["crabbox", "inspect"], stdout="not-json\n"), "lease_inspect_invalid"),
        (
            _completed(
                ["crabbox", "inspect"],
                stdout=json.dumps({**json.loads(_inspect()), "ready": False}),
            ),
            "lease_not_ready",
        ),
        (
            _completed(
                ["crabbox", "inspect"],
                stdout=json.dumps(
                    {key: value for key, value in json.loads(_inspect()).items() if key != "sshKey"}
                ),
            ),
            "lease_inspect_incomplete",
        ),
    ],
)
def test_post_warmup_inspect_refusal_stops_the_named_lease_once(
    tmp_path: Path,
    inspect_result: subprocess.CompletedProcess[str],
    refusal_code: str,
) -> None:
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            inspect_result,
            _completed(["crabbox", "stop"]),
        ]
    )
    runtime = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=tmp_path / "missing")

    with pytest.raises(CrabboxRefusal, match=refusal_code) as caught:
        runtime.lease()

    assert caught.value.code == refusal_code
    stop_calls = [call[0] for call in runner.calls if call[0][1:2] == ["stop"]]
    assert stop_calls == [
        [
            "crabbox",
            "stop",
            "--id",
            "cbx_abc123",
            "--provider",
            "local-container",
        ]
    ]


def test_cleanup_failure_keeps_inspect_refusal_primary_and_records_stop_diagnostic(
    tmp_path: Path,
) -> None:
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            _completed(["crabbox", "inspect"], stdout="not-json\n"),
            _completed(["crabbox", "stop"], rc=1, stderr="cleanup failed\n"),
        ]
    )
    runtime = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=tmp_path / "missing")

    with pytest.raises(CrabboxRefusal, match="lease_inspect_invalid") as caught:
        runtime.lease()

    assert caught.value.code == "lease_inspect_invalid"
    stop_diagnostics = [row for row in runtime.diagnostic_records() if row["operation"] == "stop"]
    assert len(stop_diagnostics) == 1
    assert stop_diagnostics[0]["returncode"] == 1
    assert stop_diagnostics[0]["stderr"] == "cleanup failed\n"
    cleanup_diagnostics = [
        row for row in runtime.diagnostic_records() if row["operation"] == "cleanup"
    ]
    assert cleanup_diagnostics == [
        {
            "operation": "cleanup",
            "code": "release_failed",
            "message": "release_failed: cleanup failed",
        }
    ]


def test_ssh_probe_refusal_stops_the_lease_once(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            _completed(["crabbox", "inspect"], stdout=_inspect()),
            _completed(["ssh"], rc=255, stderr="connection refused\n"),
            _completed(["crabbox", "stop"]),
        ]
    )
    runtime = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=tmp_path / "missing")

    with pytest.raises(CrabboxRefusal, match="ssh_probe_failed") as caught:
        runtime.lease()

    assert caught.value.code == "ssh_probe_failed"
    assert sum(call[0][1:2] == ["stop"] for call in runner.calls) == 1


def test_bad_ssh_config_is_refused_before_warmup(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text("Host *\n  UseKeychain yes\n", encoding="utf-8")
    runner = ScriptedRunner(
        [_completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n")]
    )

    with pytest.raises(CrabboxRefusal, match="ssh_config_incompatible"):
        CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=config).lease()

    assert len(runner.calls) == 1


def test_unrelated_host_guard_does_not_mask_relevant_unguarded_keyword(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text(
        "Host github.com\n  IgnoreUnknown UseKeychain\nHost *\n  UseKeychain yes\n",
        encoding="utf-8",
    )
    runner = ScriptedRunner(
        [_completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n")]
    )

    with pytest.raises(CrabboxRefusal, match="ssh_config_incompatible"):
        CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=config).lease()

    assert len(runner.calls) == 1


def test_irrelevant_host_only_apple_keyword_does_not_refuse_crabbox(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text("Host github.com\n  UseKeychain yes\n", encoding="utf-8")
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            _completed(["crabbox", "inspect"], stdout=_inspect()),
            _completed(["ssh"]),
        ]
    )

    box = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=config).lease()

    assert box.id == "cbx_abc123"


def test_global_guard_must_precede_relevant_apple_keyword(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text(
        "IgnoreUnknown UseKeychain,AddKeysToKeychain\nHost *\n  UseKeychain yes\n",
        encoding="utf-8",
    )
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            _completed(["crabbox", "inspect"], stdout=_inspect()),
            _completed(["ssh"]),
        ]
    )

    box = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=config).lease()

    assert box.id == "cbx_abc123"


def test_materialize_timing_is_sanitized_linked_and_kept_inside_run(tmp_path: Path) -> None:
    def runner(argv, *, cwd=None, env, timeout):
        if "--timing-record" in argv:
            timing_path = Path(argv[argv.index("--timing-record") + 1])
            timing_path.parent.mkdir(parents=True, exist_ok=True)
            timing_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "timing": {"exitCode": 0, "elapsedMs": 8},
                        "hostPath": "/Users/private/source",
                        "credential": "top-secret-token",
                    }
                ),
                encoding="utf-8",
            )
        operation = argv[1] if argv and argv[0] == "crabbox" and len(argv) > 1 else argv[0]
        if operation == "--version":
            return _completed(list(argv), stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n")
        if operation == "warmup":
            return _completed(list(argv), stdout="ready lease=cbx_abc123\n")
        if operation == "inspect":
            return _completed(list(argv), stdout=_inspect())
        if operation == "run":
            return _completed(
                list(argv),
                stdout="/usr/bin/python3\n/usr/bin/git\n/usr/bin/curl\n/usr/bin/script\n",
            )
        return _completed(list(argv))

    from opentraces.core.arena.engine import Bench, ScenarioSource
    from opentraces.core.arena.run_store import RunStore

    scenario_path = tmp_path / "test_timing.py"
    scenario_path.write_text('def test_timing(bench):\n    """Timing stays in custody."""\n')
    runtime = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=tmp_path / "missing")
    bench = Bench(
        source=ScenarioSource(
            nodeid="test_timing.py::test_timing",
            claim="Timing stays in custody.",
            source_path=scenario_path,
            scenario_path="tests/arena/test_timing.py",
            repository="JayFarei/opentraces",
            commit="abc123",
            dirty_diff_digest=None,
            product_worktree="clean",
            product_dirty_diff_digest=None,
        ),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=tmp_path,
    )

    def condition_holds(run):
        return {"evidence_refs": []}

    with bench.run(app_state="base-only") as run:
        run.verify(condition_holds)

    timing_artifacts = [
        item for item in run.result["artifacts"] if item["kind"] == "crabbox_timing"
    ]
    assert timing_artifacts
    assert set(run.result["pins"]["app_state"]["observation_refs"]) == {
        artifact["path"] for artifact in timing_artifacts
    }
    timing = json.loads((run.final_path / timing_artifacts[0]["path"]).read_text())
    assert timing["timing"] == {"exitCode": 0, "elapsedMs": 8}
    serialized = (
        json.dumps(run.result) + (run.final_path / "artifacts/box-lifecycle.json").read_text()
    )
    assert "top-secret-token" not in serialized
    assert "/Users/private/source" not in serialized
    assert "/tmp/key" not in serialized
    assert not (tmp_path / ".crabbox" / "bench-base-provides-timing.json").exists()


def test_install_only_materialization_pins_and_observes_hf_client_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    dist = repository / "dist"
    dist.mkdir(parents=True)
    wheel = dist / "opentraces-0.0.0-py3-none-any.whl"
    wheel.write_bytes(b"local wheel bytes")
    commands: list[str] = []
    runtime = CrabboxRuntime(runner=lambda *args, **kwargs: _completed([]), home=tmp_path)
    box = Box(
        id="box",
        slug="box",
        provider="local-container",
        sandbox_tier="container",
        ssh_host="127.0.0.1",
        ssh_user="crabbox",
        ssh_port="22",
        ssh_key="/tmp/key",
    )

    monkeypatch.setattr(
        runtime,
        "copy_into_box",
        lambda _box, source, destination, timeout=120: destination,
    )
    monkeypatch.setattr(
        runtime,
        "_timing_path",
        lambda _repository, operation: tmp_path / f"{operation}.json",
    )
    monkeypatch.setattr(runtime, "_evidence_ref", lambda path: None)

    def fake_exec(_box, argv, **kwargs):
        command = " ".join(argv)
        commands.append(command)
        if "importlib.metadata" in command:
            stdout = json.dumps(EXPECTED_INSTALL_LOCK)
        else:
            stdout = "/usr/bin/opentraces\n/usr/bin/script\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(runtime, "exec", fake_exec)

    pin = runtime.materialize(box, "install-only", repository=repository)

    install = next(command for command in commands if "pip install" in command)
    assert "huggingface-hub==1.10.2" in install
    assert "hf-xet==1.4.3" in install
    assert "filelock==3.29.7" in install
    assert "pydantic-core==2.46.4" in install
    assert pin["dependencies"] == EXPECTED_INSTALL_LOCK
    assert pin["recipe"]["dependencies"] == pin["dependencies"]
    assert pin["recipe"]["dependency_lock_sha256"].startswith("sha256:")
    assert pin["recipe"]["wheel_sha256"]


def test_exec_uses_pinned_lease_flags_and_propagates_remote_result(tmp_path: Path) -> None:
    timing = tmp_path / "timing.json"

    def runner(argv, *, cwd=None, env, timeout):
        timing.write_text(json.dumps({"schemaVersion": 1, "timing": {"exitCode": 7}}))
        return _completed(argv, rc=7, stdout="out", stderr="remote command exited 7")

    runtime = CrabboxRuntime(runner=runner, home=tmp_path)
    box = Box(
        id="cbx_abc123",
        slug="steady-crab",
        provider="local-container",
        sandbox_tier="container",
        ssh_host="127.0.0.1",
        ssh_user="crabbox",
        ssh_port="32222",
        ssh_key="/tmp/key",
    )

    result = runtime.exec(box, ["sh", "-c", "exit 7"], timing_path=timing)

    assert result.returncode == 7
    assert result.stdout == "out"
    assert result.timing["timing"]["exitCode"] == 7
    assert result.argv[:8] == [
        "crabbox",
        "run",
        "--id",
        "cbx_abc123",
        "--reclaim",
        "--no-sync",
        "--provider",
        "local-container",
    ]


def test_product_exec_uses_a_dedicated_home_and_writable_recording_workspace(
    tmp_path: Path,
) -> None:
    timing = tmp_path / "product-timing.json"
    runner = ScriptedRunner([_completed(["crabbox", "run"], stdout="write-heavy-ok\n")])
    runtime = CrabboxRuntime(runner=runner, home=tmp_path)
    box = Box(
        id="cbx_abc123",
        slug="steady-crab",
        provider="local-container",
        sandbox_tier="container",
        ssh_host="127.0.0.1",
        ssh_user="crabbox",
        ssh_port="32222",
        ssh_key="/tmp/key",
    )

    result = runtime.exec_product(
        box,
        [
            "sh",
            "-c",
            'test "$(id -u)" -ne 0 && ! sudo -n true && '
            'test -w "$HOME" && test -w bench-recordings && '
            "opentraces dataset new scenario-2 --rows-file /tmp/rows.jsonl",
        ],
        env={
            "BASH_ENV": "/home/opentraces-product/product-shell-startup",
            "HF_ENDPOINT": "http://127.0.0.1:14318",
            "OPENAI_API_KEY": "ordinary-model-secret",
            "PYTHONPATH": "/home/opentraces-product/scenario-modules",
        },
        timing_path=timing,
    )

    command = runner.calls[0][0]
    rendered = " ".join(command)
    assert result.returncode == 0
    assert (
        "--allow-env BASH_ENV --allow-env HF_ENDPOINT --allow-env OPENAI_API_KEY "
        "--allow-env PYTHONPATH" in rendered
    )
    product_start = command.index("/usr/bin/sudo")
    assert command[product_start:] == [
        "/usr/bin/sudo",
        "-H",
        "-u",
        "opentraces-product",
        "--preserve-env=BASH_ENV,HF_ENDPOINT,OPENAI_API_KEY,PYTHONPATH",
        "--",
        "sh",
        "-c",
        'test "$(id -u)" -ne 0 && ! sudo -n true && '
        'test -w "$HOME" && test -w bench-recordings && '
        "opentraces dataset new scenario-2 --rows-file /tmp/rows.jsonl",
    ]
    assert "sh -c set -eu" not in rendered
    assert "opentraces dataset new scenario-2" in rendered
    assert "http://127.0.0.1:14318" not in rendered
    assert "ordinary-model-secret" not in rendered
    assert "/home/opentraces-product/product-shell-startup" not in rendered
    assert "/home/opentraces-product/scenario-modules" not in rendered
    assert "http://127.0.0.1:14318" not in json.dumps(runtime.diagnostic_records())
    assert "ordinary-model-secret" not in json.dumps(runtime.diagnostic_records())
    assert "/home/opentraces-product/product-shell-startup" not in json.dumps(
        runtime.diagnostic_records()
    )
    assert "/home/opentraces-product/scenario-modules" not in json.dumps(
        runtime.diagnostic_records()
    )


@pytest.mark.parametrize(
    "unsafe_name",
    ["PATH", "HOME", "USER", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "SUDO_ASKPASS"],
)
def test_product_exec_rejects_shell_startup_environment_before_controller_launch(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    marker = tmp_path / "controller-marker"
    hostile_value = f"{marker}; touch {marker}"
    runner = ScriptedRunner([])
    runtime = CrabboxRuntime(runner=runner, home=tmp_path)
    box = Box(
        id="cbx_abc123",
        slug="steady-crab",
        provider="local-container",
        sandbox_tier="container",
        ssh_host="127.0.0.1",
        ssh_user="crabbox",
        ssh_port="32222",
        ssh_key="/tmp/key",
    )

    with pytest.raises(ValueError, match="unsafe product environment"):
        runtime.exec_product(
            box,
            ["opentraces", "dataset", "list"],
            env={unsafe_name: hostile_value},
            timing_path=tmp_path / "hostile-timing.json",
        )

    assert runner.calls == []
    assert not marker.exists()
    assert hostile_value not in json.dumps(runtime.diagnostic_records())


def test_collect_safely_falls_back_when_tarfile_extraction_filters_are_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python 3.10 still collects safe members without weakening path checks."""

    repository = tmp_path / "repository"
    source_tar = repository / ".crabbox" / "runs" / "cbx_abc123" / "cbx_abc123-artifacts.tgz"

    def runner(argv, *, cwd=None, env, timeout):
        source_tar.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source_tar, "w:gz") as archive:
            safe = tarfile.TarInfo("proof.txt")
            safe_payload = b"retained proof\n"
            safe.size = len(safe_payload)
            archive.addfile(safe, io.BytesIO(safe_payload))

            traversal = tarfile.TarInfo("../escaped.txt")
            traversal_payload = b"must not escape\n"
            traversal.size = len(traversal_payload)
            archive.addfile(traversal, io.BytesIO(traversal_payload))

            link = tarfile.TarInfo("unsafe-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../escaped.txt"
            archive.addfile(link)
        return _completed(list(argv))

    original_extractall = tarfile.TarFile.extractall
    original_data_filter = tarfile.data_filter

    def python_310_extractall(archive, path=".", members=None, *, numeric_owner=False, **kwargs):
        if "filter" in kwargs:
            raise TypeError("extractall() got an unexpected keyword argument 'filter'")
        tarfile.data_filter = original_data_filter
        try:
            return original_extractall(
                archive,
                path=path,
                members=members,
                numeric_owner=numeric_owner,
            )
        finally:
            del tarfile.data_filter

    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    monkeypatch.setattr(tarfile.TarFile, "extractall", python_310_extractall)
    runtime = CrabboxRuntime(runner=runner, home=tmp_path)
    box = Box(
        id="cbx_abc123",
        slug="steady-crab",
        provider="local-container",
        sandbox_tier="container",
        ssh_host="127.0.0.1",
        ssh_user="crabbox",
        ssh_port="32222",
        ssh_key="/tmp/key",
    )

    collected = runtime.collect(
        box,
        ["*.txt"],
        destination=tmp_path / "collected",
        repository=repository,
    )

    assert collected["proof.txt"].read_text(encoding="utf-8") == "retained proof\n"
    assert not (tmp_path / "escaped.txt").exists()
    assert "unsafe-link" not in collected
