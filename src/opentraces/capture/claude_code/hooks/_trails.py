"""Shared Trace Trails helpers for Claude Code hook scripts."""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path


def git_head(cwd: Path) -> dict[str, str] | None:
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


def _git_toplevel(path: Path) -> Path | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except Exception:
        return None
    try:
        return Path(out).resolve()
    except Exception:
        return None


def _existing_probe(path: Path) -> Path:
    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def _root_for_path(cwd: Path, raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return _git_toplevel(_existing_probe(path.resolve()))


def _bash_cd_root(cwd: Path, command: object) -> Path | None:
    if not isinstance(command, str) or "cd" not in command:
        return None
    match = re.search(r"(?:^|[;&|]\s*)cd\s+((?:'[^']+'|\"[^\"]+\"|[^;&|]+))", command)
    if not match:
        return None
    raw_target = match.group(1).strip()
    try:
        parts = shlex.split(raw_target)
    except ValueError:
        return None
    if not parts:
        return None
    return _root_for_path(cwd, parts[0])


def _bash_absolute_path_root(cwd: Path, command: object) -> Path | None:
    if not isinstance(command, str) or "/" not in command:
        return None
    roots: dict[str, Path] = {}
    for match in re.finditer(r"/[^\s'\"`$;&|<>]+", command):
        raw_path = match.group(0).rstrip(").,]}:")
        root = _root_for_path(cwd, raw_path)
        if root is not None:
            roots[str(root)] = root
    if len(roots) == 1:
        return next(iter(roots.values()))
    return None


def _target_root(cwd: str | None, tool_name: str | None, tool_input: object) -> Path | None:
    if not cwd:
        return None
    try:
        base = Path(cwd).resolve()
    except Exception:
        return None
    if not isinstance(tool_input, dict):
        return _git_toplevel(base)

    name = (tool_name or "").lower()
    if name in {"edit", "write", "multiedit"}:
        root = _root_for_path(base, tool_input.get("file_path") or tool_input.get("path"))
        if root is not None:
            return root
    if name == "notebookedit":
        root = _root_for_path(base, tool_input.get("notebook_path"))
        if root is not None:
            return root
    if name == "bash":
        root = _bash_cd_root(base, tool_input.get("command") or tool_input.get("cmd"))
        if root is not None:
            return root
        root = _bash_absolute_path_root(
            base,
            tool_input.get("command") or tool_input.get("cmd"),
        )
        if root is not None:
            return root
    return _git_toplevel(base)


def trail_state(
    cwd: str | None,
    tool_name: str | None = None,
    tool_input: object | None = None,
) -> dict:
    root = _target_root(cwd, tool_name, tool_input)
    if root is None:
        return {}
    try:
        from opentraces.core.trails import write_worktree_tree

        return {
            "worktree_root": str(root),
            "tree_id": write_worktree_tree(root),
            "git_head": git_head(root),
        }
    except Exception:
        return {}


def observe_tool_boundary_for_hook(
    cwd: str | None,
    tool_name: str | None,
    transcript_path: str | None,
    tool_input: object | None = None,
) -> dict | None:
    try:
        from opentraces.capture.tool_boundary import observe_tool_boundary

        exclude_paths = [transcript_path] if transcript_path else None
        root = _target_root(cwd, tool_name, tool_input)
        summary = observe_tool_boundary(
            str(root) if root is not None else cwd,
            tool_name=tool_name,
            exclude_paths=exclude_paths,
        )
    except Exception:
        return None
    return summary.to_hook_payload() if summary is not None else None
