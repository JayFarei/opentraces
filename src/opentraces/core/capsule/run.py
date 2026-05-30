"""Run a capsule AS A TEST: re-execute its captured repro against a git ref.

This is the article's *"that open data becomes the replayable test"*. A capsule
that carries a ``test`` block (a repro command + an expected failure signal) can
be executed against any commit:

* ``--against <buggy-sha>``  → should ``reproduce`` (troubleshoot / confirm repro)
* ``--against HEAD``         → should be ``fixed`` after the fix landed (re-test)

The command runs in an isolated ``git worktree`` checkout of the target ref so it
never touches the maintainer's working tree. SECURITY: the command is captured,
attacker-influenceable input — the CLI gates execution behind explicit consent.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator

DEFAULT_TIMEOUT = 180
_OUTPUT_TAIL = 2000


class CapsuleTestError(RuntimeError):
    pass


@contextlib.contextmanager
def _worktree_at(repo_dir: Path, ref: str) -> Iterator[Path]:
    """Add a detached git worktree at ``ref``, yield it, always clean up."""

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


def run_capsule_test(
    capsule: dict[str, Any],
    *,
    repo_dir: Path,
    target_ref: str = "HEAD",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Execute the capsule's repro at ``target_ref`` and return a deterministic verdict.

    Verdict semantics:
      - ``reproduces`` : the original failure signal is still present
      - ``fixed``      : the failure signal is gone AND the command now succeeds
      - ``inconclusive``: no executable test, timeout, or signal gone but still failing
    """

    test = capsule.get("test") or {}
    command = test.get("command")
    if not command:
        return {
            "runnable": False,
            "verdict": "inconclusive",
            "reason": "capsule has no executable test (use `capsule replay` for intent-replay).",
            "target_ref": target_ref,
        }
    expected = test.get("expected") or {"kind": "nonzero_exit"}

    with _worktree_at(Path(repo_dir).resolve(), target_ref) as wt:
        cwd = wt
        sub = (test.get("cwd") or "").strip("/")
        if sub and (wt / sub).is_dir():
            cwd = wt / sub
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(cwd),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "runnable": True, "verdict": "inconclusive", "reason": "command timed out",
                "command": command, "target_ref": target_ref,
            }
        output = (proc.stdout or "") + (proc.stderr or "")

    kind = expected.get("kind")
    value = expected.get("value")
    signal_present = (value in output) if (kind == "error_string" and value) else None

    if kind == "error_string" and value:
        if signal_present:
            verdict = "reproduces"
        elif proc.returncode == 0:
            verdict = "fixed"
        else:
            verdict = "inconclusive"  # original error gone, but still failing for another reason
    else:  # nonzero_exit oracle
        verdict = "fixed" if proc.returncode == 0 else "reproduces"

    return {
        "runnable": True,
        "verdict": verdict,
        "exit_code": proc.returncode,
        "command": command,
        "target_ref": target_ref,
        "expected": expected,
        "signal_present": signal_present,
        "output_excerpt": output[-_OUTPUT_TAIL:],
    }


__all__ = ["CapsuleTestError", "run_capsule_test"]
