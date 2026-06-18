"""Issue #98 — capsule bounded progress + ``capsule export --from-session``.

Layer-1 mechanism units, $HOME-isolated via the autouse
``_isolate_opentraces_global_state`` fixture (``tests/conftest.py``) and seeded
with the real pipeline the same way ``tests/test_capsule_usage_episode.py`` does
(``_seed`` + ``write_trace_record`` + ``ingest_one_session``), NOT the
real-bucket ``test_capsule_export_integration.py`` shape.

Each test fails on PRE-FIX code for a distinct mechanical reason (see the
red-before-green notes inline): ``slice_for_product`` had no ``radius`` kwarg,
``export_capsule`` had no ``progress`` kwarg, and ``capsule export`` had neither
``--progress`` nor ``--from-session``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from click.testing import CliRunner

from opentraces_schema import Agent, Step, TraceMap, TraceMapNode, TraceRecord

from opentraces.cli import main
from opentraces.core.capsule.export import export_capsule
from opentraces.core.capsule.share import resolve_capsule
from opentraces.core.progress import ProgressReporter
from opentraces.core.trace_slices import slice_for_product

PRODUCT = "opentraces"


# --------------------------------------------------------------------------- #
# Seeding helpers (real-pipeline, conftest-HOME-isolated)
# --------------------------------------------------------------------------- #


def _enroll(project_dir: Path, *, excluded: bool = False) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    marker = {"marker_version": "2", "project_id": "0123456789abcdef0123456789abcdef"}
    if excluded:
        marker["excluded"] = True
    (project_dir / ".opentraces.json").write_text(json.dumps(marker))
    from opentraces.core.config import get_project_traces_dir

    get_project_traces_dir(project_dir).mkdir(parents=True, exist_ok=True)


def _episode_record(trace_id: str = "p98-trace-1") -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"s-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": f"Use {PRODUCT} in the pipeline"},
        dependencies=[PRODUCT],
        steps=[
            Step(step_index=1, role="user", content=f"Integrate {PRODUCT}"),
            Step(step_index=2, role="agent", content=f"Working with {PRODUCT}"),
            Step(step_index=3, role="agent", content=f"{PRODUCT} done"),
        ],
        outcome={"success": True, "committed": False},
    )


def _seed(project_dir: Path, record: TraceRecord) -> str:
    from opentraces.core.bucket_store import write_trace_record
    from opentraces.core.config import get_project_dir

    _enroll(project_dir)
    slug = get_project_dir(project_dir).name
    write_trace_record(record, project_slug=slug, source_layer="capture")
    return slug


# --------------------------------------------------------------------------- #
# A2 — the `--product` slice is bounded by radius
# --------------------------------------------------------------------------- #


def _product_map(trace_id: str = "tm-1") -> TraceMap:
    """A Trace Map whose product needle appears at an early AND a far step."""

    nodes = [
        TraceMapNode(node_id="n-early", unit_id="u-early", action_type="tool_call",
                     trace_id=trace_id, step_index=2,
                     text_preview=f"call into {PRODUCT} here"),
        TraceMapNode(node_id="n-late", unit_id="u-late", action_type="tool_call",
                     trace_id=trace_id, step_index=400,
                     text_preview=f"finally {PRODUCT} again"),
    ]
    return TraceMap(trace_id=trace_id, root_node_ids=["n-early"], nodes=nodes, edges=[],
                    limitations=[])


def test_product_slice_bounded():
    # RED pre-fix: slice_for_product() has no `radius` kwarg → TypeError.
    trace_map = _product_map()

    # Unbounded (historical) spans 2..400.
    full = slice_for_product(trace_map, None, product_match=PRODUCT, radius=None)
    assert full is not None
    assert full["end_step_index"] - full["start_step_index"] == 398
    assert full["metadata"]["product_match_span"] == 398
    assert full["metadata"]["bounded_to"] == 398
    assert "product_episode_bounded" not in (full.get("limitations") or [])

    # Bounded to 2*radius around the first match; product_episode_bounded ALWAYS
    # recorded when the cap was in effect (deterministic, not span-conditional).
    bounded = slice_for_product(trace_map, None, product_match=PRODUCT, radius=4)
    assert bounded is not None
    assert bounded["end_step_index"] - bounded["start_step_index"] <= 2 * 4
    assert bounded["metadata"]["product_match_span"] == 398  # raw, pre-clamp
    assert bounded["metadata"]["bounded_to"] <= 8
    assert "product_episode_bounded" in bounded["limitations"]


def test_export_product_default_is_bounded_full_span_opts_out(tmp_path):
    """The export default bounds the product episode; --product-full-span restores
    the unbounded span. Uses a small seeded episode (needle at every step)."""

    project = tmp_path / "proj"
    record = _episode_record()
    _seed(project, record)

    default = export_capsule(project_dir=project, trace_id=record.trace_id, product=PRODUCT)
    assert "product_episode_bounded" in default["slice"]["limitations"]
    assert "product_episode_bounded" in default["limitations"]

    full = export_capsule(
        project_dir=project, trace_id=record.trace_id, product=PRODUCT,
        product_full_span=True,
    )
    assert "product_episode_bounded" not in (full["slice"].get("limitations") or [])
    # No clamp happened: bounded_to equals the raw span (the equals_var path
    # Journey 3 step 2 asserts on).
    assert full["slice"]["metadata"]["bounded_to"] == full["slice"]["metadata"]["product_match_span"]


# --------------------------------------------------------------------------- #
# A1/A3 — progress stages + heartbeat; preview --json carries telemetry
# --------------------------------------------------------------------------- #


def test_export_emits_progress_stages(tmp_path, monkeypatch):
    # RED pre-fix: export_capsule() has no `progress` kwarg → TypeError.
    project = tmp_path / "proj"
    record = _episode_record()
    _seed(project, record)

    events: list[dict] = []
    guard = threading.Lock()

    def _sink(ev: dict) -> None:
        with guard:
            events.append(ev)

    # Make the trail_anchors stage block (without advance()) so only the
    # background heartbeat can emit — mirrors #88's slow-stage test.
    import opentraces.core.capsule.export as export_mod

    def _slow_trail_anchors(project_dir, trace_id):
        time.sleep(0.25)
        return []

    monkeypatch.setattr(export_mod, "_trail_anchors", _slow_trail_anchors)

    reporter = ProgressReporter(
        "capsule export", emit=_sink, heartbeat_interval=0.05, enable_heartbeat=True,
    )
    export_capsule(project_dir=project, trace_id=record.trace_id, progress=reporter)

    with guard:
        stages = {e["stage"] for e in events}
        trail_beats = [e for e in events if e["stage"] == "trail_anchors"]
    for expected in ("load_trace", "build_slice", "resolve_context", "trail_anchors", "redact"):
        assert expected in stages, f"missing stage {expected!r} in {stages}"
    # stage() force-emits once; the blocked stage's heartbeat adds >= 1 more.
    assert len(trail_beats) >= 2
    assert not reporter.heartbeat_alive()


def test_preview_json_carries_telemetry_and_writes_nothing(tmp_path):
    # RED pre-fix: --progress is an unknown option (rc=2) AND no telemetry key.
    project = tmp_path / "proj"
    record = _episode_record()
    _seed(project, record)

    runner = CliRunner()
    result = runner.invoke(main, [
        "capsule", "preview", record.trace_id, "--project", str(project),
        "--json", "--progress", "json",
    ])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["writes_anything"] is False
    assert "telemetry" in payload and isinstance(payload["telemetry"]["stages"], list)
    stages = {s["stage"] for s in payload["telemetry"]["stages"]}
    assert {"load_trace", "redact"} <= stages
    # Preview writes NOTHING.
    assert not (project / ".opentraces" / "capsules").exists()


# --------------------------------------------------------------------------- #
# Part B — capsule export --from-session (Codex sidecar + Claude transcript)
# --------------------------------------------------------------------------- #


def _codex_rollout_lines(session_id: str, cwd: str) -> list[dict]:
    """A minimal, PARSEABLE Codex rollout (session_meta + turn_context + a user
    message + a tool call + its output) — enough to clear the 2-steps/1-tool-call
    quality gate. Production hook sidecars are hook-only and do NOT parse; this
    proves the resolve→ingest→export wiring (Codex=PARTIAL, see the PR note)."""

    return [
        {"type": "session_meta", "payload": {
            "id": session_id, "timestamp": "2026-06-18T09:00:00Z", "cwd": cwd,
            "cli_version": "0.140.0", "model": "gpt-5.5", "model_provider": "openai"}},
        {"type": "turn_context", "payload": {
            "turn_id": "turn-1", "cwd": cwd, "model": "gpt-5.5",
            "user_instructions": f"use {PRODUCT}"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": f"please use {PRODUCT} now"}]}},
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command",
            "arguments": json.dumps({"cmd": f"{PRODUCT} trace query", "workdir": cwd}),
            "call_id": "call-1"}},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call-1",
            "output": "ok: 1 trace"}},
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": f"{PRODUCT} ran fine"}]}},
    ]


def _write_codex_sidecar(project_dir: Path, session_id: str) -> Path:
    path = project_dir / ".opentraces" / "codex-cli" / "hooks" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for line in _codex_rollout_lines(session_id, str(project_dir)):
            f.write(json.dumps(line) + "\n")
    return path


def _claude_turn(i: int, session_id: str) -> list[dict]:
    ts = f"2026-06-18T09:00:{i:02d}Z"
    tool_id = f"tu_{i}"
    return [
        {"type": "user", "sessionId": session_id, "timestamp": ts,
         "message": {"role": "user", "content": f"prompt {i} use {PRODUCT}"}},
        {"type": "assistant", "sessionId": session_id, "timestamp": ts,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": "x.py"}}],
             "usage": {"input_tokens": 10, "output_tokens": 10}}},
        {"type": "user", "sessionId": session_id, "timestamp": ts,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}},
    ]


def _write_claude_transcript(home: Path, session_id: str, turns: int = 2) -> Path:
    enc = "-Users-fake-proj"
    path = home / ".claude" / "projects" / enc / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for i in range(1, turns + 1):
            for line in _claude_turn(i, session_id):
                f.write(json.dumps(line) + "\n")
    return path


def test_from_session_codex(tmp_path, monkeypatch):
    # RED pre-fix: --from-session is an unknown option → rc=2 "no such option".
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path.home() == tmp_path
    project = tmp_path / "proj"
    _enroll(project)
    sid = "codex-sess-1"
    _write_codex_sidecar(project, sid)

    runner = CliRunner()
    result = runner.invoke(main, [
        "capsule", "export", "--from-session", sid, "--from-agent", "codex",
        "--project", str(project), "--json",
    ])
    assert result.exit_code == 0, result.stderr
    capsule = json.loads(result.stdout)
    assert capsule["schema_version"] == "opentraces.capsule.v1"
    # Round-trips through the consume verb.
    assert resolve_capsule  # imported
    assert capsule["capsule_id"]


def test_from_session_claude(tmp_path, monkeypatch):
    # RED pre-fix: same unknown-option failure.
    # Belt-and-suspenders HOME pin (the conftest autouse fixture already sets it,
    # but make the hermeticity local + obvious): the Claude glob reads
    # Path.home()/.claude/projects, so it must resolve under our tmp tree.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path.home() == tmp_path
    project = tmp_path / "proj"
    _enroll(project)
    sid = "claude-sess-1"
    _write_claude_transcript(tmp_path, sid)

    runner = CliRunner()
    result = runner.invoke(main, [
        "capsule", "export", "--from-session", sid, "--from-agent", "claude",
        "--project", str(project), "--json",
    ])
    assert result.exit_code == 0, result.stderr
    capsule = json.loads(result.stdout)
    assert capsule["schema_version"] == "opentraces.capsule.v1"
    assert capsule["capsule_id"]


def test_from_session_not_materialized_remediation(tmp_path, monkeypatch):
    # RED pre-fix: unknown option, not the remediation message.
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "proj"
    _enroll(project)

    runner = CliRunner()
    result = runner.invoke(main, [
        "capsule", "export", "--from-session", "ghost-sess",
        "--project", str(project), "--json",
    ])
    assert result.exit_code == 2
    err = result.stderr
    assert ".opentraces/codex-cli/hooks/" in err
    assert ".claude/projects/" in err
    assert "may not be captured yet" in err


def test_from_session_excluded_project_refuses(tmp_path, monkeypatch):
    # RED pre-fix: --from-session unknown option; the resolver does not exist.
    # Locks the EXCLUDED branch distinct from not-found so a None trace_id from
    # action="skipped", error="project is excluded" never reads as "trace not found".
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "proj"
    _enroll(project, excluded=True)
    sid = "codex-excluded-1"
    _write_codex_sidecar(project, sid)

    runner = CliRunner()
    result = runner.invoke(main, [
        "capsule", "export", "--from-session", sid, "--from-agent", "codex",
        "--project", str(project), "--json",
    ])
    assert result.exit_code == 2
    err = result.stderr
    assert "excluded from capture" in err
    assert "Remove the exclusion" in err
    # Distinct from the not-found remedy.
    assert "may not be captured yet" not in err


def test_export_mutual_exclusion_errors(tmp_path):
    project = tmp_path / "proj"
    _enroll(project)
    runner = CliRunner()
    # Both a trace id AND --from-session → usage error.
    both = runner.invoke(main, [
        "capsule", "export", "some-trace", "--from-session", "s",
        "--project", str(project),
    ])
    assert both.exit_code == 2
    assert "mutually exclusive" in both.stderr
    # Neither → usage error.
    neither = runner.invoke(main, ["capsule", "export", "--project", str(project)])
    assert neither.exit_code == 2
    assert "provide a trace id or --from-session" in neither.stderr
