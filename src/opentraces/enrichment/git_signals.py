"""Git signal extraction: VCS metadata and commit outcome detection."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from opentraces_schema.models import Outcome, VCS


def _run_git(args: list[str], cwd: Path) -> tuple[bool, str]:
    """Run a git command and return (success, stdout)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0, result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, ""


def detect_vcs(project_path: Path) -> VCS:
    """Detect VCS metadata from a project directory.

    Returns VCS with type="none" if not a git repo, or type="git" with
    base_commit and branch when available.
    """
    project_path = Path(project_path)

    ok, _ = _run_git(["rev-parse", "--is-inside-work-tree"], project_path)
    if not ok:
        return VCS(type="none")

    _, commit = _run_git(["rev-parse", "HEAD"], project_path)
    _, branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], project_path)
    _, diff = _run_git(["diff", "HEAD"], project_path)

    return VCS(
        type="git",
        base_commit=commit or None,
        branch=branch or None,
        diff=diff or None,
    )


def check_committed(
    project_path: Path,
    session_start: str,
    session_end: str,
) -> Outcome:
    """Check if the session produced a commit between session_start and session_end.

    Timestamps should be ISO 8601 format strings.
    Returns an Outcome with committed=True/False and commit details if found.
    """
    project_path = Path(project_path)

    ok, _ = _run_git(["rev-parse", "--is-inside-work-tree"], project_path)
    if not ok:
        return Outcome(committed=False)

    # Find commits made between session_start and session_end
    ok, log_output = _run_git(
        [
            "log",
            f"--after={session_start}",
            f"--before={session_end}",
            "--format=%H",
            "--reverse",
        ],
        project_path,
    )

    if not ok or not log_output:
        # Also check commits made after session_start with no upper bound,
        # in case session_end is very close to commit time
        ok, log_output = _run_git(
            [
                "log",
                f"--after={session_start}",
                "--format=%H",
                "--reverse",
                "-n", "5",
            ],
            project_path,
        )

    if not ok or not log_output:
        return Outcome(committed=False)

    commits = log_output.strip().split("\n")
    commit_sha = commits[-1]  # Use the latest commit

    # Get the patch for this commit
    _, patch = _run_git(["diff", f"{commit_sha}~1..{commit_sha}"], project_path)
    if not patch:
        # Might be the first commit
        _, patch = _run_git(["show", "--format=", "--patch", commit_sha], project_path)

    return Outcome(
        committed=True,
        commit_sha=commit_sha,
        patch=patch or None,
        signal_source="deterministic",
        signal_confidence="derived",
    )


def extract_git_signals(project_path: str | Path) -> tuple[VCS, Outcome]:
    """Extract git metadata and commit outcome from a project directory.

    Returns:
        Tuple of (VCS metadata, Outcome with commit info).
        VCS.type will be "none" if not a git repo.
        Outcome.committed will be False if no commits detected.
    """
    project_path = Path(project_path)

    vcs = detect_vcs(project_path)

    if vcs.type == "none":
        return vcs, Outcome(committed=False)

    # Use current time as a reasonable default for session bounds
    now = datetime.now(timezone.utc).isoformat()
    # Look back 24 hours as a default window
    day_ago = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).isoformat()

    outcome = check_committed(project_path, day_ago, now)
    return vcs, outcome
