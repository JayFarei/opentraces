"""Pinned Crabbox 0.38.0 adapter for disposable bench boxes."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from .diagnostics import sanitize_diagnostic_text, sanitize_diagnostic_value, sanitize_reason
from .harness_readiness import PREFERENCES_INVALID_SENTINEL
from .harnesses import (
    CLAUDE_HARNESS_EXECUTABLE,
    CLAUDE_HARNESS_NAME,
    CLAUDE_HARNESS_VERSION,
    CLAUDE_INSTALL_URL,
)


PINNED_CRABBOX_VERSION = "0.38.0"
PINNED_LOCAL_IMAGE = "ubuntu:24.04"
PINNED_HF_HUB_VERSION = "1.10.2"
PINNED_HF_XET_VERSION = "1.4.3"
DEFAULT_PROVIDER = "local-container"
PRODUCT_USER = "opentraces-product"
PRODUCT_SUDO = "/usr/bin/sudo"
PRODUCT_ENV_DENY_EXACT = frozenset(
    {
        "HOME",
        "LOGNAME",
        "PATH",
        "SHELL",
        "USER",
    }
)
PRODUCT_ENV_DENY_PREFIXES = ("DYLD_", "LD_", "SUDO_")
HF_CLIENT_LOCK_PATH = Path(__file__).parent / "emulate" / "huggingface" / "client-lock.json"


def _unsafe_product_env_name(name: str) -> bool:
    return name in PRODUCT_ENV_DENY_EXACT or name.startswith(PRODUCT_ENV_DENY_PREFIXES)


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

    def __init__(
        self,
        code: str,
        message: str,
        *,
        cleanup_lease_id: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.cleanup_lease_id = cleanup_lease_id


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
    image: str | None = None
    work_root: str | None = None
    workspace: str | None = None

    def bind_workspace(self, observed: str) -> None:
        """Bind the one materialized workspace proven by this lease."""

        if self.work_root is None:
            raise CrabboxRefusal("workspace_coordinate_missing", "lease has no work root")
        work_root = PurePosixPath(self.work_root)
        if (
            not work_root.is_absolute()
            or self.work_root == "/"
            or str(work_root) != self.work_root
            or ".." in work_root.parts
        ):
            raise CrabboxRefusal("workspace_coordinate_invalid", "lease work root is not canonical")
        workspace = PurePosixPath(observed)
        expected_parent = work_root / self.id
        if (
            not workspace.is_absolute()
            or str(workspace) != observed
            or workspace.parent != expected_parent
            or workspace.name in {"", ".", ".."}
        ):
            raise CrabboxRefusal(
                "workspace_coordinate_invalid",
                "materialized workspace is outside the inspected lease work root",
            )
        if self.workspace is not None and self.workspace != observed:
            raise CrabboxRefusal(
                "workspace_coordinate_changed", "materialized workspace changed during the lease"
            )
        object.__setattr__(self, "workspace", observed)


@dataclass(frozen=True)
class BoxCommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timing: dict[str, Any]


@dataclass
class LocalPortForward:
    """A bounded host-loopback bridge into one leased box port."""

    endpoint: str
    process: subprocess.Popen[str]

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)


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


def _partial_output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _unsafe_lease_id_adjacency(value: str | int) -> bool:
    r"""True when the rune adjacent to a candidate lease id is untrustworthy.

    Deliberately fail-closed and ASCII-only: real Crabbox output delimits the
    lease id with ASCII whitespace/punctuation, so ANY non-ASCII rune fused
    directly to a candidate (em dash, NBSP, emoji, replacement bytes) marks it
    a lookalike and the candidate is discarded rather than guessed. This is a
    known, intended narrowing versus the older permissive ``\b``-boundary
    regex, which would accept a clean id fused to a non-ASCII separator; the
    timed-out warmup path has pinned this refusal since A7, and issue #337
    extended the same rule to completed warmups. Refusing costs one clean
    lease at worst; guessing an identity risks inspecting or stopping the
    wrong box.
    """

    if isinstance(value, int):
        return value >= 128 or chr(value) in "_-" or chr(value).isalnum()
    return not value.isascii() or value in "_-" or value.isalnum()


def _lease_id_candidates(value: str | bytes | None) -> set[str]:
    if isinstance(value, bytes):
        pattern: re.Pattern[str] | re.Pattern[bytes] = re.compile(rb"cbx_[A-Za-z0-9]+")
    elif isinstance(value, str):
        pattern = re.compile(r"cbx_[A-Za-z0-9]+", flags=re.ASCII)
    else:
        return set()
    candidates: set[str] = set()
    for match in pattern.finditer(value):
        if match.start() and _unsafe_lease_id_adjacency(value[match.start() - 1]):
            continue
        if match.end() < len(value) and _unsafe_lease_id_adjacency(value[match.end()]):
            continue
        candidate = match.group(0)
        candidates.add(candidate.decode("ascii") if isinstance(candidate, bytes) else candidate)
    return candidates


def _unique_lease_identity(stdout: str | bytes | None, stderr: str | bytes | None) -> str | None:
    candidates = _lease_id_candidates(stdout) | _lease_id_candidates(stderr)
    if len(candidates) != 1:
        return None
    return candidates.pop()


def _hf_client_lock() -> tuple[dict[str, str], str]:
    try:
        encoded = HF_CLIENT_LOCK_PATH.read_bytes()
        value = json.loads(encoded)
        packages = value["packages"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CrabboxRefusal(
            "app_state_dependency_lock_invalid",
            "the committed HF client environment lock is missing or invalid",
        ) from exc
    if not isinstance(packages, Mapping) or not all(
        isinstance(name, str) and isinstance(version, str) for name, version in packages.items()
    ):
        raise CrabboxRefusal(
            "app_state_dependency_lock_invalid",
            "the committed HF client environment lock has invalid package entries",
        )
    normalized = {str(name): str(version) for name, version in sorted(packages.items())}
    required = {
        "huggingface-hub": PINNED_HF_HUB_VERSION,
        "hf-xet": PINNED_HF_XET_VERSION,
    }
    if any(normalized.get(name) != version for name, version in required.items()):
        raise CrabboxRefusal(
            "app_state_dependency_lock_invalid",
            "the committed HF client environment lock disagrees with the client pins",
        )
    return normalized, f"sha256:{hashlib.sha256(encoded).hexdigest()}"


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


_CONTAINER_PROVIDERS = frozenset({"local-container", "e2b", "modal", "cloudflare"})
_MICROVM_PROVIDERS = frozenset({"firecracker", "apple-vm", "proxmox"})
_UNCONTAINED_PROVIDERS = frozenset({"ssh", "host", "direct"})


def sandbox_tier_for_provider(provider: str) -> str:
    """Classify containment ourselves; provider metadata is never a trust tier."""

    if provider in _MICROVM_PROVIDERS:
        return "microvm"
    if provider in _CONTAINER_PROVIDERS:
        return "container"
    if provider in _UNCONTAINED_PROVIDERS:
        return "none"
    # A new provider has no reviewed containment classification. Unknown must
    # never inherit the default provider's stronger tier.
    return "none"


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
        self._run_configured = False
        self.child_env = dict(os.environ)
        tmpdir = self.home / "crabbox-tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        self.child_env["TMPDIR"] = str(tmpdir)
        colima_socket = self.home / ".colima" / "default" / "docker.sock"
        if "DOCKER_HOST" not in self.child_env and colima_socket.exists():
            self.child_env["DOCKER_HOST"] = f"unix://{colima_socket}"

    def configure_run_evidence(self, run_root: Path) -> None:
        """Route Crabbox timing records into the pending run's custody.

        A CrabboxRuntime accumulates per-run ``_diagnostics`` /
        ``_run_evidence_root`` instance state. It is safe today only because the
        CLI mints one runtime per run; enforce that contract as single-use so a
        reused runtime is explicitly refused rather than silently
        cross-contaminating a second run's evidence (issue #302 F5). This is the
        per-run hook ``BenchRun.__enter__`` already calls exactly once.
        """

        if self._run_configured:
            raise CrabboxRefusal(
                "runtime_reused",
                "CrabboxRuntime is single-use; mint a fresh runtime per bench run",
            )
        self._run_configured = True
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
            cleanup_lease_id = _unique_lease_identity(exc.stdout, exc.stderr)
            partial_stdout = sanitize_diagnostic_text(_partial_output_text(exc.stdout))
            partial_stderr = sanitize_diagnostic_text(_partial_output_text(exc.stderr))
            self._diagnostics.append(
                {
                    "operation": _operation_name(argv),
                    "returncode": None,
                    "stdout": partial_stdout,
                    "stderr": partial_stderr,
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "timed_out": True,
                }
            )
            raise CrabboxRefusal(
                "crabbox_timeout",
                f"bounded command timed out: {argv[1]}",
                cleanup_lease_id=cleanup_lease_id,
            ) from exc
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
        for raw_line in self.ssh_config.read_text(encoding="utf-8", errors="replace").splitlines():
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
                    pattern for value in values for pattern in value.replace(",", " ").split()
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
        try:
            warmup = self._call(warmup_argv, timeout=600)
        except CrabboxRefusal as primary:
            if primary.code == "crabbox_timeout" and primary.cleanup_lease_id:
                self._best_effort_release_after_refusal(
                    primary.cleanup_lease_id, provider=self.provider
                )
            raise
        lease_id = _unique_lease_identity(warmup.stdout, warmup.stderr)
        if warmup.returncode != 0:
            primary = CrabboxRefusal(
                "lease_failed", (warmup.stderr or warmup.stdout or "crabbox warmup failed").strip()
            )
            if lease_id is not None:
                self._best_effort_release_after_refusal(lease_id, provider=self.provider)
            raise primary
        if lease_id is None:
            raise CrabboxRefusal("lease_identity_missing", "warmup did not report a cbx_ lease id")
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
                raise CrabboxRefusal("lease_inspect_invalid", "inspect did not emit JSON") from exc
            if not isinstance(facts, Mapping):
                raise CrabboxRefusal("lease_inspect_invalid", "inspect did not emit an object")
            if (
                inspected.returncode != 0
                or not facts.get("ready")
                or facts.get("state") not in {"leased", "ready"}
            ):
                raise CrabboxRefusal("lease_not_ready", "inspect did not report a ready lease")
            required_facts = (
                "id",
                "slug",
                "provider",
                "sshHost",
                "sshUser",
                "sshPort",
                "sshKey",
            )
            if any(facts.get(name) is None or facts.get(name) == "" for name in required_facts):
                raise CrabboxRefusal(
                    "lease_inspect_incomplete", "inspect omitted required lease facts"
                )
            labels = facts.get("labels")
            if not isinstance(labels, Mapping) or any(
                not labels.get(name) for name in ("image", "lease", "work_root")
            ):
                raise CrabboxRefusal(
                    "lease_inspect_incomplete",
                    "inspect omitted labels.image, labels.lease, or labels.work_root",
                )
            if str(facts["id"]) != lease_id or str(labels["lease"]) != lease_id:
                raise CrabboxRefusal(
                    "lease_identity_mismatch", "inspect identity does not match the warm lease"
                )
            work_root = str(labels["work_root"])
            work_root_path = PurePosixPath(work_root)
            if (
                not work_root_path.is_absolute()
                or work_root == "/"
                or str(work_root_path) != work_root
                or ".." in work_root_path.parts
            ):
                raise CrabboxRefusal(
                    "lease_workspace_invalid", "inspect labels.work_root is not canonical"
                )
            observed_provider = str(facts["provider"])
            observed_image = str(labels["image"])
            if observed_provider != self.provider:
                raise CrabboxRefusal(
                    "lease_provider_mismatch",
                    "inspect provider does not match the requested provider",
                )
            if observed_image != self.image:
                raise CrabboxRefusal(
                    "lease_image_mismatch",
                    "inspect labels.image does not match the requested image",
                )
            box = Box(
                id=str(facts["id"]),
                slug=str(facts["slug"]),
                provider=observed_provider,
                sandbox_tier=sandbox_tier_for_provider(observed_provider),
                ssh_host=str(facts["sshHost"]),
                ssh_user=str(facts["sshUser"]),
                ssh_port=str(facts["sshPort"]),
                ssh_key=str(facts["sshKey"]),
                image=observed_image,
                work_root=work_root,
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
            self._best_effort_release_after_refusal(lease_id, provider=self.provider)
            raise
        return box

    def _best_effort_release_after_refusal(self, lease_id: str, *, provider: str) -> None:
        """Release a named partial lease without replacing its primary refusal."""

        try:
            self._release_id(lease_id, provider=provider)
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

    def exec_product(
        self,
        box: Box,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60,
        timing_path: Path,
    ) -> BoxCommandResult:
        """Execute one public-drive action as the non-sudo product identity."""

        environment = {str(name): str(value) for name, value in (env or {}).items()}
        invalid = [
            name for name in environment if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
        ]
        if invalid:
            raise ValueError(f"invalid product environment name: {invalid[0]!r}")
        unsafe = sorted(name for name in environment if _unsafe_product_env_name(name))
        if unsafe:
            raise ValueError(f"unsafe product environment: {unsafe[0]!r}")
        product_argv = [PRODUCT_SUDO, "-H", "-u", PRODUCT_USER]
        if environment:
            product_argv.append(f"--preserve-env={','.join(sorted(environment))}")
        product_argv.extend(["--", *map(str, argv)])
        return self.exec(
            box,
            product_argv,
            cwd=cwd,
            env=environment,
            timeout=timeout,
            timing_path=timing_path,
        )

    def _prepare_product_identity(self, box: Box, *, repository: Path) -> str | None:
        timing = self._timing_path(repository, "product-identity")
        prepared = self.exec(
            box,
            [
                "sh",
                "-c",
                "set -eu; "
                f"if ! id -u {PRODUCT_USER} >/dev/null 2>&1; then "
                f"sudo useradd --create-home --home-dir /home/{PRODUCT_USER} "
                f"--shell /bin/sh {PRODUCT_USER}; fi; "
                f'test "$(id -u {PRODUCT_USER})" -ne 0; '
                f"sudo -u {PRODUCT_USER} test -w /home/{PRODUCT_USER}; "
                "transport_group=$(id -gn); "
                f'sudo chown -R -h {PRODUCT_USER}:"$transport_group" "$PWD"; '
                'sudo chmod -R g+rwX "$PWD"; '
                f'sudo -u {PRODUCT_USER} test -w "$PWD"; '
                f"sudo install -d -m 0755 -o {PRODUCT_USER} -g {PRODUCT_USER} "
                '"$PWD/bench-recordings"; '
                f"if sudo -u {PRODUCT_USER} sudo -n true >/dev/null 2>&1; then exit 1; fi; "
                "sudo install -d -m 0755 /etc/ssh/sshd_config.d; "
                "printf '%s\n' 'AcceptEnv *' | "
                "sudo tee /etc/ssh/sshd_config.d/opentraces-agent-env.conf >/dev/null; "
                "sudo sshd -t; "
                "if test -r /run/sshd.pid; then "
                "sudo kill -HUP \"$(cat /run/sshd.pid)\"; "
                "else sudo pkill -HUP -x sshd || true; fi; "
                "printf 'OPENTRACES_WORKSPACE=%s\\n' \"$(pwd -P)\"",
            ],
            cwd=repository,
            timeout=30,
            timing_path=timing,
        )
        if prepared.returncode != 0:
            raise CrabboxRefusal(
                "product_identity_invalid",
                "the dedicated product identity is missing, non-writable, or sudo-capable",
            )
        workspace_lines = [
            line.removeprefix("OPENTRACES_WORKSPACE=")
            for line in prepared.stdout.splitlines()
            if line.startswith("OPENTRACES_WORKSPACE=")
        ]
        if len(workspace_lines) != 1:
            raise CrabboxRefusal(
                "workspace_coordinate_missing",
                "product identity preparation did not report one workspace",
            )
        box.bind_workspace(workspace_lines[0])
        return self._evidence_ref(timing)

    def open_port_forward(self, box: Box, remote_port: int) -> LocalPortForward:
        """Expose one box-loopback port only on a fresh host-loopback port."""

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            local_port = int(reservation.getsockname()[1])
        process = subprocess.Popen(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-N",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "LogLevel=ERROR",
                "-i",
                box.ssh_key,
                "-p",
                box.ssh_port,
                "-L",
                f"127.0.0.1:{local_port}:127.0.0.1:{int(remote_port)}",
                f"{box.ssh_user}@{box.ssh_host}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        forward = LocalPortForward(
            endpoint=f"http://127.0.0.1:{local_port}",
            process=process,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise CrabboxRefusal(
                    "port_forward_failed",
                    "the host-to-box port forward exited before becoming ready",
                )
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.1):
                    return forward
            except OSError:
                time.sleep(0.05)
        forward.close()
        raise CrabboxRefusal(
            "port_forward_failed",
            "the host-to-box port forward did not become ready within 5 seconds",
        )

    def copy_into_box(
        self,
        box: Box,
        source: Path,
        destination: str,
        *,
        timeout: float = 120,
    ) -> str:
        """Copy one exact host file into the leased box."""

        source = Path(source).resolve(strict=True)
        destination_path = Path(destination)
        staged = (
            f"/tmp/opentraces-copy-{hashlib.sha256(str(destination).encode()).hexdigest()[:12]}"
        )
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
                str(source),
                f"{box.ssh_user}@{box.ssh_host}:{staged}",
            ],
            timeout=timeout,
        )
        if copied.returncode != 0:
            raise CrabboxRefusal("app_state_copy_failed", copied.stderr.strip())
        installed = self._call(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-o",
                "StrictHostKeyChecking=no",
                "-i",
                box.ssh_key,
                "-p",
                box.ssh_port,
                f"{box.ssh_user}@{box.ssh_host}",
                "sudo",
                "sh",
                "-c",
                shlex.quote(
                    f"mkdir -p {shlex.quote(str(destination_path.parent))} && "
                    f"install -m 0755 {shlex.quote(staged)} {shlex.quote(destination)} && "
                    f"rm -f {shlex.quote(staged)}"
                ),
            ],
            timeout=timeout,
        )
        if installed.returncode != 0:
            raise CrabboxRefusal("app_state_copy_failed", installed.stderr.strip())
        return destination

    def materialize(self, box: Box, app_state: str, *, repository: Path) -> dict[str, Any]:
        """Materialize the first concrete recipe from locally built wheels."""

        if app_state == "base-only":
            timing_path = self._timing_path(repository, "base-provides")
            probe = self.exec(
                box,
                [
                    "sh",
                    "-c",
                    "command -v python3 && command -v git && command -v curl && command -v script",
                ],
                timeout=30,
                timing_path=timing_path,
            )
            if probe.returncode != 0:
                raise CrabboxRefusal("app_state_provides_missing", probe.stderr.strip())
            identity_ref = self._prepare_product_identity(box, repository=repository)
            material = (
                f"{box.provider}\n{self.image}\npython3\ngit\ncurl\nscript\n"
                f"product_user={PRODUCT_USER}\nproduct_sudo=false\n"
            )
            pin = {
                "name": app_state,
                "digest": f"sha256:{hashlib.sha256(material.encode()).hexdigest()}",
                "provides": ["python3", "git", "curl", "script"],
            }
            timing_ref = self._evidence_ref(timing_path)
            observation_refs = [ref for ref in (timing_ref, identity_ref) if ref is not None]
            if observation_refs:
                pin["observation_refs"] = observation_refs
            return pin
        if app_state not in {"install-only", "agent-ready"}:
            raise CrabboxRefusal("unknown_app_state", f"no recipe named {app_state!r}")
        recipe_root = os.environ.get("OT_BENCH_RECIPE_ROOT")
        wheel_root = Path(recipe_root) if recipe_root else repository / "dist"
        wheels = sorted(wheel_root.glob("*.whl"))
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
            self.copy_into_box(box, wheel, remote, timeout=120)
            remote_wheels.append(remote)
        dependency_lock, dependency_lock_sha256 = _hf_client_lock()
        locked_requirements = " ".join(
            shlex.quote(f"{name}=={version}") for name, version in dependency_lock.items()
        )
        timing = self._timing_path(repository, "materialize")
        install = self.exec(
            box,
            [
                "sh",
                "-c",
                "set -eu; sudo apt-get update >/dev/null; "
                "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip >/dev/null; "
                f"sudo python3 -m pip install --break-system-packages --no-deps "
                f"{' '.join(map(shlex.quote, remote_wheels))} "
                f"{locked_requirements}",
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
        dependency_timing = self._timing_path(repository, "dependencies")
        package_names = json.dumps(sorted(dependency_lock))
        dependency_probe = self.exec(
            box,
            [
                "python3",
                "-c",
                (
                    "import importlib.metadata as m,json; "
                    f"names=json.loads({package_names!r}); "
                    "print(json.dumps({name:m.version(name) for name in names},sort_keys=True))"
                ),
            ],
            timeout=30,
            timing_path=dependency_timing,
        )
        try:
            dependencies = json.loads(dependency_probe.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CrabboxRefusal(
                "app_state_dependency_probe_failed",
                "installed HF client versions were not observable",
            ) from exc
        expected_dependencies = dependency_lock
        if dependency_probe.returncode != 0 or dependencies != expected_dependencies:
            raise CrabboxRefusal(
                "app_state_dependency_mismatch",
                f"expected {expected_dependencies}, observed {dependencies}",
            )
        identity_ref = self._prepare_product_identity(box, repository=repository)
        harness_preflight_ref: str | None = None
        harness_install_ref: str | None = None
        harness_probe_ref: str | None = None
        harness_readiness_ref: str | None = None
        harness_recipe: dict[str, str] | None = None
        if app_state == "agent-ready":
            if box.workspace is None:
                raise CrabboxRefusal(
                    "workspace_coordinate_missing",
                    "agent-ready requires a validated materialized workspace",
                )
            harness_preflight_timing = self._timing_path(
                repository, "agent-harness-preflight"
            )
            harness_preflight = self.exec_product(
                box,
                [
                    "python3",
                    "-m",
                    "opentraces.core.arena.harness_readiness",
                    "--check",
                    "--workspace",
                    box.workspace,
                    "--version",
                    CLAUDE_HARNESS_VERSION,
                ],
                timeout=30,
                timing_path=harness_preflight_timing,
            )
            if harness_preflight.returncode != 0:
                if (
                    harness_preflight.returncode == 2
                    and PREFERENCES_INVALID_SENTINEL in harness_preflight.stdout
                ):
                    raise CrabboxRefusal(
                        "agent_harness_preferences_invalid",
                        "existing Claude Code preferences are invalid and cannot be safely amended",
                    )
                diagnostic = sanitize_diagnostic_text(harness_preflight.stderr.strip())
                raise CrabboxRefusal(
                    "agent_harness_preflight_failed",
                    diagnostic
                    or (
                        "the Claude Code harness readiness preflight failed with exit code "
                        f"{harness_preflight.returncode}"
                    ),
                )
            harness_install_timing = self._timing_path(repository, "agent-harness-install")
            harness_install = self.exec_product(
                box,
                [
                    "sh",
                    "-c",
                    f"curl -fsSL {CLAUDE_INSTALL_URL} | "
                    f"bash -s -- {CLAUDE_HARNESS_VERSION}",
                ],
                timeout=600,
                timing_path=harness_install_timing,
            )
            if harness_install.returncode != 0:
                raise CrabboxRefusal(
                    "agent_harness_install_failed",
                    "the exact supported Claude Code harness could not be installed",
                )
            harness_probe_timing = self._timing_path(repository, "agent-harness-probe")
            harness_probe = self.exec_product(
                box,
                [CLAUDE_HARNESS_EXECUTABLE, "--version"],
                timeout=30,
                timing_path=harness_probe_timing,
            )
            observed_harness_version = harness_probe.stdout.strip().split(maxsplit=1)[0]
            if (
                harness_probe.returncode != 0
                or observed_harness_version != CLAUDE_HARNESS_VERSION
            ):
                raise CrabboxRefusal(
                    "agent_harness_version_mismatch",
                    f"expected Claude Code {CLAUDE_HARNESS_VERSION}, "
                    f"observed {observed_harness_version or 'unavailable'}",
                )
            harness_readiness_timing = self._timing_path(
                repository, "agent-harness-readiness"
            )
            harness_readiness = self.exec_product(
                box,
                [
                    "python3",
                    "-m",
                    "opentraces.core.arena.harness_readiness",
                    "--workspace",
                    box.workspace,
                    "--version",
                    CLAUDE_HARNESS_VERSION,
                ],
                timeout=30,
                timing_path=harness_readiness_timing,
            )
            if harness_readiness.returncode != 0:
                raise CrabboxRefusal(
                    "agent_harness_preferences_invalid",
                    "Claude Code readiness preferences could not be established",
                )
            harness_recipe = {
                "name": CLAUDE_HARNESS_NAME,
                "executable": CLAUDE_HARNESS_EXECUTABLE,
                "version": CLAUDE_HARNESS_VERSION,
                "readiness": "claude-global-preferences-v1",
            }
            harness_preflight_ref = self._evidence_ref(harness_preflight_timing)
            harness_install_ref = self._evidence_ref(harness_install_timing)
            harness_probe_ref = self._evidence_ref(harness_probe_timing)
            harness_readiness_ref = self._evidence_ref(harness_readiness_timing)
        recipe = {
            # Bind provider + image into the install-only digest material so two
            # runs on different images/providers cannot collide on one digest —
            # base-only already binds them (issue #302 F5). This intentionally
            # changes install-only digests versus the pre-#302 wheel-only shape.
            "provider": box.provider,
            "image": self.image,
            "wheel_sha256": digests,
            "dependencies": expected_dependencies,
            "dependency_lock_sha256": dependency_lock_sha256,
            "execution_identity": {"user": PRODUCT_USER, "sudo": False},
        }
        if harness_recipe is not None:
            recipe["harness"] = harness_recipe
        app_digest = hashlib.sha256(
            json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        pin = {
            "name": app_state,
            "digest": f"sha256:{app_digest}",
            "provides": [
                "cli",
                "script",
                *([f"agent:{CLAUDE_HARNESS_NAME}"] if harness_recipe is not None else []),
            ],
            "dependencies": expected_dependencies,
            "recipe": recipe,
        }
        observation_refs = [
            ref
            for ref in (
                self._evidence_ref(timing),
                self._evidence_ref(provides_timing),
                self._evidence_ref(dependency_timing),
                identity_ref,
                harness_preflight_ref,
                harness_install_ref,
                harness_probe_ref,
                harness_readiness_ref,
            )
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
        return {path.name: path for path in extracted.rglob("*") if path.is_file()}

    def _release_id(self, lease_id: str, *, provider: str) -> None:
        stopped = self._call(
            [self.command, "stop", "--id", lease_id, "--provider", provider],
            timeout=60,
        )
        if stopped.returncode != 0:
            raise CrabboxRefusal("release_failed", (stopped.stderr or stopped.stdout).strip())

    def release(self, box: Box) -> None:
        self._release_id(box.id, provider=box.provider)
