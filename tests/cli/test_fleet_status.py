"""Hermetic tests for the fleet bucket status dashboard (issue #161).

Two flavours:

* **Crafted-manifest units** — write a ``bucket/manifest.json`` with known per-row
  status accelerators and assert the safety gate directly. This is the fast,
  deterministic core; it never runs the capture pipeline.
* **Real-bucket L6** — seed a small bucket with ``write_trace_record`` and prove
  the trace count + security version reported by ``opentraces status`` equal
  ``bucket manifest --json`` and ``bucket security status --json`` on the SAME
  bucket (the cross-sibling self-consistency the agent-contract lint's L6 asserts).

Every test runs under the conftest HOME isolation, so ``paths.bucket_dir()``
points at the tmp bucket.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core import paths
from opentraces.core.bucket_layout import bucket_manifest_path
from opentraces.core.bucket_models import BUCKET_MANIFEST_SCHEMA
from opentraces.core.fleet_status import (
    READY_MIN_SCANNED_FRACTION,
    build_fleet_status,
)
from opentraces.security.version import SECURITY_VERSION


# --------------------------------------------------------------------------
# crafted-manifest helpers
# --------------------------------------------------------------------------
def _row(project: str, trace_id: str, status: dict | None) -> dict:
    row = {"project_slug": project, "trace_id": trace_id}
    if status is not None:
        row["status"] = status
    return row


def _scanned_clean(project: str, trace_id: str) -> dict:
    return _row(project, trace_id, {
        "known": True, "syncable": True, "security_stale": False,
        "privacy_off": False, "written_at": "2026-01-01T00:00:00Z",
    })


def _scanned_unfiltered(project: str, trace_id: str) -> dict:
    return _row(project, trace_id, {
        "known": True, "syncable": False, "security_stale": False,
        "privacy_off": False, "written_at": "2026-01-01T00:00:00Z",
    })


def _unscanned(project: str, trace_id: str) -> dict:
    # No object envelope was ever recorded for this row: known=False.
    return _row(project, trace_id, {
        "known": False, "syncable": None, "security_stale": None,
        "privacy_off": None, "written_at": None,
    })


def _write_manifest(rows: list[dict], *, security_version: str = SECURITY_VERSION) -> None:
    manifest = {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "bucket_root": "",
        "root": str(paths.bucket_dir()),
        "generated_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "security_version": security_version,
        "traces": rows,
        "trace_records": {"object_count": len(rows)},
        "events_v1": {},
        "trail": {},
        "sync": {},
        "raw_sources": {},
        "trail_events": {},
        "context_trees": {},
    }
    path = bucket_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")


# --------------------------------------------------------------------------
# (a) sparse-scanned bucket -> ready != true, no green verdict, unscanned > 0
# --------------------------------------------------------------------------
def test_sparse_scanned_bucket_is_never_ready():
    _write_manifest([
        _scanned_clean("demo", "t0"),
        _scanned_clean("demo", "t1"),
        _unscanned("demo", "t2"),
        _unscanned("demo", "t3"),
    ])
    s = build_fleet_status()
    assert s["store"]["trace_count"] == 4
    assert s["safety"]["scanned_count"] == 2
    assert s["safety"]["unscanned_count"] == 2
    # The load-bearing gate: green is impossible while scanned < total.
    assert s["ready"] is False
    assert s["safety"]["verdict"] == "not_cleared"
    assert "unscanned_records" in s["sync"]["blocked_reasons"]
    assert s["sync"]["eligible"] is False
    # Freshness must own up to the partial scan.
    assert s["freshness"]["understates_safety"] is True


def test_scanned_but_unfiltered_is_not_cleared_though_fully_scanned():
    # Every row is scanned (known) but one holds unresolved redactable content.
    _write_manifest([
        _scanned_clean("demo", "t0"),
        _scanned_unfiltered("demo", "t1"),
    ])
    s = build_fleet_status()
    assert s["safety"]["scanned_count"] == 2  # both assessed
    assert s["safety"]["unscanned_count"] == 0
    assert s["safety"]["not_cleared_count"] == 1  # the unfiltered one
    assert s["ready"] is False
    assert s["safety"]["verdict"] == "not_cleared"
    assert "unfiltered_records" in s["sync"]["blocked_reasons"]


# --------------------------------------------------------------------------
# (b) fully-scanned clean bucket -> ready may be true, verdict clear
# --------------------------------------------------------------------------
def test_fully_scanned_clean_bucket_is_ready():
    _write_manifest([
        _scanned_clean("demo", "t0"),
        _scanned_clean("demo", "t1"),
        _scanned_clean("alpha", "t2"),
    ])
    s = build_fleet_status()
    assert s["store"]["trace_count"] == 3
    assert s["store"]["project_count"] == 2
    assert s["safety"]["scanned_count"] == 3
    assert s["safety"]["unscanned_count"] == 0
    assert s["safety"]["not_cleared_count"] == 0
    assert s["safety"]["verdict"] == "clear"
    assert s["ready"] is True
    assert s["sync"]["eligible"] is True
    assert s["sync"]["blocked_reasons"] == []
    # fleet rollup biggest-first
    projects = [p["project"] for p in s["fleet"]["by_project"]]
    assert projects[0] == "demo"  # 2 traces > alpha's 1


def test_ready_threshold_is_strict_full_scan():
    # Documented contract: readiness requires the whole bucket scanned.
    assert READY_MIN_SCANNED_FRACTION == 1.0


# --------------------------------------------------------------------------
# (c) --short contract: red on unscanned>0 even when unfiltered==0
# --------------------------------------------------------------------------
def test_short_line_not_clean_when_unscanned_even_with_zero_unfiltered():
    _write_manifest([
        _scanned_clean("demo", "t0"),  # unfiltered == 0 across the bucket
        _unscanned("demo", "t1"),
    ])
    runner = CliRunner()
    res = runner.invoke(main, ["status", "--short"])
    assert res.exit_code == 0, res.output
    line = res.output.strip()
    # Clean signal must NOT fire while a trace is unscanned.
    assert "status=not-cleared" in line
    assert "unscanned=1" in line
    # And there are zero unfiltered rows — proving the signal is not unfiltered-only.
    s = build_fleet_status()
    assert s["safety"]["scanned_count"] == 1
    assert s["safety"]["unscanned_count"] == 1
    assert "unfiltered_records" not in s["sync"]["blocked_reasons"]


def test_short_line_clean_only_when_fully_scanned_and_syncable():
    _write_manifest([_scanned_clean("demo", "t0"), _scanned_clean("demo", "t1")])
    runner = CliRunner()
    res = runner.invoke(main, ["status", "--short"])
    assert res.exit_code == 0, res.output
    assert "status=clean" in res.output
    assert "stale=no" in res.output


# --------------------------------------------------------------------------
# (d) O(1) discipline — never calls the full-scan primitive
# --------------------------------------------------------------------------
def test_status_never_triggers_full_scan(monkeypatch):
    _write_manifest([_scanned_clean("demo", "t0")])

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("full-scan primitive called on the O(1) status path")

    import opentraces.core.bucket_store as bs

    monkeypatch.setattr(bs, "bucket_status", _boom)
    monkeypatch.setattr(bs, "bucket_manifest", _boom)

    s = build_fleet_status()
    assert s["store"]["trace_count"] == 1
    assert s["ready"] is True


def test_absent_manifest_is_clean_empty_not_a_full_scan(monkeypatch):
    import opentraces.core.bucket_store as bs

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("full-scan primitive called on absent manifest")

    monkeypatch.setattr(bs, "bucket_status", _boom)
    monkeypatch.setattr(bs, "bucket_manifest", _boom)

    s = build_fleet_status()
    assert s["ok"] is True
    assert s["store"]["trace_count"] == 0
    assert s["safety"]["verdict"] == "empty"
    assert s["freshness"]["stale"] is False


# --------------------------------------------------------------------------
# --project scoping
# --------------------------------------------------------------------------
def test_project_scope_counts_only_that_project():
    _write_manifest([
        _scanned_clean("demo", "t0"),
        _unscanned("demo", "t1"),
        _scanned_clean("alpha", "t2"),
    ])
    s = build_fleet_status(project="alpha")
    assert s["scope_project"] == "alpha"
    assert s["store"]["trace_count"] == 1
    assert s["store"]["project_count"] == 1
    assert s["safety"]["unscanned_count"] == 0
    assert s["ready"] is True


# --------------------------------------------------------------------------
# degraded read model
# --------------------------------------------------------------------------
def test_too_large_manifest_is_degraded_not_ready():
    # Force the byte cap tiny so the crafted manifest reads as too-large.
    monkey = pytest.MonkeyPatch()
    monkey.setenv("OPENTRACES_DOCTOR_BUCKET_MANIFEST_MAX_BYTES", "1024")
    try:
        _write_manifest([_scanned_clean("demo", f"t{i}") for i in range(200)])
        s = build_fleet_status()
    finally:
        monkey.undo()
    assert s["ok"] is False
    assert s["ready"] is False
    assert s["freshness"]["source"] == "degraded"
    assert "bucket repair" in " ".join(s["next_steps"])


# --------------------------------------------------------------------------
# L6 cross-sibling self-consistency on a REAL seeded bucket (issue #161 item 6)
# --------------------------------------------------------------------------
@pytest.fixture
def real_seeded_bucket():
    from opentraces.core.bucket_store import write_trace_record
    from opentraces_schema import Agent, Step, TraceRecord

    for i in range(3):
        rec = TraceRecord(
            trace_id=f"fs-trace-{i}",
            session_id=f"fs-session-{i}",
            agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
            task={"description": "seed"},
            steps=[Step(step_index=1, role="user", content="seed")],
            outcome={"success": True, "committed": False},
        )
        rec.security.scanned = True
        rec.security.classifier_version = SECURITY_VERSION
        write_trace_record(
            rec,
            project_slug="fs-demo",
            source_layer="canonical",
            legacy_mirror=False,
            privacy_tier="medium",
        )
    # Materialize the persisted manifest so the O(1) status reads real rows.
    from opentraces.core.bucket_store import bucket_manifest
    bucket_manifest(write=True, heal=True)


def _json_invoke(argv: list[str]) -> dict:
    runner = CliRunner()
    res = runner.invoke(main, ["--json", *argv])
    assert res.exit_code == 0, res.output
    return json.loads(res.output.strip())


def test_l6_status_agrees_with_manifest_and_security(real_seeded_bucket):
    status_doc = _json_invoke(["status"])
    manifest_doc = _json_invoke(["bucket", "manifest"])
    security_doc = _json_invoke(["bucket", "security", "status"])

    # Trace count agreement.
    assert status_doc["store"]["trace_count"] == 3
    assert manifest_doc["manifest"]["trace_records"]["object_count"] == 3
    assert security_doc["security"]["total"] == 3

    # Security version agreement (read from code, not a stale doc).
    assert status_doc["security_version"] == SECURITY_VERSION
    assert manifest_doc["manifest"]["security_version"] == SECURITY_VERSION
    assert security_doc["security"]["security_version"] == SECURITY_VERSION
