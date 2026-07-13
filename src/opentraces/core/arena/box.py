"""Pinned Crabbox 0.38.0 adapter for disposable bench boxes."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .diagnostics import sanitize_diagnostic_text, sanitize_diagnostic_value, sanitize_reason


PINNED_CRABBOX_VERSION = "0.38.0"
PINNED_LOCAL_IMAGE = "ubuntu:24.04"
DEFAULT_PROVIDER = "local-container"

VERSION_REAUDIT = (
    "re-audit warmup/run/checkpoint flags, the explicit image default, inspect JSON, "
    "timing-record schema, and any new ssh-config override before changing the pin"
)
TMPDIR_REMEDY = (
    "bench: TMPDIR must resolve under $HOME for the colima docker backend; set "
    "TMPDIR=$HOME/crabbox-tmp (bench does this automatically in the crabbox child env). "
    "On Docker Desktop or Linux native docker this check is a no-op."
)
SSH_REMEDY = (
    "bench: your ~/.ssh/config contains a keyword this OpenSSH build rejects "
    "(UseKeychain/AddKeysToKeychain) outside an IgnoreUnknown guard, which makes "
    "crabbox warmup hang in ssh-auth on a healthy box. Add 'IgnoreUnknown "
    "UseKeychain,AddKeysToKeychain' at the top of ~/.ssh/config, or move those "
    "keywords under the specific Host block that needs them. bench will not edit "
    "your ssh config for you."
)


class CrabboxRefusal(RuntimeError):
    """A named precondition refusal, never an unbounded Crabbox hang."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Box:
    id: str
    slug: str
    provider: str
    sandbox_tier: str
    ssh_host: str
    ssh_user: str
    ssh_port: str
    ssh_key: str


@dataclass(frozen=True)
class BoxCommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timing: dict[str, Any]


Runner = Callable[..., subprocess.CompletedProcess[str]]


_CRABBOX_HOST_CANDIDATES = (
    "crabbox",
    "crabbox.local",
    "cbx_probe",
    "localhost",
    "127.0.0.1",
)
def _host_scope_may_apply(patterns: Sequence[str] | None) -> bool:
    """Approximate the Host scopes Crabbox may traverse during warmup.

    Literal third-party blocks such as ``Host github.com`` are irrelevant,
    while wildcard/global and Crabbox/loopback blocks remain conservative.
    Negated patterns follow OpenSSH's "a negation wins" matching rule.
    """

    if patterns is None:
        return True
    positive = [pattern for pattern in patterns if not pattern.startswith("!")]
    negative = [pattern[1:] for pattern in patterns if pattern.startswith("!")]
    for candidate in _CRABBOX_HOST_CANDIDATES:
        if not any(fnmatch.fnmatchcase(candidate, pattern) for pattern in positive):
            continue
        if any(fnmatch.fnmatchcase(candidate, pattern) for pattern in negative):
            continue
        return True
    return False


def _ignored(keyword: str, patterns: set[str]) -> bool:
    return any(fnmatch.fnmatchcase(keyword.lower(), pattern.lower()) for pattern in patterns)


def _sanitize_timing(value: Any) -> Any:
    """Timing records are numeric evidence; redact all free-form strings."""

    sanitized = sanitize_diagnostic_value(value)
    if isinstance(sanitized, Mapping):
        return {str(key): _sanitize_timing(child) for key, child in sanitized.items()}
    if isinstance(sanitized, list):
        return [_sanitize_timing(item) for item in sanitized]
    if isinstance(sanitized, str):
        return sanitized if sanitized in {"[redacted]", "[host-path]"} else "[redacted]"
    return sanitized


def _operation_name(argv: Sequence[str]) -> str:
    if not argv:
        return "unknown"
    if len(argv) > 1 and argv[0] == "crabbox":
        return str(argv[1]).lstrip("-") or "version"
    return Path(str(argv[0])).name


def _subprocess_runner(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        env=dict(env),
        timeout=timeout,
        text=True,
        capture_output=True,
        check=False,
    )


def sandbox_tier_for_provider(provider: str) -> str:
    """Classify containment ourselves; provider metadata is never a trust tier."""

    if provider in {"firecracker", "apple-vm", "proxmox"}:
        return "microvm"
    if provider in {"ssh", "host", "direct"}:
        return "none"
    return "container"


class CrabboxRuntime:
    """Concrete five-verb box adapter frozen against Crabbox 0.38.0."""

    crabbox_version = PINNED_CRABBOX_VERSION

    def __init__(
        self,
        *,
        runner: Runner = _subprocess_runner,
        home: Path | None = None,
        ssh_config: Path | None = None,
        provider: str = DEFAULT_PROVIDER,
        image: str = PINNED_LOCAL_IMAGE,
        command: str = "crabbox",
    ) -> None:
        self.runner = runner
        self.home = Path(home) if home is not None else Path.home()
        self.ssh_config = ssh_config or self.home / ".ssh" / "config"
        self.provider = provider
        self.image = image
        self.command = command
        self._diagnostics: list[dict[str, Any]] = []
        self._run_evidence_root: Path | None = None
        self.child_env = dict(os.environ)
        tmpdir = self.home / "crabbox-tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        self.child_env["TMPDIR"] = str(tmpdir)
        colima_socket = self.home / ".colima" / "default" / "docker.sock"
        if "DOCKER_HOST" not in self.child_env and colima_socket.exists():
            self.child_env["DOCKER_HOST"] = f"unix://{colima_socket}"

    def configure_run_evidence(self, run_root: Path) -> None:
        """Route Crabbox timing records into the pending run's custody."""

        self._run_evidence_root = Path(run_root)

    def _timing_path(self, repository: Path, name: str) -> Path:
        if self._run_evidence_root is not None:
            return self._run_evidence_root / "artifacts" / "crabbox-timing" / f"{name}.json"
        return repository / ".crabbox" / f"bench-{name}-timing.json"

    def _evidence_ref(self, path: Path) -> str | None:
        if self._run_evidence_root is None:
            return None
        try:
            return path.relative_to(self._run_evidence_root).as_posix()
        except ValueError:
            return None

    def _call(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        child_env = dict(self.child_env)
        if env:
            child_env.update({str(key): str(value) for key, value in env.items()})
        started = time.monotonic()
        try:
            completed = self.runner(list(argv), cwd=cwd, env=child_env, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._diagnostics.append(
                {
                    "operation": _operation_name(argv),
                    "returncode": None,
                    "stdout": sanitize_diagnostic_text(str(exc.stdout or "")),
                    "stderr": sanitize_diagnostic_text(str(exc.stderr or "")),
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "timed_out": True,
                }
            )
            raise CrabboxRefusal("crabbox_timeout", f"bounded command timed out: {argv[1]}") from exc
        self._diagnostics.append(
            {
                "operation": _operation_name(argv),
                "returncode": completed.returncode,
                "stdout": sanitize_diagnostic_text(completed.stdout),
                "stderr": sanitize_diagnostic_text(completed.stderr),
                "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                "timed_out": False,
            }
        )
        return completed

    def diagnostic_records(self) -> list[dict[str, Any]]:
        """Return the private raw lifecycle observations collected so far."""

        return [dict(record) for record in self._diagnostics]

    def _assert_version(self) -> None:
        result = self._call([self.command, "--version"], timeout=10)
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", f"{result.stdout}\n{result.stderr}")
        observed = match.group(1) if match else "unparseable"
        if result.returncode != 0 or observed != PINNED_CRABBOX_VERSION:
            raise CrabboxRefusal(
                "crabbox_version_mismatch",
                f"requires crabbox {PINNED_CRABBOX_VERSION}, observed {observed}; {VERSION_REAUDIT}",
            )

    def _assert_ssh_config(self) -> None:
        if not self.ssh_config.is_file():
            return
        host_patterns: list[str] | None = None
        global_ignored: set[str] = set()
        local_ignored: set[str] = set()
        for raw_line in self.ssh_config.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            directive = parts[0].lower()
            values = parts[1:]
            if directive == "host":
                host_patterns = values
                local_ignored = set()
                continue
            if directive == "ignoreunknown":
                patterns = {
                    pattern
                    for value in values
                    for pattern in value.replace(",", " ").split()
                }
                if host_patterns is None:
                    global_ignored.update(patterns)
                elif _host_scope_may_apply(host_patterns):
                    local_ignored.update(patterns)
                continue
            if directive not in {"usekeychain", "addkeystokeychain"}:
                continue
            if not _host_scope_may_apply(host_patterns):
                continue
            canonical = "UseKeychain" if directive == "usekeychain" else "AddKeysToKeychain"
            if not _ignored(canonical, global_ignored | local_ignored):
                raise CrabboxRefusal("ssh_config_incompatible", SSH_REMEDY)

    def lease(self) -> Box:
        self._assert_version()
        self._assert_ssh_config()
        warmup_argv = [
            self.command,
            "warmup",
            "--provider",
            self.provider,
            "--local-container-image",
            self.image,
        ]
        warmup = self._call(warmup_argv, timeout=600)
        if warmup.returncode != 0:
            raise CrabboxRefusal(
                "lease_failed", (warmup.stderr or warmup.stdout or "crabbox warmup failed").strip()
            )
        match = re.search(r"\bcbx_[A-Za-z0-9]+\b", f"{warmup.stdout}\n{warmup.stderr}")
        if not match:
            raise CrabboxRefusal("lease_identity_missing", "warmup did not report a cbx_ lease id")
        lease_id = match.group(0)
        try:
            inspected = self._call(
                [
                    self.command,
                    "inspect",
                    "--id",
                    lease_id,
                    "--provider",
                    self.provider,
                    "--json",
                ],
                timeout=30,
            )
            try:
                facts = json.loads(inspected.stdout)
            except json.JSONDecodeError as exc:
                raise CrabboxRefusal(
                    "lease_inspect_invalid", "inspect did not emit JSON"
                ) from exc
            if not isinstance(facts, Mapping):
                raise CrabboxRefusal(
                    "lease_inspect_invalid", "inspect did not emit an object"
                )
            if inspected.returncode != 0 or not facts.get("ready") or facts.get(
                "state"
            ) not in {"leased", "ready"}:
                raise CrabboxRefusal(
                    "lease_not_ready", "inspect did not report a ready lease"
                )
            required_facts = ("id", "slug", "sshHost", "sshUser", "sshPort", "sshKey")
            if any(
                facts.get(name) is None or facts.get(name) == ""
                for name in required_facts
            ):
                raise CrabboxRefusal(
                    "lease_inspect_incomplete", "inspect omitted required lease facts"
                )
            box = Box(
                id=str(facts["id"]),
                slug=str(facts["slug"]),
                provider=self.provider,
                sandbox_tier=sandbox_tier_for_provider(self.provider),
                ssh_host=str(facts["sshHost"]),
                ssh_user=str(facts["sshUser"]),
                ssh_port=str(facts["sshPort"]),
                ssh_key=str(facts["sshKey"]),
            )
            ssh_probe = self._call(
                [
                    "ssh",
                    "-F",
                    "/dev/null",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-i",
                    box.ssh_key,
                    "-p",
                    box.ssh_port,
                    f"{box.ssh_user}@{box.ssh_host}",
                    "true",
                ],
                timeout=10,
            )
            if ssh_probe.returncode != 0:
                raise CrabboxRefusal("ssh_probe_failed", SSH_REMEDY)
        except Exception:
            try:
                self._release_id(lease_id, provider=self.provider)
            except Exception as cleanup_error:
                self._diagnostics.append(
                    {
                        "operation": "cleanup",
                        **sanitize_reason(
                            getattr(cleanup_error, "code", "release_failed"),
                            cleanup_error,
                        ),
                    }
                )
            raise
        return box

    def exec(
        self,
        box: Box,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60,
        timing_path: Path,
    ) -> BoxCommandResult:
        timing_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.command,
            "run",
            "--id",
            box.id,
            "--reclaim",
            "--no-sync",
            "--provider",
            box.provider,
            "--timing-record",
            str(timing_path),
        ]
        for name in sorted((env or {}).keys()):
            command.extend(["--allow-env", name])
        command.extend(["--", *map(str, argv)])
        completed = self._call(command, cwd=cwd, env=env, timeout=timeout)
        timing: dict[str, Any] = {}
        if timing_path.is_file():
            try:
                timing = _sanitize_timing(json.loads(timing_path.read_text(encoding="utf-8")))
                timing_path.write_text(
                    json.dumps(timing, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
            except json.JSONDecodeError:
                timing = {"invalid": True}
        if self._diagnostics:
            self._diagnostics[-1]["timing"] = timing
            timing_ref = self._evidence_ref(timing_path)
            if timing_ref is not None:
                self._diagnostics[-1]["timing_ref"] = timing_ref
        return BoxCommandResult(
            argv=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timing=timing,
        )

    def materialize(self, box: Box, app_state: str, *, repository: Path) -> dict[str, Any]:
        """Materialize the first concrete recipe from locally built wheels."""

        if app_state == "base-only":
            timing_path = self._timing_path(repository, "base-provides")
            probe = self.exec(
                box,
                ["sh", "-c", "command -v python3 && command -v git && command -v curl && command -v script"],
                timeout=30,
                timing_path=timing_path,
            )
            if probe.returncode != 0:
                raise CrabboxRefusal("app_state_provides_missing", probe.stderr.strip())
            material = f"{box.provider}\n{self.image}\npython3\ngit\ncurl\nscript\n"
            pin = {
                "name": app_state,
                "digest": f"sha256:{hashlib.sha256(material.encode()).hexdigest()}",
                "provides": ["python3", "git", "curl", "script"],
            }
            timing_ref = self._evidence_ref(timing_path)
            if timing_ref is not None:
                pin["observation_refs"] = [timing_ref]
            return pin
        if app_state != "install-only":
            raise CrabboxRefusal("unknown_app_state", f"no recipe named {app_state!r}")
        wheels = sorted((repository / "dist").glob("*.whl"))
        if not wheels:
            raise CrabboxRefusal(
                "app_state_wheel_missing",
                "install-only requires locally built wheels under dist/*.whl",
            )
        digests: list[str] = []
        remote_wheels: list[str] = []
        for wheel in wheels:
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            digests.append(digest)
            remote = f"/tmp/{wheel.name}"
            copied = self._call(
                [
                    "scp",
                    "-F",
                    "/dev/null",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-i",
                    box.ssh_key,
                    "-P",
                    box.ssh_port,
                    str(wheel),
                    f"{box.ssh_user}@{box.ssh_host}:{remote}",
                ],
                timeout=120,
            )
            if copied.returncode != 0:
                raise CrabboxRefusal("app_state_copy_failed", copied.stderr.strip())
            remote_wheels.append(remote)
        timing = self._timing_path(repository, "materialize")
        install = self.exec(
            box,
            [
                "sh",
                "-c",
                "set -eu; sudo apt-get update >/dev/null; "
                "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip >/dev/null; "
                f"sudo python3 -m pip install --break-system-packages {' '.join(map(shlex.quote, remote_wheels))}",
            ],
            timeout=600,
            timing_path=timing,
        )
        if install.returncode != 0:
            raise CrabboxRefusal("app_state_install_failed", install.stderr.strip())
        provides_timing = self._timing_path(repository, "provides")
        probe = self.exec(
            box,
            ["sh", "-c", "command -v opentraces && command -v script"],
            timeout=30,
            timing_path=provides_timing,
        )
        if probe.returncode != 0:
            raise CrabboxRefusal("app_state_provides_missing", probe.stderr.strip())
        app_digest = hashlib.sha256("\n".join(digests).encode()).hexdigest()
        pin = {
            "name": app_state,
            "digest": f"sha256:{app_digest}",
            "provides": ["cli", "script"],
        }
        observation_refs = [
            ref
            for ref in (self._evidence_ref(timing), self._evidence_ref(provides_timing))
            if ref is not None
        ]
        if observation_refs:
            pin["observation_refs"] = observation_refs
        return pin

    def collect(
        self,
        box: Box,
        globs: Sequence[str],
        *,
        destination: Path,
        repository: Path,
    ) -> dict[str, Path]:
        """Collect one lease tarball immediately, before Crabbox overwrites it."""

        command = [
            self.command,
            "run",
            "--id",
            box.id,
            "--reclaim",
            "--no-sync",
            "--provider",
            box.provider,
        ]
        for pattern in globs:
            if Path(pattern).is_absolute():
                raise ValueError("Crabbox artifact globs must be workdir-relative")
            command.extend(["--artifact-glob", pattern])
        command.extend(["--", "sh", "-c", ":"])
        collected = self._call(command, cwd=repository, timeout=120)
        if collected.returncode != 0:
            return {}
        source_tar = repository / ".crabbox" / "runs" / box.id / f"{box.id}-artifacts.tgz"
        if not source_tar.is_file():
            return {}
        destination.mkdir(parents=True, exist_ok=True)
        frozen_tar = destination / "collected.tgz"
        source_tar.replace(frozen_tar)
        extracted = destination / "files"
        extracted.mkdir()
        with tarfile.open(frozen_tar, "r:gz") as archive:
            safe = []
            for member in archive.getmembers():
                member_path = Path(member.name)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or not (member.isfile() or member.isdir())
                ):
                    continue
                safe.append(member)
            if hasattr(tarfile, "data_filter"):
                archive.extractall(extracted, members=safe, filter="data")
            else:
                archive.extractall(extracted, members=safe)
        return {
            path.name: path
            for path in extracted.rglob("*")
            if path.is_file()
        }

    def _release_id(self, lease_id: str, *, provider: str) -> None:
        stopped = self._call(
            [self.command, "stop", "--id", lease_id, "--provider", provider],
            timeout=60,
        )
        if stopped.returncode != 0:
            raise CrabboxRefusal("release_failed", (stopped.stderr or stopped.stdout).strip())

    def release(self, box: Box) -> None:
        self._release_id(box.id, provider=box.provider)
