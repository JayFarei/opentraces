"""Framework-aware oracle adapters (codex finding: substring/exit is too weak)."""

from __future__ import annotations

import pytest

from opentraces.core.capsule.oracle import classify_result, detect_framework


@pytest.mark.parametrize(
    "command,expected_fw",
    [
        ("python -m pytest -q test_x.py", "pytest"),
        ("pytest tests/", "pytest"),
        ("python -m unittest test_x", "unittest"),
        ("go test ./...", "go"),
        ("npm test", "node"),
        ("python -c 'assert add(2,3)==5'", "generic"),
    ],
)
def test_detect_framework(command, expected_fw):
    assert detect_framework(command) == expected_fw


def _v(command, output, code, expected=None):
    return classify_result(command=command, output=output, exit_code=code, expected=expected)["verdict"]


def test_pytest_exit_codes_map_correctly():
    # 0 pass -> fixed, 1 fail -> reproduces, 5 no-tests -> inconclusive (NOT fixed!)
    assert _v("pytest test_x.py", "1 passed", 0) == "fixed"
    assert _v("pytest test_x.py", "1 failed", 1) == "reproduces"
    assert _v("pytest test_x.py", "no tests ran", 5) == "inconclusive"
    assert _v("pytest test_x.py", "usage error", 4) == "inconclusive"


def test_pytest_no_tests_is_not_a_false_fixed():
    # The key win over exit-code-only: a selector that matches nothing is NOT 'fixed'.
    assert _v("pytest does_not_exist.py::nope", "no tests ran in 0.01s", 5) == "inconclusive"


def test_unittest_zero_tests_is_inconclusive():
    assert _v("python -m unittest test_x", "Ran 0 tests in 0.0s\n\nOK", 0) == "inconclusive"
    assert _v("python -m unittest test_x", "Ran 1 test\n\nOK", 0) == "fixed"
    assert _v("python -m unittest test_x", "FAILED (failures=1)", 1) == "reproduces"


def test_go_no_test_files_is_inconclusive():
    assert _v("go test ./...", "?   pkg   [no test files]", 0) == "inconclusive"
    assert _v("go test ./...", "ok   pkg   0.1s", 0) == "fixed"
    assert _v("go test ./...", "--- FAIL: TestX", 1) == "reproduces"


def test_command_not_found_is_never_a_false_reproduce():
    # exit 127 = env problem, must be inconclusive, not 'reproduces'.
    assert _v("pytest test_x.py", "pytest: command not found", 127) == "inconclusive"
    assert _v("make test", "make: command not found", 127) == "inconclusive"


def test_generic_error_string_oracle():
    r = classify_result(command="python run.py", output="AssertionError: boom", exit_code=1,
                        expected={"kind": "error_string", "value": "AssertionError"})
    assert r["verdict"] == "reproduces" and r["signal_present"] is True
    r = classify_result(command="python run.py", output="all good", exit_code=0,
                        expected={"kind": "error_string", "value": "AssertionError"})
    assert r["verdict"] == "fixed" and r["signal_present"] is False
    # error string gone but command still fails -> inconclusive (not a false fixed)
    r = classify_result(command="python run.py", output="DifferentError", exit_code=1,
                        expected={"kind": "error_string", "value": "AssertionError"})
    assert r["verdict"] == "inconclusive"
