"""Install the opentraces skill globally under ~/.agents/skills/opentraces/.

Canonical copy lives under ``~/.agents/skills/opentraces/`` (vendor-neutral
staging dir any agent tool can share). Each supported agent harness gets a
symlink: ``~/.<harness>/skills/opentraces -> ~/.agents/skills/opentraces``.

Upgrade is always a full overwrite: rm canonical, re-copy packaged skill,
stamp version, re-link harnesses. A pre-existing non-symlink harness dir is
moved aside to ``opentraces.bak.<ts>`` before linking.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ... import __version__
from .._base import HookInstallResult

CANONICAL = Path.home() / ".agents" / "skills" / "opentraces"
VERSION_FILE = ".opentraces-version"

# Per-harness skill directories (relative to $HOME).
HARNESS_DIRS: dict[str, Path] = {
    "claude-code": Path.home() / ".claude" / "skills" / "opentraces",
}


def _resolve_skill_source() -> Path | None:
    """Find the packaged skill/ directory (wheel force-include or source tree)."""
    pkg = Path(__file__).resolve().parent.parent.parent / "skill"
    if (pkg / "SKILL.md").exists():
        return pkg
    repo = Path(__file__).resolve().parent.parent.parent.parent.parent / "skill"
    if (repo / "SKILL.md").exists():
        return repo
    return None


def _refresh_canonical(source: Path) -> Path:
    """Wipe + repopulate the canonical dir. Returns the canonical path."""
    CANONICAL.parent.mkdir(parents=True, exist_ok=True)
    if CANONICAL.exists() or CANONICAL.is_symlink():
        if CANONICAL.is_symlink() or CANONICAL.is_file():
            CANONICAL.unlink()
        else:
            shutil.rmtree(CANONICAL)
    shutil.copytree(source, CANONICAL)
    (CANONICAL / VERSION_FILE).write_text(__version__ + "\n")
    return CANONICAL


def _link_harness(harness: str, dest: Path) -> tuple[bool, str | None]:
    """Ensure dest is a symlink to CANONICAL. Returns (linked, backup_path)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup: str | None = None
    if dest.is_symlink():
        if dest.resolve() == CANONICAL.resolve():
            return False, None
        dest.unlink()
    elif dest.exists():
        bak = dest.with_name(f"{dest.name}.bak.{int(time.time())}")
        dest.rename(bak)
        backup = str(bak)
    dest.symlink_to(CANONICAL, target_is_directory=True)
    return True, backup


def _harness_status(dest: Path) -> dict:
    """Report the symlink path and what it points to (or doesn't).

    ``symlink_path`` is the location in the agent's own skills namespace
    (e.g. ``~/.claude/skills/opentraces``). ``target`` is where that
    symlink resolves to (the canonical copy, ideally).
    """
    base = {"symlink_path": str(dest)}
    if dest.is_symlink():
        try:
            points_to = dest.resolve()
        except OSError:
            points_to = None
        return {
            **base,
            "present": True,
            "kind": "symlink",
            "target": str(points_to) if points_to else None,
            "canonical": points_to == CANONICAL.resolve() if points_to else False,
        }
    if dest.exists():
        return {**base, "present": True, "kind": "directory", "target": None, "canonical": False}
    return {**base, "present": False, "kind": None, "target": None, "canonical": False}


def _installed_version() -> str | None:
    vf = CANONICAL / VERSION_FILE
    if not vf.exists():
        return None
    try:
        return vf.read_text().strip() or None
    except OSError:
        return None


@dataclass
class SkillInstaller:
    """HookInstaller for the opentraces Claude-Code-compatible skill."""

    installer_name: str = "skill"
    harnesses: list[str] = field(
        default_factory=lambda: list(HARNESS_DIRS.keys())
    )

    def plan(self) -> list[dict]:
        out: list[dict] = [
            {"event": "canonical", "source": "<packaged skill/>", "dest": str(CANONICAL)}
        ]
        for h in self.harnesses:
            target = HARNESS_DIRS.get(h)
            if target is None:
                continue
            out.append({"event": f"symlink:{h}", "source": str(CANONICAL), "dest": str(target)})
        return out

    def install(self) -> HookInstallResult:
        src = _resolve_skill_source()
        if src is None:
            return HookInstallResult(ok=False, notes=["packaged skill/ not found"])
        _refresh_canonical(src)
        added: list[str] = ["canonical"]
        notes: list[str] = []
        for h in self.harnesses:
            dest = HARNESS_DIRS.get(h)
            if dest is None:
                notes.append(f"unknown harness: {h}")
                continue
            try:
                linked, backup = _link_harness(h, dest)
            except OSError as e:
                notes.append(f"{h}: {e}")
                continue
            if linked:
                added.append(h)
            if backup:
                notes.append(f"{h}: backed up existing directory to {backup}")
        return HookInstallResult(
            ok=True,
            installed={"canonical": str(CANONICAL), **{h: str(HARNESS_DIRS[h]) for h in self.harnesses if h in HARNESS_DIRS}},
            added=added,
            notes=notes,
        )

    def remove(self) -> HookInstallResult:
        removed: list[str] = []
        for h in self.harnesses:
            dest = HARNESS_DIRS.get(h)
            if dest is None:
                continue
            if dest.is_symlink():
                dest.unlink()
                removed.append(h)
        if CANONICAL.exists():
            shutil.rmtree(CANONICAL)
            removed.append("canonical")
        return HookInstallResult(ok=True, removed=removed)

    def status(self) -> dict:
        installed = CANONICAL.exists() and (CANONICAL / "SKILL.md").exists()
        inst_ver = _installed_version()
        harness_states = {
            h: _harness_status(HARNESS_DIRS[h])
            for h in HARNESS_DIRS
        }
        drift = installed and inst_ver != __version__
        broken = [
            h for h, s in harness_states.items()
            if s.get("present") and not s.get("canonical")
        ]
        return {
            "installer": "skill",
            "installed": installed,
            "canonical": str(CANONICAL),
            "installed_version": inst_ver,
            "package_version": __version__,
            "drift": drift,
            "harnesses": harness_states,
            "broken_harnesses": broken,
        }
