"""Packaging and runtime gates for the Hugging Face bench sidecar."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


BUN_VERSION = "1.3.6"
COMPILE_TARGET = "bun-linux-arm64"
DEFAULT_PORT = 14318
DEFAULT_READINESS_TIMEOUT = 5.0
SERVER_SOURCE = Path(__file__).with_name("server.ts")
REMOTE_BINARY = "/opt/bench/emulators/opentraces-hf-emulator"
REMOTE_LEDGER = "/var/lib/opentraces-bench/huggingface.jsonl"
LEDGER_EVIDENCE_REF = "ledgers/huggingface.jsonl"
WORLD_EVIDENCE_REF = "world/huggingface.json"
BASELINE_TOKEN = "hf_bench_user_token"
PROVENANCE_SCHEMA = "opentraces.hf-emulator-build.v1"


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
    source_sha256: str | None = None
    build_inputs_sha256: str | None = None
    provenance: str = "unverified"

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def emulator_binary_pin(path: Path) -> EmulatorBinaryPin:
    """Return the pin of record for one compiled sidecar binary."""

    digest = hashlib.sha256()
    with path.open("rb") as binary:
        for chunk in iter(lambda: binary.read(1024 * 1024), b""):
            digest.update(chunk)
    return EmulatorBinaryPin(sha256=digest.hexdigest(), size_bytes=path.stat().st_size)


def _build_inputs() -> dict[str, str]:
    return {
        "bun_version": BUN_VERSION,
        "target": COMPILE_TARGET,
        "contract_version": "huggingface.v1",
        "source_sha256": hashlib.sha256(SERVER_SOURCE.read_bytes()).hexdigest(),
    }


def _build_inputs_sha256() -> str:
    encoded = json.dumps(_build_inputs(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def emulator_provenance_path(binary: Path) -> Path:
    return binary.with_name(f"{binary.name}.provenance.json")


def verified_emulator_binary_pin(path: Path) -> EmulatorBinaryPin:
    """Return a pin only when the binary matches the frozen build inputs."""

    pin = emulator_binary_pin(path)
    provenance_path = emulator_provenance_path(path)
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("HF emulator binary provenance is missing or invalid") from exc
    expected_inputs = _build_inputs()
    expected = {
        "schema_version": PROVENANCE_SCHEMA,
        **expected_inputs,
        "build_inputs_sha256": _build_inputs_sha256(),
        "binary_sha256": pin.sha256,
        "size_bytes": pin.size_bytes,
    }
    if provenance != expected:
        raise RuntimeError("HF emulator binary provenance does not match build inputs")
    return EmulatorBinaryPin(
        sha256=pin.sha256,
        size_bytes=pin.size_bytes,
        bun_version=BUN_VERSION,
        target=COMPILE_TARGET,
        contract_version="huggingface.v1",
        source_sha256=expected_inputs["source_sha256"],
        build_inputs_sha256=expected["build_inputs_sha256"],
        provenance="verified",
    )


def app_state_digest(recipe: Mapping[str, Any], *, hf_emulator: EmulatorBinaryPin) -> str:
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
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


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
        "recipe": {
            "base": recipe,
            "emulators": {"huggingface": hf_emulator.to_dict()},
        },
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
    pin = emulator_binary_pin(output)
    provenance = {
        "schema_version": PROVENANCE_SCHEMA,
        **_build_inputs(),
        "build_inputs_sha256": _build_inputs_sha256(),
        "binary_sha256": pin.sha256,
        "size_bytes": pin.size_bytes,
    }
    emulator_provenance_path(output).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return verified_emulator_binary_pin(output)


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


class _BoxRuntime(Protocol):
    """The existing concrete Crabbox operations used by this sidecar."""

    def copy_into_box(
        self,
        box: Any,
        source: Path,
        destination: str,
        *,
        timeout: float = 120,
    ) -> str: ...

    def exec(
        self,
        box: Any,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60,
        timing_path: Path,
    ) -> Any: ...

    def collect(
        self,
        box: Any,
        globs: list[str],
        *,
        destination: Path,
        repository: Path,
    ) -> dict[str, Path]: ...


class HuggingFaceLedger:
    """Read the independently collected append-only JSONL witness."""

    def __init__(self, handle: "HuggingFaceEmulator") -> None:
        self._handle = handle

    @property
    def evidence_ref(self) -> str:
        return LEDGER_EVIDENCE_REF

    def rows(self) -> list[dict[str, Any]]:
        path = self._handle.snapshot_ledger()
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def contains(
        self,
        *,
        method: str,
        path_prefix: str,
        operation_id: str,
    ) -> bool:
        return any(
            row.get("method") == method
            and str(row.get("path") or "").startswith(path_prefix)
            and row.get("operation_id") == operation_id
            and (row.get("response") or {}).get("status") == 200
            for row in self.rows()
        )


class HuggingFaceEmulator:
    """The one concrete bench.v0 world sidecar and its witness lifecycle."""

    def __init__(
        self,
        *,
        runtime: _BoxRuntime,
        box: Any,
        repository: Path,
        run_path: Path,
        binary_pin: EmulatorBinaryPin,
        world_setup: dict[str, Any],
    ) -> None:
        self.runtime = runtime
        self.box = box
        self.repository = Path(repository)
        self.run_path = Path(run_path)
        self.binary_pin = binary_pin
        self.world_setup = world_setup
        self.env = {
            "HF_ENDPOINT": f"http://127.0.0.1:{DEFAULT_PORT}",
            "HF_TOKEN": BASELINE_TOKEN,
        }
        self.ledger = HuggingFaceLedger(self)
        self._stopped = False
        self._ledger_path: Path | None = None

    @property
    def pin(self) -> dict[str, Any]:
        return {
            **self.binary_pin.to_dict(),
            "setup": self.world_setup,
            "evidence_ref": WORLD_EVIDENCE_REF,
        }

    def stop(self) -> None:
        if self._stopped:
            return
        self.snapshot_ledger()
        timing = self.run_path / "artifacts" / "crabbox-timing" / "hf-stop.json"
        stopped = self.runtime.exec(
            self.box,
            [
                "sh",
                "-c",
                "if test -f /tmp/opentraces-hf-emulator.pid; then "
                "kill -- -$(cat /tmp/opentraces-hf-emulator.pid) 2>/dev/null || "
                "kill $(cat /tmp/opentraces-hf-emulator.pid) 2>/dev/null || true; fi",
            ],
            cwd=self.repository,
            timeout=30,
            timing_path=timing,
        )
        if stopped.returncode != 0:
            raise RuntimeError("Hugging Face emulator did not stop cleanly")
        self._stopped = True

    def snapshot_ledger(self) -> Path:
        if self._ledger_path is not None:
            return self._ledger_path
        target = self.run_path / LEDGER_EVIDENCE_REF
        target.parent.mkdir(parents=True, exist_ok=True)
        observed = self.runtime.exec(
            self.box,
            ["curl", "-fsS", f"{self.env['HF_ENDPOINT']}/_emulate/ledger"],
            cwd=self.repository,
            timeout=30,
            timing_path=(
                self.run_path / "artifacts" / "crabbox-timing" / "hf-ledger.json"
            ),
        )
        if observed.returncode != 0:
            raise RuntimeError("Hugging Face emulator ledger could not be collected")
        target.write_text(observed.stdout, encoding="utf-8")
        self._ledger_path = target
        return target


def _binary_for_run() -> Path:
    configured = os.environ.get("OPENTRACES_HF_EMULATOR_BINARY")
    if configured:
        binary = Path(configured).expanduser().resolve()
        if not binary.is_file():
            raise FileNotFoundError(f"configured HF emulator binary does not exist: {binary}")
        verified_emulator_binary_pin(binary)
        return binary
    build_digest = _build_inputs_sha256()
    binary = (
        Path(tempfile.gettempdir())
        / "opentraces-bench"
        / "huggingface"
        / build_digest
        / "opentraces-hf-emulator"
    )
    if not binary.is_file() or not emulator_provenance_path(binary).is_file():
        build_hf_emulator_binary(binary)
    verified_emulator_binary_pin(binary)
    return binary


def start_huggingface_emulator(
    *,
    runtime: _BoxRuntime,
    box: Any,
    repository: Path,
    run_path: Path,
) -> HuggingFaceEmulator:
    """Stage, attest, start, and identify the concrete Hugging Face world."""

    binary = _binary_for_run()
    pin = verified_emulator_binary_pin(binary)
    remote = runtime.copy_into_box(box, binary, REMOTE_BINARY)
    checksum = runtime.exec(
        box,
        ["sha256sum", remote],
        cwd=repository,
        timeout=30,
        timing_path=run_path / "artifacts" / "crabbox-timing" / "hf-sha256.json",
    )
    observed_digest = checksum.stdout.strip().split(maxsplit=1)[0] if checksum.stdout else ""
    if checksum.returncode != 0 or observed_digest != pin.sha256:
        raise RuntimeError("Hugging Face emulator in-box checksum mismatch")

    prepared = runtime.exec(
        box,
        [
            "sh",
            "-c",
            "set -eu; "
            "if ! id -u opentraces-hf >/dev/null 2>&1; then "
            "sudo useradd --system --no-create-home --shell /usr/sbin/nologin opentraces-hf; "
            "fi; "
            "sudo install -d -m 0700 -o opentraces-hf -g opentraces-hf "
            "/var/lib/opentraces-bench; "
            f"sudo install -m 0600 -o opentraces-hf -g opentraces-hf /dev/null {REMOTE_LEDGER}",
        ],
        cwd=repository,
        timeout=30,
        timing_path=run_path / "artifacts" / "crabbox-timing" / "hf-custody.json",
    )
    if prepared.returncode != 0:
        raise RuntimeError("Hugging Face emulator ledger custody boundary failed")
    custody_probe = runtime.exec(
        box,
        [
            "sh",
            "-c",
            f"if printf CUSTODY_PROBE >> {REMOTE_LEDGER} 2>/dev/null; "
            "then exit 1; else exit 0; fi",
        ],
        cwd=repository,
        timeout=30,
        timing_path=(
            run_path / "artifacts" / "crabbox-timing" / "hf-custody-probe.json"
        ),
    )
    if custody_probe.returncode != 0:
        raise RuntimeError("product user can write the Hugging Face witness ledger")

    started = runtime.exec(
        box,
        [
            "sh",
            "-c",
            f"setsid sudo -u opentraces-hf env PORT={DEFAULT_PORT} "
            f"LEDGER_PATH={REMOTE_LEDGER} {REMOTE_BINARY} "
            ">/tmp/opentraces-hf-emulator.stdout "
            "2>/tmp/opentraces-hf-emulator.stderr </dev/null & "
            "echo $! >/tmp/opentraces-hf-emulator.pid",
        ],
        cwd=repository,
        timeout=30,
        timing_path=run_path / "artifacts" / "crabbox-timing" / "hf-start.json",
    )
    if started.returncode != 0:
        raise RuntimeError("Hugging Face emulator failed to start")

    readiness = runtime.exec(
        box,
        [
            "sh",
            "-c",
            "i=0; while test $i -lt 100; do "
            f"if curl -fsS http://127.0.0.1:{DEFAULT_PORT}/_emulate/manifest; "
            "then exit 0; fi; i=$((i+1)); sleep 0.05; done; exit 1",
        ],
        cwd=repository,
        timeout=DEFAULT_READINESS_TIMEOUT,
        timing_path=run_path / "artifacts" / "crabbox-timing" / "hf-readiness.json",
    )
    try:
        manifest = json.loads(readiness.stdout)
    except json.JSONDecodeError as exc:
        raise EmulatorReadinessError("Hugging Face emulator manifest was not JSON") from exc
    if readiness.returncode != 0 or manifest.get("id") != "huggingface":
        raise EmulatorReadinessError("Hugging Face emulator manifest identity mismatch")
    operations = list((manifest.get("specs") or [{}])[0].get("operations") or [])
    capabilities = {
        status: sorted(
            str(operation.get("operationId"))
            for operation in operations
            if operation.get("status") == status
        )
        for status in ("hand-authored", "partial", "unsupported")
    }
    world_setup = {
        "schema_version": "opentraces.bench.world.huggingface.v1",
        "endpoint": f"http://127.0.0.1:{DEFAULT_PORT}",
        "port": DEFAULT_PORT,
        "readiness": {
            "path": "/_emulate/manifest",
            "service_id": manifest.get("id"),
        },
        "baseline": {
            "identity": {"name": "bench", "type": "user"},
            "seeded_state": {"repos": []},
        },
        "capabilities": capabilities,
        "manifest": manifest,
        "ledger_custody": {
            "writer": "opentraces-hf",
            "product_writable": False,
            "collection": "read-only sidecar endpoint",
        },
    }
    world_path = run_path / WORLD_EVIDENCE_REF
    world_path.parent.mkdir(parents=True, exist_ok=True)
    world_path.write_text(
        json.dumps(world_setup, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return HuggingFaceEmulator(
        runtime=runtime,
        box=box,
        repository=repository,
        run_path=run_path,
        binary_pin=pin,
        world_setup=world_setup,
    )
