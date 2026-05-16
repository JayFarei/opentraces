"""`c-prereqs-present` — c-empty + verified python3.10+, git, rsync."""

from __future__ import annotations

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register


def _check(driver: Driver, box: Box, argv: list[str], name: str) -> str:
    """Run a prerequisite probe inside the box; refuse if absent."""
    result = driver.exec(box, argv, timeout=15)
    if not result.ok:
        raise CheckpointError(
            f"prerequisite missing on box: {name!r} probe failed "
            f"(rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:200]}"
        )
    return result.stdout.strip()


def _prereqs_delta(driver: Driver, box: Box) -> None:
    """Verify python3.10+, git, rsync are reachable from the box.

    Plan 061 R1: refuse, do not install. otbox never sudo-installs
    anything on someone else's machine; the box either has the
    prereqs or this checkpoint raises.
    """
    audit: dict = {}

    # Python — accept python3 if it's 3.10+, else search the standard
    # versioned probe names (highest version first).
    py_versions = ("python3.14", "python3.13", "python3.12", "python3.11",
                   "python3.10", "python3")
    py_ok: str | None = None
    py_version_str: str | None = None
    for candidate in py_versions:
        probe = driver.exec(
            box,
            [candidate, "-c", "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 2)"],
            timeout=10,
        )
        if probe.ok:
            version = driver.exec(box, [candidate, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"], timeout=5)
            py_ok, py_version_str = candidate, version.stdout.strip()
            break
    if py_ok is None:
        raise CheckpointError(
            "prerequisite missing on box: python3.10+ not found. "
            "Install python3.10+ on the box and retry — otbox refuses "
            "to install system packages on a leased machine."
        )
    audit["python"] = {"binary": py_ok, "version": py_version_str}

    audit["git"] = _check(driver, box, ["git", "--version"], "git")
    audit["rsync"] = _check(driver, box, ["rsync", "--version"], "rsync").splitlines()[0]

    box.notes["c_prereqs_audit"] = audit


register(
    Checkpoint(
        name="c-prereqs-present",
        composed_from="c-empty",
        delta=_prereqs_delta,
        # Worth caching: this checkpoint's value is "we *checked* that
        # prereqs are present" — restoring is cheaper than re-probing.
        cache=True,
        description="c-empty + verified python3.10+, git, rsync. Refuses if any is missing.",
        provides={},
    )
)
