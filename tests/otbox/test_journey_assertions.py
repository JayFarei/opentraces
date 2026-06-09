"""Unit contract for the otbox assertion registry (otbox 2.0 phase 1).

Pins three properties:

1. Every kind in ``ASSERTION_KINDS`` is dispatchable and behaves per its
   documented contract (existing kinds byte-compatible with the pre-registry
   if-chain).
2. ``equals_var`` resolves BOTH documented forms — cross-step
   (``"step-id:json.path"``) and ctx-variable — and an unresolvable variable
   FAILS the assertion rather than passing (the 1.0 defect was assertions
   that could never evaluate hiding inside never-run journeys).
3. Unknown kinds FAIL at eval time (phase 2 promotes this to collection-time
   lint, reading the same registry).
"""

from __future__ import annotations

import json

from .drivers.base import ExecResult
from .journey import ASSERTION_KINDS, StepResult, _eval_assertion


def _step(step_id: str, stdout: str = "", stderr: str = "", rc: int = 0) -> StepResult:
    result = ExecResult(
        argv=["fake"], returncode=rc, stdout=stdout, stderr=stderr, duration_s=0.01
    )
    return StepResult(
        index=0, step_id=step_id, type="cli", detail={}, result=result, ok=rc == 0
    )


def _json_step(step_id: str, payload: dict) -> StepResult:
    return _step(step_id, stdout=json.dumps(payload))


def _run(spec: dict, steps: list[StepResult], ctx: dict | None = None):
    return _eval_assertion(0, spec, steps, ctx or {})


# -- 1. registry shape -------------------------------------------------------

def test_registry_covers_all_documented_kinds():
    expected = {
        "returncode",
        "stdout_contains", "stderr_contains",
        "stdout_not_contains", "stderr_not_contains",
        "stdout_json", "stdout_json_equals_var",
        "stdout_json_array_equals", "stdout_json_set_contains",
        "stdout_json_length_equals", "stdout_json_path_absent",
        "stdout_json_greater_equal", "stdout_json_contains", "duration_ms_max",
        "path_exists", "path_not_exists", "file_count_min",
    }
    assert expected == set(ASSERTION_KINDS)


def test_stdout_json_contains_and_duration_budget():
    steps = [_json_step("s1", {"msg": "hello otbox world"})]
    assert _run(
        {"kind": "stdout_json_contains", "step": "s1", "path": "msg", "value": "otbox"},
        steps).ok
    assert not _run(
        {"kind": "stdout_json_contains", "step": "s1", "path": "msg", "value": "absent"},
        steps).ok
    # duration_s=0.01 from the fixture -> 10ms
    assert _run({"kind": "duration_ms_max", "step": "s1", "max": 100}, steps).ok
    assert not _run({"kind": "duration_ms_max", "step": "s1", "max": 5}, steps).ok


def test_unknown_kind_fails_never_passes():
    res = _run({"kind": "no_such_kind"}, [_step("s1")])
    assert not res.ok
    assert "unknown assertion kind" in res.message


# -- 2. existing kinds: behavior preserved -----------------------------------

def test_returncode_pass_and_fail():
    steps = [_step("s1", rc=3)]
    assert _run({"kind": "returncode", "step": "s1", "equals": 3}, steps).ok
    assert not _run({"kind": "returncode", "step": "s1", "equals": 0}, steps).ok


def test_stream_contains_both_streams():
    steps = [_step("s1", stdout="hello out", stderr="oops err")]
    assert _run({"kind": "stdout_contains", "step": "s1", "value": "hello"}, steps).ok
    assert _run({"kind": "stderr_contains", "step": "s1", "value": "oops"}, steps).ok
    assert not _run({"kind": "stdout_contains", "step": "s1", "value": "oops"}, steps).ok


def test_stdout_json_equals_and_bare_existence():
    steps = [_json_step("s1", {"a": {"b": 7}, "z": None})]
    assert _run({"kind": "stdout_json", "step": "s1", "path": "a.b", "equals": 7}, steps).ok
    assert not _run({"kind": "stdout_json", "step": "s1", "path": "a.b", "equals": 8}, steps).ok
    assert _run({"kind": "stdout_json", "step": "s1", "path": "a.b"}, steps).ok
    # bare existence: null value is NOT ok (matches pre-registry behavior)
    assert not _run({"kind": "stdout_json", "step": "s1", "path": "z"}, steps).ok


def test_array_set_and_length_kinds():
    steps = [_json_step("s1", {"xs": ["a", "b", "c"]})]
    assert _run(
        {"kind": "stdout_json_array_equals", "step": "s1", "path": "xs",
         "equals": ["a", "b", "c"]}, steps).ok
    assert not _run(
        {"kind": "stdout_json_array_equals", "step": "s1", "path": "xs",
         "equals": ["c", "b", "a"]}, steps).ok
    assert _run(
        {"kind": "stdout_json_set_contains", "step": "s1", "path": "xs",
         "equals": ["c", "a"]}, steps).ok
    assert not _run(
        {"kind": "stdout_json_set_contains", "step": "s1", "path": "xs",
         "equals": ["d"]}, steps).ok
    assert _run(
        {"kind": "stdout_json_length_equals", "step": "s1", "path": "xs",
         "equals": 3}, steps).ok


def test_path_exists_and_file_count_min(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    assert _run({"kind": "path_exists", "path": str(f)}, [_step("s1")]).ok
    assert not _run({"kind": "path_exists", "path": str(tmp_path / "no")}, [_step("s1")]).ok
    assert _run(
        {"kind": "file_count_min", "path": str(tmp_path), "min": 1}, [_step("s1")]).ok
    assert not _run(
        {"kind": "file_count_min", "path": str(tmp_path), "min": 2}, [_step("s1")]).ok


# -- 3. equals_var: cross-step form ------------------------------------------

def test_equals_var_cross_step_matches():
    steps = [
        _json_step("push", {"remote": {"digest": "sha256:abc"}}),
        _json_step("pull", {"remote": {"digest": "sha256:abc"}}),
    ]
    res = _run(
        {"kind": "stdout_json_equals_var", "step": "pull", "path": "remote.digest",
         "equals_var": "push:remote.digest"}, steps)
    assert res.ok, res.message


def test_equals_var_cross_step_detects_divergence():
    steps = [
        _json_step("push", {"remote": {"digest": "sha256:abc"}}),
        _json_step("pull", {"remote": {"digest": "sha256:DIFFERENT"}}),
    ]
    res = _run(
        {"kind": "stdout_json_equals_var", "step": "pull", "path": "remote.digest",
         "equals_var": "push:remote.digest"}, steps)
    assert not res.ok


# -- 4. equals_var: ctx-variable form ----------------------------------------

def test_equals_var_ctx_on_stdout_json():
    steps = [_json_step("prune", {"record_count": 5})]
    res = _run(
        {"kind": "stdout_json", "step": "prune", "path": "record_count",
         "equals_var": "active_path_length"},
        steps, ctx={"active_path_length": "5"})
    assert res.ok, res.message


def test_equals_var_ctx_on_length_and_set_kinds():
    steps = [_json_step("s1", {"uuid_set": ["u1", "u2"], "active_path": [1, 2, 3]})]
    assert _run(
        {"kind": "stdout_json_length_equals", "step": "s1", "path": "active_path",
         "equals_var": "n"}, steps, ctx={"n": "3"}).ok
    assert _run(
        {"kind": "stdout_json_set_contains", "step": "s1", "path": "uuid_set",
         "equals_var": "uuids"}, steps, ctx={"uuids": '["u1"]'}).ok


def test_unresolved_equals_var_fails_never_passes():
    """The 1.0 lesson, pinned: an assertion whose expectation cannot resolve
    must FAIL loudly — silent pass or silent skip both hide vacuity."""
    steps = [_json_step("s1", {"v": 1})]
    res = _run(
        {"kind": "stdout_json", "step": "s1", "path": "v",
         "equals_var": "missing_variable"}, steps, ctx={})
    assert not res.ok
    assert "unresolved equals_var" in res.message


# -- 5. negative-space kinds --------------------------------------------------

def test_not_contains_kinds():
    steps = [_step("s1", stdout="clean output", stderr="warn")]
    assert _run(
        {"kind": "stdout_not_contains", "step": "s1", "value": "sk-test-"}, steps).ok
    assert not _run(
        {"kind": "stdout_not_contains", "step": "s1", "value": "clean"}, steps).ok
    assert _run(
        {"kind": "stderr_not_contains", "step": "s1", "value": "fatal"}, steps).ok


def test_path_not_exists(tmp_path):
    assert _run({"kind": "path_not_exists", "path": str(tmp_path / "no")}, [_step("s1")]).ok
    f = tmp_path / "yes.txt"
    f.write_text("x")
    assert not _run({"kind": "path_not_exists", "path": str(f)}, [_step("s1")]).ok


def test_stdout_json_path_absent():
    steps = [_json_step("s1", {"present": 1})]
    assert _run({"kind": "stdout_json_path_absent", "step": "s1", "path": "gone"}, steps).ok
    assert not _run(
        {"kind": "stdout_json_path_absent", "step": "s1", "path": "present"}, steps).ok


def test_stdout_json_greater_equal():
    steps = [_json_step("s1", {"count": 4})]
    assert _run(
        {"kind": "stdout_json_greater_equal", "step": "s1", "path": "count", "min": 4},
        steps).ok
    assert not _run(
        {"kind": "stdout_json_greater_equal", "step": "s1", "path": "count", "min": 5},
        steps).ok
