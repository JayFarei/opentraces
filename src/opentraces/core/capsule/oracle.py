"""Framework-aware verdict adapters (replaces raw substring/exit-code).

Codex's review: a substring match + "any non-zero = reproduces" gives wrong
verdicts (a no-op success reads as fixed; an env failure reads as reproduces).
These adapters use each framework's KNOWN exit-code + output semantics so the
verdict is a real assertion. Unknown commands fall back to the generic
error-string/exit oracle (with the command-not-found guard).

All adapters return ``(verdict, reason)`` with verdict in
``{fixed, reproduces, inconclusive}``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

VERDICTS = ("fixed", "reproduces", "inconclusive")


def detect_framework(command: str) -> str:
    c = command.lower()
    if re.search(r"(^|\s)(pytest|py\.test)(\s|$)|-m\s+pytest", c):
        return "pytest"
    if re.search(r"-m\s+unittest(\s|$)", c):
        return "unittest"
    if re.search(r"(^|\s)go\s+test(\s|$)", c):
        return "go"
    if re.search(r"(^|\s)(jest|vitest|mocha)(\s|$)|npm\s+(run\s+)?test|yarn\s+test", c):
        return "node"
    return "generic"


def _pytest(output: str, code: int, expected: dict[str, Any]) -> tuple[str, str]:
    # pytest exit codes: 0 all passed · 1 tests failed · 2 interrupted ·
    # 3 internal error · 4 usage error · 5 no tests collected.
    if code == 0:
        return "fixed", "pytest: all selected tests passed (exit 0)"
    if code == 1:
        return "reproduces", "pytest: tests failed (exit 1)"
    if code == 5:
        return "inconclusive", "pytest: no tests collected (exit 5) — selector wrong or test removed"
    return "inconclusive", f"pytest: error/usage, not a test result (exit {code})"


def _unittest(output: str, code: int, expected: dict[str, Any]) -> tuple[str, str]:
    if re.search(r"Ran 0 tests", output):
        return "inconclusive", "unittest: ran 0 tests"
    if code == 0:
        return "fixed", "unittest: OK (exit 0)"
    if re.search(r"\b(FAILED|ERROR)\b", output):
        return "reproduces", "unittest: FAILED/ERROR"
    return "inconclusive", f"unittest: non-zero exit without a test summary (exit {code})"


def _go(output: str, code: int, expected: dict[str, Any]) -> tuple[str, str]:
    if re.search(r"no test files", output):
        return "inconclusive", "go test: no test files"
    if code == 0:
        return "fixed", "go test: PASS (exit 0)"
    if re.search(r"(^|\n)(--- FAIL|FAIL\b)", output):
        return "reproduces", "go test: FAIL"
    return "inconclusive", f"go test: non-zero without a FAIL marker (exit {code})"


def _node(output: str, code: int, expected: dict[str, Any]) -> tuple[str, str]:
    if re.search(r"No tests found|0 (passing|tests)", output, re.IGNORECASE):
        return "inconclusive", "node test: no tests found"
    if code == 0:
        return "fixed", "node test runner: exit 0"
    return "reproduces", f"node test runner: exit {code}"


def _generic(output: str, code: int, expected: dict[str, Any]) -> tuple[str, str]:
    kind = (expected or {}).get("kind")
    value = (expected or {}).get("value")
    if kind == "error_string" and value:
        if value in output:
            return "reproduces", "original error string present"
        if code == 0:
            return "fixed", "original error string gone and command exited 0"
        return "inconclusive", "original error string gone but command still exits non-zero"
    return ("fixed", "command exited 0") if code == 0 else ("reproduces", f"command exited {code}")


_ADAPTERS: dict[str, Callable[[str, int, dict[str, Any]], tuple[str, str]]] = {
    "pytest": _pytest,
    "unittest": _unittest,
    "go": _go,
    "node": _node,
    "generic": _generic,
}


def classify_result(
    *, command: str, output: str, exit_code: int, expected: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return ``{verdict, framework, reason, signal_present}`` for a run."""

    expected = expected or {}
    framework = detect_framework(command)
    value = expected.get("value")
    signal_present = (value in output) if value else None

    # An env failure (not-found / not-executable) is never a reproduction, for
    # any framework, UNLESS the original error signal is genuinely present.
    if exit_code in (126, 127) and not signal_present:
        return {
            "verdict": "inconclusive",
            "framework": framework,
            "reason": f"command could not run (exit {exit_code}: not found / not executable); environment differs",
            "signal_present": signal_present,
        }

    verdict, reason = _ADAPTERS[framework](output, exit_code, expected)
    return {
        "verdict": verdict,
        "framework": framework,
        "reason": reason,
        "signal_present": signal_present,
    }


__all__ = ["VERDICTS", "classify_result", "detect_framework"]
