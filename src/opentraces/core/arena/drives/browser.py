"""Browser drive with Playwright-shaped operations and independent recordings."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ..diagnostics import sanitize_diagnostic_value, sanitize_reason
from ..run_store import RunDraft
from .actions import RunActionSequence


BROWSER_CHANNELS = ("browser_video", "playwright_trace", "browser_screenshots")


class BrowserSetupRefusal(RuntimeError):
    """The optional Playwright/Chromium runtime is unavailable before driving."""

    code = "browser_setup_refused"


class BrowserSession(Protocol):
    def navigate(self, url: str) -> Mapping[str, Any]: ...

    def locate(self, selector: str) -> Mapping[str, Any]: ...

    def click(self, selector: str) -> Mapping[str, Any]: ...

    def fill(self, selector: str, value: str) -> Mapping[str, Any]: ...

    def wait(self, selector: str, *, state: str, timeout_ms: int) -> Mapping[str, Any]: ...

    def inspect(self, selector: str) -> Mapping[str, Any]: ...

    def screenshot(self, *, full_page: bool) -> bytes: ...

    def finalize_channel(self, kind: str) -> list[tuple[str, bytes]]: ...

    def close(self) -> None: ...


BrowserFactory = Callable[[], BrowserSession]


@dataclass(frozen=True)
class BrowserResult:
    state: dict[str, Any]
    duration_ms: int
    invocation_ref: str
    result_ref: str


class _PlaywrightSession:
    """Lazy adapter so Playwright remains an explicit bench prerequisite."""

    def __init__(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise BrowserSetupRefusal(
                "Playwright is not installed; install Playwright and its Chromium browser"
            ) from exc

        self._temporary = tempfile.TemporaryDirectory(prefix="opentraces-browser-")
        self._root = Path(self._temporary.name)
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch()
            self._context = self._browser.new_context(record_video_dir=self._root / "video")
            self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self._page = self._context.new_page()
        except Exception as exc:
            self._playwright.stop()
            self._temporary.cleanup()
            raise BrowserSetupRefusal(
                "Playwright Chromium is unavailable; install the Chromium browser before bench"
            ) from exc
        self._context_closed = False
        self._trace_stopped = False

    @staticmethod
    def _locator_state(locator: Any, selector: str) -> dict[str, Any]:
        count = locator.count()
        return {
            "selector": selector,
            "count": count,
            "visible": bool(count and locator.first.is_visible()),
        }

    def navigate(self, url: str) -> Mapping[str, Any]:
        self._page.goto(url)
        return {"url": self._page.url, "title": self._page.title()}

    def locate(self, selector: str) -> Mapping[str, Any]:
        return self._locator_state(self._page.locator(selector), selector)

    def click(self, selector: str) -> Mapping[str, Any]:
        self._page.locator(selector).click()
        return {"selector": selector, "url": self._page.url}

    def fill(self, selector: str, value: str) -> Mapping[str, Any]:
        self._page.locator(selector).fill(value)
        return {"selector": selector}

    def wait(self, selector: str, *, state: str, timeout_ms: int) -> Mapping[str, Any]:
        self._page.locator(selector).wait_for(state=state, timeout=timeout_ms)
        return {"selector": selector, "state": state, "timeout_ms": timeout_ms}

    def inspect(self, selector: str) -> Mapping[str, Any]:
        locator = self._page.locator(selector)
        state = self._locator_state(locator, selector)
        state.update(
            {
                "text": locator.first.inner_text() if state["count"] else None,
                "enabled": bool(state["count"] and locator.first.is_enabled()),
                "url": self._page.url,
            }
        )
        return state

    def screenshot(self, *, full_page: bool) -> bytes:
        return self._page.screenshot(full_page=full_page)

    def finalize_channel(self, kind: str) -> list[tuple[str, bytes]]:
        if kind == "browser_screenshots":
            return [("final.png", self._page.screenshot(full_page=True))]
        if kind == "playwright_trace":
            trace = self._root / "trace.zip"
            self._context.tracing.stop(path=trace)
            self._trace_stopped = True
            return [(trace.name, trace.read_bytes())]
        if kind == "browser_video":
            video = self._page.video
            self._context.close()
            self._context_closed = True
            path = Path(video.path())
            return [("session.webm", path.read_bytes())]
        raise ValueError(f"unknown browser recording channel: {kind}")

    def close(self) -> None:
        if not self._trace_stopped and not self._context_closed:
            try:
                self._context.tracing.stop()
            except Exception:
                pass
        if not self._context_closed:
            self._context.close()
        self._browser.close()
        self._playwright.stop()
        self._temporary.cleanup()


def open_playwright_session() -> BrowserSession:
    return _PlaywrightSession()


class BrowserDrive:
    """Drive public browser state and freeze every operation in the run exhaust."""

    def __init__(
        self,
        *,
        draft: RunDraft,
        actions: RunActionSequence,
        factory: BrowserFactory = open_playwright_session,
    ) -> None:
        self.draft = draft
        self.actions = actions
        self.factory = factory
        self._session: BrowserSession | None = None
        self._has_actions = False
        self._screenshot_refs: list[str] = []
        self._channels: list[dict[str, Any]] = []
        self._finalized = False
        self._setup_refused = False

    @property
    def has_actions(self) -> bool:
        return self._has_actions

    def _active_session(self) -> BrowserSession:
        if self._session is None:
            try:
                self._session = self.factory()
            except BrowserSetupRefusal:
                self._setup_refused = True
                raise
        return self._session

    def _perform(
        self,
        kind: str,
        invocation: dict[str, Any],
        operation: Callable[[BrowserSession], Mapping[str, Any]],
    ) -> BrowserResult:
        allocation = self.actions.allocate()
        self._has_actions = True
        action = f"actions/{allocation.ordinal:04d}"
        invocation_ref = f"{action}/invocation.json"
        result_ref = f"{action}/result.json"
        self.draft.write_json(
            invocation_ref,
            {
                "ordinal": allocation.ordinal,
                "kind": kind,
                "started_at": allocation.started_at,
                **invocation,
            },
        )
        try:
            observed = dict(operation(self._active_session()))
        except Exception as exc:
            duration_ms = self.actions.duration_ms(allocation)
            reason = sanitize_reason(getattr(exc, "code", "browser_operation_error"), exc)
            self.draft.write_json(
                result_ref,
                {"execution_status": "error", "duration_ms": duration_ms, "reason": reason},
            )
            raise
        duration_ms = self.actions.duration_ms(allocation)
        state = sanitize_diagnostic_value(observed)
        self.draft.write_json(result_ref, {"duration_ms": duration_ms, "state": state})
        return BrowserResult(
            state=state,
            duration_ms=duration_ms,
            invocation_ref=invocation_ref,
            result_ref=result_ref,
        )

    def navigate(self, url: str) -> BrowserResult:
        return self._perform("navigate", {"url": url}, lambda session: session.navigate(url))

    def locate(self, selector: str) -> BrowserResult:
        return self._perform(
            "locate", {"selector": selector}, lambda session: session.locate(selector)
        )

    def click(self, selector: str) -> BrowserResult:
        return self._perform("click", {"selector": selector}, lambda session: session.click(selector))

    def fill(self, selector: str, value: str) -> BrowserResult:
        value_pin = f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"

        def fill_publicly(session: BrowserSession) -> Mapping[str, Any]:
            session.fill(selector, value)
            return {"selector": selector}

        return self._perform(
            "fill", {"selector": selector, "value_pin": value_pin}, fill_publicly
        )

    def wait(self, selector: str, *, state: str = "visible", timeout_ms: int = 30_000) -> BrowserResult:
        return self._perform(
            "wait",
            {"selector": selector, "state": state, "timeout_ms": timeout_ms},
            lambda session: session.wait(selector, state=state, timeout_ms=timeout_ms),
        )

    def inspect(self, selector: str) -> BrowserResult:
        return self._perform(
            "inspect", {"selector": selector}, lambda session: session.inspect(selector)
        )

    def screenshot(self, name: str, *, full_page: bool = False) -> BrowserResult:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("browser screenshot name must be one relative filename stem")
        allocation = self.actions.allocate()
        self._has_actions = True
        action = f"actions/{allocation.ordinal:04d}"
        invocation_ref = f"{action}/invocation.json"
        result_ref = f"{action}/result.json"
        self.draft.write_json(
            invocation_ref,
            {
                "ordinal": allocation.ordinal,
                "kind": "screenshot",
                "started_at": allocation.started_at,
                "name": name,
                "full_page": full_page,
            },
        )
        try:
            payload = self._active_session().screenshot(full_page=full_page)
        except Exception as exc:
            duration_ms = self.actions.duration_ms(allocation)
            reason = sanitize_reason("browser_screenshot_error", exc)
            self.draft.write_json(
                result_ref,
                {"execution_status": "error", "duration_ms": duration_ms, "reason": reason},
            )
            raise
        screenshot_ref = f"recordings/browser/screenshots/{name}.png"
        self.draft.write_bytes(screenshot_ref, payload)
        self._screenshot_refs.append(screenshot_ref)
        duration_ms = self.actions.duration_ms(allocation)
        state = {"path": screenshot_ref}
        self.draft.write_json(result_ref, {"duration_ms": duration_ms, "state": state})
        return BrowserResult(
            state=state,
            duration_ms=duration_ms,
            invocation_ref=invocation_ref,
            result_ref=result_ref,
        )

    @staticmethod
    def _channel_root(kind: str) -> str:
        return {
            "browser_video": "recordings/browser/video",
            "playwright_trace": "recordings/browser/trace",
            "browser_screenshots": "recordings/browser/screenshots",
        }[kind]

    def finalize_recordings(self) -> None:
        if self._finalized or self._session is None:
            return
        self._finalized = True
        channels: dict[str, dict[str, Any]] = {}
        for kind in ("browser_screenshots", "playwright_trace", "browser_video"):
            try:
                artifacts = self._session.finalize_channel(kind)
                refs: list[str] = []
                for filename, payload in artifacts:
                    if Path(filename).name != filename:
                        raise ValueError("browser recorder returned an unsafe artifact name")
                    ref = f"{self._channel_root(kind)}/{filename}"
                    self.draft.write_bytes(ref, payload)
                    refs.append(ref)
                if kind == "browser_screenshots":
                    self._screenshot_refs.extend(ref for ref in refs if ref not in self._screenshot_refs)
                    if not self._screenshot_refs:
                        raise ValueError("browser screenshots recorder produced no artifacts")
                    path = "recordings/browser/screenshots.json"
                    self.draft.write_json(path, {"screenshots": self._screenshot_refs})
                else:
                    if len(refs) != 1:
                        raise ValueError(f"{kind} recorder must return exactly one artifact")
                    path = refs[0]
                channels[kind] = {"kind": kind, "complete": True, "path": path, "reason": None}
            except Exception as exc:
                channels[kind] = {
                    "kind": kind,
                    "complete": False,
                    "path": None,
                    "reason": sanitize_reason(f"{kind}_recording_failed", exc)["message"],
                }
        try:
            self._session.close()
        except Exception:
            pass
        self._channels = [channels[kind] for kind in BROWSER_CHANNELS]

    def recording_summary(self) -> dict[str, Any]:
        if self._setup_refused:
            return {
                "rewatchable": False,
                "channels": [
                    {
                        "kind": kind,
                        "complete": False,
                        "path": None,
                        "reason": "browser setup refused before recording",
                    }
                    for kind in BROWSER_CHANNELS
                ],
            }
        return {
            "rewatchable": bool(self._channels) and all(
                channel["complete"] for channel in self._channels
            ),
            "channels": list(self._channels),
        }
