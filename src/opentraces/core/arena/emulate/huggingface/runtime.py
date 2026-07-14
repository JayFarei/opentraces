"""Packaging and runtime gates for the Hugging Face bench sidecar."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol


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
TRUSTED_BUILD_SCHEMA = "opentraces.hf-emulator-trusted-build.v1"
TRUSTED_BUILD_MANIFEST = SERVER_SOURCE.with_name("trusted-build.json")
BUILD_TIMEOUT_SECONDS = 120.0
BUILD_LOCK_TIMEOUT_SECONDS = BUILD_TIMEOUT_SECONDS + 10.0
BUILD_WORK_ROOT = Path("/tmp/opentraces-hf-build-v1")
BUN_TOOLCHAIN_ROOT = BUILD_WORK_ROOT / "bun-toolchains"


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

    def to_dict(self) -> dict[str, Any]:
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


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"HF emulator {label} is missing or invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"HF emulator {label} is not an object")
    return value


def _trusted_build_record() -> dict[str, Any]:
    record = _read_json_object(TRUSTED_BUILD_MANIFEST, label="trusted build manifest")
    if record.get("schema_version") != TRUSTED_BUILD_SCHEMA:
        raise RuntimeError("HF emulator trusted build manifest has the wrong schema")
    return record


def verified_emulator_binary_pin(path: Path) -> EmulatorBinaryPin:
    """Return a pin only when the binary matches the frozen build inputs."""

    pin = emulator_binary_pin(path)
    provenance_path = emulator_provenance_path(path)
    provenance = _read_json_object(provenance_path, label="binary provenance")
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
    trusted = _trusted_build_record()
    trusted_expected = {
        "binary_sha256": pin.sha256,
        "size_bytes": pin.size_bytes,
        "source_sha256": expected_inputs["source_sha256"],
        "build_inputs_sha256": expected["build_inputs_sha256"],
        "bun_version": BUN_VERSION,
        "target": COMPILE_TARGET,
        "contract_version": "huggingface.v1",
    }
    if any(trusted.get(name) != value for name, value in trusted_expected.items()):
        raise RuntimeError("HF emulator binary does not match the repository trusted build")
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


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _provenance_for_binary(path: Path) -> dict[str, Any]:
    pin = emulator_binary_pin(path)
    return {
        "schema_version": PROVENANCE_SCHEMA,
        **_build_inputs(),
        "build_inputs_sha256": _build_inputs_sha256(),
        "binary_sha256": pin.sha256,
        "size_bytes": pin.size_bytes,
    }


def _is_exact_bun_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        observed = subprocess.run(
            [str(path), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return observed == BUN_VERSION


def _materialize_npx_bun(npx: str) -> Path:
    workspace = BUN_TOOLCHAIN_ROOT / BUN_VERSION
    record_path = workspace / "executable.json"
    with _exclusive_build_lock(workspace / ".resolve.lock"):
        if record_path.is_file():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                cached = Path(record["executable"])
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                cached = None
            if cached is not None and _is_exact_bun_executable(cached):
                return cached

        resolved = subprocess.run(
            [npx, "--yes", "--package", f"bun@{BUN_VERSION}", "which", "bun"],
            check=True,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_SECONDS,
        ).stdout.strip()
        if not resolved:
            raise RuntimeError(f"npx did not materialize Bun {BUN_VERSION}")
        candidate = Path(resolved.splitlines()[-1]).resolve()
        if not _is_exact_bun_executable(candidate):
            raise RuntimeError(f"npx materialized an invalid Bun {BUN_VERSION} executable")
        _write_json_atomic(
            record_path,
            {"bun_version": BUN_VERSION, "executable": str(candidate)},
        )
        return candidate


def pinned_bun_command(*args: str) -> list[str]:
    """Return a command for the exact Bun toolchain on ordinary dev/CI hosts."""

    bunx = shutil.which("bunx")
    if bunx is not None:
        return [bunx, f"bun@{BUN_VERSION}", *args]

    npx = shutil.which("npx")
    if npx is not None:
        return [str(_materialize_npx_bun(npx)), *args]

    bun = shutil.which("bun")
    if bun is not None:
        observed = subprocess.run(
            [bun, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if observed == BUN_VERSION:
            return [bun, *args]
        raise RuntimeError(f"Bun {BUN_VERSION} is required; found {observed or 'unknown'}")

    raise RuntimeError(f"Bun {BUN_VERSION} requires bunx, npx, or an exact bun binary")


def build_hf_emulator_binary(
    output: Path,
    *,
    update_trusted_manifest: bool = False,
) -> EmulatorBinaryPin:
    """Compile the sidecar with the exact toolchain and target pins."""

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workspace = BUILD_WORK_ROOT / _build_inputs_sha256()
    workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
    if workspace.stat().st_uid != os.getuid():
        raise RuntimeError("HF emulator deterministic build workspace has the wrong owner")
    with _exclusive_build_lock(workspace / ".compile.lock"):
        candidate = workspace / "opentraces-hf-emulator"
        candidate_provenance = emulator_provenance_path(candidate)
        candidate.unlink(missing_ok=True)
        candidate_provenance.unlink(missing_ok=True)
        subprocess.run(
            pinned_bun_command(
                "build",
                str(SERVER_SOURCE),
                "--compile",
                f"--target={COMPILE_TARGET}",
                f"--outfile={candidate}",
            ),
            check=True,
            timeout=BUILD_TIMEOUT_SECONDS,
        )
        provenance = _provenance_for_binary(candidate)
        _write_json_atomic(candidate_provenance, provenance)
        if not update_trusted_manifest:
            verified_emulator_binary_pin(candidate)
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_binary:
            temporary_binary.write(candidate.read_bytes())
            temporary_binary_path = Path(temporary_binary.name)
        temporary_binary_path.chmod(0o755)
        temporary_provenance = output.parent / f".{output.name}.provenance.tmp"
        temporary_provenance.write_bytes(candidate_provenance.read_bytes())
        os.replace(temporary_binary_path, output)
        os.replace(temporary_provenance, emulator_provenance_path(output))
    if update_trusted_manifest:
        provenance = _read_json_object(emulator_provenance_path(output), label="binary provenance")
        _write_json_atomic(
            TRUSTED_BUILD_MANIFEST,
            {
                "schema_version": TRUSTED_BUILD_SCHEMA,
                "binary_sha256": provenance["binary_sha256"],
                "size_bytes": provenance["size_bytes"],
                "source_sha256": provenance["source_sha256"],
                "build_inputs_sha256": provenance["build_inputs_sha256"],
                "bun_version": provenance["bun_version"],
                "target": provenance["target"],
                "contract_version": provenance["contract_version"],
                "evidence": "actual pinned build; attach a new real-box proof before merge",
            },
        )
    return verified_emulator_binary_pin(output)


@contextmanager
def _exclusive_build_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + BUILD_LOCK_TIMEOUT_SECONDS
    with path.open("a+", encoding="utf-8") as lock:
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for the HF emulator build cache lock")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


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

    def exec_product(
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
        self._ledger_finalized = False

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
        self.snapshot_ledger(final=True)
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

    def snapshot_ledger(self, *, final: bool = False) -> Path:
        if self._stopped or self._ledger_finalized:
            if self._ledger_path is None:
                raise RuntimeError("Hugging Face emulator stopped without a ledger snapshot")
            return self._ledger_path
        target = self.run_path / LEDGER_EVIDENCE_REF
        target.parent.mkdir(parents=True, exist_ok=True)
        observed = self.runtime.exec(
            self.box,
            ["curl", "-fsS", f"{self.env['HF_ENDPOINT']}/_emulate/ledger"],
            cwd=self.repository,
            timeout=30,
            timing_path=(self.run_path / "artifacts" / "crabbox-timing" / "hf-ledger.json"),
        )
        if observed.returncode != 0:
            raise RuntimeError("Hugging Face emulator ledger could not be collected")
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(observed.stdout, encoding="utf-8")
        os.replace(temporary, target)
        self._ledger_path = target
        self._ledger_finalized = final
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
    with _exclusive_build_lock(binary.parent / ".build.lock"):
        try:
            verified_emulator_binary_pin(binary)
        except (OSError, RuntimeError):
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
    custody_probe = runtime.exec_product(
        box,
        [
            "sh",
            "-c",
            f"if printf CUSTODY_PROBE >> {REMOTE_LEDGER} 2>/dev/null; then exit 1; else exit 0; fi",
        ],
        cwd=repository,
        timeout=30,
        timing_path=(run_path / "artifacts" / "crabbox-timing" / "hf-custody-probe.json"),
    )
    if custody_probe.returncode != 0:
        raise RuntimeError("product user can write the Hugging Face witness ledger")
    custody_unchanged = runtime.exec(
        box,
        ["sh", "-c", f"test ! -s {REMOTE_LEDGER}"],
        cwd=repository,
        timeout=30,
        timing_path=(run_path / "artifacts" / "crabbox-timing" / "hf-custody-unchanged.json"),
    )
    if custody_unchanged.returncode != 0:
        raise RuntimeError("product custody probe changed the Hugging Face witness ledger")

    launch_nonce = secrets.token_hex(32)
    if pin.source_sha256 is None or pin.build_inputs_sha256 is None:
        raise RuntimeError("verified Hugging Face emulator pin is incomplete")

    started = runtime.exec(
        box,
        [
            "sh",
            "-c",
            f'setsid sudo -u opentraces-hf sh -c \'printf "%s\\n" "$$" '
            ">/tmp/opentraces-hf-emulator.pid; exec env "
            f"PORT={DEFAULT_PORT} LEDGER_PATH={REMOTE_LEDGER} "
            f"OPENTRACES_HF_LAUNCH_NONCE={launch_nonce} "
            f"OPENTRACES_HF_CONTRACT_VERSION={pin.contract_version} "
            f"OPENTRACES_HF_SOURCE_SHA256={pin.source_sha256} "
            f"OPENTRACES_HF_BUILD_INPUTS_SHA256={pin.build_inputs_sha256} "
            f"OPENTRACES_HF_BINARY_SHA256={pin.sha256} {REMOTE_BINARY}' "
            ">/tmp/opentraces-hf-emulator.stdout "
            "2>/tmp/opentraces-hf-emulator.stderr </dev/null &",
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
    launch = manifest.get("launch")
    expected_launch = {
        "nonce": launch_nonce,
        "contract_version": pin.contract_version,
        "source_sha256": pin.source_sha256,
        "build_inputs_sha256": pin.build_inputs_sha256,
        "binary_sha256": pin.sha256,
    }
    if not isinstance(launch, dict) or any(
        launch.get(name) != value for name, value in expected_launch.items()
    ):
        raise EmulatorReadinessError("Hugging Face emulator launch identity mismatch")
    pid = launch.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        raise EmulatorReadinessError("Hugging Face emulator launch identity has no valid PID")
    process_binding = runtime.exec(
        box,
        [
            "sh",
            "-c",
            "set -eu; pid=$(cat /tmp/opentraces-hf-emulator.pid); "
            f'test "$pid" = "{pid}"; sudo -u opentraces-hf test -r "/proc/$pid/stat"; '
            "state=$(sudo -u opentraces-hf sed -n "
            '"s/^.*) \\([A-Z]\\) .*$/\\1/p" "/proc/$pid/stat"); '
            'test -n "$state"; test "$state" != "Z"; '
            "owner_uid=$(sudo -u opentraces-hf awk '/^Uid:/{print $2}' "
            '"/proc/$pid/status"); '
            'sidecar_uid=$(id -u opentraces-hf); test "$owner_uid" = "$sidecar_uid"; '
            'executable=$(sudo -u opentraces-hf readlink -f "/proc/$pid/exe"); '
            'digest=$(sudo -u opentraces-hf sha256sum "$executable" | cut -d" " -f1); '
            f'test "$digest" = "{pin.sha256}"; '
            'printf "%s\\n%s\\n%s\\n" "$pid" "$owner_uid" "$digest"',
        ],
        cwd=repository,
        timeout=30,
        timing_path=(run_path / "artifacts" / "crabbox-timing" / "hf-process-binding.json"),
    )
    if process_binding.returncode != 0:
        raise EmulatorReadinessError(
            "Hugging Face emulator readiness is not bound to the running process"
        )
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
            "launch": launch,
            "process_binding": {
                "pid": pid,
                "user": "opentraces-hf",
                "binary_sha256": pin.sha256,
            },
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
