"""Trace Slicer Library — independent verifier + agent-loop + mutation tests
(issue #141).

Trust the world, not the narration. Every tiling verdict here is recomputed by
``_independent_recompute`` — a SEPARATE implementation that does NOT import
``opentraces.core.slicing.tiling``, so it cannot be fooled by the slicer's own
``tiling.valid`` self-report (the mutant (c) proof rests on this).

Covers:
  - tiling recomputed valid for every slicer over a deterministic synthetic
    corpus + edge cases (empty / single / autonomous / control-leak); a crash
    on any trace is a FAIL.
  - S1/S2 byte-identical re-run (idempotence) + pinned-boundary reproduction.
  - the agent-loop fixture: rc=10 with well-formed JudgmentRequests on call 1,
    rc=0 + recomputed-valid tiling on call 2.
  - the three red-capability mutants (off-by-one / dropped continuation /
    self-report-valid-with-gap) each caught by the independent recompute.
  - the frozen envelope schema_version + no TraceRecord.SCHEMA_VERSION bump.
"""

from __future__ import annotations

import json

import pytest
from opentraces_schema import (
    SCHEMA_VERSION,
    Agent,
    Observation,
    Step,
    ToolCall,
    TraceRecord,
)

from opentraces.capture.claude_code.parse import ClaudeCodeParser
from opentraces.core import slicing
from opentraces.core.slicing.models import Trajectory
from opentraces.core.trace_map import build_trace_map
from opentraces.core.trace_slices import (
    TraceMaterializationRef,
    materialize_trajectory,
    slice_by_steps,
)
from opentraces.core.trails.lineage import parse_trail_ref

from tests.helpers.release_capture import (
    REAL_CAPTURE_FIXTURE as _REAL_CAPTURE_FIXTURE,
    REAL_CAPTURE_PROVENANCE as _REAL_CAPTURE_PROVENANCE,
    context_stamped_capture_record,
    sha256_path as _sha256,
    verify_release_asset_provenance,
)


# --------------------------------------------------------------------------
# INDEPENDENT recompute — deliberately NOT importing slicing.tiling.
# --------------------------------------------------------------------------
def _independent_recompute(trajectories: list[dict], total: int) -> dict:
    """A standalone tiling checker. Never reads any self-reported value."""
    if total <= 0:
        return {"valid": len(trajectories) == 0, "coverage": 1.0, "gaps": 0, "overlaps": 0}
    cover = [0] * total
    bad = 0
    for t in trajectories:
        s, e = int(t["start"]), int(t["end"])
        if e < s:
            bad += 1
            continue
        for i in range(s, e + 1):
            if 0 <= i < total:
                cover[i] += 1
            else:
                bad += 1
    covered = sum(1 for c in cover if c >= 1)
    overlaps = sum(1 for c in cover if c >= 2)
    gaps = sum(1 for c in cover if c == 0)
    spans = sorted((int(t["start"]), int(t["end"])) for t in trajectories)
    contiguous = (
        bool(spans)
        and spans[0][0] == 0
        and spans[-1][1] == total - 1
        and all(spans[i][0] == spans[i - 1][1] + 1 for i in range(1, len(spans)))
    )
    return {
        "valid": covered == total and overlaps == 0 and gaps == 0 and bad == 0 and contiguous,
        "coverage": covered / total,
        "gaps": gaps,
        "overlaps": overlaps,
    }


# --------------------------------------------------------------------------
# deterministic synthetic corpus (hermetic — no ~/.opentraces dependency)
# --------------------------------------------------------------------------
def _mk(spec: list[tuple]) -> TraceRecord:
    """spec rows: (role, content, [tool_names], obs_text, obs_error)."""
    steps = []
    for i, (role, content, tools, otext, oerr) in enumerate(spec):
        tcs = [
            ToolCall(tool_call_id=f"c{i}_{j}", tool_name=t, input={"file_path": "a.py"})
            for j, t in enumerate(tools)
        ]
        obs = []
        if otext or oerr:
            obs = [
                Observation(
                    source_call_id=f"c{i}_0", content=otext or None, error="err" if oerr else None
                )
            ]
        steps.append(
            Step(step_index=i, role=role, content=content, tool_calls=tcs, observations=obs)
        )
    return TraceRecord(
        trace_id="synthetic", session_id="s", agent=Agent(name="claude-code"), steps=steps
    )


def _u(text):  # user ask
    return ("user", text, [], "", False)


def _edit(ok=True):  # agent edit
    return ("agent", "editing", ["Edit"], "updated" if ok else "", not ok)


def _pass():  # agent green test
    return ("agent", "ran tests", ["Bash"], "5 passed, 0 failed", False)


def _fail():  # agent failed test (must NOT close an S3 milestone)
    return ("agent", "ran tests", ["Bash"], "3 passed, 1 failed", True)


CORPUS: dict[str, TraceRecord] = {
    "empty": _mk([]),
    "single": _mk([_u("do the thing")]),
    "single_agent": _mk([_edit()]),
    "autonomous": _mk([_edit(), _edit(), _pass(), ("agent", "done", [], "", False)]),
    "control_leak": _mk(
        [
            _u("add a helper"),
            _edit(),
            ("user", "<task-notification> bg task done </task-notification>", [], "", False),
            ("user", "continue", [], "", False),
            _edit(),
            _u("now another helper"),
            _edit(),
            ("agent", "all done", [], "", False),
        ]
    ),
    "synthetic_marker_leak": _mk(
        [
            _u("start"),
            ("user", "<<autonomous-loop-dynamic>>", [], "", False),
            _edit(),
        ]
    ),
    "error_heavy": _mk([_u("fix it"), _fail(), _edit(False), _fail(), _edit(), _pass()]),
    "multi_turn": _mk(
        [
            _u("ask one"),
            _edit(),
            _pass(),
            _u("ask two"),
            _edit(),
            _u("ask three"),
            ("agent", "answer", [], "", False),
        ]
    ),
    "long_explore": _mk([_u("research")] + [("agent", "reading", ["Read"], "x", False)] * 40),
}

SLICERS = ("s1", "s2", "s3", "s4")


# --------------------------------------------------------------------------
# acceptance #3/#4 — tiling recomputed valid; idempotence; no crashes
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(CORPUS))
@pytest.mark.parametrize("slicer", SLICERS)
def test_tiling_recomputed_valid_every_trace(name, slicer):
    rec = CORPUS[name]
    rc, env = slicing.partition_trace(
        trace_id=rec.trace_id, slicer_name=slicer, steps=rec.steps, judge="deterministic"
    )
    assert rc == 0
    assert env["schema_version"] == "opentraces.slicing.v1"
    indep = _independent_recompute(env["trajectories"], env["total_steps"])
    assert indep["valid"], f"{name}/{slicer}: independent verifier says {indep}"


@pytest.mark.parametrize("name", list(CORPUS))
@pytest.mark.parametrize("slicer", ("s1", "s2"))
def test_s1_s2_byte_identical_rerun(name, slicer):
    rec = CORPUS[name]
    a = slicing.partition_trace(
        trace_id=rec.trace_id, slicer_name=slicer, steps=rec.steps, judge="deterministic"
    )[1]
    b = slicing.partition_trace(
        trace_id=rec.trace_id, slicer_name=slicer, steps=rec.steps, judge="deterministic"
    )[1]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_s3_never_closes_on_a_failure():
    """Phase-0 remediation: a milestone must not end on a test failure."""
    rec = CORPUS["error_heavy"]
    rc, env = slicing.partition_trace(
        trace_id=rec.trace_id, slicer_name="s3", steps=rec.steps, judge="deterministic"
    )
    # the only unambiguous pass is the last step (index 5); failures at 1,3 must
    # not have opened a boundary at 2 / 4 via a close.
    closes = {t["start"] for t in env["trajectories"]} - {0}
    assert 2 not in closes and 4 not in closes, env["trajectories"]


# --------------------------------------------------------------------------
# acceptance #4 — S1/S2 pinned boundaries on the rich fixture
# --------------------------------------------------------------------------
def _fixture_record() -> TraceRecord:
    # Position-oriented conformance input. Real parser-assigned step indices are
    # covered separately by the committed captured TraceRecord regression below.
    return _mk(
        [
            _u("Add a farewell(name) helper to src/app.py."),
            _edit(),
            (
                "user",
                "<task-notification> background task wu7 finished </task-notification>",
                [],
                "",
                False,
            ),
            ("user", "Noted the background task.", [], "", False),
            ("user", "continue", [], "", False),
            _edit(),
            ("user", "Now also add a hello(name) helper.", [], "", False),
            _edit(),
            ("user", "Added farewell, goodbye, and hello helpers.", [], "", False),
        ]
    )


def test_s1_pinned_boundaries():
    rec = _fixture_record()
    env = slicing.partition_trace(
        trace_id="f", slicer_name="s1", steps=rec.steps, judge="deterministic"
    )[1]
    spans = [(t["start"], t["end"]) for t in env["trajectories"]]
    assert spans == [(0, 2), (3, 5), (6, 7), (8, 8)], spans  # control msgs at 2,4 absorbed


def test_s2_pinned_boundaries():
    rec = _fixture_record()
    env = slicing.partition_trace(
        trace_id="f", slicer_name="s2", steps=rec.steps, judge="deterministic"
    )[1]
    spans = [(t["start"], t["end"]) for t in env["trajectories"]]
    assert spans == [(0, 0), (1, 2), (3, 5), (6, 7), (8, 8)], spans


def _real_capture_record() -> TraceRecord:
    record = ClaudeCodeParser().parse_session(_REAL_CAPTURE_FIXTURE)
    assert record is not None
    return record


def test_coordinate_fixture_requires_a3_verified_release_chain():
    """#270 consumes A3's independently verified real-agent release asset.

    The release-asset provenance chain is verified by the one shared helper
    (#298) both A3 (portable capture) and A4 (slicing) now delegate to.
    """

    verify_release_asset_provenance()


def test_release_session_independently_proves_position_address_mismatch():
    """The real parser emits 1-based steps while slicing-v1 emits positions."""

    record = ClaudeCodeParser().parse_session(_REAL_CAPTURE_FIXTURE)
    assert record is not None
    canonical_steps = [step.step_index for step in record.steps]
    assert canonical_steps == [1, 2, 3, 4, 5, 6]

    rc, envelope = slicing.partition_trace(
        trace_id=record.trace_id,
        slicer_name="s1",
        steps=record.steps,
        judge="deterministic",
    )
    assert rc == 0
    positions = [
        position
        for trajectory in envelope["trajectories"]
        for position in range(trajectory["start"], trajectory["end"] + 1)
    ]
    assert positions == [0, 1, 2, 3, 4, 5]
    assert positions != canonical_steps

    materialized_steps = [
        step["step_index"]
        for trajectory in envelope["trajectories"]
        for step in materialize_trajectory(TraceMaterializationRef.from_record(record), trajectory)[
            "steps"
        ]
    ]
    assert materialized_steps == canonical_steps


def test_coordinate_fixture_has_verifiable_real_capture_provenance():
    """The release-derived session documents its narrow sanitization."""

    provenance = json.loads(_REAL_CAPTURE_PROVENANCE.read_text())

    assert provenance.get("source_agent") == "claude"
    assert provenance.get("source_session_path") == (
        "home/.claude/projects/<captured-project>/164b9ad1-4c1b-4602-9102-5276951a1594.jsonl"
    )
    assert provenance.get("derivation") == [
        "retained authentic user, assistant, and opentraces_hook rows",
        "removed only opaque assistant thinking signature fields",
        "rewrote the captured otbox project root to /workspace/claude-real-edit-box/project",
    ]

    rows = [json.loads(line) for line in _REAL_CAPTURE_FIXTURE.read_text().splitlines()]
    assert {row.get("type") for row in rows} >= {"user", "assistant", "opentraces_hook"}
    assert {row.get("version") for row in rows if row.get("version")} == {"2.1.175"}

    fixture_bytes = _REAL_CAPTURE_FIXTURE.read_bytes()
    assert b"/Users/" not in fixture_bytes
    assert b"/home/" not in fixture_bytes


def test_real_capture_trajectory_materializes_in_step_address_coordinates():
    """Regression for #270: slicer positions are not ``Step.step_index``.

    This committed fixture is a sanitized captured session whose parser-assigned
    step indices are 1-based. The slicing-v1 trajectory remains the frozen
    0-based position span; materialization is the one boundary that translates
    it to the canonical trace/ctx/Trail address coordinate.
    """

    record = _real_capture_record()
    assert [step.step_index for step in record.steps] == [1, 2, 3, 4, 5, 6]

    trace_slice = materialize_trajectory(
        TraceMaterializationRef.from_record(record),
        Trajectory(start=0, end=2, kind="user_turn", label="captured run"),
    )

    assert trace_slice["start_step_index"] == 1
    assert trace_slice["end_step_index"] == 3
    assert [step["step_index"] for step in trace_slice["steps"]] == [1, 2, 3]


def test_captured_position_span_translates_before_step_slice():
    """The RED position span now resolves through the translation boundary."""

    record = _real_capture_record()
    trajectory = Trajectory(start=0, end=2, kind="user_turn", label="captured run")
    translated = materialize_trajectory(TraceMaterializationRef.from_record(record), trajectory)
    naive = slice_by_steps(
        build_trace_map(record),
        record,
        start_step_index=trajectory.start,
        end_step_index=trajectory.end,
    )

    assert [step["step_index"] for step in translated["steps"]] == [1, 2, 3]
    # This is the killed pre-fix route: positional 0..2 is misread as the
    # canonical address 0..2 and therefore omits captured step 3.
    assert [step["step_index"] for step in naive["steps"]] == [1, 2]
    assert [step["step_index"] for step in naive["steps"]] != [1, 2, 3]


@pytest.mark.parametrize(
    ("start_position", "end_position"),
    [(0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2)],
)
def test_trajectory_materialization_matches_shared_address_span(start_position, end_position):
    record = _real_capture_record()
    canonical = [step.step_index for step in record.steps]
    start_step = canonical[start_position]
    end_step = canonical[end_position]
    # The sanitized excerpt uses a fixture trace id; route it through a
    # grammar-safe id while retaining its captured step payload and coordinates.
    address_trace_id = "trace-fixture"

    trace_id, point, span, reserved = parse_trail_ref(f"{address_trace_id}:{start_step}-{end_step}")
    assert (trace_id, point, span, reserved) == (
        address_trace_id,
        None,
        (start_step, end_step),
        "span",
    )

    materialized = materialize_trajectory(
        TraceMaterializationRef.from_record(record),
        Trajectory(
            start=start_position,
            end=end_position,
            kind="user_turn",
            label="property span",
        ),
    )
    addressed = slice_by_steps(
        build_trace_map(record),
        record,
        start_step_index=start_step,
        end_step_index=end_step,
    )
    expected = canonical[start_position : end_position + 1]

    assert [step["step_index"] for step in materialized["steps"]] == expected
    assert [step["step_index"] for step in addressed["steps"]] == expected
    assert materialized["map_node_refs"] == addressed["map_node_refs"]

    # Point addresses are the shared Trace/ctx/Trail join key. Every step
    # materialized from the slicing span must round-trip through that grammar.
    for step_index in expected:
        assert parse_trail_ref(f"{address_trace_id}:{step_index}") == (
            address_trace_id,
            step_index,
            None,
            None,
        )


def test_frozen_slicing_envelope_tiles_same_real_capture_steps_when_materialized():
    record = _real_capture_record()
    rc, envelope = slicing.partition_trace(
        trace_id=record.trace_id,
        slicer_name="s1",
        steps=record.steps,
        judge="deterministic",
    )

    assert rc == 0
    assert envelope["schema_version"] == "opentraces.slicing.v1"
    assert envelope["trajectories"][0]["start"] == 0

    materialized_steps = [
        step["step_index"]
        for trajectory in envelope["trajectories"]
        for step in materialize_trajectory(TraceMaterializationRef.from_record(record), trajectory)[
            "steps"
        ]
    ]
    assert materialized_steps == [step.step_index for step in record.steps]


def test_non_null_context_join_survives_slicing_to_step_address_materialization():
    """#298: a non-null Context join survives slicing-position -> step-address
    materialization, alongside the existing Trail patch/anchor and map-node joins.

    The record is *derived* from the same ``otbox-captures-v1`` real session by
    the genuine Context Tree capture path (not a hand-authored synthetic), so
    every ``context_node_id`` is an authentic content-addressed node id. The
    committed real coordinate regression above stays on the null-context session;
    this test adds the missing non-null-Context mutation sensitivity.
    """

    record = context_stamped_capture_record()
    source_join = {step.step_index: step.context_node_id for step in record.steps}

    # Precondition the existing null-context fixture cannot give us: at least one
    # authentic non-null Context key exists to join through materialization.
    non_null_steps = {k for k, v in source_join.items() if v is not None}
    assert non_null_steps, source_join

    ref = TraceMaterializationRef.from_record(record)

    # Tile the whole trace, materialize every trajectory to step-address
    # coordinates, and prove the Context join key rides along unchanged.
    rc, envelope = slicing.partition_trace(
        trace_id=record.trace_id,
        slicer_name="s1",
        steps=record.steps,
        judge="deterministic",
    )
    assert rc == 0
    materialized_join: dict[int, str | None] = {}
    for trajectory in envelope["trajectories"]:
        for step in materialize_trajectory(ref, trajectory)["steps"]:
            materialized_join[step["step_index"]] = step.get("context_node_id")

    # If materialization dropped the Context join, the non-null keys would come
    # back None and this equality would fail (RED control verified locally).
    assert materialized_join == source_join
    assert {k: materialized_join[k] for k in non_null_steps} == {
        k: source_join[k] for k in non_null_steps
    }

    # The same non-null Context join survives a partial slicing span materialized
    # at the exact step-address coordinates the #270 tests already check.
    partial = materialize_trajectory(
        ref, Trajectory(start=0, end=2, kind="user_turn", label="context join span")
    )
    assert [step["step_index"] for step in partial["steps"]] == [1, 2, 3]
    for step in partial["steps"]:
        assert step.get("context_node_id") == source_join[step["step_index"]]
    assert any(step.get("context_node_id") is not None for step in partial["steps"])


@pytest.mark.parametrize(
    "trajectory",
    [
        Trajectory(start=-1, end=0, kind="user_turn", label="negative"),
        Trajectory(start=1, end=0, kind="user_turn", label="reversed"),
        Trajectory(start=0, end=6, kind="user_turn", label="outside"),
        {"start": 0.9, "end": 1, "kind": "user_turn", "label": "float"},
        {"start": True, "end": 1, "kind": "user_turn", "label": "bool"},
        {"start": "0", "end": 1, "kind": "user_turn", "label": "string"},
    ],
)
def test_trajectory_materialization_rejects_invalid_positions(trajectory):
    with pytest.raises(ValueError):
        materialize_trajectory(
            TraceMaterializationRef.from_record(_real_capture_record()), trajectory
        )


def test_trajectory_materialization_preserves_trail_patch_and_anchor_joins():
    record = _real_capture_record()
    bash_position, bash_call = next(
        (position, step.tool_calls[0])
        for position, step in enumerate(record.steps)
        if step.tool_calls
    )

    class Projection:
        def patches_for_trace(self, trace_id):
            assert trace_id == record.trace_id
            return [
                {
                    "trace_patch_id": "tp-materialized",
                    "git_anchor_id": "ga-materialized",
                    "commit_sha": "abc123",
                    "file_path": "src/generated.py",
                    "attribution_role": "leaf_writer",
                    "step_metadata": {
                        "tool_call_id": bash_call.tool_call_id,
                        "tool_name": bash_call.tool_name,
                    },
                }
            ]

    enriched_ref = TraceMaterializationRef.from_record(record, trail_projection=Projection())
    trace_ref = TraceMaterializationRef(
        record=record,
        trace_map=enriched_ref.trace_map.model_copy(
            update={"limitations": ["path_normalization_failed"]}
        ),
    )
    trace_slice = materialize_trajectory(
        trace_ref,
        Trajectory(
            start=bash_position,
            end=bash_position,
            kind="change_burst",
            label="verified write",
        ),
    )

    assert trace_slice["trace_patch_refs"] == ["tp-materialized"]
    assert trace_slice["git_anchor_refs"] == ["ga-materialized"]
    assert trace_slice["limitations"] == ["path_normalization_failed"]
    assert trace_slice["map"]["limitations"] == ["path_normalization_failed"]


# --------------------------------------------------------------------------
# acceptance #5 — the agent-loop fixture: rc=10 -> rc=0
# --------------------------------------------------------------------------
@pytest.mark.parametrize("slicer", ("s3", "s4"))
def test_agent_loop_rc10_then_rc0(slicer):
    rec = CORPUS["multi_turn"]
    rc1, env1 = slicing.partition_trace(
        trace_id="f", slicer_name=slicer, steps=rec.steps, judge="agent"
    )
    if not env1.get("judgment_requests"):
        pytest.skip("this fixture raised no judgments for this slicer")
    assert rc1 == slicing.RC_NEEDS_JUDGMENT
    assert env1["schema_version"] == "opentraces.slicing.needs_judgment.v1"
    for r in env1["judgment_requests"]:
        assert set(r) >= {"id", "kind", "window", "candidate_step", "prompt", "answer_schema"}
        assert r["kind"] in ("same_outcome", "terminal_vs_intermediate")

    answers = {
        r["id"]: {"decision": r["answer_schema"]["decision"][0], "confidence": 0.9}
        for r in env1["judgment_requests"]
    }
    rc2, env2 = slicing.partition_trace(
        trace_id="f", slicer_name=slicer, steps=rec.steps, judge="agent", answers=answers
    )
    assert rc2 == 0
    assert env2["schema_version"] == "opentraces.slicing.v1"
    indep = _independent_recompute(env2["trajectories"], env2["total_steps"])
    assert indep["valid"], indep


# --------------------------------------------------------------------------
# acceptance #6 — the three red-capability mutants, caught by the INDEPENDENT
# recompute (never by the envelope's self-reported tiling.valid).
# --------------------------------------------------------------------------
def _arm(monkeypatch, site):
    monkeypatch.setenv("OT_FAULTPOINT", site)
    monkeypatch.setenv("OT_OTBOX_FAULT_OK", "1")


def test_mutant_off_by_one_changes_boundaries(monkeypatch):
    rec = _fixture_record()
    base = slicing.partition_trace(
        trace_id="f", slicer_name="s1", steps=rec.steps, judge="deterministic"
    )[1]
    _arm(monkeypatch, "slicer-boundary-off-by-one")
    mut = slicing.partition_trace(
        trace_id="f", slicer_name="s1", steps=rec.steps, judge="deterministic"
    )[1]
    base_spans = [(t["start"], t["end"]) for t in base["trajectories"]]
    mut_spans = [(t["start"], t["end"]) for t in mut["trajectories"]]
    assert mut_spans != base_spans  # the pinned-boundary journey assertion would fail


def test_mutant_dropped_continuation_opens_control_messages(monkeypatch):
    rec = _fixture_record()
    base = slicing.partition_trace(
        trace_id="f", slicer_name="s1", steps=rec.steps, judge="deterministic"
    )[1]
    _arm(monkeypatch, "slicer-drop-continuation")
    mut = slicing.partition_trace(
        trace_id="f", slicer_name="s1", steps=rec.steps, judge="deterministic"
    )[1]
    # control messages now open trajectories -> strictly more of them
    assert len(mut["trajectories"]) > len(base["trajectories"])


def test_mutant_self_report_valid_gap_is_caught_by_recompute(monkeypatch):
    rec = _fixture_record()
    _arm(monkeypatch, "slicer-self-report-valid-gap")
    env = slicing.partition_trace(
        trace_id="f", slicer_name="s1", steps=rec.steps, judge="deterministic"
    )[1]
    # The envelope LIES: it claims its own tiling is valid...
    assert env["tiling"]["valid"] is True
    # ...but the INDEPENDENT recompute from trajectories[] finds the real gap.
    indep = _independent_recompute(env["trajectories"], env["total_steps"])
    assert indep["valid"] is False
    assert indep["gaps"] > 0


# --------------------------------------------------------------------------
# acceptance #8 — derive-on-demand: NO captured-schema bump
# --------------------------------------------------------------------------
def test_no_schema_version_bump():
    assert SCHEMA_VERSION == "0.9.0", (
        "Trace Slicer Library is derive-on-demand; do not bump SCHEMA_VERSION for slicing (0.9.0 is the #212 dataset-facet MINOR, unrelated to this module)"
    )
    assert slicing.SLICING_SCHEMA_VERSION == "opentraces.slicing.v1"


# --------------------------------------------------------------------------
# trajectory labeling — upgrades labels without touching boundaries/tiling
# --------------------------------------------------------------------------
def test_labeler_replaces_labels_but_not_boundaries():
    rec = CORPUS["multi_turn"]
    base = slicing.partition_trace(
        trace_id="x", slicer_name="s3", steps=rec.steps, judge="deterministic"
    )[1]

    seen = {}

    def fake_labeler(slicer_name, steps, trajectories):
        seen["n"] = len(trajectories)
        return [f"LABEL-{i}" for i in range(len(trajectories))]

    rc, env = slicing.partition_trace(
        trace_id="x", slicer_name="s3", steps=rec.steps, judge="deterministic", labeler=fake_labeler
    )
    assert rc == 0
    # boundaries identical, labels replaced
    assert [(t["start"], t["end"]) for t in env["trajectories"]] == [
        (t["start"], t["end"]) for t in base["trajectories"]
    ]
    assert [t["label"] for t in env["trajectories"]] == [f"LABEL-{i}" for i in range(seen["n"])]
    assert _independent_recompute(env["trajectories"], env["total_steps"])["valid"]


def test_labeler_failure_falls_back_to_deterministic():
    rec = CORPUS["multi_turn"]
    base = slicing.partition_trace(
        trace_id="x", slicer_name="s3", steps=rec.steps, judge="deterministic"
    )[1]

    def bad_labeler(slicer_name, steps, trajectories):
        return None  # provider unavailable / malformed -> keep deterministic

    env = slicing.partition_trace(
        trace_id="x", slicer_name="s3", steps=rec.steps, judge="deterministic", labeler=bad_labeler
    )[1]
    assert [t["label"] for t in env["trajectories"]] == [t["label"] for t in base["trajectories"]]


def test_provider_labeler_is_none_when_provider_disabled(monkeypatch):
    monkeypatch.setenv("OT_LLM_PROVIDER", "none")
    from opentraces.core.slicing.labeler import provider_labeler

    assert provider_labeler() is None
