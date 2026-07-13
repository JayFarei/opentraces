"""Portable, source-honest capture orchestration.

This module owns capture finalization for both long-running installations and
leased workspaces.  Callers open one session, inject its bindings, and call
``finish`` once; they never sequence source-specific flush or projection steps.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.error import URLError
from urllib.request import urlopen

from .. import __version__

CapturePlacement = Literal["persistent", "leased"]
CaptureSourceName = Literal[
    "session_jsonl",
    "telemetry",
    "watcher",
    "git",
    "bucket",
]
CaptureViewName = Literal["model_boundary", "harness", "world_effects"]
Completeness = Literal["full", "partial", "missing"]
SourceStatus = Literal["finalized", "partial", "unavailable", "timed_out"]

CAPTURE_RESULT_SCHEMA = "opentraces.capture.result.v1"
_KNOWN_SOURCES = frozenset({"session_jsonl", "telemetry", "watcher", "git", "bucket"})
_KNOWN_SECURITY_POLICIES = frozenset(
    {"configured", "off", "basic", "recommended", "strict"}
)
_FINALIZATION_ORDER: tuple[CaptureSourceName, ...] = (
    "session_jsonl",
    "telemetry",
    "watcher",
    "git",
    "bucket",
)
_SOURCE_VIEW: dict[str, CaptureViewName] = {
    "telemetry": "model_boundary",
    "session_jsonl": "harness",
    "watcher": "world_effects",
    "git": "world_effects",
    "bucket": "world_effects",
}


@dataclass(frozen=True)
class CapturePlan:
    """One placement-neutral request for observed capture evidence."""

    project: Path
    workspace: Path
    placement: CapturePlacement
    requested_sources: tuple[CaptureSourceName, ...]
    required_sources: tuple[CaptureSourceName, ...] = ()
    observer_version: str = __version__
    product_under_test_version: str = __version__
    result_dir: Path | None = None
    actor: str | None = None
    session_id: str | None = None
    session_path: Path | None = None
    trace_id: str | None = None
    security_policy: str = "configured"
    raw_body_retention: str = "delete"
    require_observer_separation: bool = False
    product_under_test_pid: int | None = None

    def __post_init__(self) -> None:
        requested = tuple(dict.fromkeys(self.requested_sources))
        required = tuple(dict.fromkeys(self.required_sources))
        unknown = (set(requested) | set(required)) - _KNOWN_SOURCES
        if unknown:
            raise ValueError(f"unknown capture sources: {sorted(unknown)}")
        if not set(required).issubset(requested):
            raise ValueError("required_sources must be included in requested_sources")
        if self.placement not in ("persistent", "leased"):
            raise ValueError("placement must be 'persistent' or 'leased'")
        if not self.observer_version.strip():
            raise ValueError("observer_version must be pinned")
        if not self.product_under_test_version.strip():
            raise ValueError("product_under_test_version must be pinned")
        if self.security_policy not in _KNOWN_SECURITY_POLICIES:
            raise ValueError(
                f"unknown capture security policy {self.security_policy!r}; "
                f"choose one of: {', '.join(sorted(_KNOWN_SECURITY_POLICIES))}"
            )
        if self.product_under_test_pid is not None and self.product_under_test_pid <= 0:
            raise ValueError("product_under_test_pid must be positive")
        object.__setattr__(self, "project", Path(self.project).resolve())
        object.__setattr__(self, "workspace", Path(self.workspace).resolve())
        object.__setattr__(self, "requested_sources", requested)
        object.__setattr__(self, "required_sources", required)
        if self.result_dir is not None:
            object.__setattr__(self, "result_dir", Path(self.result_dir).resolve())
        if self.session_path is not None:
            object.__setattr__(self, "session_path", Path(self.session_path).resolve())


@dataclass(frozen=True)
class CaptureBindings:
    """Values a placement owner injects into the observed actor."""

    env: dict[str, str]
    otlp_endpoint: str | None = None
    raw_bodies_dir: str | None = None
    watched_roots: tuple[str, ...] = ()
    hook_commands: tuple[tuple[str, ...], ...] = ()
    hook_commands_status: Literal["observed", "unavailable", "not_requested"] = (
        "not_requested"
    )
    hook_commands_reason: str | None = None


@dataclass(frozen=True)
class CaptureSourceResult:
    name: str
    view: CaptureViewName
    requested: bool
    required: bool
    status: SourceStatus
    completeness: Completeness
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    duration_ms: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureViewResult:
    name: CaptureViewName
    completeness: Completeness
    methods: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class CaptureResult:
    """Frozen capture outcome.

    ``sources`` preserves the caller's ``CapturePlan.requested_sources`` order
    even though finalization runs in dependency order internally.
    """

    schema_version: str
    module_version: str
    placement: CapturePlacement
    completeness: Literal["complete", "partial"]
    observer_version: str
    product_under_test_version: str
    sources: tuple[CaptureSourceResult, ...]
    views: tuple[CaptureViewResult, ...]
    limitations: tuple[str, ...]
    trace_refs: tuple[str, ...]
    security: dict[str, Any]
    result_path: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def source(self, name: str) -> CaptureSourceResult:
        for source in self.sources:
            if source.name == name:
                return source
        raise KeyError(name)

    def view(self, name: str) -> CaptureViewResult:
        for view in self.views:
            if view.name == name:
                return view
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        # JSON is the frozen representation; normalise tuple-valued dataclass
        # fields to their persisted list form so an in-memory result compares
        # byte-for-byte with the record read back from disk.
        return json.loads(json.dumps(asdict(self), sort_keys=True))


@dataclass
class _OwnedProcess:
    source: str
    process: subprocess.Popen[bytes]
    stdout: Any
    stderr: Any


class CaptureSession:
    """An open capture lifecycle with internally owned finalization."""

    def __init__(
        self,
        plan: CapturePlan,
        bindings: CaptureBindings,
        *,
        result_dir: Path,
        capture_root: Path,
        processes: dict[str, _OwnedProcess],
        open_limitations: dict[str, list[str]],
        open_details: dict[str, dict[str, Any]],
    ) -> None:
        self.plan = plan
        self.bindings = bindings
        self._result_dir = result_dir
        self._capture_root = capture_root
        self._processes = processes
        self._open_limitations = open_limitations
        self._open_details = open_details
        self._finished: CaptureResult | None = None
        self._trace_refs = list([plan.trace_id] if plan.trace_id is not None else [])
        self._interrupted_sources: set[str] = set()

    def interrupt(self, source: str) -> bool:
        """Terminate an owned leased source for an explicit fault injection."""
        owned = self._processes.get(source)
        if owned is None or owned.process.poll() is not None:
            return False
        owned.process.terminate()
        try:
            owned.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            owned.process.kill()
            owned.process.wait(timeout=2.0)
        self._interrupted_sources.add(source)
        return True

    def finish(self, deadline: float | None = None) -> CaptureResult:
        """Finalize every requested source without exceeding ``deadline``.

        ``deadline`` is an absolute :func:`time.monotonic` value.  A source that
        cannot settle inside the remaining budget is recorded as timed out; it
        is never inferred complete from the request.
        """
        if self._finished is not None:
            return self._finished
        if deadline is None:
            deadline = time.monotonic() + 30.0

        if "telemetry" in self.plan.requested_sources:
            self._quiesce_telemetry(deadline)

        finalized_by_name: dict[str, CaptureSourceResult] = {}
        requested = set(self.plan.requested_sources)
        ordered_sources = [source for source in _FINALIZATION_ORDER if source in requested]
        for index, source in enumerate(ordered_sources):
            if time.monotonic() >= deadline:
                finalized_by_name[source] = self._timed_out(
                    source, "finalization deadline exhausted"
                )
                continue
            sources_left = len(ordered_sources) - index
            remaining = max(0.0, deadline - time.monotonic())
            # Reserve a cold-start allowance for every later child. A silent
            # early adapter must not consume the whole budget and leave a
            # later source only a process-start timeout report.
            if sources_left == 1:
                source_budget = remaining
            elif remaining < 3.0:
                source_budget = remaining * 0.15
            else:
                source_budget = remaining / sources_left
            source_deadline = time.monotonic() + max(0.0, source_budget)
            if source == "telemetry":
                finalized = self._finalize_telemetry(source_deadline)
            else:
                finalized = self._run_isolated_finalizer(source, source_deadline)
            finalized_by_name[source] = finalized
            trace_id = finalized.details.get("trace_id")
            if trace_id and trace_id not in self._trace_refs:
                self._trace_refs.append(str(trace_id))

        source_results = [
            finalized_by_name[source] for source in self.plan.requested_sources
        ]

        self._stop_processes(deadline)
        views = _summarize_views(source_results)
        observer_pid = os.getpid()
        product_pid = self.plan.product_under_test_pid
        product_alive = _pid_is_alive(product_pid)
        separation_proven = bool(
            product_pid is not None
            and product_alive
            and product_pid != observer_pid
        )
        complete = bool(source_results) and all(
            source.completeness == "full" for source in source_results
        ) and (not self.plan.require_observer_separation or separation_proven)
        source_limitations = tuple(
            limitation
            for source in source_results
            for limitation in source.limitations
        )
        provenance = {
            "observer": {
                "version": __version__,
                "derivation": "runtime",
                "caller_claim": self.plan.observer_version,
                "pid": observer_pid,
                "executable": sys.executable,
            },
            "product_under_test": {
                "version": self.plan.product_under_test_version,
                "derivation": "runtime_pid" if product_pid is not None else "caller_claim",
                "pid": product_pid,
                "pid_alive": product_alive,
            },
            "separation": {
                "required": self.plan.require_observer_separation,
                "proven": separation_proven,
                "reason": (
                    None
                    if separation_proven
                    else "observer and product process separation was not proven"
                ),
            },
        }
        provenance_limitations = (
            ()
            if separation_proven
            else ("non-self-observation separation is not proven for this capture",)
        )
        declared_security = [
            source.details.get("capture_security_policy")
            for source in source_results
            if isinstance(source.details.get("capture_security_policy"), dict)
        ]
        security_observations = [
            source.details.get("security_observation")
            for source in source_results
            if isinstance(source.details.get("security_observation"), dict)
            and source.details["security_observation"].get("observed") is True
        ]
        configured_tools = (
            list(declared_security[0].get("configured_tools") or [])
            if declared_security
            else []
        )
        tools_applied = list(
            dict.fromkeys(
                str(tool)
                for observation in security_observations
                for tool in observation.get("tools_applied") or []
            )
        )
        security_limitations = (
            ()
            if security_observations
            else (
                "security applied-state observation unavailable; "
                "no sanitized TraceRecord was finalized",
            )
        )
        limitations = (
            *source_limitations,
            *provenance_limitations,
            *security_limitations,
        )
        result_path = self._result_dir / "capture_result.json"
        result = CaptureResult(
            schema_version=CAPTURE_RESULT_SCHEMA,
            module_version=__version__,
            placement=self.plan.placement,
            completeness="complete" if complete else "partial",
            observer_version=__version__,
            product_under_test_version=self.plan.product_under_test_version,
            sources=tuple(source_results),
            views=tuple(views),
            limitations=limitations,
            trace_refs=tuple(self._trace_refs),
            security={
                "policy": self.plan.security_policy,
                "declared_policy": self.plan.security_policy,
                "configured_tools": configured_tools,
                "tools_applied": tools_applied,
                # Backward-compatible alias; its value now comes from the
                # observed record rather than from enabled configuration.
                "effective_tools": tools_applied,
                "observed": bool(security_observations),
                "raw_body_retention": self.plan.raw_body_retention,
            },
            provenance=provenance,
            result_path=str(result_path),
        )
        _atomic_write_json(result_path, result.to_dict())
        self._finished = result
        return result

    def _finalize_telemetry(self, deadline: float) -> CaptureSourceResult:
        started = time.monotonic()
        limitations = list(self._open_limitations.get("telemetry", ()))
        owned = self._processes.get("telemetry")
        if "telemetry" in self._interrupted_sources:
            limitations.append("source process exited after explicit interruption")
            return CaptureSourceResult(
                name="telemetry",
                view="model_boundary",
                requested=True,
                required="telemetry" in self.plan.required_sources,
                status="unavailable",
                completeness="missing",
                limitations=tuple(limitations),
                duration_ms=_duration_ms(started),
            )
        if (
            owned is not None
            and owned.process.poll() is not None
            and owned.process.returncode != 0
        ):
            limitations.append(
                f"source process exited with code {owned.process.returncode}"
            )
            return CaptureSourceResult(
                name="telemetry",
                view="model_boundary",
                requested=True,
                required="telemetry" in self.plan.required_sources,
                status="unavailable",
                completeness="missing",
                limitations=tuple(limitations),
                duration_ms=_duration_ms(started),
            )
        if limitations:
            return CaptureSourceResult(
                name="telemetry",
                view="model_boundary",
                requested=True,
                required="telemetry" in self.plan.required_sources,
                status="unavailable",
                completeness="missing",
                limitations=tuple(limitations),
                duration_ms=_duration_ms(started),
            )
        if time.monotonic() >= deadline:
            return self._timed_out("telemetry", "telemetry finalization timed out")
        finalized = self._run_isolated_finalizer("telemetry", deadline)
        return CaptureSourceResult(
            name=finalized.name,
            view=finalized.view,
            requested=finalized.requested,
            required=finalized.required,
            status=finalized.status,
            completeness=finalized.completeness,
            evidence_refs=finalized.evidence_refs,
            limitations=finalized.limitations,
            duration_ms=_duration_ms(started),
            details=finalized.details,
        )

    def _quiesce_telemetry(self, deadline: float) -> None:
        owned = self._processes.get("telemetry")
        if owned is None or owned.process.poll() is not None:
            return
        owned.process.terminate()
        try:
            owned.process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            owned.process.kill()
            owned.process.wait(timeout=1.0)
            self._open_limitations.setdefault("telemetry", []).append(
                "telemetry receiver could not drain before the finalization deadline"
            )
        self._open_details.setdefault("telemetry", {})["ingress_quiesced"] = True

    def _run_isolated_finalizer(
        self,
        source: str,
        deadline: float,
    ) -> CaptureSourceResult:
        started = time.monotonic()
        finalizers = self._result_dir / "finalizers"
        finalizers.mkdir(parents=True, exist_ok=True)
        request_path = finalizers / f"{source}.request.json"
        report_path = finalizers / f"{source}.report.json"
        request = {
            "source": source,
            "project": str(self.plan.project),
            "workspace": str(self.plan.workspace),
            "actor": self.plan.actor,
            "session_id": self.plan.session_id,
            "session_path": str(self.plan.session_path) if self.plan.session_path else None,
            "trace_id": self._trace_refs[-1] if self._trace_refs else None,
            "remaining_seconds": max(0.0, deadline - time.monotonic() - 0.05),
            "deadline_monotonic": max(time.monotonic(), deadline - 0.05),
            "security_policy": self.plan.security_policy,
            "raw_body_retention": self.plan.raw_body_retention,
            "open_details": self._open_details.get(source) or {},
            "requested_sources": list(self.plan.requested_sources),
        }
        _atomic_write_json(request_path, request)
        stdout = (finalizers / f"{source}.stdout").open("ab")
        stderr = (finalizers / f"{source}.stderr").open("ab")
        env = dict(os.environ)
        env["OT_OPENTRACES_DIR"] = str(self._capture_root)
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "opentraces.capture._source_finalizer",
                    "--request",
                    str(request_path),
                    "--report",
                    str(report_path),
                ],
                cwd=self.plan.workspace,
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
        except OSError as exc:
            stdout.close()
            stderr.close()
            return self._unavailable(
                source,
                f"source finalizer launch failed: {type(exc).__name__}: {exc}",
            )
        try:
            remaining = max(0.0, deadline - time.monotonic())
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
            return CaptureSourceResult(
                name=source,
                view=_SOURCE_VIEW[source],
                requested=True,
                required=source in self.plan.required_sources,
                status="timed_out",
                completeness="missing",
                evidence_refs=(
                    request_path.relative_to(self._result_dir).as_posix(),
                ),
                limitations=("source finalizer exceeded the capture deadline",),
                duration_ms=_duration_ms(started),
            )
        finally:
            stdout.close()
            stderr.close()
        if not report_path.is_file():
            return self._unavailable(
                source,
                f"source finalizer exited {process.returncode} without a report",
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        refs = [
            request_path.relative_to(self._result_dir).as_posix(),
            report_path.relative_to(self._result_dir).as_posix(),
        ]
        refs.extend(str(ref) for ref in report.get("evidence_refs") or [])
        return CaptureSourceResult(
            name=source,
            view=_SOURCE_VIEW[source],
            requested=True,
            required=source in self.plan.required_sources,
            status=report.get("status", "unavailable"),
            completeness=report.get("completeness", "missing"),
            evidence_refs=tuple(refs),
            limitations=tuple(report.get("limitations") or []),
            duration_ms=_duration_ms(started),
            details=dict(report.get("details") or {}),
        )

    def _unavailable(self, source: str, reason: str) -> CaptureSourceResult:
        return CaptureSourceResult(
            name=source,
            view=_SOURCE_VIEW[source],
            requested=True,
            required=source in self.plan.required_sources,
            status="unavailable",
            completeness="missing",
            limitations=(reason,),
        )

    def _timed_out(self, source: str, reason: str) -> CaptureSourceResult:
        return CaptureSourceResult(
            name=source,
            view=_SOURCE_VIEW[source],
            requested=True,
            required=source in self.plan.required_sources,
            status="timed_out",
            completeness="missing",
            limitations=(reason,),
        )

    def _stop_processes(self, deadline: float) -> None:
        for owned in self._processes.values():
            try:
                if owned.process.poll() is None:
                    owned.process.terminate()
                    remaining = max(0.0, deadline - time.monotonic())
                    owned.process.wait(timeout=min(remaining, 1.0))
            except (OSError, subprocess.TimeoutExpired):
                if owned.process.poll() is None:
                    owned.process.kill()
            finally:
                owned.stdout.close()
                owned.stderr.close()


class Capture:
    """Public entry point for the portable capture lifecycle."""

    @staticmethod
    def open(plan: CapturePlan) -> CaptureSession:
        lifecycle_opened_at = time.time()
        if not plan.project.is_dir():
            raise FileNotFoundError(f"capture project does not exist: {plan.project}")
        if not plan.workspace.is_dir():
            raise FileNotFoundError(f"capture workspace does not exist: {plan.workspace}")

        result_dir = plan.result_dir or (plan.workspace / ".opentraces" / "capture")
        result_dir.mkdir(parents=True, exist_ok=True)
        if plan.placement == "persistent":
            from ..core import paths as capture_paths

            capture_root = Path(capture_paths.OPENTRACES_DIR).resolve()
            raw_bodies = capture_paths.raw_bodies_dir()
        else:
            capture_root = result_dir / "runtime"
            raw_bodies = capture_root / "raw-bodies"
        capture_root.mkdir(parents=True, exist_ok=True)
        raw_bodies.mkdir(parents=True, exist_ok=True)

        env = {"OT_OPENTRACES_DIR": str(capture_root)}
        endpoint: str | None = None
        processes: dict[str, _OwnedProcess] = {}
        limitations: dict[str, list[str]] = {}
        open_details: dict[str, dict[str, Any]] = {}
        if "telemetry" in plan.requested_sources:
            open_details["telemetry"] = {
                "opened_at_unix": lifecycle_opened_at,
            }
            if plan.placement == "leased":
                endpoint, owned, reason = _start_leased_receiver(
                    capture_root=capture_root,
                    raw_bodies=raw_bodies,
                    result_dir=result_dir,
                )
                if owned is not None:
                    processes["telemetry"] = owned
                if reason is not None:
                    limitations.setdefault("telemetry", []).append(reason)
            else:
                endpoint = os.environ.get(
                    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318"
                )
            env.update(
                {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_METRICS_EXPORTER": "otlp",
                    "OTEL_TRACES_EXPORTER": "otlp",
                    "OTEL_LOG_RAW_API_BODIES": f"file:{raw_bodies}",
                }
            )

        if "watcher" in plan.requested_sources:
            from .fs_watcher.runtime import poll_project_once

            watcher_state_path = result_dir / "runtime" / "watcher-state.json"
            root_details = {
                "observed_root": str(plan.project),
                "declared_workspace": str(plan.workspace),
            }
            if plan.workspace != plan.project:
                open_details["watcher"] = {
                    **root_details,
                    "state_path": str(watcher_state_path),
                    "poll_succeeded": False,
                    "baseline_proven": False,
                    "root_binding_proven": False,
                }
                limitations.setdefault("watcher", []).append(
                    "declared workspace differs from the watcher observed root"
                )
            else:
                try:
                    baseline = poll_project_once(
                        plan.project,
                        state_path=watcher_state_path,
                        exclude_paths=[plan.session_path] if plan.session_path else None,
                        writer="portable-capture-watcher",
                    )
                    baseline_proven = watcher_state_path.is_file()
                    open_details["watcher"] = {
                        **root_details,
                        "state_path": str(watcher_state_path),
                        "poll_succeeded": True,
                        "baseline_proven": baseline_proven,
                        "baseline_initialized": baseline.baseline_initialized,
                        "paths_seen": baseline.paths_seen,
                        "skipped_paths": baseline.skipped_paths,
                        "root_binding_proven": True,
                    }
                    if not baseline_proven:
                        limitations.setdefault("watcher", []).append(
                            "watcher baseline could not be established at Capture.open"
                        )
                except Exception as exc:  # noqa: BLE001 - persisted honesty boundary
                    open_details["watcher"] = {
                        **root_details,
                        "state_path": str(watcher_state_path),
                        "poll_succeeded": False,
                        "baseline_proven": False,
                        "root_binding_proven": True,
                    }
                    limitations.setdefault("watcher", []).append(
                        "watcher baseline failed at Capture.open: "
                        f"{type(exc).__name__}: {exc}"
                    )

        hook_commands, hook_status, hook_reason = _hook_bindings(plan)
        bindings = CaptureBindings(
            env=env,
            otlp_endpoint=endpoint,
            raw_bodies_dir=str(raw_bodies),
            watched_roots=(str(plan.project),),
            hook_commands=hook_commands,
            hook_commands_status=hook_status,
            hook_commands_reason=hook_reason,
        )
        return CaptureSession(
            plan,
            bindings,
            result_dir=result_dir,
            capture_root=capture_root,
            processes=processes,
            open_limitations=limitations,
            open_details=open_details,
        )


def _start_leased_receiver(
    *,
    capture_root: Path,
    raw_bodies: Path,
    result_dir: Path,
) -> tuple[str, _OwnedProcess | None, str | None]:
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    logs = result_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout = (logs / "telemetry.stdout").open("ab")
    stderr = (logs / "telemetry.stderr").open("ab")
    env = dict(os.environ)
    env["OT_OPENTRACES_DIR"] = str(capture_root)
    command = [
        sys.executable,
        "-m",
        "opentraces.capture._leased_receiver",
        "--port",
        str(port),
        "--raw-bodies-dir",
        str(raw_bodies),
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=capture_root,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
    except OSError as exc:
        stdout.close()
        stderr.close()
        return endpoint, None, f"receiver launch failed: {type(exc).__name__}: {exc}"
    owned = _OwnedProcess("telemetry", process, stdout, stderr)
    if not _wait_for_health(endpoint, process, timeout=3.0):
        return endpoint, owned, "receiver did not become healthy"
    return endpoint, owned, None


def _hook_bindings(
    plan: CapturePlan,
) -> tuple[
    tuple[tuple[str, ...], ...],
    Literal["observed", "unavailable", "not_requested"],
    str | None,
]:
    if "session_jsonl" not in plan.requested_sources:
        return (), "not_requested", None
    actor = plan.actor or "claude-code"
    if plan.placement != "persistent":
        return (), "unavailable", "leased hook integration was not observed"
    if actor != "claude-code":
        return (), "unavailable", f"persistent hook observation is unavailable for {actor}"
    from .claude_code.install import registered_hook_commands

    observed = registered_hook_commands()
    if not observed:
        return (), "unavailable", "Claude Code OpenTraces hooks are not installed"
    return tuple(tuple(shlex.split(command)) for command in observed), "observed", None


def _wait_for_health(
    endpoint: str,
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with urlopen(f"{endpoint}/health", timeout=0.2) as response:  # noqa: S310
                return response.status == 200
        except (OSError, URLError):
            time.sleep(0.03)
    return False


def _summarize_views(
    sources: list[CaptureSourceResult],
) -> list[CaptureViewResult]:
    rows: list[CaptureViewResult] = []
    for name in ("model_boundary", "harness", "world_effects"):
        members = [source for source in sources if source.view == name]
        if not members:
            completeness: Completeness = "missing"
        elif all(source.completeness == "full" for source in members):
            completeness = "full"
        elif any(source.completeness in ("full", "partial") for source in members):
            completeness = "partial"
        else:
            completeness = "missing"
        rows.append(
            CaptureViewResult(
                name=name,  # type: ignore[arg-type]
                completeness=completeness,
                methods=tuple(source.name for source in members),
                evidence_refs=tuple(
                    ref for source in members for ref in source.evidence_refs
                ),
                limitations=tuple(
                    limitation
                    for source in members
                    for limitation in source.limitations
                ),
            )
        )
    return rows


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
