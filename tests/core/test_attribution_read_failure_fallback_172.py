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


class _FakeEvent:
    """Minimal dumpable stand-in for a TrailEvent (the projection writes
    ``event.model_dump(mode=...)`` into the trail companion)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, *args, **kwargs):
        return dict(self.__dict__)


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


def test_events_for_export_loop_signals_read_failure(monkeypatch, tmp_path):
    """The shared-loop reader returns ``([], False)`` when the underlying
    ``read_events`` raises, so repair/rebuild callers can mark the empty event set
    NON-authoritative (#172 review fix — a failed shared read must not be treated
    as authoritative zero anchors)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def _boom(*_a, **_k):
        raise RuntimeError("transient log read failure")

    monkeypatch.setattr("opentraces.core.trails.read_events", _boom)
    events, read_ok = be._events_for_export_loop(repo)
    assert events == []
    assert read_ok is False


def test_project_per_trace_verbatim_when_shared_read_not_authoritative(
    tmp_path, monkeypatch
):
    """The exact caller path codex flagged: trace ids are known but the shared
    read FAILED (``events=[]`` with ``events_authoritative=False``). The live
    projection must write the record's ``found=True`` anchor VERBATIM, never
    de-attribute it from an authoritative-looking empty event set."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, tid, sha = "proj-slug-172", "trace-shared-fail-172", "c" * 40
    rec = TraceRecord(
        trace_id=tid,
        session_id="sess-172",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        steps=[Step(step_index=1, role="user", content="task")],
        patches=[
            Patch(
                patch_id="pid-shared",
                file_path="a.py",
                anchor=GitAnchor(last_searched_at="t", found=True, commit_sha=sha),
            )
        ],
    )
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    # Simulate the repair loop passing a FAILED shared read: events=[] +
    # events_authoritative=False. (read_events_for_trace is never consulted here
    # because events is not None; the mirror is empty in this relocated bucket.)
    be.project_per_trace_exports(
        repo,
        project_slug=slug,
        trace_id=tid,
        record=rec,
        events=[],
        events_authoritative=False,
    )
    tj = paths.bucket_dir() / "traces" / "v1" / slug / tid / "trace.json"
    anchor = (json.loads(tj.read_text())["patches"][0].get("anchor")) or {}
    assert anchor.get("found") is True, "anchor de-attributed on a failed shared read"
    assert anchor.get("commit_sha") == sha


def test_project_per_trace_derives_when_shared_read_authoritative(
    tmp_path, monkeypatch
):
    """Control: with an AUTHORITATIVE shared read (events_authoritative=True) that
    genuinely holds no git_anchor_created for the trace, an unbacked found=True
    anchor IS correctly de-attributed (proves the fallback is not a blanket
    'never de-attribute')."""
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / ".opentraces")
    slug, tid, sha = "proj-slug-172", "trace-shared-ok-172", "d" * 40
    rec = TraceRecord(
        trace_id=tid,
        session_id="sess-172",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        steps=[Step(step_index=1, role="user", content="task")],
        patches=[
            Patch(
                patch_id="pid-phantom",
                file_path="a.py",
                anchor=GitAnchor(last_searched_at="t", found=True, commit_sha=sha),
            )
        ],
    )
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    # Authoritative shared read that contains events for the trace but NO
    # git_anchor_created for pid-phantom -> single-source de-attributes it.
    other = _FakeEvent(
        event_type="trace_patch_created",
        trace_id=tid,
        event_sequence=1,
        event_time="t",
        payload={"trace_id": tid},
    )
    be.project_per_trace_exports(
        repo,
        project_slug=slug,
        trace_id=tid,
        record=rec,
        events=[other],
        events_authoritative=True,
    )
    tj = paths.bucket_dir() / "traces" / "v1" / slug / tid / "trace.json"
    anchor = json.loads(tj.read_text())["patches"][0].get("anchor")
    assert anchor is None or anchor.get("found") is False, (
        "unbacked anchor should be de-attributed on an authoritative read"
    )
