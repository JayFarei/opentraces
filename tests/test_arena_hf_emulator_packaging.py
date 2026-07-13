from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from opentraces.core.arena.emulate.huggingface.runtime import (
    BUN_VERSION,
    COMPILE_TARGET,
    EmulatorReadinessError,
    app_state_digest,
    app_state_pin,
    build_hf_emulator_binary,
    emulator_binary_pin,
    wait_for_hf_emulator,
)

ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT / "src/opentraces/core/arena/emulate/huggingface/server.ts"
PACKAGING_RECORD = SERVER_SOURCE.with_name("packaging.json")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
    }


def test_readiness_waits_for_manifest_and_rejects_a_missing_sidecar(
    tmp_path: Path,
) -> None:
    compiled_binary = os.environ.get("OPENTRACES_HF_EMULATOR_BINARY")
    if compiled_binary is not None:
        command = [compiled_binary]
    else:
        bun = shutil.which("bun")
        if bun is None:
            pytest.fail(
                "the HF emulator readiness contract requires bun or "
                "OPENTRACES_HF_EMULATOR_BINARY"
            )
        command = [bun, "run", str(SERVER_SOURCE)]

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


def test_build_produces_pinned_linux_arm64_binary(tmp_path: Path) -> None:
    if shutil.which("bunx") is None:
        if os.environ.get("OPENTRACES_RUNTIME_FREE_BOX") == "1":
            pytest.skip("runtime-free leased box verifies the precompiled pinned binary")
        pytest.fail("bunx is required to build the pinned emulator binary")

    output = tmp_path / "hf-emulator"
    pin = build_hf_emulator_binary(output)

    assert output.read_bytes()[:4] == b"\x7fELF"
    assert os.access(output, os.X_OK)
    assert pin.sha256 == emulator_binary_pin(output).sha256
    assert pin.bun_version == BUN_VERSION == "1.3.6"
    assert pin.target == COMPILE_TARGET == "bun-linux-arm64"


def test_packaging_record_keeps_upstream_and_fallback_decisions_honest() -> None:
    record = json.loads(PACKAGING_RECORD.read_text())

    assert record["toolchain"] == {
        "bun_version": "1.3.6",
        "target": "bun-linux-arm64",
    }
    assert record["port"] == 4318
    assert record["core_reconciliation"]["canonical_upstream"] == (
        "vercel-labs/emulate"
    )
    assert record["core_reconciliation"]["runtime_dependency"] is None
    assert record["fallback"]["strategy"] == "bun-in-checkpoint-plus-bundle"
    assert record["fallback"]["measurement"]["scope"] == "real-box"
    assert record["fallback"]["measurement"]["image"] == "ubuntu:24.04"
    assert record["fallback"]["measurement"]["install_milliseconds"] > 0
    assert record["fallback"]["measurement"]["installed_size_bytes"] > 0
