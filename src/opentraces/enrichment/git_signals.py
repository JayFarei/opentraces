"""Git signal extraction: VCS metadata and commit outcome detection.

Two commit detection strategies:
1. detect_commits_from_steps() -- scans Bash tool calls in the session for
   `git commit` commands with successful output. Works from session data alone,
   no project directory needed. Strict: only claims committed=True when the
   session itself contains the commit.
2. check_committed() -- runs `git log` against the project directory with a
   time window. Requires the project directory to exist on disk.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from opentraces_schema.models import Outcome, Step, VCS

MAX_VCS_DIFF_CHARS = 250_000


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


def _truncate_diff(diff: str) -> str:
    """Bound serialized VCS diffs so trace processing stays tractable."""
    if len(diff) <= MAX_VCS_DIFF_CHARS:
        return diff
    omitted = len(diff) - MAX_VCS_DIFF_CHARS
    suffix = f"\n\n[TRUNCATED opentraces.vcs.diff omitted_chars={omitted}]"
    keep = max(0, MAX_VCS_DIFF_CHARS - len(suffix))
    return diff[:keep] + suffix


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
    diff = _truncate_diff(diff) if diff else diff

    return VCS(
        type="git",
        base_commit=commit or None,
        branch=branch or None,
        diff=diff or None,
    )


def check_committed(
    project_path: Path,
    session_start: str | datetime,
    session_end: str | datetime,
) -> Outcome:
    """Check if the session produced a commit between session_start and session_end.

    Timestamps can be ISO 8601 format strings or datetime objects.
    Returns an Outcome with committed=True/False and commit details if found.
    """
    project_path = Path(project_path)

    if isinstance(session_start, datetime):
        session_start = session_start.isoformat()
    if isinstance(session_end, datetime):
        session_end = session_end.isoformat()

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
        success=True,  # A session that produced a commit is a reasonable success proxy
        committed=True,
        commit_sha=commit_sha,
        patch=patch or None,
        signal_source="deterministic",
        signal_confidence="derived",
    )


def detect_commits_from_steps(steps: list[Step]) -> Outcome:
    """Detect commits by scanning Bash tool calls in the session.

    Looks for `git commit` commands in Bash tool_call inputs and extracts
    the commit SHA from the observation stdout (the [branch sha] pattern).

    This is the strict approach: only claims committed=True when the session
    itself contains the git commit command with a successful result.
    No time-window correlation, no guessing.

    Works from session data alone, no project directory needed.
    """
    commit_shas: list[str] = []
    commit_messages: list[str] = []

    for step in steps:
        for tc in step.tool_calls:
            if tc.tool_name.lower() != "bash":
                continue

            command = tc.input.get("command", "")
            if not command or "git commit" not in command:
                continue

            # Found a git commit command. Check the observation for success.
            # The observation is linked by source_call_id.
            obs = None
            for o in step.observations:
                if o.source_call_id == tc.tool_call_id:
                    obs = o
                    break

            if obs is None or not obs.content:
                continue

            # Successful git commit output looks like:
            # [main abc1234] commit message here
            # or [main (root-commit) abc1234] for first commits
            sha_match = re.search(
                r"\[[\w/.-]+(?:\s+\(root-commit\))?\s+([a-f0-9]{7,40})\]",
                obs.content,
            )
            if sha_match:
                commit_shas.append(sha_match.group(1))

                # Try to extract commit message from the same output line
                msg_match = re.search(
                    r"\[[^\]]+\]\s+(.+?)(?:\n|$)",
                    obs.content,
                )
                if msg_match:
                    commit_messages.append(msg_match.group(1).strip())

    if not commit_shas:
        return Outcome(committed=False)

    # Use the last commit (most recent in the session)
    last_sha = commit_shas[-1]
    description = commit_messages[-1] if commit_messages else None

    # Build a patch from git diff if we have multiple commits,
    # or from the commit message context
    patch = None
    # We can't get the actual diff without the project directory,
    # but the commit SHA is the definitive signal.

    return Outcome(
        success=True,  # Session produced a commit, reasonable success proxy
        committed=True,
        commit_sha=last_sha,
        patch=patch,
        description=description,
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
