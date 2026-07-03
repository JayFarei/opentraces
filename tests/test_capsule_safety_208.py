"""#208 seal-family capsule-safety fixes (RED-first).

DEFECT A — the FOREIGN-capsule execution gate must fail CLOSED when the consumer
has NO provable local identity. A capsule that DECLARES a ``source.project_slug``
we cannot prove is ours (``local_slug`` empty/None) is FOREIGN and must be blocked
from executing its captured command on the host without an explicit override.

DEFECT B — ``capsule import`` must NEVER destructively overwrite a NATIVE trace's
companions. When the target ``trace_id`` already exists as a natively-captured
trace (no ``capsule_import`` provenance), the scope-merge path would overwrite the
Trail companion with only the capsule's anchors and rewrite context/sources to
``b""`` — wiping real TrailEvents, ContextTree events, and source blobs.
"""

from __future__ import annotations

import gzip
import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.core import paths
from opentraces.core._bucket_io import _atomic_write_gzip
from opentraces.core.bucket_layout import (
    trace_v1_context_path,
    trace_v1_sources_path,
    trace_v1_trail_path,
)
from opentraces.core.bucket_trace_records import write_trace_record
from opentraces.core.capsule.import_ import CapsuleImportError, import_capsule
from opentraces.core.capsule.run import (
    SandboxNotOwnedError,
    _capsule_is_foreign,
    run_capsule_test,
)
from opentraces.core.capsule.share import build_capsule_bundle
from opentraces.core.capsule.test_extract import declared_test
from opentraces_schema import TraceRecord


# --------------------------------------------------------------------------- #
# DEFECT A — foreign gate fails CLOSED on unknown local identity
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def calc_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return {"repo": repo, "sha": sha}


def _foreign_capsule(sha: str, meta: dict) -> dict:
    cmd = f"{sys.executable} -c \"from calc import add; assert add(2, 3) == 5\""
    return {
        "schema_version": "opentraces.capsule.v1",
        "capsule_id": "safety208cap001",
        "repo_pin": {"commit_sha": sha},
        "source": {"project_slug": "some-foreign-project"},
        "bundle": meta,
        "test": declared_test(cmd, None),
    }


def test_foreign_capsule_with_no_local_identity_is_blocked(calc_repo, tmp_path):
    """A DECLARED foreign source slug + NO local identity (local_slug=None) is
    FOREIGN and must be refused — the #208 fail-CLOSED fix. On the pre-fix code
    ``_capsule_is_foreign`` returned False here, so the captured command RAN."""

    meta, data = build_capsule_bundle(calc_repo["repo"], calc_repo["sha"])
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(data)

    cap = _foreign_capsule(calc_repo["sha"], meta)
    with pytest.raises(SandboxNotOwnedError):
        run_capsule_test(cap, bundle_path=bundle, local_slug=None)


def test_foreign_capsule_no_local_identity_runs_with_explicit_override(calc_repo, tmp_path):
    """The explicit override still lets the operator run it, honestly tier none."""

    meta, data = build_capsule_bundle(calc_repo["repo"], calc_repo["sha"])
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(data)

    cap = _foreign_capsule(calc_repo["sha"], meta)
    res = run_capsule_test(
        cap, bundle_path=bundle, local_slug=None, unsafe_run_on_host=True
    )
    assert res["runnable"] is True
    assert res["sandbox_tier"] == "none"


def test_capsule_is_foreign_predicate_fails_closed_on_no_local_identity():
    # DECLARED source slug we cannot prove is ours (no local identity) => FOREIGN.
    assert _capsule_is_foreign({"source": {"project_slug": "p"}}, None) is True
    assert _capsule_is_foreign({"source": {"project_slug": "p"}}, "") is True
    # No declared source slug at all + no local identity => nothing to be foreign to.
    assert _capsule_is_foreign({}, None) is False
    assert _capsule_is_foreign({"source": {}}, None) is False
    # Matched (own) capsule stays non-foreign; a spoofed/mismatched slug is foreign.
    assert _capsule_is_foreign({"source": {"project_slug": "p"}}, "p") is False
    assert _capsule_is_foreign({"source": {"project_slug": "p"}}, "q") is True


# --------------------------------------------------------------------------- #
# DEFECT B — import must not destroy a native trace's companions
# --------------------------------------------------------------------------- #


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    return tmp_path


def _native_trace(slug: str, tid: str) -> None:
    """Materialize a NATIVE trace (no capsule_import provenance) plus real,
    non-empty Trail/Context/Sources companions — the shape a live capture leaves."""

    rec = TraceRecord.model_validate(
        {
            "trace_id": tid,
            "session_id": tid,
            "agent": {"name": "claude"},
            "steps": [
                {"step_index": 0, "role": "agent", "content": "native work"},
                {"step_index": 1, "role": "agent", "content": "more native work"},
            ],
        }
    )
    write_trace_record(rec, project_slug=slug, source_layer="claude_code")
    # A real TrailEvent companion carries ``event_type``; two context + two source rows.
    _atomic_write_gzip(
        trace_v1_trail_path(slug, tid),
        b'{"event_type":"git_anchor_created","trace_patch_id":"tp:native"}\n',
    )
    _atomic_write_gzip(trace_v1_context_path(slug, tid), b'{"ctx":1}\n{"ctx":2}\n')
    _atomic_write_gzip(trace_v1_sources_path(slug, tid), b'{"src":1}\n{"src":2}\n')


def _import_capsule_over(slug: str, tid: str) -> dict:
    from opentraces.core.capsule.contract import freeze_capsule

    return freeze_capsule(
        capsule_id="cid_safety208xxx",
        source={
            "project_slug": slug,
            "trace_id": tid,
            "agent": "codex",
            "capture_method": "transcript_reconstruction",
        },
        summary={"title": "session"},
        test=None,
        environment={"dependencies": [], "language_ecosystem": "python", "setup": []},
        bundle=None,
        intent={"headline": "did stuff"},
        failing_step={"index": 0},
        slice_payload={
            "slice_id": "s",
            "trace_id": tid,
            "steps": [{"step_index": 0, "role": "agent", "content": "capsule step"}],
            "start_step_index": 0,
            "end_step_index": 0,
            "limitations": [],
        },
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": None},
        trail_anchors=[{"git_anchor_id": "ga:capsule", "survival_state": "unknown"}],
        repo_pin={"remote_url": "https://github.com/o/r", "commit_sha": "abc", "changed_files": []},
        redaction={"manifest": {"floor": [], "floor_satisfied": True}},
        render_state={
            "redaction": "redacted_ok",
            "closure": "closure_full",
            "replay": "replay_unverified",
        },
        limitations=[],
        created_with="test",
    )


def test_import_over_native_trace_is_refused_and_preserves_companions(bucket):
    slug, tid = "proj", "deadbeef-0000-0000-0000-000000000001"
    _native_trace(slug, tid)

    cap = _import_capsule_over(slug, tid)
    with pytest.raises(CapsuleImportError):
        import_capsule(cap)

    # The native companions are UNTOUCHED (the refuse happens before any write).
    assert gzip.decompress(trace_v1_context_path(slug, tid).read_bytes()) == b'{"ctx":1}\n{"ctx":2}\n'
    assert gzip.decompress(trace_v1_sources_path(slug, tid).read_bytes()) == b'{"src":1}\n{"src":2}\n'
    trail_body = gzip.decompress(trace_v1_trail_path(slug, tid).read_bytes()).decode("utf-8")
    assert "event_type" in trail_body, "native TrailEvent companion must survive"
    assert "ga:capsule" not in trail_body, "capsule anchors must not overwrite native trail"


def test_capsule_over_capsule_scope_merge_still_works(bucket):
    """The import-created -> import-created scope-merge path is unaffected."""

    slug, tid = "proj", "deadbeef-0000-0000-0000-000000000002"
    first = _import_capsule_over(slug, tid)
    first["capsule_id"] = "cid_first0000001"
    report1 = import_capsule(first)
    assert report1["status"] == "created"

    second = _import_capsule_over(slug, tid)
    second["capsule_id"] = "cid_second000001"
    report2 = import_capsule(second)
    assert report2["status"] == "scope_merged"
