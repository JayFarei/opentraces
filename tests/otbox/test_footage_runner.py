"""Meta-tests for the termctrl-backed journey-footage recorder.

Two tiers:

  1. A fast unit test that runs in DEFAULT CI: when ``termctrl`` is faked
     absent (monkeypatched ``shutil.which``), ``record_simulated_session``
     returns verdict ``SKIP`` without raising. This proves graceful-degrade.

  2. An end-to-end test gated on a real ``termctrl`` install: drive the
     synthetic ``echo-meta`` scenario all the way through
     ``record_scenario("echo-meta")`` and assert the ``.termctrl`` recording,
     the ``.mp4``, ``markers.json``, ``result.json``, and the gallery all
     land correctly. echo needs no real agent so this is reproducible.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.otbox.simulated_users import footage_runner
from tests.otbox.simulated_users.footage_runner import (
    FootageResult,
    Turn,
    record_simulated_session,
)


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Neutralise the repo-wide conftest HOME redirect — the otbox harness
    isolates HOME via the driver (same pattern as test_runner.py)."""
    yield


# ---------------------------------------------------------------------------
# 1. DEFAULT-CI unit test: termctrl absent → SKIP, never raises
# ---------------------------------------------------------------------------
def test_record_skips_when_termctrl_missing(tmp_path, monkeypatch):
    """When termctrl is not on PATH the recorder must return SKIP cleanly."""

    def fake_which(name):
        if name == "termctrl":
            return None
        return f"/usr/bin/{name}"

    monkeypatch.setattr(footage_runner.shutil, "which", fake_which)

    result = record_simulated_session(
        driver=None,
        box=None,  # never touched — SKIP happens before box prep
        binary="/bin/echo",
        turns=[Turn(prompt="hi", expect_regex="hello", timeout_s=1.0)],
        output_dir=tmp_path / "out",
        footage_dir=tmp_path / "footage",
        scenario="unit",
    )
    assert isinstance(result, FootageResult)
    assert result.verdict == "SKIP"
    assert result.turn_count == 0
    assert "termctrl" in result.error_message
    assert result.mp4_path == ""


def test_record_skips_when_binary_missing(tmp_path, monkeypatch):
    """termctrl present but the agent binary absent → SKIP (no raise)."""

    def fake_which(name):
        if name == "termctrl":
            return "/usr/bin/termctrl"
        return None  # binary not found

    monkeypatch.setattr(footage_runner.shutil, "which", fake_which)

    result = record_simulated_session(
        driver=None,
        box=None,
        binary="/definitely/not/a/real/agent-xyz",
        turns=[Turn(prompt="hi", expect_regex="hello", timeout_s=1.0)],
        output_dir=tmp_path / "out",
        footage_dir=tmp_path / "footage",
        scenario="unit",
    )
    assert result.verdict == "SKIP"
    assert "not found" in result.error_message


# ---------------------------------------------------------------------------
# 2. End-to-end: real termctrl drives echo-meta → real MP4 + gallery
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    shutil.which("termctrl") is None,
    reason="termctrl not installed (cargo install terminal-control)",
)
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not installed (needed for MP4 export)",
)
def test_record_scenario_echo_meta_produces_footage():
    """Full echo-meta drive: .termctrl, .mp4, markers, result, gallery."""
    from tests.otbox.footage import (
        _footage_dir,
        build_gallery,
        record_scenario,
    )

    result = record_scenario("echo-meta", fps=20)

    assert result.verdict in {"PASS", "FAIL"}, (
        f"unexpected verdict {result.verdict}: {result.error_message}"
    )
    # echo-meta should drive all 3 turns to a match.
    assert result.verdict == "PASS", (
        f"echo-meta footage FAILED: {result.error_message}\n"
        f"turns: {[(t.index, t.matched) for t in result.turns]}"
    )

    footage_dir = _footage_dir("echo-meta", "echo")

    # .termctrl recording exists and is non-empty.
    termctrl_path = Path(result.termctrl_path)
    assert termctrl_path.exists(), f"missing .termctrl at {termctrl_path}"
    assert termctrl_path.stat().st_size > 0, ".termctrl is empty"

    # .mp4 exists and is non-empty.
    mp4_path = Path(result.mp4_path)
    assert mp4_path.exists(), f"missing .mp4 at {mp4_path}"
    assert mp4_path.stat().st_size > 0, ".mp4 is empty"

    # markers.json has the expected marker shape: ready, per-turn pairs, stop.
    markers = json.loads((footage_dir / "markers.json").read_text())
    assert "ready" in markers
    assert "stop" in markers
    for i in range(len(result.turns)):
        assert f"turn-{i}-prompt" in markers, markers
        assert f"turn-{i}-response" in markers, markers

    # result.json parses and round-trips the verdict.
    persisted = json.loads((footage_dir / "result.json").read_text())
    assert persisted["verdict"] == result.verdict
    assert persisted["scenario"] == "echo-meta"

    # build_gallery produces a gallery.html that references the mp4.
    gallery_path = build_gallery([result])
    assert gallery_path.exists()
    gallery_html = gallery_path.read_text()
    assert "otbox journey" in gallery_html
    assert "echo-meta" in gallery_html
    # The card embeds the mp4 by a path relative to the gallery dir.
    import os

    mp4_rel = os.path.relpath(mp4_path.resolve(), gallery_path.parent.resolve())
    assert mp4_rel in gallery_html, (
        f"gallery does not reference mp4 ({mp4_rel})"
    )
