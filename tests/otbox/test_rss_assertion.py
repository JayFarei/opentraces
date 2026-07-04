"""RSS assertion capability tests (seal-family W5, issue #213).

The scale-world perf ceilings (`mature-bucket-perf.toml`) assert a `max_rss_mb`
budget per step, mirroring `duration_ms_max`. This module proves the RSS
plumbing has real RED capability — it FAILS on a deliberately memory-hogging
child and PASSES on a small one — and that the platform unit normalization
(macOS ru_maxrss = bytes, Linux = KB) is handled at BOTH conventions, the exact
footgun that bit the reconciler memory check.
"""

from __future__ import annotations

import shutil

import pytest

from tests.otbox.drivers.local import LocalDriver, maxrss_to_kb
from tests.otbox.env import Box, ensure_state_root, new_box_id
from tests.otbox.journey import ASSERTION_KINDS, StepResult, _eval_assertion


# ---------------------------------------------------------------------------
# 1. unit normalization — both kernel conventions
# ---------------------------------------------------------------------------
def test_maxrss_normalization_darwin_reports_bytes():
    # macOS ru_maxrss is BYTES: 300 MiB reported raw must normalize to KB.
    raw_bytes = 300 * 1024 * 1024
    assert maxrss_to_kb(raw_bytes, platform="darwin") == 300 * 1024


def test_maxrss_normalization_linux_reports_kb():
    # Linux ru_maxrss is already KILOBYTES: passed through unchanged.
    raw_kb = 300 * 1024
    assert maxrss_to_kb(raw_kb, platform="linux") == 300 * 1024


def test_maxrss_normalization_diverges_by_platform():
    # The SAME raw integer means different things per platform — the bug the
    # normalization exists to kill. 300 MiB worth of bytes on darwin is the
    # same physical footprint as 300 MiB worth of KB reported on linux only
    # after normalization; the raw readings differ by 1024x.
    raw = 300 * 1024 * 1024  # bytes on darwin, KB on linux (absurdly large)
    assert maxrss_to_kb(raw, platform="darwin") == 300 * 1024
    assert maxrss_to_kb(raw, platform="linux") == raw
    assert maxrss_to_kb(None, platform="linux") is None


def test_max_rss_mb_registered():
    assert "max_rss_mb" in ASSERTION_KINDS


# ---------------------------------------------------------------------------
# 2. RED capability — a real memory-hogging child trips the ceiling
# ---------------------------------------------------------------------------
_HOG_MB = 350
_SMALL_CODE = "print('hi')"
# Allocate + touch a large bytearray so it is genuinely resident (not lazily
# reserved), then hold it while the process exits — the RSS peak the driver
# reads from rusage.
_HOG_CODE = (
    f"b = bytearray({_HOG_MB} * 1024 * 1024)\n"
    "for i in range(0, len(b), 4096):\n"
    "    b[i] = 1\n"
    "import sys; sys.stdout.write('hogged')\n"
)


def _step_for(result) -> StepResult:
    return StepResult(index=0, step_id="probe", type="cli", detail={}, result=result, ok=True)


def _assert_rss(step: StepResult, budget_mb: float):
    spec = {"kind": "max_rss_mb", "step": "probe", "max": budget_mb}
    return _eval_assertion(0, spec, [step], {}, driver=None, box=None)


@pytest.fixture()
def local_box():
    if shutil.which("python3") is None:  # pragma: no cover - unix dev/CI only
        pytest.skip("python3 not available")
    driver = LocalDriver()
    ensure_state_root()
    box = Box(box_id=new_box_id(), driver="local")
    driver.provision(box)
    box.save()
    try:
        yield driver, box
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_max_rss_mb_fails_on_hog_and_passes_on_small(local_box):
    driver, box = local_box
    py = shutil.which("python3")

    small = driver.exec(box, [py, "-c", _SMALL_CODE])
    assert small.returncode == 0, small.stderr
    assert small.max_rss_kb is not None, "driver did not measure RSS"

    hog = driver.exec(box, [py, "-c", _HOG_CODE])
    assert hog.returncode == 0, hog.stderr
    assert hog.max_rss_kb is not None
    # The hog genuinely resides ~350 MB more than the tiny interpreter — the
    # measurement must reflect it (independent of the assertion budget).
    assert hog.max_rss_kb > small.max_rss_kb + 200 * 1024, (
        f"hog RSS {hog.max_rss_kb}KB not meaningfully above small {small.max_rss_kb}KB"
    )

    # A 150 MB budget: the small child clears it, the hog blows through it.
    budget_mb = 150.0
    small_verdict = _assert_rss(_step_for(small), budget_mb)
    hog_verdict = _assert_rss(_step_for(hog), budget_mb)
    assert small_verdict.ok, f"small child should pass 150MB budget: {small_verdict.message}"
    assert not hog_verdict.ok, f"hog child should FAIL 150MB budget: {hog_verdict.message}"


def test_max_rss_mb_reports_missing_measurement():
    # A step with no rusage reading (e.g. a spawn that never happened) must be
    # an explicit FAIL, never a silent pass.
    from tests.otbox.drivers.base import ExecResult

    result = ExecResult(
        argv=["x"], returncode=0, stdout="", stderr="", duration_s=0.0, max_rss_kb=None
    )
    verdict = _assert_rss(_step_for(result), 100.0)
    assert not verdict.ok
    assert "no RSS measurement" in verdict.message
