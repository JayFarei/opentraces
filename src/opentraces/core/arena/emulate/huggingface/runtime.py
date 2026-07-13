"""Packaging and runtime gates for the Hugging Face bench sidecar."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


BUN_VERSION = "1.3.6"
COMPILE_TARGET = "bun-linux-arm64"
DEFAULT_PORT = 4318
DEFAULT_READINESS_TIMEOUT = 5.0
SERVER_SOURCE = Path(__file__).with_name("server.ts")


class EmulatorReadinessError(RuntimeError):
    """Raised when the fixed-port sidecar does not expose its manifest."""


@dataclass(frozen=True)
class EmulatorBinaryPin:
    """Content and toolchain identity folded into an app-state digest."""

    sha256: str
    size_bytes: int
    bun_version: str = BUN_VERSION
    target: str = COMPILE_TARGET
    contract_version: str = "huggingface.v1"

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def emulator_binary_pin(path: Path) -> EmulatorBinaryPin:
    """Return the pin of record for one compiled sidecar binary."""

    digest = hashlib.sha256()
    with path.open("rb") as binary:
        for chunk in iter(lambda: binary.read(1024 * 1024), b""):
            digest.update(chunk)
    return EmulatorBinaryPin(sha256=digest.hexdigest(), size_bytes=path.stat().st_size)


def app_state_digest(
    recipe: Mapping[str, Any], *, hf_emulator: EmulatorBinaryPin
) -> str:
    """Digest a recipe with the exact Hugging Face sidecar content pin."""

    payload = {
        "recipe": recipe,
        "emulators": {"huggingface": hf_emulator.to_dict()},
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def app_state_pin(
    *,
    name: str,
    recipe: Mapping[str, Any],
    provides: tuple[str, ...] | list[str],
    hf_emulator: EmulatorBinaryPin,
) -> dict[str, Any]:
    """Return the runner-shaped app-state pin including emulator provenance."""

    return {
        "name": name,
        "digest": app_state_digest(recipe, hf_emulator=hf_emulator),
        "provides": list(provides),
        "emulators": {"huggingface": hf_emulator.to_dict()},
    }


def build_hf_emulator_binary(output: Path) -> EmulatorBinaryPin:
    """Compile the sidecar with the exact toolchain and target pins."""

    bunx = shutil.which("bunx")
    if bunx is None:
        raise RuntimeError("bunx is required to build the Hugging Face emulator")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            bunx,
            f"bun@{BUN_VERSION}",
            "build",
            str(SERVER_SOURCE),
            "--compile",
            f"--target={COMPILE_TARGET}",
            f"--outfile={output}",
        ],
        check=True,
    )
    return emulator_binary_pin(output)


def wait_for_hf_emulator(
    endpoint: str,
    *,
    timeout: float = DEFAULT_READINESS_TIMEOUT,
    poll_interval: float = 0.05,
) -> dict[str, Any]:
    """Block until the sidecar's identity-bearing manifest is live."""

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    manifest_url = f"{endpoint.rstrip('/')}/_emulate/manifest"
    while True:
        try:
            with urllib.request.urlopen(manifest_url, timeout=min(1.0, timeout)) as response:
                manifest = json.load(response)
            if not isinstance(manifest, dict) or manifest.get("id") != "huggingface":
                raise ValueError("readiness manifest has the wrong service identity")
            return manifest
        except (OSError, ValueError, json.JSONDecodeError) as error:
            last_error = error

        if time.monotonic() >= deadline:
            detail = f": {last_error}" if last_error is not None else ""
            raise EmulatorReadinessError(
                f"Hugging Face emulator did not become ready within {timeout:.2f}s{detail}"
            ) from last_error
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
