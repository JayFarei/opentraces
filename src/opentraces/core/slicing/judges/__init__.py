"""Pluggable judges that resolve the cheap-LLM step (issue #141).

A judge turns a slicer's :class:`JudgmentRequest` set into answers:

  - ``deterministic`` — identity; use the slicer's deterministic defaults
    (S1/S2 raise no requests, so this is also their judge).
  - ``agent`` (DEFAULT) — "the CLI prompts the agent that ran it." If requests
    exist and no ``--answers`` were supplied, resolve returns
    :data:`NEEDS_JUDGMENT`; the CLI then prints the requests + an instruction
    and exits ``rc=10``. The invoking agent answers them and re-runs with
    ``--answers`` (``rc=0``). No credentials, no setup.
  - ``provider`` — optional unattended fallback via ``core.llm_provider``.
  - ``human`` — same requests answered by a person (an ``--answers`` file).

The judge NEVER affects tiling — it only chooses boundaries.
"""
from __future__ import annotations

from typing import Any

from . import agent as _agent
from . import deterministic as _deterministic
from . import human as _human
from . import provider as _provider

NEEDS_JUDGMENT = _agent.NEEDS_JUDGMENT
"""Sentinel: the slicer needs judgments the current judge cannot supply inline."""

_BACKENDS = {
    "deterministic": _deterministic,
    "agent": _agent,
    "provider": _provider,
    "human": _human,
}
JUDGE_NAMES = tuple(_BACKENDS)
DEFAULT_JUDGE = "agent"


def resolve(
    backend: str,
    *,
    slicer_name: str,
    steps: list[Any],
    requests: list[Any],
    provided_answers: dict | None,
) -> dict | object:
    """Resolve ``requests`` to an answers dict, or return NEEDS_JUDGMENT.

    Returns ``{request_id: {"decision", "confidence"}}``. An empty dict means
    "use the slicer's deterministic defaults". Answers always win, whatever the
    backend (recorded-answer replay), so the operation stays a pure function of
    ``(trace, answers)``.
    """
    mod = _BACKENDS.get(backend)
    if mod is None:
        raise KeyError(f"unknown judge {backend!r} (known: {', '.join(JUDGE_NAMES)})")
    if provided_answers:
        return provided_answers
    return mod.resolve(slicer_name=slicer_name, steps=steps, requests=requests)
