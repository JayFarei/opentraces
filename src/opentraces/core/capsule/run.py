"""Run a capsule AS A TEST: re-execute its repro against a snapshot of the code.

Two run sources (both isolated):
  - ``git``    : a detached ``git worktree`` of a target ref (re-test HEAD / a sha).
  - ``bundle`` : extract the capsule's hermetic source bundle (works even when the
                 pinned commit is private / force-pushed away / never pushed).

Hardening from the codex review:
  - the verdict comes from framework-aware adapters (``oracle.classify_result``),
    not a raw substring/exit-code guess;
  - the captured command runs under a minimal ENV ALLOWLIST with HOME pointed at a
    throwaway dir, so host secrets (~/.aws, ~/.netrc, tokens) don't reach untrusted
    captured input;
  - an optional setup step (deps/build) runs before the test command;
  - bundle extraction is guarded against path traversal.

SECURITY: the command is still attacker-influenceable; the CLI gates execution
behind explicit consent. The env allowlist and throwaway HOME reduce, not
eliminate, the blast radius. A container/microVM sandbox is the next step.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import venv as _venv
from pathlib import Path
from typing import Any, Iterator

from ..isolation import SANDBOX_TIER_NONE as _SANDBOX_TIER
from .consumes import consumes_used, resolve_consumes
from .oracle import classify_result

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 180
_OUTPUT_TAIL = 2000

# Keep the command runnable + deterministic; keep host secrets out of it.
_ENV_ALLOW = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "TZ",
    "SHELL", "TEMP", "TMP", "SystemRoot", "WINDIR",
)

# --- sandbox v1 (#157) ----------------------------------------------------- #
# ``_SANDBOX_TIER`` (imported above as ``SANDBOX_TIER_NONE``) is the HONEST tier
# this run ever emits. v1 is ALWAYS S0 "none": the env-scrub + throwaway-$HOME S0
# stack provides NO real filesystem containment (a same-UID child still reads the
# operator's real HOME / bucket by absolute path — the M1 lesson,
# ``core/isolation.py``). Claiming any tier above ``none`` would OVER-CLAIM; the
# ``jail`` / ``container`` / ``microvm`` rungs are M3 spike-gated, never
# relabelled. Reusing the isolation module's vocabulary keeps the two honest S0
# surfaces from drifting.


class CapsuleTestError(RuntimeError):
    pass


class SandboxNotOwnedError(CapsuleTestError):
    """A FOREIGN capsule was asked to execute on the host with no isolation and
    no explicit override (ADR-0008 §5 / #157 sandbox v1). Sandbox v1 provides no
    real containment, so running a foreign capsule's captured command on the host
    is a deliberate, acknowledged act — never the silent default."""


class BundleHashMismatchError(CapsuleTestError):
    """A capsule's source bundle does not match the sha256 the capsule pins; a
    tampered bundle is REFUSED before it is extracted or executed (#157)."""


def _host_isolation_detected() -> bool:
    """True when this process is already inside a container / VM boundary.

    A foreign capsule is safe to run WITHOUT an explicit override when the host
    itself is the sandbox (Docker / OCI / systemd-nspawn / k8s). Probes the
    well-known markers only; never claims isolation it cannot observe.
    """

    if Path("/.dockerenv").exists():
        return True
    if os.environ.get("container"):
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        cgroup = ""
    return any(marker in cgroup for marker in ("docker", "kubepods", "containerd", "lxc"))


def _capsule_is_foreign(capsule: dict[str, Any], local_slug: str | None) -> bool:
    """A capsule is FOREIGN when its source project slug is known and differs
    from the local repo identity. Unknown provenance is NOT treated as foreign
    (there is nothing to compare against); the honest ``sandbox_tier`` label
    still applies to every run regardless."""

    if not local_slug:
        return False
    src_slug = str(((capsule.get("source") or {}).get("project_slug")) or "").strip()
    if not src_slug:
        return False
    return src_slug != str(local_slug).strip()


def _safe_env(home: Path, inherit: bool, extra: dict[str, str] | None = None) -> dict[str, str]:
    if inherit:
        env = dict(os.environ)
    else:
        env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    env["HOME"] = str(home)
    env.setdefault("PATH", os.environ.get("PATH", ""))
    # Consumed-dependency wiring (venv PATH / service endpoints) overrides the base.
    if extra:
        env.update(extra)
    return env


def _venv_bin(venv_dir: Path) -> Path:
    scripts = venv_dir / "Scripts"
    return scripts if scripts.is_dir() else venv_dir / "bin"


@contextlib.contextmanager
def _consumes_setup(
    capsule: dict[str, Any], with_overrides: dict[str, str] | None, timeout: int,
) -> Iterator[tuple[dict[str, str], dict[str, str], str | None]]:
    """Stand up the consumed dependencies; yield ``(extra_env, used_label, error)``.

    ``package`` consumes -> an isolated venv with the pinned/overridden specs
    installed (its bin prepended to PATH). ``service`` consumes -> the endpoint
    injected as the client's env var. ``error`` non-None means setup failed and
    the caller should return an ``inconclusive`` verdict (an install/env problem
    is never a reproduction).
    """

    consumes = (capsule.get("environment") or {}).get("consumes") or []
    resolved = resolve_consumes(consumes, with_overrides)
    used = consumes_used(resolved)
    extra_env: dict[str, str] = {}
    for svc in (e for e in resolved if e["kind"] == "service"):
        if svc["endpoint"]:
            extra_env[svc["env"]] = svc["endpoint"]

    packages = [e for e in resolved if e["kind"] == "package"]
    venv_dir: Path | None = None
    error: str | None = None
    if packages:
        venv_dir = Path(tempfile.mkdtemp(prefix="capsule-venv-"))
        try:
            _venv.create(venv_dir, with_pip=True)
            py = _venv_bin(venv_dir) / "python"
            specs = ["pytest", *[p["spec"] for p in packages]]
            # --no-cache-dir is REQUIRED for a correct verdict: pip's wheel cache is
            # keyed by (name, version), so two different sources sharing a version
            # string (a fork, a moved tag) would otherwise serve a stale wheel and
            # flip reproduces<->fixed wrongly. Build the pinned source every time.
            proc = subprocess.run(
                [str(py), "-m", "pip", "install", "-q", "--no-cache-dir", *specs],
                capture_output=True, text=True, timeout=max(timeout, 300),
            )
            if proc.returncode != 0:
                error = (
                    "consumed-dependency install failed: "
                    + (proc.stderr or proc.stdout).strip()[-500:]
                )
            else:
                extra_env["PATH"] = f"{_venv_bin(venv_dir)}{os.pathsep}{os.environ.get('PATH', '')}"
                extra_env["VIRTUAL_ENV"] = str(venv_dir)
        except subprocess.TimeoutExpired:
            error = "consumed-dependency install timed out"
        except Exception as exc:  # noqa: BLE001 - report, never crash the run
            error = f"consumed-dependency setup error: {exc}"
    try:
        yield extra_env, used, error
    finally:
        if venv_dir:
            shutil.rmtree(venv_dir, ignore_errors=True)


@contextlib.contextmanager
def _worktree_at(repo_dir: Path, ref: str) -> Iterator[Path]:
    tmp = tempfile.mkdtemp(prefix="capsule-test-")
    added = False
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "worktree", "add", "--detach", tmp, ref],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise CapsuleTestError(
                f"could not check out ref {ref!r} in {repo_dir}: "
                f"{(proc.stderr or proc.stdout).strip()}"
            )
        added = True
        yield Path(tmp)
    finally:
        if added:
            subprocess.run(
                ["git", "-C", str(repo_dir), "worktree", "remove", "--force", tmp],
                capture_output=True, text=True,
            )
        shutil.rmtree(tmp, ignore_errors=True)


def _safe_extractall(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract guarding against path traversal (the bundle is untrusted)."""

    dest = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if target != dest and not str(target).startswith(str(dest) + os.sep):
            raise CapsuleTestError(f"unsafe path in capsule bundle: {member.name!r}")
        if member.issym() or member.islnk():
            link_target = (target.parent / member.linkname).resolve()
            if not str(link_target).startswith(str(dest) + os.sep):
                raise CapsuleTestError(f"unsafe link in capsule bundle: {member.name!r}")
    tf.extractall(dest)


@contextlib.contextmanager
def _bundle_extracted(bundle_path: Path) -> Iterator[Path]:
    tmp = tempfile.mkdtemp(prefix="capsule-bundle-")
    try:
        with tarfile.open(bundle_path, "r:gz") as tf:
            _safe_extractall(tf, Path(tmp))
        yield Path(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _confined_cwd(root: Path, sub: str | None) -> Path:
    sub = (sub or "").strip()
    if not sub:
        return root
    candidate = (root / sub).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return root  # traversal attempt — run at the root instead
    return candidate if candidate.is_dir() else root


def _run_in(
    run_dir: Path, capsule: dict[str, Any], *, timeout: int, inherit_env: bool,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    test = capsule.get("test") or {}
    command = test["command"]
    env = _safe_env(run_dir, inherit_env, extra_env)
    cwd = _confined_cwd(run_dir, test.get("cwd"))

    setup_log = ""
    for setup_cmd in (capsule.get("environment") or {}).get("setup", []) or []:
        try:
            sp = subprocess.run(
                setup_cmd, shell=True, cwd=str(cwd), env=env,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"runnable": True, "verdict": "inconclusive",
                    "reason": "setup step timed out", "command": command, "setup": setup_cmd}
        setup_log += f"$ {setup_cmd}\n{(sp.stdout or '')}{(sp.stderr or '')}\n"
        if sp.returncode != 0:
            return {
                "runnable": True, "verdict": "inconclusive",
                "reason": f"setup step failed (exit {sp.returncode}): {setup_cmd}",
                "command": command, "output_excerpt": setup_log[-_OUTPUT_TAIL:],
            }

    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd), env=env,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"runnable": True, "verdict": "inconclusive", "reason": "command timed out",
                "command": command}
    output = (proc.stdout or "") + (proc.stderr or "")

    classification = classify_result(
        command=command, output=output, exit_code=proc.returncode,
        expected=test.get("expected"),
    )
    return {
        "runnable": True,
        "verdict": classification["verdict"],
        "framework": classification["framework"],
        "reason": classification["reason"],
        "signal_present": classification["signal_present"],
        "exit_code": proc.returncode,
        "command": command,
        "expected": test.get("expected"),
        "env_isolated": not inherit_env,
        "output_excerpt": (setup_log + output)[-_OUTPUT_TAIL:],
        "oracle_caveat": (
            "framework adapter where recognized, else substring/exit-code. "
            "Confirm before auto-closing; not a sandbox."
        ),
    }


def run_capsule_test(
    capsule: dict[str, Any],
    *,
    repo_dir: Path | None = None,
    target_ref: str = "HEAD",
    bundle_path: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    inherit_env: bool = False,
    with_overrides: dict[str, str] | None = None,
    local_slug: str | None = None,
    i_own_isolation: bool = False,
    unsafe_run_on_host: bool = False,
) -> dict[str, Any]:
    """Execute the capsule's repro and return a deterministic verdict.

    Run source: ``bundle_path`` (hermetic source bundle) takes priority; else a
    ``git worktree`` of ``target_ref`` in ``repo_dir``.

    ``with_overrides`` ({consumed-dep name -> version/spec/url}) varies the
    CONSUMED dependency for this run — the dependency-unblock axis (plan 089).
    Each ``package`` consume is installed in an isolated venv; each ``service``
    consume's endpoint is injected as the client's env var.

    Sandbox v1 (#157): a FOREIGN capsule (``source.project_slug`` differs from
    ``local_slug``) is BLOCKED from executing on the host unless the operator
    owns the isolation (``i_own_isolation`` / already inside a container) or
    explicitly accepts the risk (``unsafe_run_on_host``); an OWN-repo capsule
    only WARNS so the re-test loop stays frictionless. A bundle whose bytes do
    not match the capsule's pinned sha256 is REFUSED before extraction. Every run
    stamps the HONEST ``sandbox_tier="none"`` (S0 — no real containment) on the
    result AND at the top-level ``capsule["sandbox_tier"]`` key the U1 clamp reads.
    """

    test = capsule.get("test") or {}
    if not test.get("command"):
        return {
            "runnable": False, "verdict": "inconclusive",
            "reason": "capsule has no executable test (use `capsule replay` for intent-replay).",
            "target_ref": target_ref,
        }

    # --- sandbox v1 ownership gate (#157) -------------------------------- #
    if _capsule_is_foreign(capsule, local_slug):
        if not (i_own_isolation or unsafe_run_on_host or _host_isolation_detected()):
            src_slug = (capsule.get("source") or {}).get("project_slug")
            raise SandboxNotOwnedError(
                f"refusing to run a FOREIGN capsule on the host: its source project "
                f"({src_slug!r}) is not this repo ({local_slug!r}). Sandbox v1 provides "
                "NO real filesystem containment, so a foreign capsule's captured command "
                "could read your host by absolute path. Re-run inside a container "
                "(i_own_isolation=True) or accept the risk (unsafe_run_on_host=True)."
            )
    elif local_slug and (capsule.get("source") or {}).get("project_slug"):
        # Own-repo capsule (slug matches): WARN-not-block — the frictionless
        # dependency-unblock / re-test-HEAD loop. The tier stays honestly ``none``.
        logger.warning(
            "running an OWN-repo capsule on the host under sandbox_tier=none "
            "(no real filesystem containment); this is the frictionless re-test path."
        )

    # --- bundle integrity HARD gate (#157) ------------------------------- #
    # A tampered bundle is REFUSED before it is extracted or executed. Reuses the
    # present-not-enforced verify_bundle sha256 check; a bundle with no pinned
    # sha256 (verify_bundle returns True) preserves the pre-gate behaviour.
    if bundle_path is not None:
        from .share import verify_bundle

        expected_sha256 = (capsule.get("bundle") or {}).get("sha256")
        if not verify_bundle(Path(bundle_path), expected_sha256):
            raise BundleHashMismatchError(
                f"capsule bundle at {bundle_path} does not match the sha256 the capsule "
                "pins; refusing to extract or run a tampered bundle."
            )

    # Honest sandbox_tier label (#157): the run's own S0 self-report, stamped on the
    # capsule dict as a truthful record of THIS run's isolation. Replay's trust
    # computation (core/capsule/replay.py::_derive_trust_factors, C1) never reads
    # this field OFF a capsule — a producer could pre-stamp it — it trusts only a
    # LOCAL run_result threaded by the caller; here that tier is honestly ``none``,
    # so a foreign-capsule verdict floors either way. Determined by the run
    # ENVIRONMENT (S0), not the outcome.
    capsule["sandbox_tier"] = _SANDBOX_TIER

    with _consumes_setup(capsule, with_overrides, timeout) as (extra_env, used, cerr):
        if cerr:
            return {
                "runnable": True, "verdict": "inconclusive", "reason": cerr,
                "command": test["command"], "consumes_used": used, "target_ref": target_ref,
                "sandbox_tier": _SANDBOX_TIER,
            }
        if bundle_path is not None:
            with _bundle_extracted(Path(bundle_path)) as run_dir:
                result = _run_in(run_dir, capsule, timeout=timeout,
                                 inherit_env=inherit_env, extra_env=extra_env)
            result["run_source"] = "bundle"
            result.setdefault("target_ref", (capsule.get("bundle") or {}).get("source_sha"))
        elif repo_dir is None:
            raise CapsuleTestError("need either bundle_path or repo_dir to run a capsule test")
        else:
            with _worktree_at(Path(repo_dir).resolve(), target_ref) as run_dir:
                result = _run_in(run_dir, capsule, timeout=timeout,
                                 inherit_env=inherit_env, extra_env=extra_env)
            result["run_source"] = "git"
            result["target_ref"] = target_ref
        if used:
            result["consumes_used"] = used
        result["sandbox_tier"] = _SANDBOX_TIER
        return result


__all__ = [
    "CapsuleTestError",
    "SandboxNotOwnedError",
    "BundleHashMismatchError",
    "run_capsule_test",
]
