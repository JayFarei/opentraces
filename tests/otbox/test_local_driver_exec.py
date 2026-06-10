"""Local driver exec robustness (otbox 2.0 phase 6, CI-portability).

A missing binary must be a non-zero ExecResult, not a harness crash — the
first CI nightly crashed the whole sweep because _prereqs.py probes
python3.14 (present locally, absent on the ubuntu runner) and the unguarded
subprocess raised FileNotFoundError instead of falling through.
"""

from __future__ import annotations

from .drivers import get_driver
from .checkpoints import resolve_checkpoint


def test_missing_binary_returns_nonzero_not_raises():
    driver = get_driver("local")
    cp = resolve_checkpoint(driver, "c-installed-source")
    try:
        res = driver.exec(cp.box, ["definitely-not-a-real-binary-xyz", "-c", "pass"])
        assert not res.ok
        assert res.returncode == 127
        assert "not found" in res.stderr
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)


def test_prereqs_python_probe_falls_through_missing_versions():
    """The python candidate list must survive a missing high version and
    settle on whatever the runner actually has (>=3.10)."""
    driver = get_driver("local")
    cp = resolve_checkpoint(driver, "c-prereqs-present")
    try:
        audit = cp.box.notes.get("c_prereqs_audit", {})
        assert audit.get("python", {}).get("binary"), "no python resolved"
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)
