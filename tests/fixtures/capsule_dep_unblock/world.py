"""Build the capsule dependency-unblock fixture world (plan 089, U0).

One shared client user story — ``delay("1h30m") == 5400`` — blocked by a CONSUMED
dependency. This module deterministically materializes:

  * ``humanduration`` — a tiny real library with two git tags:
        v0.1.0 (buggy: ignores minutes -> 3600) and v0.2.0 (fixed -> 5400).
        Installable by tag: ``pip install "humanduration @ git+file://<repo>@v0.1.0"``.
  * ``client`` — a project that consumes it; ``test_delay.py`` encodes the story.

The same source builds the local fixture (tests / hermetic runner) and, in U5, the
public ``JayFarei/humanduration`` repo for the live GitHub loop. Keep the bug identical
to the convert-api service variant (U6) so the capsule abstracts over *where* the
dependency lives, not *what* the bug is.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# --------------------------------------------------------------------------- #
# Library source — the ONLY difference between the two versions is parse().
# --------------------------------------------------------------------------- #

_PYPROJECT = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "humanduration"
version = "{version}"
description = "Parse human durations like '1h30m' into seconds."
requires-python = ">=3.8"

[tool.setuptools]
packages = ["humanduration"]
"""

# v0.1.0 — BUG: only hours are summed; minutes and seconds are silently dropped.
_PARSE_BUGGY = '''\
"""humanduration v0.1.0 — parse human durations into seconds."""
import re

_UNIT = {"h": 3600, "m": 60, "s": 1}


def parse(text: str) -> int:
    """Return the number of seconds in a duration string like '1h30m'.

    v0.1.0 bug: the unit pattern only recognises hours, so the minutes and
    seconds components are dropped. parse("1h30m") -> 3600 (should be 5400).
    """
    total = 0
    for value, unit in re.findall(r"(\\d+)(h)", text):  # <-- only 'h'
        total += int(value) * _UNIT[unit]
    return total
'''

# v0.2.0 — FIX: recognise h, m and s.
_PARSE_FIXED = '''\
"""humanduration v0.2.0 — parse human durations into seconds."""
import re

_UNIT = {"h": 3600, "m": 60, "s": 1}


def parse(text: str) -> int:
    """Return the number of seconds in a duration string like '1h30m'.

    v0.2.0: recognises hours, minutes and seconds. parse("1h30m") -> 5400.
    """
    total = 0
    for value, unit in re.findall(r"(\\d+)([hms])", text):
        total += int(value) * _UNIT[unit]
    return total
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write_lib(repo: Path, version: str, parse_src: str) -> None:
    (repo / "pyproject.toml").write_text(_PYPROJECT.format(version=version), encoding="utf-8")
    pkg = repo / "humanduration"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text(parse_src, encoding="utf-8")


def build_humanduration_repo(dest: Path) -> dict[str, str | Path]:
    """Create the humanduration git repo with v0.1.0 (buggy) and v0.2.0 (fixed) tags."""

    repo = Path(dest)
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixtures@opentraces.test")
    _git(repo, "config", "user.name", "opentraces fixtures")

    _write_lib(repo, "0.1.0", _PARSE_BUGGY)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "humanduration 0.1.0 (drops minutes/seconds)")
    _git(repo, "tag", "v0.1.0")
    v010 = _git(repo, "rev-parse", "HEAD")

    _write_lib(repo, "0.2.0", _PARSE_FIXED)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "humanduration 0.2.0 — recognise h/m/s")
    _git(repo, "tag", "v0.2.0")
    v020 = _git(repo, "rev-parse", "HEAD")

    return {"repo": repo, "v0.1.0": v010, "v0.2.0": v020}


# --------------------------------------------------------------------------- #
# Client — consumes humanduration; the user story lives in its test.
# --------------------------------------------------------------------------- #

_CLIENT_APP = '''\
"""Client app: schedule a task after a human-readable delay."""
from humanduration import parse


def delay_seconds(spec: str) -> int:
    """How many seconds to wait before running the task."""
    return parse(spec)
'''

_CLIENT_TEST = '''\
"""The client user story: a '1h30m' delay must be 5400 seconds."""
from app import delay_seconds


def test_delay_is_5400():
    assert delay_seconds("1h30m") == 5400
'''


def build_client(dest: Path, *, humanduration_repo: Path, ref: str = "v0.1.0") -> Path:
    """Create the client project consuming humanduration pinned at ``ref``."""

    client = Path(dest)
    client.mkdir(parents=True, exist_ok=True)
    (client / "app.py").write_text(_CLIENT_APP, encoding="utf-8")
    (client / "test_delay.py").write_text(_CLIENT_TEST, encoding="utf-8")
    # The consumed-dependency pin the capsule will record (file:// for the fixture;
    # the live U5 repo swaps this for the public github.com URL).
    (client / "requirements.txt").write_text(
        f"humanduration @ git+file://{humanduration_repo}@{ref}\n", encoding="utf-8"
    )
    return client


def build_world(root: Path) -> dict:
    """Build the full fixture world under ``root``; return its coordinates."""

    lib = build_humanduration_repo(root / "humanduration")
    client = build_client(root / "client", humanduration_repo=lib["repo"], ref="v0.1.0")
    return {
        "humanduration_repo": lib["repo"],
        "v0.1.0": lib["v0.1.0"],
        "v0.2.0": lib["v0.2.0"],
        "client": client,
        "story": 'delay_seconds("1h30m") == 5400',
    }
