#!/usr/bin/env python3
"""Claude Code Stop hook for opentraces.

Two responsibilities, both best-effort and never-raising:

  1. Append an ``opentraces_hook`` line to the session transcript
     capturing git state at the exact moment the turn ends. This data
     is not otherwise present in the JSONL and enriches commit/outcome
     signals in the parser.
  2. Fire-and-forget ``opentraces _ingest-session <transcript>`` so the
     fresh turn's content lands in the inbox in ~seconds rather than
     waiting on the 5-minute watcher tick. The subprocess is detached
     (``start_new_session=True``) so Claude Code never blocks on it;
     a missing ``opentraces`` on PATH is silently skipped (the watcher
     sweep picks it up later).

Install via: opentraces setup claude-code
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _git_info(cwd: str) -> dict:
    """Return git state dict, empty on any failure.

    Captures the file list at `git diff HEAD` (tracked mods + staged +
    committed-but-dirty) so build_attribution can compute
    Attribution.unaccounted_files from the hook side even for
    uncommitted sessions. Plan 041 R8.
    """
    if not cwd:
        return {}
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd, text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=cwd, text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        status_lines = [line for line in status_out.splitlines() if line.strip()]
        # `git diff HEAD --name-only` is the authoritative list for R8:
        # it includes tracked changes vs HEAD (staged + unstaged) and
        # excludes untracked files we didn't mean to claim attribution for.
        try:
            diff_names = subprocess.check_output(
                ["git", "diff", "HEAD", "--name-only"],
                cwd=cwd, text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            changed_paths = [p for p in diff_names.splitlines() if p.strip()]
        except Exception:
            changed_paths = []
        return {
            "sha": sha,
            "dirty": bool(status_lines),
            "files_changed": len(status_lines),
            "changed_paths": changed_paths,
        }
    except Exception:
        return {}


def _git_head(cwd: Path) -> dict[str, str] | None:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except Exception:
        return None
    return {"algo": "sha1", "hex": sha}


def _trail_state(cwd: str | None) -> dict:
    if not cwd:
        return {}
    try:
        from opentraces.core.trails import write_worktree_tree

        root = Path(cwd).resolve()
        return {
            "worktree_root": str(root),
            "tree_id": write_worktree_tree(root),
            "git_head": _git_head(root),
        }
    except Exception:
        return {}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        sys.exit(0)

    cwd = payload.get("cwd") or ""  # empty string handled by _git_info guard
    line = json.dumps({
        "type": "opentraces_hook",
        "event": "Stop",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "session_id": payload.get("session_id"),
            "agent_type": payload.get("agent_type"),
            "permission_mode": payload.get("permission_mode"),
            "stop_hook_active": payload.get("stop_hook_active"),
            "git": _git_info(cwd),
            "trail": _trail_state(cwd),
        },
    })

    try:
        with open(transcript_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Never break Claude Code on our account

    _spawn_ingest(transcript_path, cwd)


def _spawn_ingest(transcript_path: str, cwd: str) -> None:
    """Fire-and-forget ``opentraces _ingest-session <path>``.

    Detached (start_new_session=True), stdio -> /dev/null, no wait. Any
    failure — including opentraces missing from PATH — is silently
    swallowed; the watcher sweep is the safety net.
    """
    binary = shutil.which("opentraces")
    if binary is None:
        return
    try:
        devnull = subprocess.DEVNULL
        argv = [binary, "_ingest-session", transcript_path]
        if cwd:
            argv.extend(["--project", cwd])
        subprocess.Popen(
            argv,
            stdin=devnull, stdout=devnull, stderr=devnull,
            start_new_session=True, close_fds=True,
            cwd=cwd or None,
        )
    except Exception:
        return


if __name__ == "__main__":
    main()
