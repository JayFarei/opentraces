"""The deep bench.v0 run lifecycle shared by thin authoring adapters."""

from __future__ import annotations

import hashlib
import inspect
import os
import time
import traceback as traceback_module
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping

from ... import __version__
from .box import Box, CrabboxRuntime
from .contract import build_result
from .diagnostics import sanitize_diagnostic_value, sanitize_reason
from .drives.terminal import TerminalDrive
from .emulate.huggingface.runtime import (
    HuggingFaceEmulator,
    app_state_pin as app_state_with_hf,
    start_huggingface_emulator,
)
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


class EvidenceReferenceError(ValueError):
    """A verifier referenced material outside the immutable run exhaust."""


class VerificationFailed(AssertionError):
    """An assertion failure that preserves the evidence it examined."""

    def __init__(self, message: str, *, evidence_refs: list[str]) -> None:
        super().__init__(message)
        self.evidence_refs = evidence_refs


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
        runtime_home = os.environ.get("OT_BENCH_REAL_HOME")
        self.box_runtime = box_runtime or CrabboxRuntime(
            home=Path(runtime_home) if runtime_home else None
        )
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
        self._emulators: dict[str, HuggingFaceEmulator] = {}
        self._lifecycle_diagnostics: list[dict[str, Any]] = []

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
            configure_evidence = getattr(self.bench.box_runtime, "configure_run_evidence", None)
            if callable(configure_evidence):
                configure_evidence(self.draft.path)
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
            if self.box is not None:
                try:
                    self.bench.box_runtime.release(self.box)
                except Exception as release_exc:
                    # The original setup failure remains primary; both the
                    # run result and Crabbox's failure bundle preserve it.
                    self._lifecycle_diagnostics.append(
                        sanitize_reason(getattr(release_exc, "code", "release_failed"), release_exc)
                    )
            self._finalize(
                execution_status="error",
                verdict=None,
                reason=sanitize_reason(getattr(exc, "code", "setup_error"), exc),
            )
            raise
        return self

    def skip(self, code: str, message: str) -> None:
        raise BenchSkip(code, message)

    def emulate(self, name: str) -> HuggingFaceEmulator:
        """Start the one concrete bench.v0 provider world."""

        if name != "huggingface":
            raise ValueError(f"unknown emulator {name!r}; expected 'huggingface'")
        if self.draft is None or self.box is None:
            raise RuntimeError("BenchRun is not active")
        existing = self._emulators.get(name)
        if existing is not None:
            return existing
        emulator = start_huggingface_emulator(
            runtime=self.bench.box_runtime,
            box=self.box,
            repository=self.bench.repository_path,
            run_path=self.draft.path,
        )
        self._emulators[name] = emulator
        self._app_state_pin = app_state_with_hf(
            name=str(self._app_state_pin.get("name") or self.app_state),
            recipe=self._app_state_pin,
            provides=[
                *list(self._app_state_pin.get("provides") or []),
                "hf-emulator",
            ],
            hf_emulator=emulator.binary_pin,
        )
        return emulator

    def _source_ref(self, source_object: object) -> dict[str, str]:
        path_value = inspect.getsourcefile(source_object)
        if path_value is None:
            raise RuntimeError(f"cannot locate verifier source for {source_object!r}")
        path = Path(path_value).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            public_path = path.relative_to(self.bench.repository_path.resolve()).as_posix()
        except ValueError:
            public_path = f"external/{digest[:12]}{path.suffix}"
        return {
            "path": public_path,
            "digest": f"sha256:{digest}",
        }

    def _verifier_source_refs(self, verifier: Callable[..., Any]) -> list[dict[str, str]]:
        """Return the verifier and its direct, statically referenced Python sources.

        This is deliberately a one-level source closure, not dependency packaging:
        ``inspect.getclosurevars`` exposes only globals/nonlocals named by the
        verifier's code, and source-bearing functions, classes, methods, or modules
        among those values are recorded. A helper's own imports are not traversed.
        """

        refs = [self._source_ref(verifier)]
        try:
            closure = inspect.getclosurevars(inspect.unwrap(verifier))
        except TypeError:
            return refs
        source_objects = [*closure.globals.values(), *closure.nonlocals.values()]
        dependencies: list[dict[str, str]] = []
        for source_object in source_objects:
            if not (
                inspect.ismodule(source_object)
                or inspect.isfunction(source_object)
                or inspect.ismethod(source_object)
                or inspect.isclass(source_object)
            ):
                continue
            try:
                ref = self._source_ref(source_object)
            except (OSError, RuntimeError, TypeError):
                continue
            if ref not in refs and ref not in dependencies:
                dependencies.append(ref)
        refs.extend(sorted(dependencies, key=lambda item: (item["path"], item["digest"])))
        return refs

    def _persisted_evidence_ref(self, value: object) -> str:
        if self.draft is None:
            raise RuntimeError("BenchRun is not active")
        raw = Path(str(value))
        candidate = raw if raw.is_absolute() else self.draft.path / raw
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(self.draft.path.resolve())
        except (OSError, ValueError) as exc:
            raise EvidenceReferenceError("evidence reference is not persisted in this run") from exc
        if not resolved.is_file():
            raise EvidenceReferenceError("evidence reference must identify a persisted file")
        return relative.as_posix()

    def verify(self, verifier: Callable[..., Any], /, **inputs: Any) -> dict[str, Any]:
        if self.draft is None:
            raise RuntimeError("BenchRun is not active")
        started = time.monotonic()
        source_refs = self._verifier_source_refs(verifier)
        source_ref = source_refs[0]
        reason: dict[str, str] | None = None
        evidence_refs: list[str] = []
        try:
            returned = verifier(self, **inputs)
            if isinstance(returned, Mapping):
                evidence_refs = [
                    self._persisted_evidence_ref(item) for item in returned.get("evidence_refs", [])
                ]
            status = "pass"
        except BenchSkip as exc:
            status = "skip"
            reason = sanitize_reason(exc.code, exc)
        except AssertionError as exc:
            status = "fail"
            reason = sanitize_reason("assertion_failed", str(exc) or "assertion failed")
            try:
                evidence_refs = [
                    self._persisted_evidence_ref(item)
                    for item in getattr(exc, "evidence_refs", [])
                ]
            except EvidenceReferenceError:
                evidence_refs = []
                status = "error"
                reason = {
                    "code": "invalid_evidence_ref",
                    "message": "verifier evidence must reference a persisted file in this run",
                }
        except EvidenceReferenceError:
            evidence_refs = []
            status = "error"
            reason = {
                "code": "invalid_evidence_ref",
                "message": "verifier evidence must reference a persisted file in this run",
            }
        except Exception as exc:
            status = "error"
            reason = sanitize_reason("verifier_error", f"{type(exc).__name__}: {exc}")
        record = {
            "name": f"{verifier.__module__}.{verifier.__qualname__}",
            "source_ref": source_ref,
            "status": status,
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            "evidence_refs": evidence_refs,
            "reason": reason,
        }
        self.verifiers.append(record)
        for ref in source_refs:
            if ref not in self.verifier_sources:
                self.verifier_sources.append(ref)
        self.draft.write_json("source/verifiers.json", {"sources": self.verifier_sources})
        return record

    def _outcome_from_verifiers(self) -> tuple[str, str | None, dict[str, str] | None]:
        if not self.verifiers:
            return (
                "error",
                None,
                {
                    "code": "no_verifiers_called",
                    "message": "bench adjudication requires at least one run.verify call",
                },
            )
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

    def _persist_assertion_failure(
        self, exc: AssertionError, traceback: TracebackType | None
    ) -> str:
        if self.draft is None:
            raise RuntimeError("BenchRun is not active")
        last = traceback
        while last is not None and last.tb_next is not None:
            last = last.tb_next
        location_path = "[external]"
        location_line = 0
        location_function = "[unknown]"
        if last is not None:
            raw_path = Path(last.tb_frame.f_code.co_filename).resolve()
            try:
                location_path = raw_path.relative_to(
                    self.bench.repository_path.resolve()
                ).as_posix()
            except ValueError:
                pass
            location_line = last.tb_lineno
            location_function = last.tb_frame.f_code.co_name
        artifact_ref = "artifacts/scenario-assertion.json"
        self.draft.write_json(
            artifact_ref,
            {
                "schema_version": "opentraces.bench.assertion-failure.v0",
                "type": type(exc).__name__,
                "message": sanitize_diagnostic_value(str(exc) or "assertion failed"),
                "location": {
                    "path": sanitize_diagnostic_value(location_path),
                    "line": location_line,
                    "function": sanitize_diagnostic_value(location_function),
                },
                "traceback": sanitize_diagnostic_value(
                    "".join(traceback_module.format_exception(type(exc), exc, traceback))
                ),
            },
        )
        return artifact_ref

    def _finalize(
        self,
        *,
        execution_status: str,
        verdict: str | None,
        reason: dict[str, str] | None,
        scenario_assertion: bool = False,
        scenario_assertion_ref: str | None = None,
    ) -> None:
        if self.draft is None:
            return
        runtime_diagnostics = getattr(self.bench.box_runtime, "diagnostic_records", None)
        events = list(runtime_diagnostics()) if callable(runtime_diagnostics) else []
        events.extend(self._lifecycle_diagnostics)
        events = [sanitize_diagnostic_value(event) for event in events]
        artifacts: list[dict[str, Any]] = []
        timing_root = self.draft.path / "artifacts" / "crabbox-timing"
        if timing_root.is_dir():
            for timing_path in sorted(timing_root.glob("*.json")):
                artifacts.append(
                    {
                        "path": timing_path.relative_to(self.draft.path).as_posix(),
                        "media_type": "application/json",
                        "kind": "crabbox_timing",
                    }
                )
        if events:
            diagnostic_ref = "artifacts/box-lifecycle.json"
            self.draft.write_json(diagnostic_ref, {"events": events})
            artifacts.append(
                {
                    "path": diagnostic_ref,
                    "media_type": "application/json",
                    "kind": "lifecycle_diagnostics",
                }
            )
        if scenario_assertion_ref is not None:
            artifacts.append(
                {
                    "path": scenario_assertion_ref,
                    "media_type": "application/json",
                    "kind": "assertion_failure",
                }
            )
        duration_ms = max(0, int((time.monotonic() - self._started) * 1000))
        evidence_requirements = [
            {
                "name": verifier["name"],
                "complete": verifier["status"] in {"pass", "fail"},
                "evidence_refs": verifier["evidence_refs"],
            }
            for verifier in self.verifiers
        ]
        if scenario_assertion:
            evidence_requirements.append(
                {
                    "name": "scenario.assertion",
                    "complete": scenario_assertion_ref is not None,
                    "evidence_refs": (
                        [scenario_assertion_ref] if scenario_assertion_ref is not None else []
                    ),
                }
            )
        if verdict == "skip" and not evidence_requirements:
            evidence_requirements.append(
                {
                    "name": str((reason or {}).get("code") or "bench.prerequisite"),
                    "complete": False,
                    "evidence_refs": [],
                }
            )
        if execution_status == "error":
            evidence_requirements.append(
                {
                    "name": "bench.adjudication",
                    "complete": False,
                    "evidence_refs": [],
                }
            )
        evidence_complete = all(item["complete"] for item in evidence_requirements)
        box_pin = (
            {
                "provider": self.box.provider,
                "image": self.box.image,
                "sandbox_tier": self.box.sandbox_tier,
                "runtime": {
                    "name": "crabbox",
                    "version": getattr(self.bench.box_runtime, "crabbox_version", None),
                },
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
            artifacts=artifacts,
            capture=None,
            pins={
                "product": {"commit": self.bench.source.commit},
                "observer": {"package": "opentraces", "version": __version__},
                "environment": box_pin,
                "harness": None,
                "model_wire": None,
                "emulators": {name: emulator.pin for name, emulator in self._emulators.items()},
                "app_state": self._app_state_pin,
            },
        )
        if os.environ.get("OT_BENCH_DEFER_FINALIZE") == "1":
            self.draft.stage_result(result)
            self.final_path = self.draft.path
        else:
            self.final_path = self.draft.finalize(result)
        self.result = result

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        suppressed = False
        scenario_assertion = False
        scenario_assertion_ref: str | None = None
        if exc is None:
            execution_status, verdict, reason = self._outcome_from_verifiers()
        elif isinstance(exc, BenchSkip):
            execution_status, verdict = "complete", "skip"
            reason = sanitize_reason(exc.code, exc)
            suppressed = True
        elif isinstance(exc, AssertionError):
            execution_status, verdict = "complete", "fail"
            reason = sanitize_reason("assertion_failed", str(exc) or "assertion failed")
            scenario_assertion = True
            try:
                scenario_assertion_ref = self._persist_assertion_failure(exc, traceback)
            except Exception as evidence_exc:
                self._lifecycle_diagnostics.append(
                    sanitize_reason("assertion_evidence_failed", evidence_exc)
                )
            suppressed = True
        else:
            execution_status, verdict = "error", None
            reason = sanitize_reason(
                "machinery_error",
                f"{type(exc).__name__}: {exc}" if exc is not None else "unknown error",
            )

        for emulator in self._emulators.values():
            try:
                emulator.stop()
                emulator.snapshot_ledger()
            except Exception as emulator_error:
                self._lifecycle_diagnostics.append(
                    sanitize_reason("emulator_cleanup_failed", emulator_error)
                )

        release_error: Exception | None = None
        if self.box is not None:
            try:
                self.bench.box_runtime.release(self.box)
            except Exception as caught:
                release_error = caught
        if release_error is not None:
            self._lifecycle_diagnostics.append(
                sanitize_reason(getattr(release_error, "code", "release_failed"), release_error)
            )
        self._finalize(
            execution_status=execution_status,
            verdict=verdict,
            reason=reason,
            scenario_assertion=scenario_assertion,
            scenario_assertion_ref=scenario_assertion_ref,
        )
        return suppressed
