from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from opentraces.capture.otlp import start_receiver
from opentraces.core.arena.emulate.huggingface.runtime import (
    BUN_VERSION,
    COMPILE_TARGET,
    DEFAULT_PORT as HF_DEFAULT_PORT,
    EmulatorReadinessError,
    app_state_digest,
    app_state_pin,
    build_hf_emulator_binary,
    emulator_binary_pin,
    pinned_bun_command,
    wait_for_hf_emulator,
)

ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT / "src/opentraces/core/arena/emulate/huggingface/server.ts"
PACKAGING_RECORD = SERVER_SOURCE.with_name("packaging.json")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_standard_dev_install_provisions_the_pinned_real_client() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dev_dependencies = pyproject.split("dev = [", 1)[1].split("]", 1)[0]

    assert '"huggingface-hub==1.10.2"' in dev_dependencies
    assert '"hf-xet==1.4.3"' in dev_dependencies


def test_pinned_bun_command_materializes_a_serving_ready_npx_toolchain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    npx = hf_runtime.shutil.which("npx")
    assert npx is not None

    monkeypatch.setenv("npm_config_cache", str(tmp_path / "cold-npm-cache"))
    monkeypatch.setattr(
        hf_runtime,
        "BUN_TOOLCHAIN_ROOT",
        tmp_path / "resolved-bun-toolchain",
        raising=False,
    )
    executables = {"bun": None, "bunx": None, "npx": npx}
    monkeypatch.setattr(hf_runtime.shutil, "which", executables.get)

    command = hf_runtime.pinned_bun_command("run", str(SERVER_SOURCE))
    port = _free_port()
    process = subprocess.Popen(
        command,
        env={
            **os.environ,
            "PORT": str(port),
            "LEDGER_PATH": str(tmp_path / "npx-materialized-ledger.jsonl"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        manifest = wait_for_hf_emulator(
            f"http://127.0.0.1:{port}",
            timeout=2.0,
            poll_interval=0.01,
        )
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=2)

    assert Path(command[0]).is_file()
    assert command[0] != npx
    assert command[1:] == ["run", str(SERVER_SOURCE)]
    observed_version = subprocess.run(
        [command[0], "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    ).stdout.strip()
    assert observed_version == "1.3.6"
    assert manifest["id"] == "huggingface"


def test_binary_sha_is_part_of_app_state_digest(tmp_path: Path) -> None:
    first_binary = tmp_path / "hf-emulator-first"
    second_binary = tmp_path / "hf-emulator-second"
    first_binary.write_bytes(b"compiled-binary-v1")
    second_binary.write_bytes(b"compiled-binary-v2")

    first_pin = emulator_binary_pin(first_binary)
    second_pin = emulator_binary_pin(second_binary)
    recipe = {"base_image": "ubuntu:24.04", "product_commit": "abc123"}

    assert first_pin.sha256 != second_pin.sha256
    assert app_state_digest(recipe, hf_emulator=first_pin) != app_state_digest(
        recipe, hf_emulator=second_pin
    )

    serialized_pin = app_state_pin(
        name="with-huggingface",
        recipe=recipe,
        provides=("python3", "hf-emulator"),
        hf_emulator=first_pin,
    )
    assert serialized_pin == {
        "name": "with-huggingface",
        "digest": app_state_digest(recipe, hf_emulator=first_pin),
        "provides": ["python3", "hf-emulator"],
        "emulators": {"huggingface": first_pin.to_dict()},
        "recipe": {
            "base": recipe,
            "emulators": {"huggingface": first_pin.to_dict()},
        },
    }


def test_readiness_waits_for_manifest_and_rejects_a_missing_sidecar(
    tmp_path: Path,
) -> None:
    compiled_binary = os.environ.get("OPENTRACES_HF_EMULATOR_BINARY")
    if compiled_binary is not None:
        command = [compiled_binary]
    else:
        command = pinned_bun_command("run", str(SERVER_SOURCE))

    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    ledger = tmp_path / "readiness-ledger.jsonl"
    process: subprocess.Popen[str] | None = None

    def delayed_start() -> None:
        nonlocal process
        time.sleep(0.15)
        process = subprocess.Popen(
            command,
            env={**os.environ, "PORT": str(port), "LEDGER_PATH": str(ledger)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    starter = threading.Thread(target=delayed_start)
    starter.start()
    started = time.monotonic()
    try:
        manifest = wait_for_hf_emulator(endpoint, timeout=2, poll_interval=0.01)
    finally:
        starter.join()
        if process is not None:
            process.terminate()
            process.wait(timeout=2)

    assert time.monotonic() - started >= 0.1
    assert manifest["id"] == "huggingface"

    with pytest.raises(EmulatorReadinessError, match="did not become ready"):
        wait_for_hf_emulator(endpoint, timeout=0.05, poll_interval=0.01)


def test_default_hf_emulator_coexists_with_default_portable_otlp_receiver(
    tmp_path: Path,
) -> None:
    compiled_binary = os.environ.get("OPENTRACES_HF_EMULATOR_BINARY")
    if compiled_binary is not None:
        command = [compiled_binary]
    else:
        command = pinned_bun_command("run", str(SERVER_SOURCE))

    receiver = None
    try:
        receiver = start_receiver()
        assert receiver.wait_until_ready(timeout=2.0)
    except OSError:
        with urllib.request.urlopen("http://127.0.0.1:4318/health", timeout=1) as response:
            assert json.load(response) == {"status": "ok"}

    process = subprocess.Popen(
        command,
        env={
            **os.environ,
            "LEDGER_PATH": str(tmp_path / "coexistence-ledger.jsonl"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        manifest = wait_for_hf_emulator(
            f"http://127.0.0.1:{HF_DEFAULT_PORT}",
            timeout=2.0,
            poll_interval=0.01,
        )
        with urllib.request.urlopen("http://127.0.0.1:4318/health", timeout=1) as response:
            assert json.load(response) == {"status": "ok"}
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=2)
        if receiver is not None:
            receiver.shutdown()

    assert HF_DEFAULT_PORT == 14318
    assert manifest["id"] == "huggingface"


def test_build_produces_pinned_linux_arm64_binary(tmp_path: Path) -> None:
    output = tmp_path / "hf-emulator"
    pin = build_hf_emulator_binary(output)

    assert output.read_bytes()[:4] == b"\x7fELF"
    assert os.access(output, os.X_OK)
    assert pin.sha256 == emulator_binary_pin(output).sha256
    assert pin.bun_version == BUN_VERSION == "1.3.6"
    assert pin.target == COMPILE_TARGET == "bun-linux-arm64"


def test_compile_has_a_finite_deadline_and_failed_build_cannot_replace_last_good_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    output = tmp_path / "hf-emulator"
    output.write_bytes(b"last-good-binary")
    provenance = hf_runtime.emulator_provenance_path(output)
    provenance.write_text('{"last":"good"}\n', encoding="utf-8")
    observed_timeout = None

    def timed_out(argv, **kwargs):
        nonlocal observed_timeout
        observed_timeout = kwargs.get("timeout")
        candidate = Path(
            next(item.split("=", 1)[1] for item in argv if item.startswith("--outfile="))
        )
        candidate.write_bytes(b"partial-new-binary")
        raise subprocess.TimeoutExpired(argv, observed_timeout)

    monkeypatch.setattr(hf_runtime.shutil, "which", lambda _name: "/usr/bin/bunx")
    monkeypatch.setattr(hf_runtime.subprocess, "run", timed_out)

    with pytest.raises(subprocess.TimeoutExpired):
        hf_runtime.build_hf_emulator_binary(output)

    assert isinstance(observed_timeout, (int, float)) and 0 < observed_timeout <= 120
    assert output.read_bytes() == b"last-good-binary"
    assert provenance.read_text(encoding="utf-8") == '{"last":"good"}\n'


def test_interrupted_publication_never_exposes_a_torn_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1 (#305): binary + provenance publish as one crash-consistent generation.

    RED intent: with the pre-fix two-rename publish, a crash after the binary
    rename and before the provenance rename leaves ``output`` pointing at new
    bytes whose provenance sidecar is stale/absent -- a torn pair that a second
    process cannot verify. With the single-pointer generation swap the binary
    only becomes visible together with its provenance, so the pair verifies.
    """

    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    output = tmp_path / "hf-emulator"

    real_replace = os.replace
    injected: dict[str, bool] = {"crashed": False}
    published = os.path.abspath(output)

    def crash_when_output_is_published(src, dst, *args, **kwargs):
        real_replace(src, dst, *args, **kwargs)
        # Compare lexically: after the swap ``output`` resolves through the new
        # symlink, so resolving it would miss the publication boundary.
        if not injected["crashed"] and os.path.abspath(dst) == published:
            # Simulate a process crash at the first moment ``output`` is swapped.
            injected["crashed"] = True
            raise KeyboardInterrupt("injected crash at publication boundary")

    monkeypatch.setattr(hf_runtime.os, "replace", crash_when_output_is_published)

    with pytest.raises(KeyboardInterrupt):
        hf_runtime.build_hf_emulator_binary(output)

    assert injected["crashed"] is True
    # A second process recovers a single verified Bun 1.3.6 executable/provenance
    # pair from the interrupted publication -- no torn generation survives.
    pin = hf_runtime.verified_emulator_binary_pin(output)
    assert pin.provenance == "verified"
    assert pin.bun_version == "1.3.6"
    assert output.read_bytes()[:4] == b"\x7fELF"


def test_two_processes_share_one_cold_cache_materialization(
    tmp_path: Path,
) -> None:
    """F2 (#305): the cross-process materialization lock, exercised for real.

    Two independent OS processes race a cold Bun toolchain cache with only a
    controlled ``npx`` materializer on PATH. Both must return the identical
    verified executable while the materializer runs exactly once.
    """

    toolchain_root = tmp_path / "cold-bun-toolchain"
    counter = tmp_path / "materializations.log"
    fake_bun = tmp_path / "fake-bun" / "bun"
    fake_bun.parent.mkdir(parents=True, exist_ok=True)
    fake_bun.write_text("#!/bin/sh\necho 1.3.6\n", encoding="utf-8")
    fake_bun.chmod(0o755)

    bin_dir = tmp_path / "cold-path-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    npx = bin_dir / "npx"
    npx.write_text(
        "#!/bin/sh\n"
        f'printf "x" >> "{counter}"\n'
        "# emulate: npx --yes --package bun@1.3.6 which bun\n"
        "sleep 0.2\n"
        f'printf "%s\\n" "{fake_bun}"\n',
        encoding="utf-8",
    )
    npx.chmod(0o755)

    driver = tmp_path / "materialize.py"
    driver.write_text(
        "import hashlib, json, sys\n"
        "from pathlib import Path\n"
        "from opentraces.core.arena.emulate.huggingface import runtime as hf\n"
        "hf.BUN_TOOLCHAIN_ROOT = Path(sys.argv[1])\n"
        'command = hf.pinned_bun_command("--version")\n'
        "exe = command[0]\n"
        'sha = hashlib.sha256(Path(exe).read_bytes()).hexdigest()\n'
        'print(json.dumps({"exe": exe, "sha": sha}))\n',
        encoding="utf-8",
    )

    child_env = {
        **os.environ,
        "PATH": str(bin_dir),
        "PYTHONPATH": str(ROOT / "src")
        + os.pathsep
        + str(ROOT / "packages/opentraces-schema/src"),
    }

    processes = [
        subprocess.Popen(
            [sys.executable, str(driver), str(toolchain_root)],
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout.strip().splitlines()[-1]))

    assert results[0] == results[1]
    assert Path(results[0]["exe"]).is_file()
    materializations = counter.read_bytes() if counter.is_file() else b""
    assert len(materializations) == 1, (
        f"materializer ran {len(materializations)} times; the cross-process "
        "lock must serialize both workers onto one materialization"
    )


def test_retained_a2_transcript_marks_the_remote_blocker_as_historical() -> None:
    """F3 (#305): the retained transcript cannot read as the current A2 gate."""

    transcript = (
        ROOT / "tests/manual/a2_hf_emulator_real_box_transcript.txt"
    ).read_text(encoding="utf-8")

    # The original remote-only blocker prose is preserved for the record...
    assert "REMOTE-ONLY BLOCKER" in transcript
    # ...but it is explicitly marked historical and linked to the amended
    # ruling plus the A7 follow-up, so it cannot be read as the operative gate.
    assert "HISTORICAL" in transcript
    assert "amended" in transcript.lower() and "#264" in transcript
    assert "#281" in transcript

    # The honest false remote-real-lease stamp is never relabeled upward.
    proof = json.loads((ROOT / "tests/manual/a2_hf_emulator_real_box.json").read_text())
    assert proof["proof_scope"]["satisfies_remote_real_lease_gate"] is False


def test_shared_binary_cache_serializes_concurrent_builders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentraces.core.arena.emulate.huggingface import runtime as hf_runtime

    monkeypatch.delenv("OPENTRACES_HF_EMULATOR_BINARY", raising=False)
    monkeypatch.setattr(hf_runtime.tempfile, "gettempdir", lambda: str(tmp_path))
    active = 0
    maximum_active = 0
    build_count = 0
    guard = threading.Lock()

    def fake_build(output: Path):
        nonlocal active, maximum_active, build_count
        with guard:
            active += 1
            build_count += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.15)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"complete-binary")
        hf_runtime.emulator_provenance_path(output).write_text("{}", encoding="utf-8")
        with guard:
            active -= 1
        return hf_runtime.emulator_binary_pin(output)

    monkeypatch.setattr(hf_runtime, "build_hf_emulator_binary", fake_build)
    monkeypatch.setattr(hf_runtime, "verified_emulator_binary_pin", hf_runtime.emulator_binary_pin)

    with ThreadPoolExecutor(max_workers=2) as pool:
        binaries = list(pool.map(lambda _index: hf_runtime._binary_for_run(), range(2)))

    assert binaries[0] == binaries[1]
    assert build_count == 1
    assert maximum_active == 1


def test_retained_a2_proof_names_base_and_binary_inclusive_app_state_digests() -> None:
    proof = json.loads((ROOT / "tests/manual/a2_hf_emulator_real_box.json").read_text())

    assert proof["app_state"]["base"]["digest"] == (
        "sha256:5ba324c2659645e4edc449f9c11316130300d36760499340fcda30a19a94bc69"
    )
    assert proof["app_state"]["with_huggingface"]["digest"] == (
        "sha256:fa9edc5b26247f70f1222cb2b59caebd2b0fbdbfe7aa5b82cdd076fafb03ddbf"
    )


def test_retained_a2_proof_is_honest_local_diagnostic_with_actual_page_capture() -> None:
    proof = json.loads((ROOT / "tests/manual/a2_hf_emulator_real_box.json").read_text())
    page = ROOT / "tests/manual" / proof["page_capture"]["file"]

    assert proof["product_source"]["commit"] == ("f4f91848ca37913d97c0956a06487cb032c8f757")
    assert proof["product_source"]["proof_only_commits_follow_product_commit"] is False
    assert proof["product_source"]["post_pin_product_commits"] == [
        "dbfa19f5616",
        "10b8bf89bcb",
    ]
    assert proof["proof_scope"] == {
        "blocker": (
            "Issue #264 still requires the same proof on a genuine remote provider lease. "
            "No remote provider was invoked by this proof."
        ),
        "classification": "LOCAL diagnostic",
        "provider": "local-container",
        "remote_gate_issue": 264,
        "satisfies_remote_real_lease_gate": False,
    }
    assert page.is_file()
    assert hashlib.sha256(page.read_bytes()).hexdigest() == proof["page_capture"]["sha256"]
    assert proof["public_runs"]["happy"]["container_absent"] is True
    assert proof["public_runs"]["emulator_down"]["container_absent"] is True


def test_retained_a2_proof_points_to_the_repository_trusted_build_manifest() -> None:
    proof = json.loads((ROOT / "tests/manual/a2_hf_emulator_real_box.json").read_text())
    manifest = ROOT / proof["binary"]["trusted_build_manifest"]

    assert manifest == SERVER_SOURCE.with_name("trusted-build.json")
    assert manifest.is_file()


def test_packaging_record_keeps_upstream_and_fallback_decisions_honest() -> None:
    record = json.loads(PACKAGING_RECORD.read_text())

    assert record["toolchain"] == {
        "bun_version": "1.3.6",
        "target": "bun-linux-arm64",
    }
    assert record["port"] == 14318
    assert record["core_reconciliation"]["canonical_upstream"] == ("vercel-labs/emulate")
    assert record["core_reconciliation"]["runtime_dependency"] is None
    assert record["fallback"]["strategy"] == "bun-in-checkpoint-plus-bundle"
    assert record["fallback"]["measurement"]["scope"] == "real-box"
    assert record["fallback"]["measurement"]["image"] == "ubuntu:24.04"
    assert record["fallback"]["measurement"]["install_milliseconds"] > 0
    assert record["fallback"]["measurement"]["installed_size_bytes"] > 0
