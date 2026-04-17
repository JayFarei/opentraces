"""Subprocess wrapper for the entity-diff binary (upstream: Ataraxy-Labs/sem).

Contract (our side, locked in):

    sem --version                                  -> prints version line
    sem diff --commit <sha> --format json (cwd=.)  -> prints entity-diff JSON

The output JSON shape is sem's own; we pass it through to the attribution
cache without re-interpreting structure. Missing binary, non-zero exit,
or invalid JSON are all non-fatal — ``available()`` / ``diff_commit()``
return graceful sentinels so callers can skip the entity pass without
aborting the surrounding pipeline.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .version import ENTITY_BINARY_VERSION

# Honoured so airgapped installs can point at a pre-placed binary
# without hitting the installer's download path.
_ENV_OVERRIDE = "OPENTRACES_ENTITY_BIN"

# Matches manifest.json's binary_name. The binary lives at
# ``~/.opentraces/bin/<BINARY_NAME>-<version>`` once installed.
_BINARY_NAME = "sem"


def _default_binary_path() -> Path:
    return (
        Path.home() / ".opentraces" / "bin" / f"{_BINARY_NAME}-{ENTITY_BINARY_VERSION}"
    )


def resolve_binary_path(explicit: Path | None = None) -> Path:
    """Resolution order: explicit arg > env override > ~/.opentraces/bin/ot-entities-<ver>."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(_ENV_OVERRIDE)
    if env:
        return Path(env)
    return _default_binary_path()


class EntityRunner:
    """Thin wrapper around the `ot-entities` binary.

    All methods are safe to call when the binary is missing — they return
    ``False`` / ``None`` / empty dicts instead of raising, so the rest of
    the backfill pipeline can degrade gracefully.
    """

    def __init__(self, binary_path: Path | None = None, *, timeout: float = 30.0) -> None:
        self._binary = resolve_binary_path(binary_path)
        self._timeout = timeout

    @property
    def binary_path(self) -> Path:
        return self._binary

    # --- availability ------------------------------------------------------

    def available(self) -> bool:
        """True iff the binary exists and responds to --version with exit 0."""
        if not self._binary.is_file():
            return False
        try:
            r = subprocess.run(
                [str(self._binary), "--version"],
                capture_output=True, text=True, timeout=self._timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0

    def version(self) -> str | None:
        if not self._binary.is_file():
            return None
        try:
            r = subprocess.run(
                [str(self._binary), "--version"],
                capture_output=True, text=True, timeout=self._timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        raw = (r.stdout or r.stderr).strip()
        if not raw:
            return None
        # Upstream binary emits e.g. "sem 0.3.19" — strip the binary name so
        # doctor / help output doesn't leak it into user-facing surfaces.
        parts = raw.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == _BINARY_NAME.lower():
            return parts[1].strip() or None
        return raw

    # --- diff --------------------------------------------------------------

    def diff_commit(self, repo: Path, sha: str) -> dict[str, Any]:
        """Run ``sem diff --commit <sha> --format json`` from ``repo`` and parse stdout.

        On any failure (missing binary, non-zero exit, invalid JSON) returns
        an empty ``{"entities": []}`` — callers should treat that as
        "no entity info available" rather than a hard error.
        """
        if not self._binary.is_file():
            return {"entities": []}
        try:
            r = subprocess.run(
                [str(self._binary), "diff", "--commit", sha, "--format", "json"],
                cwd=str(repo),
                capture_output=True, text=True, timeout=self._timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return {"entities": []}
        if r.returncode != 0:
            return {"entities": []}
        try:
            data = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            return {"entities": []}
        if not isinstance(data, dict):
            return {"entities": []}
        data.setdefault("entities", [])
        return data
