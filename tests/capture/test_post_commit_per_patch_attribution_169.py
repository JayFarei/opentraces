"""Epic #169 (B-attr) — attribution single source of truth.

Two defects this guards against:

* **Over-attribution.** ``correlate`` matches a trace to a commit at the TRACE
  level; the post-commit writer must NOT fan that one match across patches whose
  own file the commit never touched. ``_apply_anchor_to_patches`` now gates each
  patch on per-patch file membership in the commit's changed-file set.
* **Creation gap.** A patch must be ``anchor.found=True`` only when a backing
  ``git_anchor_created`` event exists for it. ``rederive_bucket_anchors`` rebuilds
  the persisted ``trace.json`` anchors + manifest ``anchored_count`` from the
  canonical events, so any standalone stamp with no backing event is
  de-attributed.

These are hermetic unit tests (no live bucket, no live event log) sibling to the
grader-owned integration probes under ``tests/integration/``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from opentraces_schema import Agent, Patch, Step, TraceRecord

# Bind the event_log submodule FIRST: importing post_commit / bucket_maintenance
# ahead of it leaves ``opentraces.core.trails.event_log`` unbound for later
# attribute resolution (a re-entrant-import quirk observed in collection).
from opentraces.core.trails import event_log as evlog
from opentraces.capture.git.post_commit import _apply_anchor_to_patches
from opentraces.core.bucket_maintenance import rederive_bucket_anchors


def _trace_with_patches(*file_paths: str) -> TraceRecord:
    patches = [
        Patch(patch_id=f"pid-{i}-{fp}", file_path=fp)
        for i, fp in enumerate(file_paths)
    ]
    return TraceRecord(
        trace_id="trace-169-attr",
        session_id="sess-169",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        steps=[Step(step_index=1, role="user", content="task")],
        patches=patches,
    )


def test_apply_anchor_gates_each_patch_on_file_membership():
    """Over-attribution cure: a trace-level ``tool_emitted`` match anchors ONLY
    the patches whose own file the commit changed; a patch on an untouched file
    is recorded ``found=False``, never fanned to the commit."""
    trace = _trace_with_patches(
        "src/touched.py",  # in the commit
        "src/untouched.py",  # NOT in the commit
    )
    # The commit's changed-file set == the parsed hunk keys.
    hunks = {"src/touched.py": [{"added_text": "x = 1\n"}]}

    any_found = _apply_anchor_to_patches(
        trace,
        "tool_emitted",
        "deadbeef" * 5,
        now_iso="2026-06-30T00:00:00+00:00",
        hunks=hunks,
    )

    by_file = {p.file_path: p for p in trace.patches}
    assert any_found is True
    touched = by_file["src/touched.py"].anchor
    untouched = by_file["src/untouched.py"].anchor
    assert touched is not None and touched.found is True
    assert touched.commit_sha == "deadbeef" * 5
    # The headline #139 symptom: the untouched-file patch must NOT be anchored
    # to a commit that never changed its file.
    assert untouched is not None and untouched.found is False
    assert untouched.commit_sha is None


def test_apply_anchor_legacy_behaviour_when_hunks_unknown():
    """``hunks=None`` keeps the pre-#169 trace-level behaviour for callers that
    have not threaded the changed-file set (back-compat guard)."""
    trace = _trace_with_patches("a.py", "b.py")
    _apply_anchor_to_patches(
        trace, "tool_emitted", "c0ffee" * 6, now_iso="t", hunks=None,
    )
    assert all(p.anchor is not None and p.anchor.found for p in trace.patches)


# --------------------------------------------------------------------------- #
# found=True implies a backing git_anchor_created event (re-derive invariant).
# --------------------------------------------------------------------------- #


def _fake_anchor_event(trace_id: str, patch_id: str, commit_hex: str, seq: int):
    return SimpleNamespace(
        event_type="git_anchor_created",
        trace_id=trace_id,
        event_sequence=seq,
        event_time="2026-06-30T00:00:00+00:00",
        payload={
            "trace_patch_id": patch_id,
            "commit_id": {"algo": "sha1", "hex": commit_hex},
            "path": "src/backed.py",
            "evidence_tier": "exact_range_hash",
            "evidence_firmness": "firm",
        },
    )


def test_rederive_found_implies_backing_event(tmp_path, monkeypatch):
    """``rederive_bucket_anchors`` makes the persisted surface derive from the
    canonical events: a patch with a backing ``git_anchor_created`` event keeps
    ``found=True`` (re-pointed to the event's commit), and a patch with NO
    backing event is DE-ATTRIBUTED — even though both were stamped ``found=True``
    by the over-attribution bug. The manifest ``anchored_count`` becomes the
    DISTINCT backed-patch count."""

    slug = "proj-169"
    trace_id = "trace-rederive-169"
    backed_pid = "pid-backed"
    phantom_pid = "pid-phantom"
    real_commit = "a" * 40
    wrong_commit = "f" * 40

    # A bucket COPY layout: traces/v1/<slug>/<trace>/trace.json + manifest.json.
    root = tmp_path / "bucket-copy"
    trace_dir = root / "traces" / "v1" / slug / trace_id
    trace_dir.mkdir(parents=True)
    # BOTH patches stamped found=True at a wrong commit (the over-attribution /
    # creation-gap state the bug produced).
    doc = {
        "trace_id": trace_id,
        "patches": [
            {
                "patch_id": backed_pid,
                "file_path": "src/backed.py",
                "anchor": {
                    "last_searched_at": "t",
                    "found": True,
                    "commit_sha": wrong_commit,
                    "evidence_tier": "exact_range_hash",
                },
            },
            {
                "patch_id": phantom_pid,
                "file_path": "src/phantom.py",
                "anchor": {
                    "last_searched_at": "t",
                    "found": True,
                    "commit_sha": wrong_commit,
                    "evidence_tier": "exact_range_hash",
                },
            },
        ],
    }
    (trace_dir / "trace.json").write_text(json.dumps(doc))
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bucket.manifest.v2",
                "traces": [
                    {
                        "trace_id": trace_id,
                        "project_slug": slug,
                        "summary": {"anchored_count": 2},
                    }
                ],
            }
        )
    )

    # Canonical truth: ONLY ``backed_pid`` has a git_anchor_created event.
    dummy_repo = tmp_path / "proj"
    dummy_repo.mkdir()
    monkeypatch.setattr(
        "opentraces.core.bucket_maintenance._iter_opted_in_projects",
        lambda: [(dummy_repo, slug)],
    )
    monkeypatch.setattr(
        evlog,
        "read_events_scoped",
        lambda repo, **kw: [
            _fake_anchor_event(trace_id, backed_pid, real_commit, 10),
        ],
    )

    result = rederive_bucket_anchors(root)
    assert result["patches_deattributed"] == 1
    assert result["patches_anchored"] == 1

    out = json.loads((trace_dir / "trace.json").read_text())
    by_pid = {p["patch_id"]: p for p in out["patches"]}
    # Backed patch: found=True, re-pointed to the EVENT's commit.
    assert by_pid[backed_pid]["anchor"]["found"] is True
    assert by_pid[backed_pid]["anchor"]["commit_sha"] == real_commit
    # Phantom patch: de-attributed (no backing event).
    assert by_pid[phantom_pid]["anchor"]["found"] is False

    # Manifest anchored_count == DISTINCT backed-patch count (1, not 2).
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["traces"][0]["summary"]["anchored_count"] == 1


def test_rederive_surfaces_one_row_per_anchor_commit(tmp_path, monkeypatch):
    """A patch that genuinely lands in several commits (amend / repeated content)
    is surfaced as one ``trace.json`` row per distinct anchor commit, so the
    lineage surface set EXACTLY equals the canonical anchor set — while the
    manifest ``anchored_count`` still counts the patch once (DISTINCT)."""

    slug = "proj-169-multi"
    trace_id = "trace-multi-169"
    pid = "pid-multi"
    c1, c2 = "1" * 40, "2" * 40

    root = tmp_path / "bucket-copy"
    trace_dir = root / "traces" / "v1" / slug / trace_id
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.json").write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "patches": [{"patch_id": pid, "file_path": "src/m.py"}],
            }
        )
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bucket.manifest.v2",
                "traces": [
                    {
                        "trace_id": trace_id,
                        "project_slug": slug,
                        "summary": {"anchored_count": 0},
                    }
                ],
            }
        )
    )

    dummy_repo = tmp_path / "proj"
    dummy_repo.mkdir()
    monkeypatch.setattr(
        "opentraces.core.bucket_maintenance._iter_opted_in_projects",
        lambda: [(dummy_repo, slug)],
    )
    monkeypatch.setattr(
        evlog,
        "read_events_scoped",
        lambda repo, **kw: [
            _fake_anchor_event(trace_id, pid, c1, 5),
            _fake_anchor_event(trace_id, pid, c2, 9),
        ],
    )

    rederive_bucket_anchors(root)

    out = json.loads((trace_dir / "trace.json").read_text())
    surface = {
        (p["patch_id"], p["anchor"]["commit_sha"])
        for p in out["patches"]
        if (p.get("anchor") or {}).get("found")
    }
    assert surface == {(pid, c1), (pid, c2)}
    manifest = json.loads((root / "manifest.json").read_text())
    # DISTINCT patch count, not surface-row count.
    assert manifest["traces"][0]["summary"]["anchored_count"] == 1
