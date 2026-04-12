"""Session intent summarization (plan 038 phase 2).

Deep module: takes an already-redacted :class:`TraceRecord` and a model
client, returns the same trace with :class:`Intent` populated.

Knows nothing about Claude Code's hook payload, file layout, or
subprocess concerns. Thin adapters (hook, CLI) call :func:`enrich_intent`.

Invariants:
    * Redaction ordering — this module never runs before the security
      pipeline. Callers pass post-redaction traces only; enforced by the
      pipeline wiring, not this file.
    * Idempotency — ``trace.intent`` already present and not ``source=user``
      is skipped unless ``force=True``.
    * User-edit protection — ``trace.intent.source == "user"`` is never
      overwritten, even with ``force=True``.
    * Token budget — prompt input is bounded by ``max_input_chars``
      regardless of step count.
    * Non-fatal failure — any exception from the model client is logged
      and :func:`enrich_intent` returns the trace unchanged; the UI
      falls back to the first-message snippet.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Callable, Protocol

from opentraces_schema import Intent, TraceRecord

logger = logging.getLogger(__name__)

# Approximate characters-per-token for budgeting; conservative for English
# code+prose. We budget on chars rather than tokens to stay dependency-free.
_CHARS_PER_TOKEN_APPROX = 4

DEFAULT_TOKEN_BUDGET = 6000
DEFAULT_MAX_INPUT_CHARS = DEFAULT_TOKEN_BUDGET * _CHARS_PER_TOKEN_APPROX
DEFAULT_FIRST_N_USER_MESSAGES = 6
DEFAULT_PER_MESSAGE_CHAR_CAP = 2000


class ModelClient(Protocol):
    """Callable contract: (model_id, prompt) -> response_text.

    Implementations are free to wrap any backend (Anthropic SDK, the
    ``claude`` CLI, Ollama, a stub). Only the two-arg signature and the
    expectation that the response is a string are contractual.
    """

    def __call__(self, model_id: str, prompt: str) -> str: ...


def _collect_user_messages(trace: TraceRecord, limit: int, per_cap: int) -> list[str]:
    out: list[str] = []
    for step in trace.steps:
        if step.role != "user":
            continue
        content = (step.content or "").strip()
        if not content:
            continue
        if len(content) > per_cap:
            content = content[:per_cap].rstrip() + " …[truncated]"
        out.append(content)
        if len(out) >= limit:
            break
    return out


def _tool_histogram(trace: TraceRecord) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for step in trace.steps:
        for call in step.tool_calls:
            counter[call.tool_name] += 1
    return counter.most_common()


def _outcome_digest(trace: TraceRecord) -> str:
    parts: list[str] = []
    o = trace.outcome
    if o.success is True:
        parts.append("outcome=success")
    elif o.success is False:
        parts.append("outcome=failure")
    if o.terminal_state:
        parts.append(f"terminal_state={o.terminal_state}")
    if o.committed:
        parts.append("committed=true")
    if o.commit_sha:
        parts.append(f"commit_sha={o.commit_sha[:12]}")
    if o.description:
        desc = o.description.strip()
        if len(desc) > 300:
            desc = desc[:300].rstrip() + "…"
        parts.append(f"outcome_desc={desc}")
    return "; ".join(parts) or "outcome=unknown"


def build_prompt(
    trace: TraceRecord,
    *,
    first_n_user_messages: int = DEFAULT_FIRST_N_USER_MESSAGES,
    per_message_char_cap: int = DEFAULT_PER_MESSAGE_CHAR_CAP,
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
) -> str:
    """Construct the summarization prompt from a parsed trace.

    Bounded by ``max_input_chars`` — the final string is hard-truncated
    regardless of how many steps the session has.
    """
    user_msgs = _collect_user_messages(trace, first_n_user_messages, per_message_char_cap)
    tools = _tool_histogram(trace)
    tool_str = ", ".join(f"{name}:{n}" for name, n in tools[:20]) or "(no tool calls)"
    outcome = _outcome_digest(trace)

    sections = [
        "You are summarizing an agent coding session. Return ONLY a JSON object "
        "with keys `title` (<= 12 words) and `summary` (1-3 sentences). No prose "
        "outside the JSON.",
        f"## Task description\n{trace.task.description or '(none)'}",
        "## First user messages\n" + ("\n---\n".join(user_msgs) if user_msgs else "(none)"),
        f"## Tool-call histogram\n{tool_str}",
        f"## Outcome\n{outcome}",
        "## Respond now with the JSON object.",
    ]
    prompt = "\n\n".join(sections)
    if len(prompt) > max_input_chars:
        prompt = prompt[:max_input_chars].rstrip() + "\n\n[prompt truncated to budget]"
    return prompt


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_model_response(text: str) -> tuple[str | None, str | None]:
    """Extract ``(title, summary)`` from a model response.

    Accepts a bare JSON object, a JSON object wrapped in ``` fences, or a
    JSON object embedded in surrounding text. Returns ``(None, None)`` if
    nothing parseable was found.
    """
    if not text:
        return None, None
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None, None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    title = obj.get("title")
    summary = obj.get("summary")
    if not isinstance(title, str):
        title = None
    if not isinstance(summary, str):
        summary = None
    return (title.strip() if title else None, summary.strip() if summary else None)


def _should_skip(trace: TraceRecord, force: bool) -> bool:
    if trace.intent is None:
        return False
    # User edits are never overwritten, even with --force.
    if trace.intent.source == "user":
        return True
    # A machine-produced intent is kept unless the caller asks for --force.
    return not force


def enrich_intent(
    trace: TraceRecord,
    model_client: ModelClient,
    *,
    model_id: str | None = None,
    force: bool = False,
    first_n_user_messages: int = DEFAULT_FIRST_N_USER_MESSAGES,
    per_message_char_cap: int = DEFAULT_PER_MESSAGE_CHAR_CAP,
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
) -> TraceRecord:
    """Populate ``trace.intent`` via the given model client.

    Returns the same ``TraceRecord`` instance (mutated) for caller
    convenience. On any failure — missing model id, exception raised by
    the client, unparseable response — logs and returns the trace
    unchanged. This is non-fatal by design: the UI falls back to the
    first-message snippet when ``intent`` is absent.
    """
    if _should_skip(trace, force):
        logger.debug(
            "intent.enrich: skipping trace=%s source=%s force=%s",
            trace.trace_id,
            trace.intent.source if trace.intent else None,
            force,
        )
        return trace

    resolved_model = model_id or trace.agent.model
    if not resolved_model:
        logger.warning("intent.enrich: no model id available; leaving intent unset")
        return trace

    prompt = build_prompt(
        trace,
        first_n_user_messages=first_n_user_messages,
        per_message_char_cap=per_message_char_cap,
        max_input_chars=max_input_chars,
    )
    try:
        response = model_client(resolved_model, prompt)
    except Exception as exc:
        logger.warning("intent.enrich: model client failed (%s); leaving intent unset", exc)
        return trace

    title, summary = parse_model_response(response)
    if not title and not summary:
        logger.warning(
            "intent.enrich: could not parse title/summary from response; leaving intent unset"
        )
        return trace

    trace.intent = Intent(
        title=title,
        summary=summary,
        source="llm_hook",
        model=resolved_model,
    )
    return trace


__all__ = [
    "DEFAULT_FIRST_N_USER_MESSAGES",
    "DEFAULT_MAX_INPUT_CHARS",
    "DEFAULT_PER_MESSAGE_CHAR_CAP",
    "DEFAULT_TOKEN_BUDGET",
    "ModelClient",
    "build_prompt",
    "enrich_intent",
    "parse_model_response",
]
