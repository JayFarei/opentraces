"""Plan 56 public trace index and map model contracts."""

from opentraces_schema import (
    CandidatePacket,
    TraceFacet,
    TraceMap,
    TraceMapEdge,
    TraceMapNode,
    TraceSignal,
    TraceUnit,
)


def test_candidate_packet_round_trips_with_bounded_map_preview():
    facet = TraceFacet(
        name="skill.name",
        value="grill-me",
        source="exact_schema",
        evidence_ref="tmn:trace-alpha:3",
    )
    signal = TraceSignal(
        name="tested_successful_fix_candidate",
        value=True,
        confidence="high",
        evidence_refs=["tmn:trace-alpha:5", "tmn:trace-alpha:8"],
    )
    unit = TraceUnit(
        unit_id="tu:trace-alpha:test-signal",
        unit_type="test_or_error_signal",
        trace_id="trace-alpha",
        project_slug="demo",
        files=["src/parser.py"],
        skills=["grill-me"],
        title_text="Fix parser regression",
        intent_text="Fix the failing parser test",
        action_text="Edited src/parser.py and reran pytest",
        evidence_text="pytest failed, then pytest passed",
        artifact_text="src/parser.py",
        facets=[facet],
        signals=[signal],
        trail_refs=["ot://trace/trace-alpha/patches/patch-1/trail"],
    )
    start = TraceMapNode(
        node_id="tmn:trace-alpha:1",
        trace_id="trace-alpha",
        unit_id="tu:trace-alpha:intent",
        action_type="user_instruction",
        step_index=1,
        next_node_id="tmn:trace-alpha:2",
        text_preview="Fix the failing parser test",
    )
    test_run = TraceMapNode(
        node_id="tmn:trace-alpha:2",
        trace_id="trace-alpha",
        unit_id=unit.unit_id,
        action_type="test_run",
        step_index=2,
        previous_node_id=start.node_id,
        text_preview="pytest tests/test_parser.py",
    )
    edge = TraceMapEdge(
        edge_id="tme:trace-alpha:1:2",
        trace_id="trace-alpha",
        source_node_id=start.node_id,
        target_node_id=test_run.node_id,
        edge_type="previous_next",
    )
    trace_map = TraceMap(
        trace_id="trace-alpha",
        root_node_ids=[start.node_id],
        nodes=[start, test_run],
        edges=[edge],
    )

    packet = CandidatePacket(
        unit_id=unit.unit_id,
        unit_type=unit.unit_type,
        trace_id=unit.trace_id,
        project_slug=unit.project_slug,
        title="Fix parser regression",
        intent_preview="Fix the failing parser test",
        candidate_kind="bug_fix",
        match_explanation="skill.name matched grill-me; test signal matched",
        score=12.5,
        score_parts={"lexical": 4.0, "signal": 8.5},
        matched_fields={"evidence_text": ["pytest", "passed"]},
        facets=unit.facets,
        signals=unit.signals,
        skills=unit.skills,
        files=unit.files,
        test_commands=["pytest tests/test_parser.py"],
        trail_refs=unit.trail_refs,
        map_ref="ot://trace/trace-alpha/map",
        map_node_refs=[start.node_id, test_run.node_id],
        refs={"unit": unit.unit_id, "trace": unit.trace_id},
        slice_preview=trace_map,
    )

    restored = CandidatePacket.model_validate_json(packet.model_dump_json())

    assert restored.unit_id == "tu:trace-alpha:test-signal"
    assert restored.slice_preview is not None
    assert [node.action_type for node in restored.slice_preview.nodes] == [
        "user_instruction",
        "test_run",
    ]
    assert restored.facets[0].source == "exact_schema"
    assert restored.signals[0].evidence_refs == ["tmn:trace-alpha:5", "tmn:trace-alpha:8"]
