"""Review lifecycle operations shared by CLI, TUI, and web clients.

Each function here preserves the exact behavior of the inline site it was
extracted from, including any pre-existing discrepancies between clients.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .inbox import redact_pattern, redact_step
from .state import StateManager, TraceStatus

logger = logging.getLogger(__name__)


@dataclass
class RedactResult:
    """Outcome of a redact_step_and_persist call."""

    ok: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    step_index: int = -1


def redact_step_and_persist(
    staging_dir: Path,
    trace_id: str,
    step_index: int,
) -> RedactResult:
    """Redact a step in a staging JSONL file and atomically rewrite the file.

    Mirrors the duplicated implementation previously present in both
    ``cli.py::session_redact`` and ``web_server.py::api_redact_step``. Callers
    remain responsible for trace_id format validation and for emitting
    surface-specific user messages based on the returned RedactResult.
    """
    staging_file = staging_dir / f"{trace_id}.jsonl"
    if not staging_file.exists():
        return RedactResult(
            ok=False,
            error=f"Staging file not found for {trace_id}",
            error_code="NOT_FOUND",
            step_index=step_index,
        )

    text = staging_file.read_text().strip()
    if not text:
        return RedactResult(
            ok=False,
            error="Staging file is empty",
            error_code="EMPTY",
            step_index=step_index,
        )

    trace_data = json.loads(text.splitlines()[0])
    steps = trace_data.get("steps", [])
    if step_index < 0 or step_index >= len(steps):
        return RedactResult(
            ok=False,
            error=f"Step index {step_index} out of range",
            error_code="OUT_OF_RANGE",
            step_index=step_index,
        )

    redact_step(steps[step_index])

    # Atomic write: temp file + os.replace for crash safety
    new_line = json.dumps(trace_data, ensure_ascii=False)
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(staging_dir),
        suffix=".jsonl.tmp",
        delete=False,
    )
    try:
        fd.write(new_line + "\n")
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
        os.replace(fd.name, str(staging_file))
    except BaseException:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            logger.debug("Failed to clean up temp file: %s", fd.name)
        raise

    return RedactResult(ok=True, step_index=step_index)


def redact_pattern_and_persist(
    staging_dir: Path,
    trace_id: str,
    pattern: str,
    *,
    regex: bool = False,
    field: Optional[str] = None,
    step: Optional[int] = None,
) -> RedactResult:
    """Apply :func:`redact_pattern` to a staged trace and atomically rewrite it.

    Mirrors :func:`redact_step_and_persist` — same discovery, tempfile +
    ``os.replace`` atomic write, and ``RedactResult`` shape — so callers can
    swap helpers based on whether they want whole-step blanking or targeted
    find-and-replace.
    """
    staging_file = staging_dir / f"{trace_id}.jsonl"
    if not staging_file.exists():
        return RedactResult(
            ok=False,
            error=f"Staging file not found for {trace_id}",
            error_code="NOT_FOUND",
            step_index=step if step is not None else -1,
        )

    text = staging_file.read_text().strip()
    if not text:
        return RedactResult(
            ok=False,
            error="Staging file is empty",
            error_code="EMPTY",
            step_index=step if step is not None else -1,
        )

    trace_data = json.loads(text.splitlines()[0])

    try:
        redact_pattern(
            trace_data, pattern, regex=regex, field=field, step=step,
        )
    except ValueError as exc:
        msg = str(exc)
        if "out of range" in msg:
            code = "OUT_OF_RANGE"
        elif "pattern must be non-empty" in msg or "invalid regex" in msg:
            code = "INVALID_PATTERN"
        else:
            code = "INVALID_PATTERN"
        return RedactResult(
            ok=False,
            error=msg,
            error_code=code,
            step_index=step if step is not None else -1,
        )

    new_line = json.dumps(trace_data, ensure_ascii=False)
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(staging_dir),
        suffix=".jsonl.tmp",
        delete=False,
    )
    try:
        fd.write(new_line + "\n")
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
        os.replace(fd.name, str(staging_file))
    except BaseException:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            logger.debug("Failed to clean up temp file: %s", fd.name)
        raise

    return RedactResult(ok=True, step_index=step if step is not None else -1)


def reject_trace(
    state: StateManager,
    trace_id: str,
    *,
    with_session_kwarg: bool,
) -> None:
    """Mark a trace as REJECTED.

    The CLI variant historically omitted the ``session_id`` kwarg; web_server
    and TUI both pass it. ``with_session_kwarg`` preserves that discrepancy.
    """
    if with_session_kwarg:
        state.set_trace_status(trace_id, TraceStatus.REJECTED, session_id=trace_id)
    else:
        state.set_trace_status(trace_id, TraceStatus.REJECTED)


def discard_trace(
    state: StateManager,
    trace_id: str,
    *,
    staging_file: Path,
) -> None:
    """CLI discard flow: delete staging file + pop trace from state dict.

    Mirrors ``cli.py::session_discard`` exactly, including direct mutation of
    ``state._state['traces']`` followed by ``state.save()``.
    """
    if staging_file.exists():
        staging_file.unlink()

    entry = state.get_trace(trace_id)
    if entry is not None:
        state._state["traces"].pop(trace_id, None)
        state.save()


def discard_trace_state_only(
    state: StateManager,
    trace_id: str,
    *,
    staging_file: Path,
) -> None:
    """TUI discard flow: best-effort unlink + remove from state dict.

    Mirrors ``tui.py::action_discard`` exactly, including the try/except
    around ``staging_file.unlink()`` with logger.warning on OSError.
    """
    if staging_file.exists():
        try:
            staging_file.unlink()
        except OSError:
            logger.warning(
                "Failed to delete staging file %s", staging_file, exc_info=True
            )

    if trace_id in state._state.get("traces", {}):
        del state._state["traces"][trace_id]
        state.save()


def commit_single(
    state: StateManager,
    trace_id: str,
    task_desc: str,
) -> str:
    """Create a single-trace commit group. Returns the commit_id.

    Does NOT redundantly set COMMITTED status, matching cli/tui behavior.
    """
    return state.create_commit_group([trace_id], task_desc)


def commit_bulk(
    state: StateManager,
    trace_ids: Iterable[str],
    message: str,
) -> str:
    """Create a commit group for multiple traces and loop COMMITTED status.

    Matches ``web_server.py::api_add`` behavior exactly, including the
    redundant per-id ``set_trace_status(COMMITTED, session_id=...)`` loop that
    follows ``create_commit_group``.
    """
    ids = list(trace_ids)
    commit_id = state.create_commit_group(trace_ids=ids, message=message)
    for sid in ids:
        state.set_trace_status(sid, TraceStatus.COMMITTED, session_id=sid)
    return commit_id


def stage_trace(state: StateManager, trace_id: str) -> None:
    """Transition a trace to STAGED status (web stage route)."""
    state.set_trace_status(trace_id, TraceStatus.STAGED, session_id=trace_id)


def unstage_trace(state: StateManager, trace_id: str) -> None:
    """Transition a trace back to PARSED status (web unstage route)."""
    state.set_trace_status(trace_id, TraceStatus.PARSED, session_id=trace_id)


def reset_to_staged(state: StateManager, trace_id: str) -> None:
    """CLI reset flow: transition to STAGED (no session_id kwarg)."""
    state.set_trace_status(trace_id, TraceStatus.STAGED)


# ---------------------------------------------------------------------------
# Plan 032 Tier 2 — LLM semantic review orchestration.
#
# Extracted from cli/installers.py:review_llm_cmd. The CLI wraps this with
# flag parsing and human-readable output; everything else — dry-run estimation,
# cached-verdict detection, provider iteration — lives here.
# ---------------------------------------------------------------------------


@dataclass
class LLMReviewEstimate:
    sessions: int
    chars: int
    tokens: int
    cost_usd: float
    model: str
    api_format: str


@dataclass
class LLMReviewOutcome:
    api_format: str
    model: str
    results: list[dict]


def _collect_session_chars(records: list[dict]) -> int:
    total = 0
    for rec in records:
        for step in rec.get("steps", []) or []:
            total += len(step.get("content") or "")
            total += len(step.get("reasoning_content") or "")
    return total


def _steps_text(rec: dict) -> list[str]:
    out: list[str] = []
    for step in rec.get("steps", []) or []:
        content = step.get("content") or ""
        reasoning = step.get("reasoning_content") or ""
        if content or reasoning:
            out.append("\n".join(filter(None, [content, reasoning])))
    return out


def estimate_llm_review(
    records: list[dict], *, api_format: str, model: str,
) -> LLMReviewEstimate:
    """Dry-run token/cost estimate for an LLM review run."""
    from ..security.llm_review import estimate_cost

    chars = _collect_session_chars(records)
    est = estimate_cost(chars, model=model)
    return LLMReviewEstimate(
        sessions=len(records),
        chars=chars,
        tokens=int(est["tokens"]),
        cost_usd=float(est["cost_usd"]),
        model=model,
        api_format=api_format,
    )


def _trace_pre_blocked(rec: dict) -> str | None:
    """Return a short reason if deterministic security already blocked this trace.

    Deny-before-LLM (pattern borrowed from pi-share-hf): if Tier 1 /
    TruffleHog already marked the trace as blocked, never spend LLM
    tokens — synthesize a ``shareable=no`` verdict instead. Safer and
    prevents the LLM from accidentally approving a confirmed secret.

    Returns a reason string on hit, ``None`` otherwise.
    """
    meta = rec.get("metadata") or {}
    sec = meta.get("security") or {}
    if sec.get("blocked") is True:
        return str(sec.get("blocked_reason") or "security pipeline blocked trace")
    findings = sec.get("trufflehog_findings") or sec.get("tier_1_5_findings")
    if findings:
        return f"trufflehog findings: {len(findings)}"
    return None


def run_llm_review(
    records: list[dict],
    *,
    api_format: str,
    model: str,
    base_url: str = "",
    api_key_env: str = "",
    timeout: float = 120.0,
    prompt_version: str = "1",
    context: str = "",
    force: bool = False,
    on_progress=None,
) -> LLMReviewOutcome:
    """Run Tier 2 LLM semantic review across ``records``.

    Cached verdicts (same content_hash, api_format, base_url, model,
    prompt_version, context) are returned untouched unless ``force=True``.
    ``on_progress`` is called with (trace_id, status_str) for each record
    so the CLI can stream output.

    When a trace has already been blocked by deterministic security
    (Tier 1 / TruffleHog), the LLM is skipped and a synthetic
    ``shareable=no`` verdict is recorded instead (deny-before-LLM).
    """
    from datetime import datetime, timezone

    from ..security.llm_provider import build_provider
    from ..security.llm_review import (
        LLMReviewVerdict,
        review_key as _review_key,
        review_trace,
    )
    from ..security.verdict_display import verdict_badge, verdict_to_payload

    provider_kwargs: dict = {"timeout": timeout}
    if api_format == "openai-compat":
        if base_url:
            provider_kwargs["base_url"] = base_url
        if api_key_env:
            provider_kwargs["api_key_env"] = api_key_env
    llm = build_provider(api_format, model=model, **provider_kwargs)
    results: list[dict] = []

    def _payload(verdict, key) -> dict:
        payload = verdict_to_payload(
            verdict,
            api_format=api_format,
            model=model,
            base_url=base_url,
            reviewed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            prompt_version=prompt_version,
        )
        payload["review_key"] = key
        return payload

    for rec in records:
        trace_id = rec.get("trace_id", "?")
        content_hash = rec.get("content_hash", "")
        key = _review_key(
            content_hash, model, prompt_version, context,
            api_format=api_format, base_url=base_url,
        )

        existing = (rec.get("metadata") or {}).get("llm_review", {}) or {}
        if not force and existing.get("review_key") == key:
            badge = existing.get("badge") or "(cached)"
            if on_progress:
                on_progress(trace_id, f"[cached] {badge}")
            results.append(
                {"trace_id": trace_id, "cached": True, "verdict": existing}
            )
            continue

        # Deny-before-LLM: skip the call if deterministic security already
        # blocked this trace. Spend no tokens on confirmed-bad sessions.
        blocked_reason = _trace_pre_blocked(rec)
        if blocked_reason is not None:
            denied = LLMReviewVerdict(
                shareable="no",
                missed_sensitive_data="yes",
                summary=f"deny-before-LLM: {blocked_reason}",
            )
            payload = _payload(denied, key)
            payload["denied_before_llm"] = True
            if on_progress:
                on_progress(trace_id, f"[deny-before-llm] {blocked_reason}")
            results.append(
                {"trace_id": trace_id, "cached": False, "verdict": payload}
            )
            continue

        verdict = review_trace(
            steps=_steps_text(rec), provider=llm, context=context,
        )
        payload = _payload(verdict, key)
        if on_progress:
            on_progress(trace_id, verdict_badge(verdict))
        results.append(
            {"trace_id": trace_id, "cached": False, "verdict": payload}
        )

    return LLMReviewOutcome(api_format=api_format, model=model, results=results)
