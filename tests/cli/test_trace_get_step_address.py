"""F1 — the universal trace:step address, resolved on ``trace get``.

``trace get`` already advertised ``trace get <T>:<N>`` in trail's world
payload (``core/trails/lineage.py::build_trail_world`` next_steps) and the
ctx group help claims the token is universal across ctx / trace get / trail —
but before F1 a step address raw-tracebacked at rc=1. This suite pins the
finished join key:

* ``trace get <T>:<N>`` / ``<T>:last`` — bounded STEP CARD (the action lens)
  inside the existing ``opentraces.trace.get.v1`` envelope, carrying the
  ``context_node_id`` join key + best-effort local-map enrichment.
* ``trace get <T>:<A>-<B>`` — SPAN lens, delegated to the exact
  ``slice_by_steps`` engine behind ``trace slice`` (byte-identical object).
* ``trace get <T>:<N> --resume`` — maps to ``at_step='s<N>'`` (the
  capture/claude_code/resume.py::_find_step contract); ``:last`` resolves the
  max step_index first.
* rc contract: rc=6 lookup miss (trace or step), rc=2 malformed selector /
  reserved ``:origin`` / whole-trace-flag combos / span+--remote.
* Known-prefix refs (``ot://``, ``tu:``, ``tmn:``, ``t:``) BYPASS step
  parsing byte-identically — their colons are scheme separators.
* Short-prefix honesty: ``get_trace_path`` is EXACT-id, so
  ``trace get <8-char-prefix>:3`` is rc=6 exactly like today's bare
  ``trace get <8-char-prefix>`` — deliberate, pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import SENTINEL, main
from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord
from opentraces.core.slicing.models import Trajectory
from opentraces.core.trace_slices import TraceMaterializationRef, materialize_trajectory

TRACE_ID = "trace-step-address-f1"


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _fixture_trace() -> TraceRecord:
    """Steps 0 / 3 / 7: user ask, an Edit with observations + ctx join key,
    and the final response. step_index is an INDEX FIELD, not list position."""
    steps = [
        Step(step_index=0, role="user", content="please fix the parser " + "x" * 300),
        Step(
            step_index=3,
            role="agent",
            content="Editing the parser now.",
            reasoning_content="the tokenizer boundary is off by one",
            model="claude-opus-4-6",
            call_type="main",
            agent_role="main",
            context_node_id="sha256:f1f1f1f1",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-1",
                    tool_name="Edit",
                    input={"file_path": "src/parser.py", "old_string": "a", "new_string": "b"},
                )
            ],
            observations=[
                Observation(source_call_id="tc-1", content="ok" * 10, error=None),
                Observation(source_call_id="tc-1", content=None, error="no_result"),
            ],
        ),
        Step(step_index=7, role="agent", content="Done - parser fixed."),
    ]
    return TraceRecord(
        trace_id=TRACE_ID,
        session_id="session-step-address-f1",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Fix the parser"},
        steps=steps,
    )


def _extract_json(output: str) -> dict:
    if SENTINEL in output:
        return json.loads(output.split(SENTINEL, 1)[1].strip())
    return json.loads(output)


def _seed(tmp_path: Path) -> CliRunner:
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _fixture_trace())
    runner = CliRunner()
    rebuild = runner.invoke(main, ["trace", "index", "--json"])
    assert rebuild.exit_code == 0, rebuild.output
    return runner


# --------------------------------------------------------------------------- #
# step form — the bounded action card
# --------------------------------------------------------------------------- #
def test_step_form_returns_bounded_step_card(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:3", "--json"])
    assert res.exit_code == 0, res.output
    payload = _extract_json(res.output)

    # L5 uniform header on the existing (non-frozen) trace.get.v1 envelope.
    assert payload["status"] == "ok"
    assert payload["schema_version"] == "opentraces.trace.get.v1"
    assert payload["trace_id"] == TRACE_ID

    card = payload["step"]
    assert card["step_index"] == 3
    assert card["role"] == "agent"
    assert card["call_type"] == "main"
    assert card["model"] == "claude-opus-4-6"
    # The ctx join key — the whole point of the action lens.
    assert card["context_node_id"] == "sha256:f1f1f1f1"
    assert card["reasoning_present"] is True
    assert card["tool_calls"] == [
        {
            "tool_name": "Edit",
            "tool_call_id": "tc-1",
            "input_preview": json.dumps(
                {"file_path": "src/parser.py", "old_string": "a", "new_string": "b"},
                default=str,
                sort_keys=True,
            )[:120],
        }
    ]
    assert card["observations"] == {"count": 2, "total_chars": 20, "first_error": "no_result"}
    assert "token_usage" in card
    # Best-effort local-map enrichment (the index was just rebuilt).
    assert card["map_node_id"].startswith("tmn:")
    assert "src/parser.py" in card["files_modified"]
    # The closed cross-substrate triple.
    assert payload["next_steps"] == [
        f"ctx {TRACE_ID}:3",
        f"trail {TRACE_ID}:3",
        f"trace slice {TRACE_ID} --around-step 3 --radius 5",
    ]


def test_step_card_content_preview_is_truncated(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:0", "--json"])
    assert res.exit_code == 0, res.output
    card = _extract_json(res.output)["step"]
    assert card["step_index"] == 0
    assert len(card["content_preview"]) == 240
    assert card["content_chars"] > 240


def test_last_selector_picks_max_step_index(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:last", "--json"])
    assert res.exit_code == 0, res.output
    payload = _extract_json(res.output)
    assert payload["step"]["step_index"] == 7
    assert payload["next_steps"][0] == f"ctx {TRACE_ID}:7"


def test_step_form_human_render_is_compact(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:3"])
    assert res.exit_code == 0, res.output
    assert f"{TRACE_ID}:3  role=agent" in res.output
    assert "tools:    Edit" in res.output
    assert "ctx node: sha256:f1f1f1f1" in res.output


def test_bare_ref_unchanged_bounded_overview(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", TRACE_ID, "--json"])
    assert res.exit_code == 0, res.output
    payload = _extract_json(res.output)
    assert "trace" in payload and "step" not in payload and "slice" not in payload
    assert payload["trace"]["step_count"] == 3


# --------------------------------------------------------------------------- #
# span form — delegated to the slice engine, byte-identical object
# --------------------------------------------------------------------------- #
def test_span_form_embeds_slice_byte_identical_to_trace_slice(tmp_path):
    runner = _seed(tmp_path)
    via_get = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:0-7", "--json"])
    assert via_get.exit_code == 0, via_get.output
    via_slice = runner.invoke(
        main,
        ["trace", "slice", TRACE_ID, "--from-step", "0", "--to-step", "7", "--json"],
    )
    assert via_slice.exit_code == 0, via_slice.output

    get_payload = _extract_json(via_get.output)
    slice_payload = _extract_json(via_slice.output)
    assert get_payload["status"] == "ok"
    assert get_payload["schema_version"] == "opentraces.trace.get.v1"
    assert get_payload["span"] == {"from_step": 0, "to_step": 7}
    # One primitive, two addresses: the embedded object IS slices[0].
    assert get_payload["slice"] == slice_payload["slices"][0]


def test_materialized_slicer_span_matches_real_trace_get_route(tmp_path):
    """#270 route property: positional trajectory -> canonical address span."""

    runner = _seed(tmp_path)
    record = _fixture_trace()
    from opentraces.core.trace_index import get_trace_map

    trace_map = get_trace_map(record.trace_id)
    assert trace_map is not None
    materialized = materialize_trajectory(
        TraceMaterializationRef(record=record, trace_map=trace_map),
        Trajectory(start=0, end=1, kind="user_turn", label="first two steps"),
    )

    # Positions 0..1 name the record's canonical step indices 0..3.
    via_get = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:0-3", "--json"])
    assert via_get.exit_code == 0, via_get.output
    addressed = _extract_json(via_get.output)["slice"]

    assert [step["step_index"] for step in materialized["steps"]] == [0, 3]
    assert [step["step_index"] for step in addressed["steps"]] == [0, 3]
    assert materialized["map_node_refs"] == addressed["map_node_refs"]
    assert materialized["trace_patch_refs"] == addressed["trace_patch_refs"]
    assert materialized["git_anchor_refs"] == addressed["git_anchor_refs"]


def test_span_form_invalid_range_exits_2(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:7-0", "--json"])
    # Same rc as `trace slice --from-step 7 --to-step 0` (ValueError → rc=2).
    via_slice = runner.invoke(
        main,
        ["trace", "slice", TRACE_ID, "--from-step", "7", "--to-step", "0", "--json"],
    )
    assert res.exit_code == via_slice.exit_code, (res.output, via_slice.output)


def test_span_form_without_map_exits_6():
    res = CliRunner().invoke(main, ["trace", "get", "no-such-trace:0-3", "--json"])
    assert res.exit_code == 6, res.output
    assert "Trace Map not found" in res.output


# --------------------------------------------------------------------------- #
# rc contract — lookup misses and bad shapes
# --------------------------------------------------------------------------- #
def test_step_out_of_range_exits_6(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:999999", "--json"])
    assert res.exit_code == 6, res.output
    assert "Step not found" in res.output
    assert "steps 0..7" in res.output


def test_malformed_selector_exits_2(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:abc", "--json"])
    assert res.exit_code == 2, res.output
    assert "Unparseable step address" in res.output


def test_origin_selector_reserved_exits_2(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:origin", "--json"])
    assert res.exit_code == 2, res.output
    assert "origin" in res.output


def test_short_prefix_with_step_is_exact_id_honest_rc_6(tmp_path):
    """DELIBERATE: get_trace_path is exact-id (sqlite equality), so an
    8-char prefix step address rc=6s exactly like today's bare prefix —
    prefix widening is a coordinated follow-up, not a trace-get bug."""
    runner = _seed(tmp_path)
    prefix = TRACE_ID[:8]
    bare = runner.invoke(main, ["trace", "get", prefix, "--json"])
    addressed = runner.invoke(main, ["trace", "get", f"{prefix}:3", "--json"])
    assert bare.exit_code == 6, bare.output
    assert addressed.exit_code == 6, addressed.output
    assert "Trace not found" in addressed.output


# --------------------------------------------------------------------------- #
# known-prefix refs bypass step parsing byte-identically
# --------------------------------------------------------------------------- #
def test_known_prefix_refs_bypass_step_parsing(tmp_path):
    runner = _seed(tmp_path)
    # tu:/tmn: refs embed colons that are scheme separators, never step
    # selectors — they must hit their EXISTING rc=6 paths, not the rc=2
    # bad-shape parse.
    res_tu = runner.invoke(main, ["trace", "get", "tu:no-such:3", "--json"])
    assert res_tu.exit_code == 6, res_tu.output
    assert "Trace unit not found" in res_tu.output

    res_tmn = runner.invoke(main, ["trace", "get", "tmn:no-such:3", "--json"])
    assert res_tmn.exit_code == 6, res_tmn.output
    assert "Trace Map node not found" in res_tmn.output

    # t:<id> resolves byte-identically to the bare ref.
    via_t = runner.invoke(main, ["trace", "get", f"t:{TRACE_ID}", "--json"])
    via_bare = runner.invoke(main, ["trace", "get", TRACE_ID, "--json"])
    assert via_t.exit_code == 0, via_t.output
    assert _extract_json(via_t.output) == _extract_json(via_bare.output)


# --------------------------------------------------------------------------- #
# flag combos — whole-trace flags refuse a step/span address
# --------------------------------------------------------------------------- #
def test_whole_trace_flags_refuse_step_address(tmp_path):
    runner = _seed(tmp_path)
    for flag in ("--bursts", "--waste", "--run-intel", "--card"):
        res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:3", flag, "--json"])
        assert res.exit_code == 2, (flag, res.output)
        assert "operates on the whole trace" in res.output


def test_span_with_remote_exits_2(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(
        main, ["trace", "get", f"{TRACE_ID}:0-3", "--remote", "user/repo", "--json"]
    )
    assert res.exit_code == 2, res.output
    assert "local Trace Map" in res.output


# --------------------------------------------------------------------------- #
# resume closure — the same token is the fork point
# --------------------------------------------------------------------------- #
def test_step_address_resume_maps_to_at_step_contract(tmp_path, monkeypatch):
    runner = _seed(tmp_path)
    calls: list[tuple] = []
    monkeypatch.setattr(
        "opentraces.cli.trace._resume_trace_impl",
        lambda trace_id, at_step, dry_run, as_json: calls.append(
            (trace_id, at_step, dry_run, as_json)
        ),
    )
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:3", "--resume", "--dry-run"])
    assert res.exit_code == 0, res.output
    # at_step is exactly 's<int>' — the _find_step contract.
    assert calls == [(TRACE_ID, "s3", True, False)]


def test_last_resume_resolves_max_step_index_first(tmp_path, monkeypatch):
    runner = _seed(tmp_path)
    calls: list[tuple] = []
    monkeypatch.setattr(
        "opentraces.cli.trace._resume_trace_impl",
        lambda trace_id, at_step, dry_run, as_json: calls.append((trace_id, at_step)),
    )
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:last", "--resume", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert calls == [(TRACE_ID, "s7")]


def test_step_address_plus_explicit_at_step_conflict_exits_2(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(
        main,
        ["trace", "get", f"{TRACE_ID}:3", "--resume", "--at-step", "s1", "--dry-run"],
    )
    assert res.exit_code == 2, res.output
    assert "not both" in res.output


def test_span_plus_resume_exits_2(tmp_path):
    runner = _seed(tmp_path)
    res = runner.invoke(main, ["trace", "get", f"{TRACE_ID}:0-3", "--resume"])
    assert res.exit_code == 2, res.output
    assert "single step" in res.output
