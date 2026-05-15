"""`c-installed-source` — opentraces installed editable from synced source.

Built on top of ``c-prereqs-present``. Sets up a per-box ``.testvenv``,
wires the host venv's site-packages onto its sys.path, installs the
hatchling build backend, then ``pip install --no-deps -e`` of both
``packages/opentraces-schema`` and the opentraces project itself.

The resulting box has a freshly-installed CLI at
``box.project/.testvenv/bin/opentraces`` plus an importable
``opentraces`` module. Subsequent journeys can fork from this
checkpoint to exercise the post-install surface without paying the
20s-ish install cost each time.

This is the same install sequence as
``install-from-source-happy-path.toml`` — duplicated here intentionally
so the journey remains an independent test of the install *path* and
this checkpoint is its cached *result*.
"""

from __future__ import annotations

import sys

from ..drivers.base import Driver
from ..env import REPO_ROOT, Box
from . import Checkpoint, CheckpointError, register

# Best guess at the host python's library dir — same one
# ``install-from-source-happy-path.toml`` writes into ``host-venv.pth``.
_HOST_SITE_PACKAGES = (
    REPO_ROOT / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
)


def _check(result, label: str) -> None:
    if not result.ok:
        raise CheckpointError(
            f"c-installed-source step {label!r} failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )


def _installed_source_delta(driver: Driver, box: Box) -> None:
    """Materialize a per-box .testvenv with opentraces editable."""
    paths = driver.paths(box)
    project = paths["project"]
    testvenv = f"{project}/.testvenv"

    _check(driver.exec(box, ["python3", "-m", "venv", testvenv]), "make-venv")

    # Wire the host venv onto sys.path inside the test venv.
    site_packages_dir = f"{testvenv}/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    driver.put_text(box, f"{site_packages_dir}/host-venv.pth", str(_HOST_SITE_PACKAGES) + "\n")

    _check(
        driver.exec(box, [f"{testvenv}/bin/pip", "install", "--quiet", "hatchling", "editables"]),
        "install-hatchling",
    )
    _check(
        driver.exec(box, [
            f"{testvenv}/bin/pip", "install", "--quiet",
            "--no-deps", "--no-build-isolation",
            "-e", f"{REPO_ROOT}/packages/opentraces-schema",
        ]),
        "install-schema",
    )
    _check(
        driver.exec(box, [
            f"{testvenv}/bin/pip", "install", "--quiet",
            "--no-deps", "--no-build-isolation",
            "-e", str(REPO_ROOT),
        ]),
        "install-cli",
    )
    box.notes["c_installed_source_audit"] = {
        "testvenv": testvenv,
        "host_site_packages": str(_HOST_SITE_PACKAGES),
    }


register(
    Checkpoint(
        name="c-installed-source",
        composed_from="c-prereqs-present",
        delta=_installed_source_delta,
        cache=True,
        description="c-prereqs-present + opentraces installed editable in box.project/.testvenv from synced source.",
    )
)
