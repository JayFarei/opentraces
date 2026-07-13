"""The deep bench.v0 run lifecycle shared by thin authoring adapters."""

from __future__ import annotations

import hashlib
import inspect
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping

from .box import Box, CrabboxRuntime
from .contract import build_result
from .drives.terminal import TerminalDrive
from .run_store import RunDraft, RunStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_claim(callable_: Callable[..., Any]) -> str:
    """Return the required first docstring paragraph without rewriting it later."""

    doc = inspect.getdoc(callable_)
    if not doc:
        raise ValueError("bench scenario requires a non-empty docstring claim")
    first = doc.split("\n\n", 1)[0]
    if not first.strip():
        raise ValueError("bench scenario requires a non-empty first docstring paragraph")
    return first


@dataclass(frozen=True)
class ScenarioSource:
    nodeid: str
    claim: str
    source_path: Path
    scenario_path: str
    repository: str
    commit: str | None
    dirty_diff_digest: str | None


class BenchSkip(RuntimeError):
    """A named prerequisite absence discovered before the claim is attempted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Bench:
    """Thin factory bound to one discovered scenario source."""

    def __init__(
        self,
        *,
        source: ScenarioSource,
        store: RunStore | None = None,
        box_runtime: CrabboxRuntime | None = None,
        repository_path: Path | None = None,
    ) -> None:
        self.source = source
        self.store = store or RunStore()
        self.box_runtime = box_runtime or CrabboxRuntime()
        self.repository_path = Path(repository_path or Path.cwd())

    def run(
        self,
        *,
        app_state: str,
        execution_mode: str = "direct",
        capture_required: list[str] | None = None,
    ) -> "BenchRun":
        return BenchRun(
            bench=self,
            app_state=app_state,
            execution_mode=execution_mode,
            capture_required=list(capture_required or []),
        )


class BenchRun:
    """Lease → materialize → execute/verify → release → atomic finalize."""

    def __init__(
        self,
        *,
        bench: Bench,
        app_state: str,
        execution_mode: str,
        capture_required: list[str],
    ) -> None:
        self.bench = bench
        self.app_state = app_state
        self.execution_mode = execution_mode
        self.capture_required = capture_required
        self.draft: RunDraft | None = None
        self.box: Box | None = None
        self.terminal: TerminalDrive
        self.verifiers: list[dict[str, Any]] = []
        self.verifier_sources: list[dict[str, str]] = []
        self.final_path: Path
        self.result: dict[str, Any]
        self._started_at = ""
        self._started = 0.0
        self._app_state_pin: dict[str, Any] = {}

    def __enter__(self) -> "BenchRun":
        self._started_at = _utc_now()
        self._started = time.monotonic()
        self.draft = self.bench.store.begin()
        source = self.bench.source
        self.draft.record_source(
            source_path=source.source_path,
            nodeid=source.nodeid,
            claim=source.claim,
            scenario_path=source.scenario_path,
            repository=source.repository,
            commit=source.commit,
            dirty_diff_digest=source.dirty_diff_digest,
        )
        self.draft.write_json("source/verifiers.json", {"sources": []})
        try:
            self.box = self.bench.box_runtime.lease()
            self._app_state_pin = self.bench.box_runtime.materialize(
                self.box, self.app_state, repository=self.bench.repository_path
            )
            self.terminal = TerminalDrive(
                runtime=self.bench.box_runtime,
                box=self.box,
                draft=self.draft,
                repository=self.bench.repository_path,
            )
        except Exception as exc:
            self._finalize(
                execution_status="error",
                verdict=None,
                reason={"code": getattr(exc, "code", "setup_error"), "message": str(exc)},
            )
            raise
        return self

    def skip(self, code: str, message: str) -> None:
        raise BenchSkip(code, message)

    @staticmethod
    def _source_ref(fn: Callable[..., Any]) -> dict[str, str]:
        path_value = inspect.getsourcefile(fn)
        if path_value is None:
            raise RuntimeError(f"cannot locate verifier source for {fn!r}")
        path = Path(path_value).resolve()
        return {
            "path": path.as_posix(),
            "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
        }

    def verify(self, verifier: Callable[..., Any], /, **inputs: Any) -> dict[str, Any]:
        if self.draft is None:
            raise RuntimeError("BenchRun is not active")
        started = time.monotonic()
        source_ref = self._source_ref(verifier)
        reason: dict[str, str] | None = None
        evidence_refs: list[str] = []
        try:
            returned = verifier(self, **inputs)
            if isinstance(returned, Mapping):
                evidence_refs = [str(item) for item in returned.get("evidence_refs", [])]
            status = "pass"
        except BenchSkip as exc:
            status = "skip"
            reason = {"code": exc.code, "message": str(exc)}
        except AssertionError as exc:
            status = "fail"
            reason = {"code": "assertion_failed", "message": str(exc) or "assertion failed"}
        except Exception as exc:
            status = "error"
            reason = {"code": "verifier_error", "message": f"{type(exc).__name__}: {exc}"}
        record = {
            "name": f"{verifier.__module__}.{verifier.__qualname__}",
            "source_ref": source_ref,
            "status": status,
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            "evidence_refs": evidence_refs,
            "reason": reason,
        }
        self.verifiers.append(record)
        if source_ref not in self.verifier_sources:
            self.verifier_sources.append(source_ref)
        self.draft.write_json("source/verifiers.json", {"sources": self.verifier_sources})
        return record

    def _outcome_from_verifiers(self) -> tuple[str, str | None, dict[str, str] | None]:
        errors = [item for item in self.verifiers if item["status"] == "error"]
        if errors:
            return "error", None, errors[0]["reason"]
        failures = [item for item in self.verifiers if item["status"] == "fail"]
        if failures:
            return "complete", "fail", failures[0]["reason"]
        skips = [item for item in self.verifiers if item["status"] == "skip"]
        if skips:
            return "complete", "skip", skips[0]["reason"]
        return "complete", "pass", None

    def _finalize(
        self,
        *,
        execution_status: str,
        verdict: str | None,
        reason: dict[str, str] | None,
    ) -> None:
        if self.draft is None:
            return
        duration_ms = max(0, int((time.monotonic() - self._started) * 1000))
        evidence_requirements = [
            {
                "name": verifier["name"],
                "complete": verifier["status"] in {"pass", "fail"},
                "evidence_refs": verifier["evidence_refs"],
            }
            for verifier in self.verifiers
        ]
        evidence_complete = all(item["complete"] for item in evidence_requirements)
        box_pin = (
            {
                "provider": self.box.provider,
                "image": getattr(self.bench.box_runtime, "image", None),
                "sandbox_tier": self.box.sandbox_tier,
            }
            if self.box is not None
            else {"provider": None, "image": None, "sandbox_tier": "none"}
        )
        result = build_result(
            run_id=self.draft.run_id,
            claim=self.bench.source.claim,
            nodeid=self.bench.source.nodeid,
            source_ref="source/scenario.py",
            execution_mode=self.execution_mode,
            started_at=self._started_at,
            duration_ms=duration_ms,
            execution_status=execution_status,
            verdict=verdict,
            reason=reason,
            verifiers=self.verifiers,
            evidence={"complete": evidence_complete, "requirements": evidence_requirements},
            recordings=(
                self.terminal.recording_summary()
                if hasattr(self, "terminal")
                else {
                    "rewatchable": False,
                    "channels": [
                        {
                            "kind": "terminal",
                            "complete": False,
                            "path": None,
                            "reason": "terminal cast not produced",
                        }
                    ],
                }
            ),
            artifacts=[],
            capture=None,
            pins={
                "product": {"commit": self.bench.source.commit},
                "observer": {},
                "environment": box_pin,
                "harness": None,
                "model_wire": None,
                "emulators": {},
                "app_state": self._app_state_pin,
            },
        )
        self.final_path = self.draft.finalize(result)
        self.result = result

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        suppressed = False
        if exc is None:
            execution_status, verdict, reason = self._outcome_from_verifiers()
        elif isinstance(exc, BenchSkip):
            execution_status, verdict = "complete", "skip"
            reason = {"code": exc.code, "message": str(exc)}
            suppressed = True
        elif isinstance(exc, AssertionError):
            execution_status, verdict = "complete", "fail"
            reason = {"code": "assertion_failed", "message": str(exc) or "assertion failed"}
            suppressed = True
        else:
            execution_status, verdict = "error", None
            reason = {
                "code": "machinery_error",
                "message": f"{type(exc).__name__}: {exc}" if exc is not None else "unknown error",
            }

        release_error: Exception | None = None
        if self.box is not None:
            try:
                self.bench.box_runtime.release(self.box)
            except Exception as caught:
                release_error = caught
        if release_error is not None and exc is None:
            execution_status, verdict = "error", None
            reason = {
                "code": getattr(release_error, "code", "release_failed"),
                "message": str(release_error),
            }
        self._finalize(
            execution_status=execution_status,
            verdict=verdict,
            reason=reason,
        )
        return suppressed
