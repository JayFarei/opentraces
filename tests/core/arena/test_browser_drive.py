from __future__ import annotations

import json
from pathlib import Path

from opentraces.core.arena.engine import Bench, ScenarioSource
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.test_engine import FakeBoxRuntime


class PublicBrowserSession:
    """External-browser boundary double exposing only rendered public state."""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.account = ""
        self.authorized = False

    def navigate(self, url: str) -> dict[str, object]:
        self.url = url
        return {"url": self.url, "title": "Authorize OpenTraces"}

    def locate(self, selector: str) -> dict[str, object]:
        return {"selector": selector, "count": 1, "visible": True}

    def click(self, selector: str) -> dict[str, object]:
        if selector == "button:has-text('Authorize')":
            self.authorized = True
        return {"selector": selector, "url": self.url}

    def fill(self, selector: str, value: str) -> dict[str, object]:
        if selector == "label=Account":
            self.account = value
        return {"selector": selector, "value": value}

    def wait(self, selector: str, *, state: str, timeout_ms: int) -> dict[str, object]:
        return {"selector": selector, "state": state, "timeout_ms": timeout_ms}

    def inspect(self, selector: str) -> dict[str, object]:
        return {
            "selector": selector,
            "text": "Authorized" if self.authorized else "Pending",
            "visible": True,
            "url": self.url,
        }

    def screenshot(self, *, full_page: bool) -> bytes:
        return b"\x89PNG\r\npublic-page"

    def finalize_channel(self, kind: str) -> list[tuple[str, bytes]]:
        return {
            "browser_video": [("session.webm", b"browser-video")],
            "playwright_trace": [("trace.zip", b"playwright-trace")],
            "browser_screenshots": [("final.png", b"\x89PNG\r\nfinal")],
        }[kind]

    def close(self) -> None:
        pass


def _scenario(tmp_path: Path) -> ScenarioSource:
    source = tmp_path / "test_browser.py"
    source.write_text(
        'def test_browser(bench):\n    """A user can authorize in a real browser."""\n',
        encoding="utf-8",
    )
    return ScenarioSource(
        nodeid="test_browser.py::test_browser",
        claim="A user can authorize in a real browser.",
        source_path=source,
        scenario_path="tests/arena/test_browser.py",
        repository="JayFarei/opentraces",
        commit="abc123",
        dirty_diff_digest=None,
    )


def test_browser_only_attempt_freezes_public_state_and_all_recording_channels(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=PublicBrowserSession,
    )

    def authorize_through_public_page(run):
        navigated = run.browser.navigate("http://127.0.0.1:8080/authorize")
        located = run.browser.locate("label=Account")
        filled = run.browser.fill("label=Account", "bench-user")
        clicked = run.browser.click("button:has-text('Authorize')")
        waited = run.browser.wait("text=Authorized", state="visible", timeout_ms=5_000)
        inspected = run.browser.inspect("main")
        screenshot = run.browser.screenshot("authorized", full_page=True)

        assert navigated.state == {
            "url": "http://127.0.0.1:8080/authorize",
            "title": "Authorize OpenTraces",
        }
        assert located.state == {"selector": "label=Account", "count": 1, "visible": True}
        assert filled.state == {"selector": "label=Account"}
        assert clicked.state["url"] == "http://127.0.0.1:8080/authorize"
        assert waited.state["state"] == "visible"
        assert inspected.state["text"] == "Authorized"
        assert screenshot.state["path"] == "recordings/browser/screenshots/authorized.png"
        return {"evidence_refs": [inspected.result_ref, screenshot.state["path"]]}

    with bench.run(app_state="install-only") as run:
        run.verify(authorize_through_public_page)

    result = json.loads((run.final_path / "result.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "pass"
    assert result["recordings"]["rewatchable"] is True
    assert result["recordings"]["channels"] == [
        {
            "kind": "browser_video",
            "complete": True,
            "path": "recordings/browser/video/session.webm",
            "reason": None,
        },
        {
            "kind": "playwright_trace",
            "complete": True,
            "path": "recordings/browser/trace/trace.zip",
            "reason": None,
        },
        {
            "kind": "browser_screenshots",
            "complete": True,
            "path": "recordings/browser/screenshots.json",
            "reason": None,
        },
    ]
    assert [
        json.loads((run.final_path / f"actions/{ordinal:04d}/invocation.json").read_text())["kind"]
        for ordinal in range(1, 8)
    ] == ["navigate", "locate", "fill", "click", "wait", "inspect", "screenshot"]
    assert (run.final_path / "recordings/browser/video/session.webm").read_bytes() == b"browser-video"
    assert (run.final_path / "recordings/browser/trace/trace.zip").read_bytes() == (
        b"playwright-trace"
    )
    screenshot_manifest = json.loads(
        (run.final_path / "recordings/browser/screenshots.json").read_text(encoding="utf-8")
    )
    assert screenshot_manifest["screenshots"] == [
        "recordings/browser/screenshots/authorized.png",
        "recordings/browser/screenshots/final.png",
    ]
