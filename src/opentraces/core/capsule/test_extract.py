"""Extract a runnable ``test`` payload (repro command + expected signal).

Three sources, in priority order (highest trust first):
  1. DECLARED — the reporter knows the repro command and passes it at export
     (``capsule export --test-command ... [--expect-error ...]``). Most reliable;
     a bug reporter just ran the failing command. With no ``--expect-error`` this
     is the declared-PASS form: ``--test-command`` alone grades exit-0 as the
     success bar (see ``oracle._generic``), so a maintainer can declare a
     passing test on a success session with no new flag.
  2. CAPTURED (error) — best-effort scan of the trace's steps for a command tool
     call (Bash/shell/run) whose observation ERRORED, near the failing step.
  3. CAPTURED (pass) — the success-path sibling (#156): a test the agent RAN
     that PASSED (exit 0). A success/design session — the corpus majority — has
     no errored command, so source 2 returns ``None`` even though the agent DID
     run a green test we can re-check. :func:`extract_passing_test_payload`
     captures it at ``source="captured_pass"``.

Returns ``None`` when nothing runnable is found — the capsule is still shareable
and falls back to ``capsule replay`` (intent-replay), which is honest for
design-task sessions that have no failing command.

Trust ordinal (:func:`oracle_trust_of`, ADR-0008 §5): ``declared`` ▸
``captured_error`` ▸ ``captured_pass`` ▸ ``intent_reposed`` ▸ ``none``. This is
the ``oracle_trust`` factor the replay clamp reads.
"""

from __future__ import annotations

from typing import Any

from .summary import _extract_error_line

_COMMAND_TOOLS = {"bash", "shell", "run", "execute", "terminal", "sh"}


def declared_test(command: str | None, expect_error: str | None, cwd: str | None = None) -> dict[str, Any] | None:
    if not command:
        return None
    expected = (
        {"kind": "error_string", "value": expect_error}
        if expect_error
        else {"kind": "nonzero_exit"}
    )
    return {"command": command, "expected": expected, "cwd": cwd or "", "source": "declared"}


def _command_from_tool_call(tc: Any) -> str | None:
    inp = getattr(tc, "input", None) or {}
    for key in ("command", "cmd", "script", "code"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_test_payload(record: Any, failing_step_index: int, *, window: int = 6) -> dict[str, Any] | None:
    """Best-effort: find the failing command nearest the failing step."""

    steps = list(getattr(record, "steps", []) or [])
    if not steps:
        return None
    lo = max(0, failing_step_index - window)
    hi = min(len(steps), failing_step_index + window + 1)
    # Search outward-ish: prefer steps at/after the failing step, then before.
    order = list(range(failing_step_index, hi)) + list(range(failing_step_index - 1, lo - 1, -1))

    for idx in order:
        step = steps[idx]
        calls = list(getattr(step, "tool_calls", []) or [])
        obs = {getattr(o, "source_call_id", None): o for o in getattr(step, "observations", []) or []}
        for tc in calls:
            if getattr(tc, "tool_name", "").lower() not in _COMMAND_TOOLS:
                continue
            command = _command_from_tool_call(tc)
            if not command:
                continue
            observation = obs.get(getattr(tc, "tool_call_id", None))
            err = getattr(observation, "error", None) if observation else None
            content = getattr(observation, "content", None) if observation else None
            error_line = (err if (err and err != "no_result") else None) or _extract_error_line(content)
            if error_line:
                return {
                    "command": command,
                    "expected": {"kind": "error_string", "value": error_line[:200]},
                    "cwd": "",
                    "source": "captured",
                    "captured_at_step": idx,
                }
    return None


def extract_passing_test_payload(record: Any, *, window: int | None = None) -> dict[str, Any] | None:
    """Best-effort: capture a TEST the agent RAN that PASSED (#156, exit 0).

    The success-path sibling of :func:`extract_test_payload`. A success/design
    session has no errored command, so :func:`extract_test_payload` returns
    ``None`` — but many such sessions DID run a test that passed (``pytest``
    exited 0, work landed), a real re-checkable oracle we currently throw away.
    Scan the whole trace (there is no failing step to window around) for a
    command-tool call that (a) LOOKS like a test — a recognized framework, not a
    bare shell command such as ``ls``/``git status`` (a random command that
    exited 0 is not "the test") — and (b) whose observation shows SUCCESS: a
    present observation with no ``error`` and no error line in its output. Return
    the LAST such (the most recent green bar wins).

    ``window`` is accepted for call-shape symmetry with
    :func:`extract_test_payload`; it is unused here (a passing session has no
    failing anchor to window around).

    The payload's ``expected`` carries ``exit_code: 0`` under a ``nonzero_exit``
    kind — the generic exit-code oracle (``oracle._generic``) grades exit-0 as
    the pass, and ``nonzero_exit`` is the frozen ``validate_capsule`` kind that
    means "grade by exit code". Trust sits at ``captured_pass``: BELOW declared
    (a captured test's selection as THE oracle is inferred, not committed) and
    ABOVE pure intent re-pose.
    """

    from .oracle import detect_framework

    steps = list(getattr(record, "steps", []) or [])
    if not steps:
        return None
    best: dict[str, Any] | None = None
    for idx, step in enumerate(steps):
        calls = list(getattr(step, "tool_calls", []) or [])
        obs = {getattr(o, "source_call_id", None): o for o in getattr(step, "observations", []) or []}
        for tc in calls:
            if getattr(tc, "tool_name", "").lower() not in _COMMAND_TOOLS:
                continue
            command = _command_from_tool_call(tc)
            if not command:
                continue
            # Only a recognized TEST command is a trustworthy passing oracle; a
            # bare `ls` / `git status` that happened to exit 0 is not "the test".
            if detect_framework(command) == "generic":
                continue
            observation = obs.get(getattr(tc, "tool_call_id", None))
            if observation is None:
                continue  # no evidence it ran cleanly — do not claim a pass
            err = getattr(observation, "error", None)
            if err and err != "no_result":
                continue  # errored — that is a captured_error, not a pass
            content = getattr(observation, "content", None)
            if content and _extract_error_line(content):
                continue  # an error line in the output — not a clean pass
            best = {
                "command": command,
                "expected": {"kind": "nonzero_exit", "exit_code": 0},
                "cwd": "",
                "source": "captured_pass",
                "captured_at_step": idx,
            }
    return best


def oracle_trust_of(test_payload: dict[str, Any] | None) -> str:
    """Map a captured/declared test payload to its ``oracle_trust`` ordinal.

    Ladder (ADR-0008 §5, #156): ``declared`` ▸ ``captured_error`` ▸
    ``captured_pass`` ▸ ``intent_reposed`` ▸ ``none``. A sealed capsule always
    carries re-posable intent (``export_capsule`` refuses a hollow capsule), so a
    MISSING test floors to ``intent_reposed`` here — never ``none``. ``none`` is
    only the clamp's absent-field default for a pre-#156 capsule that never
    stamped the ordinal at all.

    This is the SEAL-side mapper: it maps the ``source`` label verbatim. On the
    consumer/replay PROPERTIES surface, ``replay._derive_oracle_trust`` re-derives
    the ordinal and additionally CORROBORATES it (a ``declared`` / ``captured_*``
    level is credited only when the payload carries a real runnable assertion), so
    a fabricated ``{command:"true", source:"declared"}`` cannot self-certify a
    graded oracle at replay time.
    """

    if not test_payload:
        return "intent_reposed"
    source = test_payload.get("source")
    return {
        "declared": "declared",
        "captured": "captured_error",
        "captured_pass": "captured_pass",
    }.get(source, "intent_reposed")


__all__ = [
    "declared_test",
    "extract_test_payload",
    "extract_passing_test_payload",
    "oracle_trust_of",
]
