from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from opentraces.capture import _agent_harness


class _OpenCapture:
    def __init__(self) -> None:
        self.bindings = SimpleNamespace(env={"OT_OPENTRACES_DIR": "/capture"})
        self.finished = False

    def finish(self, deadline: float) -> None:
        assert deadline > 0
        self.finished = True

    def interrupt(self, _source: str) -> bool:
        return False


def test_open_capture_finishes_when_real_child_cannot_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _OpenCapture()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_agent_harness.Capture, "open", lambda _plan: capture)

    def refuse_child(_argv, *, env):
        assert env["OT_OPENTRACES_DIR"] == "/capture"
        raise OSError("child could not start")

    monkeypatch.setattr(_agent_harness.subprocess, "Popen", refuse_child)

    with pytest.raises(OSError, match="child could not start"):
        _agent_harness.run(
            [
                "--session-id",
                "2d027d8f-2977-4b2c-81bc-c210a6350651",
                "--result-dir",
                ".opentraces/bench-capture/2d027d8f-2977-4b2c-81bc-c210a6350651",
                "--required-source",
                "git",
                "--",
                "/real/claude",
            ]
        )

    assert capture.finished is True
