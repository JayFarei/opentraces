"""Codex CLI session discovery helpers.

Current Codex CLI rollouts live under::

    ~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl

The helpers accept an explicit ``home`` or ``root`` so tests and otbox can
point discovery at isolated fixture homes without patching process globals.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

_SESSION_GLOB = "[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/rollout-*.jsonl"


def codex_home(home: str | Path | None = None) -> Path:
    """Return the Codex config directory for ``home`` or the process HOME."""
    base = Path(home if home is not None else os.environ.get("HOME", "~")).expanduser()
    return base / ".codex"


def sessions_root(
    *,
    home: str | Path | None = None,
    codex_dir: str | Path | None = None,
) -> Path:
    """Return the Codex session root.

    ``codex_dir`` is useful when a test has already constructed an isolated
    ``.codex`` directory; ``home`` mirrors the real CLI layout.
    """
    if codex_dir is not None:
        return Path(codex_dir).expanduser() / "sessions"
    return codex_home(home) / "sessions"


def discover_session_files(
    *,
    home: str | Path | None = None,
    root: str | Path | None = None,
) -> Iterator[Path]:
    """Yield Codex rollout JSONL files in deterministic path order."""
    base = Path(root).expanduser() if root is not None else sessions_root(home=home)
    if not base.exists():
        return

    for path in sorted(base.glob(_SESSION_GLOB)):
        if path.is_file():
            yield path


def session_relpath(
    session_path: str | Path,
    *,
    home: str | Path | None = None,
    root: str | Path | None = None,
) -> str | None:
    """Return a stable path relative to the Codex sessions root when possible."""
    path = Path(session_path)
    base = Path(root).expanduser() if root is not None else sessions_root(home=home)
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return None
