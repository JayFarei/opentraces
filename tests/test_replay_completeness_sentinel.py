"""Replay-contract capture-completeness sentinel — RED-first regression (W6, #214).

This is the frozen recurrence test for the seal-family W2 defect (issue #210):
a captured episode whose ``Step.context_node_id`` points at a context node that
was never persisted (append failure) — a step that lies about being replayable.

The sentinel is the independent audit in ``scripts/audit_replay_completeness.py``.
These tests exercise its ``classify_trace`` invariant against REAL capture output
so the audit's red-capability is proven, not asserted:

  * ``test_fault_injected_append_failure_is_RED`` drives the REAL Claude context
    emitter (``emit_context_tree_events_from_record``) + the REAL ingest stamping
    loop with ``append_event_batch`` monkeypatched to fail — reproducing the exact
    W2 shape (steps stamped, nodes never persisted) — and asserts the sentinel
    goes RED on assertion (a). Shown red against pre-W2 code (this branch is based
    at the W2 merge-base), frozen so it stays red if the defect ever recurs.
  * ``test_clean_capture_is_GREEN`` runs the same real capture WITHOUT the fault
    and asserts the sentinel is GREEN (nodes persist, stamps resolve).
  * ``test_vacuous_green_guard_*`` prove the audit refuses to pass a context-capable
    source whose companion is empty (zero events is a lost capture, not "nothing
    to check"), while still passing a genuinely context-free source.
  * ``test_audit_cli_exits_7_on_dangling_stamp_fault`` /
    ``test_audit_cli_exits_0_on_consistent_world`` prove the full AUDIT CLI PATH
    the journey actually runs (``scripts/audit_replay_completeness.py`` as a real
    subprocess over an on-disk bucket layout), not just the ``classify_trace``
    invariant in isolation: a genuine W2-shaped dangling stamp drives the
    documented exit code 7 with the dangling reason in the JSON output, and the
    SAME harness against a deliberately-consistent world (fault absent) exits 0
    — proving the fault injection, not some other bug, is what flips the exit
    code.

Skip is a lie: nothing here degrades to a skip; a missing capture chain is a
failure.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

# The committed sentinel lives at repo-root scripts/. Import its classifier.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import audit_replay_completeness as audit  # noqa: E402

from opentraces_schema import Agent, Step, TraceRecord  # noqa: E402

_AGENT = Agent(name="claude-code")


def _trace_record(trace_id: str, steps: list) -> TraceRecord:
    return TraceRecord(trace_id=trace_id, session_id="w6-sess", agent=_AGENT, steps=steps)


# --------------------------------------------------------------------------
# a minimal, parentUuid-linked Claude transcript so the real emitter
# materializes per-step context nodes (the thin no-parentUuid fixtures do not).
# --------------------------------------------------------------------------
def _write_transcript(path: Path) -> None:
    records = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "message": {"role": "user", "content": "add a farewell() helper"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "parentUuid": "u1",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading the file."},
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "Read",
                        "input": {"file_path": "src/app.py"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "uuid": "u2",
            "parentUuid": "a1",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "def greet(): ..."}
                ],
            },
        },
        {
            "type": "assistant",
            "uuid": "a2",
            "parentUuid": "u2",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Adding the helper."},
                    {
                        "type": "tool_use",
                        "id": "tu2",
                        "name": "Edit",
                        "input": {"file_path": "src/app.py", "old_string": "a", "new_string": "b"},
                    },
                ],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _make_project(tmp_path: Path, name: str = "proj") -> Path:
    project = tmp_path / name
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    (project / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=project, check=True)
    return project


def _record_for(map_keys) -> TraceRecord:
    # Build steps whose step_index matches the emitter's active-path node
    # counter, so the ingest stamping loop actually stamps (the defect surface).
    steps = [Step(step_index=int(k), role="agent") for k in sorted(map_keys)]
    return _trace_record("w6-red-trace", steps)


def _apply_ingest_stamping(final_record: TraceRecord, step_map: dict) -> None:
    # This is the exact stamping ingest.py performs (core/ingest.py:383-387).
    # Reproduced verbatim so the test exercises the real defect surface: the
    # map is stamped regardless of whether persistence succeeded.
    if step_map:
        for step in final_record.steps:
            node_id = step_map.get(step.step_index)
            if node_id is not None:
                step.context_node_id = node_id


def _companion_signals_from_log(project: Path, trace_id: str):
    """Project the trace's context companion signals directly from the event log.

    This mirrors what ``project_per_trace_exports`` writes into
    ``context.jsonl.gz``: whatever context events actually persisted. Under the
    injected fault, nothing persisted, so this is empty — exactly the empty
    companion the real bucket would carry.
    """
    from opentraces.core.trails.event_log import read_events

    node_ids: set[str] = set()
    ctx_events = 0
    for e in read_events(project, verify=False):
        if e.trace_id != trace_id:
            continue
        et = e.event_type or ""
        if et.startswith("context_"):
            ctx_events += 1
        if et == "context_node_observed":
            nid = (e.payload or {}).get("node_id")
            if nid:
                node_ids.add(nid)
    return node_ids, ctx_events


def test_fault_injected_append_failure_is_RED(tmp_path, monkeypatch):
    """Inject an append failure during real capture → dangling stamps → RED (a)."""
    from opentraces.capture.claude_code import context_tree_capture as ctc
    import opentraces.core.trails.event_log as event_log

    project = _make_project(tmp_path)
    transcript = project / "session.jsonl"
    _write_transcript(transcript)

    # THE FAULT: append_event_batch fails (as W2 measured on real append-failure
    # traces). The emitter swallows it (adds context_tree_not_captured) but has
    # ALREADY built summary["step_node_id_map"] before the try/except.
    def _boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("injected append_event_batch failure (W2 fault)")

    monkeypatch.setattr(event_log, "append_event_batch", _boom)

    # First, a fault-free dry pass to learn the emitter's step->node map keys so
    # the trace record's steps line up (we then re-run under the fault). We do
    # this on a throwaway project so no events persist to the real one.
    dry_project = _make_project(tmp_path, name="dry_holder/proj")
    dry_transcript = dry_project / "session.jsonl"
    _write_transcript(dry_transcript)
    dry_record = _trace_record("probe", [Step(step_index=i, role="agent") for i in range(8)])
    monkeypatch.setattr(event_log, "append_event_batch",
                        lambda *a, **k: None)  # no-op for the probe
    probe = ctc.emit_context_tree_events_from_record(
        project_dir=dry_project, final_record=dry_record, transcript_path=dry_transcript,
    )
    map_keys = list((probe.get("step_node_id_map") or {}).keys())
    assert map_keys, "real emitter produced no step->node map from the parentUuid transcript"

    # Now the real run UNDER THE FAULT.
    monkeypatch.setattr(event_log, "append_event_batch", _boom)
    final_record = _record_for(map_keys)
    summary = ctc.emit_context_tree_events_from_record(
        project_dir=project, final_record=final_record, transcript_path=transcript,
    )

    # Defect surface 1: the map is present even though persistence failed.
    step_map = summary.get("step_node_id_map") or {}
    assert step_map, "step_node_id_map must be populated pre-persist (the defect input)"
    # Defect surface 2: ingest stamps the steps from that map anyway.
    _apply_ingest_stamping(final_record, step_map)
    stamped = [s.context_node_id for s in final_record.steps if s.context_node_id]
    assert stamped, "ingest stamping produced no stamped steps — cannot exercise assertion (a)"

    # Reality: nothing persisted, so the companion is empty.
    companion_nodes, companion_ctx = _companion_signals_from_log(project, final_record.trace_id)
    assert companion_ctx == 0, "append failure must leave zero persisted context events"

    # THE SENTINEL: it MUST go red on the dangling stamps.
    result = audit.classify_trace(
        stamped_ids=stamped,
        companion_node_ids=companion_nodes,
        companion_ctx_event_count=companion_ctx,
        mirror_ctx_event_count=0,  # append failed => mirror empty too
        context_capable=bool(summary.get("capture_methods")),
    )
    assert result.status == "inconsistent", (
        f"sentinel stayed {result.status!r} under the injected fault — it is asserting "
        "the wrong thing"
    )
    assert not result.ok
    assert result.dangling_node_ids, "sentinel did not flag the dangling stamps"


def test_clean_capture_is_GREEN(tmp_path, monkeypatch):
    """Same real capture WITHOUT the fault → nodes persist, stamps resolve → GREEN."""
    from opentraces.capture.claude_code import context_tree_capture as ctc

    project = _make_project(tmp_path)
    transcript = project / "session.jsonl"
    _write_transcript(transcript)

    probe = ctc.emit_context_tree_events_from_record(
        project_dir=project,
        final_record=_trace_record("clean", [Step(step_index=i, role="agent") for i in range(8)]),
        transcript_path=transcript,
    )
    step_map = probe.get("step_node_id_map") or {}
    assert step_map

    final_record = _trace_record(
        "clean", [Step(step_index=int(k), role="agent") for k in sorted(step_map)],
    )
    _apply_ingest_stamping(final_record, step_map)
    stamped = [s.context_node_id for s in final_record.steps if s.context_node_id]
    assert stamped

    companion_nodes, companion_ctx = _companion_signals_from_log(project, "clean")
    assert companion_ctx > 0, "clean capture must persist context events"

    result = audit.classify_trace(
        stamped_ids=stamped,
        companion_node_ids=companion_nodes,
        companion_ctx_event_count=companion_ctx,
        mirror_ctx_event_count=companion_ctx,
        context_capable=bool(probe.get("capture_methods")),
    )
    assert result.status == "consistent", f"clean capture classified {result.status!r}: {result.reasons}"
    assert result.ok
    assert not result.dangling_node_ids


def test_vacuous_green_guard_zero_event_context_capable_is_RED():
    """A context-capable source with an empty companion is a lost capture, not a pass."""
    result = audit.classify_trace(
        stamped_ids=[],
        companion_node_ids=[],
        companion_ctx_event_count=0,
        mirror_ctx_event_count=0,
        context_capable=True,
    )
    assert result.status == "inconsistent"
    assert not result.ok


def test_context_free_source_empty_is_legitimately_empty_GREEN():
    """A genuinely context-free source (hermes/git/pre-hook) may be empty."""
    result = audit.classify_trace(
        stamped_ids=[],
        companion_node_ids=[],
        companion_ctx_event_count=0,
        mirror_ctx_event_count=0,
        context_capable=False,
    )
    assert result.status == "legitimately_empty"
    assert result.ok


def test_stale_companion_below_mirror_is_RED():
    """Events exist in the mirror but the companion never received them → RED."""
    result = audit.classify_trace(
        stamped_ids=[],
        companion_node_ids=[],
        companion_ctx_event_count=0,
        mirror_ctx_event_count=5,
        context_capable=True,
    )
    assert result.status == "inconsistent"
    assert not result.ok


def test_audit_cli_blocks_on_missing_mirror(tmp_path):
    """Skip-is-a-lie: a missing event mirror is a blocking error (exit 2), not a pass."""
    bucket = tmp_path / "bucket"
    tdir = bucket / "traces" / "v1" / "p" / "t1"
    tdir.mkdir(parents=True)
    (tdir / "trace.json").write_text(json.dumps({"trace_id": "t1", "steps": []}))
    rc = audit.main(["--bucket", str(bucket), "--trace-id", "t1", "--json"])
    assert rc == audit.EXIT_BLOCKED


# --------------------------------------------------------------------------
# The full audit CLI path (item 3, Codex external review on PR #215).
#
# Everything above exercises ``classify_trace`` — the invariant — directly.
# The otbox journey never calls ``classify_trace``; it shells out to
# ``scripts/audit_replay_completeness.py`` as a real subprocess over a real
# on-disk bucket layout (``[[steps]] type = "shell" argv = ["{py_bin}",
# ".../audit_replay_completeness.py", "--bucket", ..., "--trace-id", ...,
# "--json"]``). These two tests build that exact on-disk shape by hand (a
# ``bucket/traces/v1/<project>/<trace_id>/{trace.json,context.jsonl.gz}`` plus
# a ``bucket/events/v1/batches/*.jsonl.gz`` mirror) and run the script the
# same way the journey does, so the argparse plumbing, bucket-path
# resolution, gzip reads, and exit-code wiring in ``main()`` are all proven,
# not just the classifier they call into.
# --------------------------------------------------------------------------
_AUDIT_SCRIPT = _REPO_ROOT / "scripts" / "audit_replay_completeness.py"


def _write_gz_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _build_cli_bucket(tmp_path: Path, *, trace_id: str, companion_has_node: bool) -> Path:
    """Build a real bucket layout for one trace with a stamped context node.

    ``companion_has_node=True`` -> the stamped node resolves inside the
    trace's own companion (a consistent world, exit 0). ``False`` -> the
    companion is empty while the event mirror still carries the event (the
    exact W2 shape: a step lies about being replayable) -> exit 7.
    """
    bucket = tmp_path / "bucket"
    trace_dir = bucket / "traces" / "v1" / "proj" / trace_id
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.json").write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "context_tree_summary": {"capture_methods": ["transcript_reconstruction"]},
                "steps": [{"step_index": 1, "context_node_id": "node-A"}],
            }
        )
    )
    companion_rows = (
        [{"event_type": "context_node_observed", "trace_id": trace_id,
          "payload": {"node_id": "node-A"}}]
        if companion_has_node
        else []
    )
    _write_gz_jsonl(trace_dir / "context.jsonl.gz", companion_rows)
    # The event mirror always carries the event — under the fault, the
    # companion never received what the mirror already has (W2's shape:
    # append_event_batch succeeded for the mirror's own bookkeeping path in
    # some deployments, or a partial write left the mirror non-empty while
    # the per-trace companion export never ran).
    _write_gz_jsonl(
        bucket / "events" / "v1" / "batches" / "0001-b.jsonl.gz",
        [{"event_type": "context_node_observed", "trace_id": trace_id,
          "payload": {"node_id": "node-A"}}],
    )
    return bucket


def _run_audit_cli(bucket: Path, trace_id: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(_AUDIT_SCRIPT), "--bucket", str(bucket),
         "--trace-id", trace_id, "--json"],
        capture_output=True, text=True, check=False,
    )
    payload = json.loads(proc.stdout)
    return proc.returncode, payload


def test_audit_cli_exits_0_on_consistent_world(tmp_path):
    """Counter-proof: the SAME harness, fault absent, exits 0 (not vacuously red)."""
    bucket = _build_cli_bucket(tmp_path, trace_id="w6-cli-clean", companion_has_node=True)
    rc, payload = _run_audit_cli(bucket, "w6-cli-clean")
    assert rc == audit.EXIT_OK, f"consistent world did not exit 0: {payload}"
    assert payload["status"] == "consistent"
    assert payload["ok"] is True
    assert payload["dangling_node_ids"] == []


def test_audit_cli_exits_7_on_dangling_stamp_fault(tmp_path):
    """The real audit CLI subprocess, not just classify_trace, goes RED (exit 7).

    Drives ``scripts/audit_replay_completeness.py`` as the journey's
    ``companion-audit`` step does: a real subprocess over a real bucket
    where the stamped ``context_node_id`` is absent from its own trace's
    companion even though the event mirror has it — the documented failure
    mode (exit code 7) with the dangling reason present in the JSON output.
    """
    bucket = _build_cli_bucket(tmp_path, trace_id="w6-cli-fault", companion_has_node=False)
    rc, payload = _run_audit_cli(bucket, "w6-cli-fault")
    assert rc == audit.EXIT_INCONSISTENT == 7, f"fault did not drive exit 7: {payload}"
    assert payload["status"] == "inconsistent"
    assert payload["ok"] is False
    assert "node-A" in payload["dangling_node_ids"]
    assert any("dangling" in r for r in payload["reasons"]), payload["reasons"]
