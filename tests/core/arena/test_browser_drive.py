from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces.core.arena.engine import Bench, ScenarioSource
from opentraces.core.arena.drives.browser import BrowserSetupRefusal
from opentraces.core.arena.run_store import RunStore
from tests.core.arena.test_engine import FakeBoxRuntime, RecordingBoxRuntime


class PublicBrowserSession:
    """External-browser boundary double exposing only rendered public state."""

    def __init__(
        self,
        *,
        suppressed_channel: str | None = None,
        empty_channel: str | None = None,
        silent: bool = False,
    ) -> None:
        self.url = "about:blank"
        self.account = ""
        self.authorized = False
        self.suppressed_channel = suppressed_channel
        self.empty_channel = empty_channel
        self.silent = silent

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
        if kind == self.suppressed_channel:
            if self.silent:
                return []
            raise RuntimeError(f"{kind} recorder token=private at /Users/private/recording")
        artifacts = {
            "browser_video": [("session.webm", b"browser-video")],
            "playwright_trace": [("trace.zip", b"playwright-trace")],
            "browser_screenshots": [("final.png", b"\x89PNG\r\nfinal")],
        }[kind]
        if kind == self.empty_channel:
            return [(filename, b"") for filename, _payload in artifacts]
        return artifacts

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
    assert bench.store.verify(run.final_path) is True
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
    fill_invocation = json.loads(
        (run.final_path / "actions/0003/invocation.json").read_text(encoding="utf-8")
    )
    assert fill_invocation["value_pin"].startswith("sha256:")
    assert "value" not in fill_invocation
    assert "bench-user" not in json.dumps(fill_invocation)
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


def _run_inspection(
    tmp_path: Path, browser_factory
) -> tuple[dict[str, object], Path]:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=browser_factory,
    )

    def inspect_public_page(run):
        inspected = run.browser.inspect("main")
        assert inspected.state["text"] == "Pending"
        return {"evidence_refs": [inspected.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(inspect_public_page)
    return run.result, run.final_path


@pytest.mark.parametrize(
    "suppressed_channel",
    ["browser_video", "playwright_trace", "browser_screenshots"],
)
def test_each_browser_recorder_failure_is_independent_and_cannot_rewrite_the_verdict(
    tmp_path: Path,
    suppressed_channel: str,
) -> None:
    result, _ = _run_inspection(
        tmp_path,
        lambda: PublicBrowserSession(suppressed_channel=suppressed_channel),
    )

    assert result["verdict"] == "pass"
    assert result["reason"] is None
    assert result["recordings"]["rewatchable"] is False
    channels = {channel["kind"]: channel for channel in result["recordings"]["channels"]}
    assert channels[suppressed_channel]["complete"] is False
    assert channels[suppressed_channel]["path"] is None
    assert "private" not in channels[suppressed_channel]["reason"]
    assert "/Users/" not in channels[suppressed_channel]["reason"]
    assert all(
        channel["complete"] is True
        for kind, channel in channels.items()
        if kind != suppressed_channel
    )


def test_silently_suppressed_screenshot_recorder_cannot_claim_complete(
    tmp_path: Path,
) -> None:
    result, final_path = _run_inspection(
        tmp_path,
        lambda: PublicBrowserSession(
            suppressed_channel="browser_screenshots",
            silent=True,
        ),
    )

    screenshot = next(
        channel
        for channel in result["recordings"]["channels"]
        if channel["kind"] == "browser_screenshots"
    )
    assert result["verdict"] == "pass"
    assert result["recordings"]["rewatchable"] is False
    assert screenshot == {
        "kind": "browser_screenshots",
        "complete": False,
        "path": None,
        "reason": "browser screenshots recorder produced no artifacts",
    }
    assert not (final_path / "recordings/browser/screenshots.json").exists()


@pytest.mark.parametrize(
    ("empty_channel", "artifact_ref"),
    [
        ("browser_video", "recordings/browser/video/session.webm"),
        ("playwright_trace", "recordings/browser/trace/trace.zip"),
        ("browser_screenshots", "recordings/browser/screenshots/final.png"),
    ],
)
def test_zero_byte_browser_artifact_makes_only_its_channel_incomplete(
    tmp_path: Path,
    empty_channel: str,
    artifact_ref: str,
) -> None:
    result, final_path = _run_inspection(
        tmp_path,
        lambda: PublicBrowserSession(empty_channel=empty_channel),
    )

    assert result["verdict"] == "pass"
    assert result["reason"] is None
    assert result["recordings"]["rewatchable"] is False
    channels = {channel["kind"]: channel for channel in result["recordings"]["channels"]}
    assert channels[empty_channel] == {
        "kind": empty_channel,
        "complete": False,
        "path": None,
        "reason": f"{empty_channel} recorder produced an empty artifact",
    }
    assert all(
        channel["complete"] is True
        for kind, channel in channels.items()
        if kind != empty_channel
    )
    assert not (final_path / artifact_ref).exists()


def test_missing_playwright_is_a_named_setup_refusal_before_browser_attempt(
    tmp_path: Path,
) -> None:
    def unavailable_browser():
        raise BrowserSetupRefusal(
            "Playwright token=private is missing at /Users/private/browser"
        )

    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=unavailable_browser,
    )

    with pytest.raises(BrowserSetupRefusal):
        with bench.run(app_state="install-only") as run:
            run.browser.navigate("http://127.0.0.1:8080/authorize")

    result = json.loads((run.final_path / "result.json").read_text(encoding="utf-8"))
    assert result["execution_status"] == "error"
    assert result["verdict"] is None
    assert result["reason"]["code"] == "browser_setup_refused"
    assert "private" not in result["reason"]["message"]
    assert "/Users/" not in result["reason"]["message"]
    assert result["recordings"] == {
        "rewatchable": False,
        "channels": [
            {
                "kind": kind,
                "complete": False,
                "path": None,
                "reason": "browser setup refused before recording",
            }
            for kind in ("browser_video", "playwright_trace", "browser_screenshots")
        ],
        "timeline_ref": "recordings/timeline.jsonl",
        "timeline": {
            "complete": True,
            "path": "recordings/timeline.jsonl",
            "reason": None,
        },
    }


def test_terminal_and_browser_share_one_run_action_domain(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=RecordingBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=PublicBrowserSession,
    )

    def cross_surface_journey(run):
        first = run.terminal.exec("printf", "before")
        browser = run.browser.inspect("main")
        last = run.terminal.exec("printf", "after")
        assert first.returncode == 0
        assert browser.state["text"] == "Pending"
        assert last.returncode == 0
        return {"evidence_refs": [first.result_ref, browser.result_ref, last.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(cross_surface_journey)

    invocations = [
        json.loads((run.final_path / f"actions/{ordinal:04d}/invocation.json").read_text())
        for ordinal in (1, 2, 3)
    ]
    assert [invocation["ordinal"] for invocation in invocations] == [1, 2, 3]
    assert [invocation.get("kind", "terminal") for invocation in invocations] == [
        "terminal",
        "inspect",
        "terminal",
    ]
    assert [invocation["started_at"] for invocation in invocations] == sorted(
        invocation["started_at"] for invocation in invocations
    )
    assert run.result["recordings"]["rewatchable"] is True
    assert [channel["kind"] for channel in run.result["recordings"]["channels"]] == [
        "terminal",
        "browser_video",
        "playwright_trace",
        "browser_screenshots",
    ]
    assert [
        marker["ordinal"] for marker in run.result["recordings"]["channels"][0]["casts"]
    ] == [1, 3]
