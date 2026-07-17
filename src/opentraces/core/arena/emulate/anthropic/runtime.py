"""Per-run scripted Anthropic wire with independent ledger custody."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any, Mapping, Protocol


SCRIPT_SCHEMA = "opentraces.anthropic-replay-script.v0"
CONTRACT_VERSION = "anthropic-messages-replay.v0"
DEFAULT_PORT = 14319
SERVER_SOURCE = Path(__file__).with_name("server.py")
REMOTE_SERVER = "/opt/bench/emulators/opentraces-anthropic-replay.py"
REMOTE_SCRIPT_STAGE = "/tmp/opentraces-anthropic-replay-script.json"
REMOTE_SCRIPT = "/var/lib/opentraces-bench/anthropic-script.json"
REMOTE_LEDGER = "/var/lib/opentraces-bench/anthropic.jsonl"
LEDGER_EVIDENCE_REF = "ledgers/anthropic.jsonl"
SCRIPT_EVIDENCE_REF = "world/anthropic-script.json"
WORLD_EVIDENCE_REF = "world/anthropic.json"
EMULATOR_USER = "opentraces-model-wire"


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _load_script(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(value, Mapping):
        script = dict(value)
    else:
        try:
            script = json.loads(Path(value).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Anthropic replay script is missing or invalid") from exc
    responses = script.get("responses")
    if script.get("schema_version") != SCRIPT_SCHEMA:
        raise ValueError("Anthropic replay script has the wrong schema")
    if not isinstance(responses, list) or not responses:
        raise ValueError("Anthropic replay script requires responses")
    required = {"id", "model", "content", "stop_reason"}
    for response in responses:
        if not isinstance(response, dict) or not required.issubset(response):
            raise ValueError("Anthropic replay response is incomplete")
        if not isinstance(response.get("content"), list) or not response["content"]:
            raise ValueError("Anthropic replay response requires content")
    return script


class _Runtime(Protocol):
    def copy_into_box(
        self, box: Any, source: Path, destination: str, *, timeout: float = 120
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


class AnthropicReplayLedger:
    def __init__(self, emulator: "AnthropicReplayEmulator") -> None:
        self._emulator = emulator

    @property
    def evidence_ref(self) -> str:
        return LEDGER_EVIDENCE_REF

    def rows(self) -> list[dict[str, Any]]:
        path = self._emulator.snapshot_ledger()
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class AnthropicReplayEmulator:
    def __init__(
        self,
        *,
        runtime: _Runtime,
        box: Any,
        repository: Path,
        run_path: Path,
        script: dict[str, Any],
        pin: dict[str, Any],
    ) -> None:
        self.runtime = runtime
        self.box = box
        self.repository = Path(repository)
        self.run_path = Path(run_path)
        self.script = script
        self.pin = pin
        self.env = {
            "ANTHROPIC_API_KEY": "opentraces-replay-not-a-live-key",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{DEFAULT_PORT}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        self.ledger = AnthropicReplayLedger(self)
        self._stopped = False
        self._ledger_path: Path | None = None

    def _persist_ledger(self, raw: str) -> Path:
        target = self.run_path / LEDGER_EVIDENCE_REF
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(raw, encoding="utf-8")
        os.replace(temporary, target)
        self._ledger_path = target
        return target

    def snapshot_ledger(self) -> Path:
        if self._stopped:
            if self._ledger_path is None:
                raise RuntimeError("Anthropic replay stopped without a ledger")
            return self._ledger_path
        observed = self.runtime.exec(
            self.box,
            ["curl", "-fsS", f"http://127.0.0.1:{DEFAULT_PORT}/_emulate/ledger"],
            cwd=self.repository,
            timeout=30,
            timing_path=self.run_path / "artifacts/crabbox-timing/anthropic-ledger.json",
        )
        if observed.returncode != 0:
            raise RuntimeError("Anthropic replay ledger could not be collected")
        return self._persist_ledger(observed.stdout)

    def stop(self) -> None:
        if self._stopped:
            return
        observed = self.runtime.exec(
            self.box,
            [
                "sh",
                "-c",
                "set -eu; pid=$(cat /tmp/opentraces-anthropic-replay.pid); "
                f'sudo -u {EMULATOR_USER} test -r "/proc/$pid/stat"; '
                "state=$(sed -n 's/^.*) \\([A-Z]\\) .*$/\\1/p' \"/proc/$pid/stat\"); "
                'test -n "$state"; test "$state" != Z; '
                "pgid=$(ps -o pgid= -p \"$pid\" | tr -d ' '); "
                'case "$pgid" in \'\'|*[!0-9]*) exit 1;; esac; test "$pgid" -gt 1; '
                "self_pgid=$(ps -o pgid= -p \"$$\" | tr -d ' '); "
                'test "$pgid" != "$self_pgid"; sudo kill -- -"$pgid"; '
                'i=0; while kill -0 "$pid" 2>/dev/null; do '
                "state=$(sed -n 's/^.*) \\([A-Z]\\) .*$/\\1/p' \"/proc/$pid/stat\" "
                '2>/dev/null || true); test "$state" = Z && break; '
                "test $i -lt 100 || exit 1; i=$((i+1)); sleep 0.05; done; "
                f"sudo -u {EMULATOR_USER} cat {REMOTE_LEDGER}",
            ],
            cwd=self.repository,
            timeout=30,
            timing_path=self.run_path / "artifacts/crabbox-timing/anthropic-stop.json",
        )
        if observed.returncode != 0:
            raise RuntimeError("Anthropic replay emulator did not stop cleanly")
        self._persist_ledger(observed.stdout)
        self._stopped = True


def start_anthropic_replay_emulator(
    *,
    runtime: _Runtime,
    box: Any,
    repository: Path,
    run_path: Path,
    script: Mapping[str, Any] | str | Path,
) -> AnthropicReplayEmulator:
    """Stage, bind, start, and identify one immutable scripted model wire."""

    parsed = _load_script(script)
    encoded = _canonical_json(parsed)
    script_sha = hashlib.sha256(encoded).hexdigest()
    source_sha = hashlib.sha256(SERVER_SOURCE.read_bytes()).hexdigest()
    local_script = Path(run_path) / SCRIPT_EVIDENCE_REF
    local_script.parent.mkdir(parents=True, exist_ok=True)
    local_script.write_bytes(encoded)
    remote_server = runtime.copy_into_box(box, SERVER_SOURCE, REMOTE_SERVER)
    remote_script_stage = runtime.copy_into_box(box, local_script, REMOTE_SCRIPT_STAGE)

    checksums = runtime.exec(
        box,
        ["sha256sum", remote_server, remote_script_stage],
        cwd=repository,
        timeout=30,
        timing_path=Path(run_path) / "artifacts/crabbox-timing/anthropic-sha256.json",
    )
    observed = {line.split()[-1]: line.split()[0] for line in checksums.stdout.splitlines()}
    if (
        checksums.returncode != 0
        or observed.get(remote_server) != source_sha
        or observed.get(remote_script_stage) != script_sha
    ):
        raise RuntimeError("Anthropic replay in-box checksum mismatch")

    prepared = runtime.exec(
        box,
        [
            "sh",
            "-c",
            "set -eu; "
            f"if ! id -u {EMULATOR_USER} >/dev/null 2>&1; then "
            f"sudo useradd --system --no-create-home --shell /usr/sbin/nologin {EMULATOR_USER}; fi; "
            f"sudo install -d -m 0700 -o {EMULATOR_USER} -g {EMULATOR_USER} "
            "/var/lib/opentraces-bench; "
            f"sudo install -m 0400 -o {EMULATOR_USER} -g {EMULATOR_USER} "
            f"{remote_script_stage} {REMOTE_SCRIPT}; "
            f"sudo install -m 0600 -o {EMULATOR_USER} -g {EMULATOR_USER} /dev/null {REMOTE_LEDGER}",
        ],
        cwd=repository,
        timeout=30,
        timing_path=Path(run_path) / "artifacts/crabbox-timing/anthropic-custody.json",
    )
    if prepared.returncode != 0:
        raise RuntimeError("Anthropic replay ledger custody boundary failed")
    custody = runtime.exec_product(
        box,
        [
            "sh",
            "-c",
            f"if printf forged >> {REMOTE_LEDGER} 2>/dev/null; then exit 1; else exit 0; fi",
        ],
        cwd=repository,
        timeout=30,
        timing_path=Path(run_path) / "artifacts/crabbox-timing/anthropic-custody-probe.json",
    )
    if custody.returncode != 0:
        raise RuntimeError("product user can write the Anthropic replay witness ledger")

    nonce = secrets.token_hex(32)
    environment = {
        "PORT": str(DEFAULT_PORT),
        "SCRIPT_PATH": REMOTE_SCRIPT,
        "LEDGER_PATH": REMOTE_LEDGER,
        "OPENTRACES_ANTHROPIC_LAUNCH_NONCE": nonce,
        "OPENTRACES_ANTHROPIC_SOURCE_SHA256": source_sha,
        "OPENTRACES_ANTHROPIC_SCRIPT_SHA256": script_sha,
    }
    started = runtime.exec(
        box,
        [
            "sh",
            "-c",
            f"setsid sudo -u {EMULATOR_USER} env "
            + " ".join(f'{name}="${name}"' for name in sorted(environment))
            + f' sh -c \'printf "%s\\n" "$$" > /tmp/opentraces-anthropic-replay.pid; '
            f"exec python3 {REMOTE_SERVER}' >/tmp/opentraces-anthropic-replay.stdout "
            "2>/tmp/opentraces-anthropic-replay.stderr </dev/null &",
        ],
        cwd=repository,
        env=environment,
        timeout=30,
        timing_path=Path(run_path) / "artifacts/crabbox-timing/anthropic-start.json",
    )
    if started.returncode != 0:
        raise RuntimeError("Anthropic replay emulator failed to start")
    ready = runtime.exec(
        box,
        [
            "sh",
            "-c",
            "i=0; while test $i -lt 100; do "
            f"if curl -fsS http://127.0.0.1:{DEFAULT_PORT}/_emulate/manifest; then exit 0; fi; "
            "i=$((i+1)); sleep 0.05; done; exit 1",
        ],
        cwd=repository,
        timeout=10,
        timing_path=Path(run_path) / "artifacts/crabbox-timing/anthropic-readiness.json",
    )
    try:
        manifest = json.loads(ready.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Anthropic replay manifest was not JSON") from exc
    launch = manifest.get("launch")
    script_pin = manifest.get("script")
    if (
        ready.returncode != 0
        or manifest.get("id") != "anthropic-scripted"
        or manifest.get("contract_version") != CONTRACT_VERSION
        or not isinstance(launch, dict)
        or launch.get("nonce") != nonce
        or launch.get("source_sha256") != source_sha
        or not isinstance(launch.get("pid"), int)
        or not isinstance(script_pin, dict)
        or script_pin.get("sha256") != script_sha
    ):
        raise RuntimeError("Anthropic replay manifest identity mismatch")
    pid = launch["pid"]
    binding = runtime.exec(
        box,
        [
            "sh",
            "-c",
            f'set -eu; pid=$(cat /tmp/opentraces-anthropic-replay.pid); test "$pid" = "{pid}"; '
            f'sudo -u {EMULATOR_USER} test -r "/proc/$pid/cmdline"; '
            f"tr '\\0' '\\n' < \"/proc/$pid/cmdline\" | grep -Fx {REMOTE_SERVER}; "
            f'test "$(sha256sum {REMOTE_SERVER} | cut -d\' \' -f1)" = "{source_sha}"',
        ],
        cwd=repository,
        timeout=30,
        timing_path=Path(run_path) / "artifacts/crabbox-timing/anthropic-process-binding.json",
    )
    if binding.returncode != 0:
        raise RuntimeError("Anthropic replay readiness is not bound to its process")

    pin = {
        "kind": "anthropic-scripted",
        "contract_version": CONTRACT_VERSION,
        "script_sha256": f"sha256:{script_sha}",
        "script_ref": SCRIPT_EVIDENCE_REF,
        "emulator_build": {
            "source_sha256": f"sha256:{source_sha}",
            "runtime": manifest.get("runtime"),
        },
        "evidence_ref": LEDGER_EVIDENCE_REF,
    }
    world = {
        "schema_version": "opentraces.bench.world.anthropic-replay.v0",
        "endpoint": f"http://127.0.0.1:{DEFAULT_PORT}",
        "manifest": manifest,
        "ledger_custody": {"writer": EMULATOR_USER, "product_writable": False},
        "pin": pin,
    }
    world_path = Path(run_path) / WORLD_EVIDENCE_REF
    world_path.parent.mkdir(parents=True, exist_ok=True)
    world_path.write_text(json.dumps(world, indent=2, sort_keys=True) + "\n")
    return AnthropicReplayEmulator(
        runtime=runtime,
        box=box,
        repository=repository,
        run_path=run_path,
        script=parsed,
        pin=pin,
    )
