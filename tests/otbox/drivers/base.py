"""Driver protocol — the substrate abstraction for otbox.

A driver owns the *box lifecycle* (provision / exec / teardown). Snapshot
and restore are driver-overridable but default to the portable
workspace-archive implementation in ``otbox.snapshot``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class ExecResult:
    """Outcome of one command executed inside a box."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    cwd: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 4),
            "cwd": self.cwd,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class Driver:
    """Substrate driver base class.

    Implementations: ``LocalDriver`` (default Tier 0), ``DockerDriver``
    (opt-in Tier 0), ``RemoteDriver`` (opt-in Tier 1). Each one overrides
    ``provision``/``exec``/``teardown`` (and ``sync``/``paths``/...
    where Tier 1 differs from Tier 0). Default implementations on this
    base class match the Tier 0 / local-filesystem behaviour, so a new
    Tier 0 driver only has to override what's substrate-specific.
    """

    name: str = "base"
    tier: int = 0

    def provision(self, box) -> None:
        raise NotImplementedError

    def exec(
        self,
        box,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env_extra: dict | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        raise NotImplementedError

    def teardown(self, box) -> None:
        raise NotImplementedError

    # ----- box-relative filesystem (driver-agnostic for seed + journey) ---
    def paths(self, box) -> dict:
        """Canonical paths *as the box sees them* (Tier 0 = laptop,
        Tier 1 = remote). Used by seed/journey templating so a journey
        TOML works unchanged across tiers."""
        return {
            "root": str(box.root),
            "project": str(box.project),
            "home": str(box.home),
            "opentraces_dir": str(box.opentraces_dir),
            "fake_remote": str(box.fake_remote),
        }

    def put_text(self, box, path: str, content: str) -> None:
        """Write text content to a box-side path."""
        from pathlib import Path as _Path
        p = _Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def mkdir(self, box, path: str, *, parents: bool = True) -> None:
        from pathlib import Path as _Path
        _Path(path).mkdir(parents=parents, exist_ok=True)

    def glob(self, box, pattern: str) -> list[str]:
        """Glob a box-side path pattern."""
        from glob import glob as _glob
        return sorted(_glob(pattern))

    def path_exists(self, box, path: str) -> bool:
        """Test whether a path exists on the box (driver-mediated)."""
        from pathlib import Path as _Path
        return _Path(path).exists()

    def count_files(self, box, root: str, pattern: str = "**/*") -> int:
        """Count files under ``root`` matching ``pattern`` on the box."""
        from pathlib import Path as _Path
        p = _Path(root)
        if not p.exists():
            return 0
        return sum(1 for f in p.glob(pattern) if f.is_file())

    def cli_argv(self, box) -> list[str]:
        """Return the argv prefix that runs the real opentraces CLI in the box.

        Tier 0 drivers return a host path to the in-tree CLI. The Tier 1
        ``RemoteDriver`` overrides this to return paths inside the
        leased machine. Journeys call this via ``driver.cli_argv(box)``
        so a journey TOML works unchanged across tiers.
        """
        from ..env import resolve_cli_argv

        return resolve_cli_argv()

    def snapshot(self, box, name: str, *, overwrite: bool = False):
        """Tar the box state to ``.otbox/snapshots/<name>.tar.gz``.

        Default impl operates on the laptop-side ``box.root`` (Tier 0).
        The ``RemoteDriver`` overrides this to tar on the remote and
        rsync-pull the archive back to the laptop, preserving the Tier 0
        ↔ Tier 1 archive interchange invariant.
        """
        from ..snapshot import create_snapshot

        return create_snapshot(box, name, overwrite=overwrite)

    def restore(self, name: str, *, box_id: str | None = None):
        """Restore a snapshot into a fresh box.

        Default impl extracts to a new local box. The ``RemoteDriver``
        overrides this to provision a fresh remote box and unpack the
        archive there.
        """
        from ..snapshot import restore_snapshot

        return restore_snapshot(name, box_id=box_id)

    def sync(self, box, *, full_resync: bool = False) -> ExecResult:
        """Sync the laptop's working tree into the box.

        No-op on Tier 0 drivers — the box already runs against the
        host's source tree. The ``RemoteDriver`` overrides this to
        rsync the dirty checkout over SSH.
        """
        return ExecResult(
            argv=["otbox", "sync", "(no-op on tier-0)"],
            returncode=0,
            stdout="",
            stderr="",
            duration_s=0.0,
            cwd="",
            timed_out=False,
        )


class _Clock:
    """Tiny helper so drivers time exec uniformly."""

    def __enter__(self) -> "_Clock":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self.duration = time.monotonic() - self._start


def timed() -> _Clock:
    return _Clock()
