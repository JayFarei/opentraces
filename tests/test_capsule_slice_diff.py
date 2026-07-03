"""Slice-scoped diff (#156, U3, Part 1) — the ``diff_trust`` factor.

Lands the ``slice_diff`` block: the diff reproducing THIS slice's burst commit
(not the whole session), a ``diff_trust`` ordinal feeding U1's clamp, and the
patches-empty Tier-2 fix (an empty patches list must never masquerade as
"nothing changed"). Replaces the ``_repo_pin`` whole-session ``changed_files``
over-capture.

All SYNTHETIC + RED-first. The discipline (from
``tests/core/test_size_independent_reads_137.py``): don't trust the projection's
self-report — APPLY the carried patch to a base tree and re-observe the real
bytes. ``sd["diff_trust"]`` is asserted, but the load-bearing check is that
``git apply`` of ``sd["format_patch"]`` materializes ONLY the slice's file.

STOP-CONDITION: a slice mapping to >1 commit is ``partial`` (D3), a snapshot-only
slice is ``unanchored`` (D4) — legitimate non-``exact`` outcomes, not fails.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# RED-first: the net-new symbols must exist.
# --------------------------------------------------------------------------- #


def test_net_new_slice_diff_symbols_exist():
    from opentraces.core.capsule import export

    assert hasattr(export, "build_slice_diff"), (
        "net-new symbol `build_slice_diff` must exist (RED-first spec)"
    )
    assert hasattr(export, "SLICE_DIFF_SCHEMA_VERSION")
    assert callable(export.build_slice_diff)


# --------------------------------------------------------------------------- #
# Git + bucket fixtures
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@opentraces.test")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")


def _commit(path: Path, filename: str, content: str, message: str) -> str:
    (path / filename).write_text(content, encoding="utf-8")
    _git(path, "add", filename)
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def paths_env(tmp_path, monkeypatch):
    from opentraces.core import paths

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    return tmp_path


def _mark_project(project_dir: Path) -> None:
    from opentraces.core import paths

    (project_dir / paths.MARKER_FILENAME).write_text(
        json.dumps({"project_id": "test-project-id"}), encoding="utf-8"
    )


def _seed(project_dir: Path, trace_id: str, steps: list[dict], patches: list[dict], **extra):
    from opentraces_schema import TraceRecord

    from opentraces.core.bucket_trace_records import write_trace_record
    from opentraces.core.config import get_project_dir

    slug = get_project_dir(project_dir).name
    body = {
        "trace_id": trace_id,
        "session_id": trace_id,
        "agent": {"name": "codex", "version": "1.0", "model": "gpt"},
        "task": {"description": "fix the flaky parser", "base_commit": "abc123def456"},
        "environment": {"language_ecosystem": ["python"]},
        "context_tree_summary": {"capture_method": "transcript_reconstruction"},
        "steps": steps,
        "patches": patches,
    }
    body.update(extra)
    rec = TraceRecord.model_validate(body)
    write_trace_record(rec, project_slug=slug, source_layer="test")
    return slug


def _anchor(sha: str) -> dict:
    return {"last_searched_at": "2026-01-01T00:00:00Z", "found": True, "commit_sha": sha}


@pytest.fixture
def three_commit_project(paths_env):
    """A repo with C1 (other.py), C2 (target.py — the slice's burst), C3 (later.py),
    and a seeded record whose slice at step 6 maps ONLY to C2."""

    project_dir = paths_env / "proj3"
    _init_repo(project_dir)
    c1 = _commit(project_dir, "other.py", "print('other')\n", "C1 unrelated")
    c2 = _commit(project_dir, "target.py", "print('target')\n", "C2 the slice burst")
    c3 = _commit(project_dir, "later.py", "print('later')\n", "C3 later")
    _mark_project(project_dir)

    tid = "aaaa1111-0000-0000-0000-000000000000"
    steps = [{"step_index": i, "role": "agent", "content": f"step {i}"} for i in range(13)]
    patches = [
        {"patch_id": "p-other", "file_path": "other.py", "step_index": 0, "anchor": _anchor(c1)},
        {"patch_id": "p-target", "file_path": "target.py", "step_index": 6, "anchor": _anchor(c2)},
        {"patch_id": "p-later", "file_path": "later.py", "step_index": 12, "anchor": _anchor(c3)},
    ]
    _seed(project_dir, tid, steps, patches)
    return project_dir, tid, {"c1": c1, "c2": c2, "c3": c3}


# --------------------------------------------------------------------------- #
# 2a — the carried diff reproduces the SLICE'S burst commit, not the whole
# session. APPLY it and re-observe the real bytes.
# --------------------------------------------------------------------------- #


def test_2a_slice_diff_reproduces_only_the_slice_burst_commit(three_commit_project, tmp_path):
    from opentraces.core.capsule.export import export_capsule

    project_dir, tid, _shas = three_commit_project
    cap = export_capsule(project_dir=project_dir, trace_id=tid, step_index=6)

    sd = cap["slice_diff"]
    assert sd["diff_trust"] == "exact"
    assert sd["changed_files"] == ["target.py"]
    assert sd["format_patch"], "D1 must carry a real format-patch"

    # Apply the carried patch to a fresh empty tree and re-observe: C2 ADDED
    # target.py (pure addition), so ONLY target.py may materialize; later.py
    # (from C3, outside the slice) must NOT exist.
    apply_dir = tmp_path / "apply"
    apply_dir.mkdir()
    _git(apply_dir, "init", "-q")
    patch_file = apply_dir / "slice.patch"
    patch_file.write_text(sd["format_patch"], encoding="utf-8")
    _git(apply_dir, "apply", "--whitespace=nowarn", str(patch_file))

    assert (apply_dir / "target.py").exists(), "the slice's file must materialize"
    assert not (apply_dir / "later.py").exists(), "a later-commit file must NOT leak in"
    assert not (apply_dir / "other.py").exists(), "an earlier-commit file must NOT leak in"


# --------------------------------------------------------------------------- #
# 2b — a whole-session over-capture is CAUGHT as a fail. The record carries
# patches for other.py + later.py; the slice-scoped diff must exclude them.
# --------------------------------------------------------------------------- #


def test_2b_whole_session_over_capture_is_rejected(three_commit_project):
    from opentraces.core.capsule.export import export_capsule

    project_dir, tid, _shas = three_commit_project
    cap = export_capsule(project_dir=project_dir, trace_id=tid, step_index=6)

    sd = cap["slice_diff"]
    # The over-capture control: files from commits OUTSIDE the slice's range are
    # rejected. This is the test that fails against the old whole-session pin.
    assert set(sd["changed_files"]) <= {"target.py"}
    assert "other.py" not in sd["changed_files"]
    assert "later.py" not in sd["changed_files"]

    # The over-capture cure landed on the pin too (no longer whole-session).
    assert cap["repo_pin"]["changed_files"] == ["target.py"]


# --------------------------------------------------------------------------- #
# 2c — patches-empty Tier-2 does NOT masquerade as "nothing changed". The
# integration/watcher path: record.patches == [] but the burst really committed.
# --------------------------------------------------------------------------- #


@pytest.fixture
def watcher_project(paths_env):
    project_dir = paths_env / "projwatch"
    _init_repo(project_dir)
    c = _commit(project_dir, "watched.py", "print('watched')\n", "watcher commit")
    _mark_project(project_dir)

    tid = "bbbb2222-0000-0000-0000-000000000000"
    # A commit observed at step 6 via the hook trail (git_head transition), with
    # NO patch rows — the exact patches-empty Tier-2 shape.
    steps = []
    for i in range(13):
        step = {"step_index": i, "role": "agent", "content": f"step {i}"}
        if i == 6:
            step["tool_calls"] = [
                {"tool_call_id": "tc_commit", "tool_name": "bash", "input": {"command": "git commit -m fix"}}
            ]
        steps.append(step)
    metadata = {"hook_post_tool_use": {"tc_commit": {"trail": {"git_head": {"hex": c}}}}}
    _seed(project_dir, tid, steps, patches=[], metadata=metadata)
    return project_dir, tid, c


def test_2c_patches_empty_tier2_derives_changed_files_from_commit(watcher_project):
    from opentraces.core.capsule.export import export_capsule

    project_dir, tid, _c = watcher_project
    cap = export_capsule(project_dir=project_dir, trace_id=tid, step_index=6)

    sd = cap["slice_diff"]
    # Empty patches + a real burst commit -> derive from `git show --name-only`,
    # NOT [] (which would masquerade as "nothing changed").
    assert sd["changed_files"] == ["watched.py"]
    assert sd["changed_files"] != []
    assert sd["diff_trust"] == "exact"
    assert "changed_files_from_commit" in sd  # the honesty marker


# --------------------------------------------------------------------------- #
# STOP-CONDITION coverage: D4 (no anchor at all) honestly declares unanchored.
# --------------------------------------------------------------------------- #


def test_d4_no_anchor_declares_unanchored_and_stops(three_commit_project):
    # Directly exercise the builder for a slice range with no patches and no
    # commit index: it must declare unanchored (position 0), never synthesize.
    from opentraces.core.capsule.export import build_slice_diff
    from opentraces.core.bucket_store import read_trace_record_object, trace_record_path
    from opentraces.core.config import get_project_dir

    project_dir, tid, _shas = three_commit_project
    slug = get_project_dir(project_dir).name
    record = read_trace_record_object(trace_record_path(slug, tid)).record

    # Steps 8..11 hold no patch rows (patches sit at 0/6/12) and no commit index.
    sd = build_slice_diff(project_dir, record, start_step=8, end_step=11)
    assert sd["diff_trust"] == "unanchored"
    assert sd["changed_files"] == []
    assert sd["format_patch"] is None
