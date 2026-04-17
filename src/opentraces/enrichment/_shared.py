"""Small shared helpers used across enrichment subpackages.

Kept deliberately minimal: hashing, line counting, path matching, and a
uniform git subprocess wrapper. Promote to this module when a helper
is needed by ≥2 siblings.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import mmh3


def content_hash(text: str) -> str:
    """Cross-tool content hash rendered as `murmur3:<32-hex>`.

    Matches the Agent Trace v0.1.0 content-hash convention. Used by
    the attribution builder, the PostToolUse hook, and the liveness
    checker; they must agree so ranges written by the hook survive
    the builder's rebuild and the liveness walk.
    """
    return f"murmur3:{mmh3.hash128(text.encode('utf-8'), signed=False):032x}"


def line_count(text: str) -> int:
    """Number of lines in `text`, treating a trailing newline as
    terminating the last line (no extra line)."""
    if not text:
        return 1
    n = text.count("\n")
    if not text.endswith("\n"):
        n += 1
    return max(n, 1)


def path_matches(a: str, b: str) -> bool:
    """True iff `a` and `b` refer to the same file.

    Edit tool calls often pass absolute paths; patch/hunk headers are
    repo-relative; hook events mix both. Symmetric suffix match is
    the consistent rule: either path ends with `"/" + other`, or
    they're equal.
    """
    if a == b:
        return True
    return a.endswith("/" + b) or b.endswith("/" + a)


def run_git(
    args: list[str], cwd: Path | str | None = None, timeout: float = 10.0,
) -> tuple[int, str, str]:
    """Run `git <args>` and return (returncode, stdout, stderr).

    Never raises on process errors — callers inspect the return code.
    Raises only on timeout. `cwd=None` means the current process cwd.
    """
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr
