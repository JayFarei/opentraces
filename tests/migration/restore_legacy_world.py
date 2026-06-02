"""Restore the frozen v0.3.3 world into a live HOME/project (plan 085 R2).

Rehydrates the scrubbed ``$LEGACY_HOME`` / ``$LEGACY_PROJECT`` placeholders to
real temp paths, re-inits a git repo under the project, and returns the paths.
This is the in-place restore the ``c-legacy-v033`` otbox checkpoint performs;
keeping it here lets pytest exercise the 0.4-CLI-over-legacy-world path without
the full otbox box.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_world_v033"


@dataclass
class LegacyWorld:
    home: Path
    project: Path


def _rehydrate(root: Path, home: str, project: str) -> None:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        new = text.replace("$LEGACY_HOME", home).replace("$LEGACY_PROJECT", project)
        if new != text:
            p.write_text(new)


def restore(dest: Path) -> LegacyWorld:
    """Materialise the frozen world under ``dest``; returns live paths."""
    if not FIXTURE.exists():
        raise FileNotFoundError(f"frozen fixture missing: {FIXTURE}")
    home = dest / "home"
    project = dest / "project"
    shutil.copytree(FIXTURE / "opentraces_home", home / ".opentraces")
    shutil.copytree(FIXTURE / "project", project)
    _rehydrate(home / ".opentraces", str(home), str(project))
    _rehydrate(project, str(home), str(project))

    # Re-init git so the 0.4 capture/anchor paths have a real repo to read.
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "legacy@opentraces.test"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Legacy User"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "restored legacy world"], cwd=project, check=True)
    return LegacyWorld(home=home, project=project)
