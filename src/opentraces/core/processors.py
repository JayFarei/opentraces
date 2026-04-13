"""Post-processor subprocess runner.

A generic, agent-agnostic primitive for running any external command
over a trace. Opentraces never inspects a processor's internals; it
only round-trips trace JSON through the subprocess.

Contract (public, stable, implicit v1):
    * Receives the full trace JSON on stdin.
    * Returns trace JSON on stdout, exit 0.
    * Non-zero exit or schema-invalid output = failure (non-fatal by
      default, fatal under ``strict=True``).
    * Byte-identical output = no-op, not a failure.
    * Environment and CLI args passed through from the config entry.

Failure modes:
    * Missing binary (not on PATH or absolute path not executable) is
      logged and skipped, non-fatal by default.
    * Timeouts are logged, non-fatal by default.
    * Schema-invalid stdout rejects the output and preserves the
      pre-invocation trace.

Redaction ordering is the caller's responsibility (see
:mod:`opentraces.pipeline`). Processors must only see post-redaction
traces.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from opentraces_schema import TraceRecord

from .config import PostProcessorConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 120


@dataclass
class ProcessorResult:
    """Per-invocation result. One of these per processor in a run."""

    name: str
    status: str  # "ok" | "noop" | "skipped_missing" | "timeout" | "nonzero_exit" | "invalid_output"
    exit_code: int | None = None
    duration_s: float = 0.0
    message: str = ""


@dataclass
class RunResult:
    """Aggregate result across a processor chain."""

    record: TraceRecord
    results: list[ProcessorResult] = field(default_factory=list)
    failed: bool = False


class ProcessorError(Exception):
    """Raised under ``strict=True`` when any processor fails."""


def _resolve_binary(command: str) -> str | None:
    p = Path(command)
    if p.is_absolute():
        return str(p) if p.exists() and p.is_file() else None
    return shutil.which(command)


def _log_structured(name: str, status: str, exit_code: int | None, duration_s: float, message: str = "") -> None:
    """Emit one structured stderr line per invocation.

    Format is a single JSON object on one line for easy grep + parse;
    matches the pattern used by our other stderr audit logs.
    """
    line = json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "post_processor",
        "name": name,
        "status": status,
        "exit_code": exit_code,
        "duration_s": round(duration_s, 3),
        "message": message,
    })
    print(line, file=sys.stderr)


def run_processor(
    record: TraceRecord,
    spec: PostProcessorConfig,
    *,
    strict: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[TraceRecord, ProcessorResult]:
    """Run a single processor over ``record``.

    Returns ``(maybe_new_record, result)``. On any failure the original
    record is returned unchanged; under ``strict=True`` the failure is
    additionally promoted to :class:`ProcessorError`.
    """
    binary = _resolve_binary(spec.command)
    if binary is None:
        res = ProcessorResult(
            name=spec.name,
            status="skipped_missing",
            message=f"binary not found: {spec.command}",
        )
        _log_structured(spec.name, res.status, None, 0.0, res.message)
        if strict:
            raise ProcessorError(res.message)
        return record, res

    input_json = record.model_dump_json()
    argv = [binary, *spec.args]
    env_overrides = dict(spec.env) if spec.env else None
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=input_json,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**_inherit_env(), **(env_overrides or {})} if env_overrides else None,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        res = ProcessorResult(
            name=spec.name,
            status="timeout",
            duration_s=duration,
            message=f"timed out after {timeout_s}s",
        )
        _log_structured(spec.name, res.status, None, duration, res.message)
        if strict:
            raise ProcessorError(res.message)
        return record, res

    duration = time.monotonic() - start

    if proc.returncode != 0:
        res = ProcessorResult(
            name=spec.name,
            status="nonzero_exit",
            exit_code=proc.returncode,
            duration_s=duration,
            message=(proc.stderr or "").strip()[:500],
        )
        _log_structured(spec.name, res.status, proc.returncode, duration, res.message)
        if strict:
            raise ProcessorError(f"{spec.name}: exit {proc.returncode}")
        return record, res

    out_text = proc.stdout
    if out_text == input_json:
        res = ProcessorResult(
            name=spec.name, status="noop", exit_code=0, duration_s=duration,
        )
        _log_structured(spec.name, res.status, 0, duration)
        return record, res

    try:
        new_record = TraceRecord.model_validate_json(out_text)
    except Exception as exc:
        res = ProcessorResult(
            name=spec.name,
            status="invalid_output",
            exit_code=0,
            duration_s=duration,
            message=str(exc)[:500],
        )
        _log_structured(spec.name, res.status, 0, duration, res.message)
        if strict:
            raise ProcessorError(f"{spec.name}: invalid output: {exc}") from exc
        return record, res

    res = ProcessorResult(name=spec.name, status="ok", exit_code=0, duration_s=duration)
    _log_structured(spec.name, res.status, 0, duration)
    return new_record, res


def _inherit_env() -> dict[str, str]:
    import os
    return dict(os.environ)


def run_chain(
    record: TraceRecord,
    specs: Iterable[PostProcessorConfig],
    *,
    strict: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> RunResult:
    """Run an ordered chain of processors, piping each output to the next."""
    result = RunResult(record=record)
    current = record
    for spec in specs:
        current, one = run_processor(current, spec, strict=strict, timeout_s=timeout_s)
        result.results.append(one)
        if one.status not in ("ok", "noop"):
            result.failed = True
    result.record = current
    return result


def probe_processors(specs: Iterable[PostProcessorConfig]) -> list[tuple[PostProcessorConfig, str | None]]:
    """Return ``[(spec, resolved_path_or_None)]`` for each processor.

    Used by ``opentraces doctor`` to enumerate configured processors and
    report which are detected on PATH.
    """
    return [(s, _resolve_binary(s.command)) for s in specs]


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ProcessorError",
    "ProcessorResult",
    "RunResult",
    "probe_processors",
    "run_chain",
    "run_processor",
]
