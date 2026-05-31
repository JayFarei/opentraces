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
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterator

from .oracle import classify_result

DEFAULT_TIMEOUT = 180
_OUTPUT_TAIL = 2000

# Keep the command runnable + deterministic; keep host secrets out of it.
_ENV_ALLOW = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "TZ",
    "SHELL", "TEMP", "TMP", "SystemRoot", "WINDIR",
)


class CapsuleTestError(RuntimeError):
    pass


def _safe_env(home: Path, inherit: bool) -> dict[str, str]:
    if inherit:
        env = dict(os.environ)
    else:
        env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    env["HOME"] = str(home)
    env.setdefault("PATH", os.environ.get("PATH", ""))
    return env


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


def _run_in(run_dir: Path, capsule: dict[str, Any], *, timeout: int, inherit_env: bool) -> dict[str, Any]:
    test = capsule.get("test") or {}
    command = test["command"]
    env = _safe_env(run_dir, inherit_env)
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
) -> dict[str, Any]:
    """Execute the capsule's repro and return a deterministic verdict.

    Run source: ``bundle_path`` (hermetic source bundle) takes priority; else a
    ``git worktree`` of ``target_ref`` in ``repo_dir``.
    """

    test = capsule.get("test") or {}
    if not test.get("command"):
        return {
            "runnable": False, "verdict": "inconclusive",
            "reason": "capsule has no executable test (use `capsule replay` for intent-replay).",
            "target_ref": target_ref,
        }

    if bundle_path is not None:
        with _bundle_extracted(Path(bundle_path)) as run_dir:
            result = _run_in(run_dir, capsule, timeout=timeout, inherit_env=inherit_env)
        result["run_source"] = "bundle"
        result.setdefault("target_ref", (capsule.get("bundle") or {}).get("source_sha"))
        return result

    if repo_dir is None:
        raise CapsuleTestError("need either bundle_path or repo_dir to run a capsule test")
    with _worktree_at(Path(repo_dir).resolve(), target_ref) as run_dir:
        result = _run_in(run_dir, capsule, timeout=timeout, inherit_env=inherit_env)
    result["run_source"] = "git"
    result["target_ref"] = target_ref
    return result


__all__ = ["CapsuleTestError", "run_capsule_test"]
