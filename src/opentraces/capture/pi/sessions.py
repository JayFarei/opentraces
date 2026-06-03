"""Pi session discovery helpers.

Pi stores native sessions as JSONL under::

    ~/.pi/agent/sessions/--<cwd-with-slashes-replaced-by-dashes>--/<timestamp>_<uuid>.jsonl

The helpers accept explicit ``home`` / ``root`` arguments so tests and otbox can
use isolated fixture homes without patching process globals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

_SESSION_GLOB = "--*--/*.jsonl"


def pi_home(home: str | Path | None = None) -> Path:
    """Return Pi's agent config directory for ``home`` or process HOME."""

    base = Path(home if home is not None else os.environ.get("HOME", "~")).expanduser()
    return base / ".pi" / "agent"


def sessions_root(
    *,
    home: str | Path | None = None,
    pi_dir: str | Path | None = None,
) -> Path:
    """Return the Pi sessions root."""

    if pi_dir is not None:
        return Path(pi_dir).expanduser() / "sessions"
    return pi_home(home) / "sessions"


def cwd_slug(cwd: str | Path) -> str:
    """Return Pi's documented ``--<cwd>--`` session directory slug."""

    text = str(Path(cwd).expanduser())
    return f"--{text.replace('/', '-')}--"


def discover_session_files(
    *,
    home: str | Path | None = None,
    root: str | Path | None = None,
) -> Iterator[Path]:
    """Yield Pi session JSONL files in deterministic path order."""

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
    """Return a stable path relative to the Pi sessions root when possible."""

    path = Path(session_path)
    base = Path(root).expanduser() if root is not None else sessions_root(home=home)
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return None


def read_session_header(path: str | Path, *, max_lines: int = 16) -> dict:
    """Read the first Pi ``type=session`` header object, if present."""

    try:
        with open(path, "rb") as f:
            for line_no, raw_line in enumerate(f):
                if line_no >= max_lines:
                    break
                try:
                    row = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict) and row.get("type") == "session":
                    return row
    except OSError:
        return {}
    return {}


def session_id_from_file(path: str | Path) -> str:
    """Return Pi's native session id from the header, falling back to filename."""

    header = read_session_header(path)
    session_id = header.get("id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return Path(path).stem
