"""#172 review fix — a canonical-event read that RAISES (transient / corrupt ref)
must NOT be mistaken for "resolved with zero anchors".

If it were, both the manifest sweep (via ``canonical_anchor_maps`` ->
``anchored_count = 0``) and the live per-trace projection (via
``project_per_trace_exports`` -> empty ``patch_anchors`` -> ``found=False``) would
DE-ATTRIBUTE valid anchors and move ``bucket_digest`` on a routine
``bucket_manifest(write=True)`` — a data-destructive, digest-moving outcome that
violates the #169/#172 constraints. The fix marks a slug resolved / derives
anchors ONLY after a successful authoritative read; a raised read falls back to
the record-derived value verbatim.

Hermetic: no live bucket, no live event log.
"""
from __future__ import annotations

import json

from opentraces_schema import Agent, GitAnchor, Patch, Step, TraceRecord

# Bind event_log before importing the bucket modules (re-entrant-import quirk).
from opentraces.core.trails import event_log as evlog
from opentraces.core import bucket_envelope as be
from opentraces.core import paths


def _opt_in(monkeypatch, tmp_path, slug="proj-slug-172"):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    monkeypatch.setattr(be, "_iter_opted_in_projects", lambda: [(repo, slug)])
    return repo, slug


def test_canonical_anchor_maps_unresolved_when_read_raises(tmp_path, monkeypatch):
    """A raised ``read_events_scoped`` leaves the slug UNRESOLVED, so callers use
    the record-derived fallback instead of zeroing anchored_count / de-attributing."""
    _repo, slug = _opt_in(monkeypatch, tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("corrupt event ref")

    monkeypatch.setattr(evlog, "read_events_scoped", _boom)
    anchors, distinct, resolved = be.canonical_anchor_maps({slug})

    assert slug not in resolved, "read failure must NOT resolve the slug"
    assert anchors == {}
    assert distinct == {}


def test_canonical_anchor_maps_resolved_on_genuine_empty_read(tmp_path, monkeypatch):
    """A successful read that returns zero anchor events DOES resolve the slug —
    an anchor-free project legitimately gets anchored_count 0 (not the fallback)."""
    _repo, slug = _opt_in(monkeypatch, tmp_path)
    monkeypatch.setattr(evlog, "read_events_scoped", lambda *_a, **_k: [])

    _anchors, _distinct, resolved = be.canonical_anchor_maps({slug})
    assert slug in resolved


def test_project_per_trace_writes_record_verbatim_when_read_raises(
    tmp_path, monkeypatch
):
    """The live per-trace projection must write the record's anchors VERBATIM when
    the canonical read raises — never strip a valid ``found=True`` anchor on a
    transient failure."""
    # Relocate the whole bucket into tmp so the live ~/.opentraces is untouched.
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, tid, sha = "proj-slug-172", "trace-verbatim-172", "a" * 40

    rec = TraceRecord(
        trace_id=tid,
        session_id="sess-172",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        steps=[Step(step_index=1, role="user", content="task")],
        patches=[
            Patch(
                patch_id="pid-verbatim",
                file_path="a.py",
                anchor=GitAnchor(last_searched_at="t", found=True, commit_sha=sha),
            )
        ],
    )

    def _boom(*_a, **_k):
        raise RuntimeError("transient read failure")

    monkeypatch.setattr(evlog, "read_events_for_trace", _boom)

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    be.project_per_trace_exports(
        repo, project_slug=slug, trace_id=tid, record=rec
    )

    tj = paths.bucket_dir() / "traces" / "v1" / slug / tid / "trace.json"
    anchor = (json.loads(tj.read_text())["patches"][0].get("anchor")) or {}
    assert anchor.get("found") is True, "valid anchor de-attributed on read failure"
    assert anchor.get("commit_sha") == sha


def test_per_trace_summary_patch_count_vs_anchored_count_semantics(
    tmp_path, monkeypatch
):
    """#172 review recommendation — make the digest-material count semantics
    explicit for a RE-EXPANDED surface. A single logical patch anchored across two
    commits surfaces as TWO rows (``patch_count == 2``) but contributes ONE to the
    DISTINCT ``anchored_count``. So the digest is byte-stable for correct
    single-commit traces and only grows for genuine amend chains (0/361 on the
    live bucket today), never inflating the anchored count by surface-row count."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    from opentraces.core.bucket_layout import traces_v1_dir, trace_v1_json_path

    slug, tid = "proj-slug-172", "trace-expanded-172"
    traces_v1_dir(slug, tid).mkdir(parents=True, exist_ok=True)
    rec = TraceRecord(
        trace_id=tid,
        session_id="sess-172",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        steps=[Step(step_index=1, role="user", content="task")],
        patches=[
            Patch(
                patch_id="pid-amend",
                file_path="a.py",
                anchor=GitAnchor(last_searched_at="t", found=True, commit_sha="a" * 40),
            ),
            Patch(
                patch_id="pid-amend",  # SAME logical patch, second anchor commit
                file_path="a.py",
                anchor=GitAnchor(last_searched_at="t", found=True, commit_sha="b" * 40),
            ),
        ],
    )
    trace_v1_json_path(slug, tid).write_text(json.dumps(rec.model_dump(mode="json")))

    summary = be._per_trace_v2_summary(slug, tid, rec)["summary"]
    assert summary["patch_count"] == 2, "patch_count is the surface-row count"
    assert summary["anchored_count"] == 1, "anchored_count is the DISTINCT patch id"
