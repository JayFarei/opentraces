"""Watermark cursor + ``dataset run --sync`` (#192).

RED-first coverage for the seal-family M2 delta-sync seam:

* the write-only cursor becomes a consumed WATERMARK (manifest digest + per-row
  ``written_at``) stamped on every successful non-dry run;
* ``run_dataset_workflow(..., sync=True)`` short-circuits to ZERO candidates
  when the bucket manifest digest is unchanged since the last sync (fast no-op,
  evidenced in the run summary's ``delta_scope``);
* a new trace landing in the bucket makes ``--sync`` project only the delta
  (the projecting workflow self-filters on the watermark threaded into the run
  packet);
* the watermark advances only on success — a failed run leaves it untouched;
* full ``run`` (no ``--sync``) behaviour is unchanged (projects everything).
"""

from __future__ import annotations

import json

import yaml

from opentraces.core import paths
from opentraces.core.workflows import load_workflow, workflows_dir


_SYNC_BUILDER = r'''
import json, os, sys

otdir = os.environ.get("OT_OPENTRACES_DIR", "")
# A forced-failure marker lets a test fail ONE run without changing the
# workflow bytes (so the pinned digest never drifts).
if otdir and os.path.exists(os.path.join(otdir, "FAIL")):
    sys.stderr.write("forced sync failure\n")
    sys.exit(1)

packet = json.load(open(os.environ["OT_RUN_PACKET"], encoding="utf-8"))
sync = packet.get("sync") or {}
watermark = sync.get("last_write_at")

candidates = []
cand_path = os.path.join(otdir, "sync_candidates.jsonl")
if os.path.exists(cand_path):
    for line in open(cand_path, encoding="utf-8"):
        line = line.strip()
        if line:
            candidates.append(json.loads(line))

rows = []
for c in candidates:
    # Self-filter on the watermark threaded into the run packet: project only
    # bucket data written AFTER the recorded watermark.
    if watermark and not (c["written_at"] > watermark):
        continue
    rows.append(c["row"])

with open(os.environ["OT_DATASET_OUTPUT"], "w", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r, sort_keys=True) + "\n")
'''


_SKILL_MD = (
    "---\n"
    "name: {name}\n"
    "description: Deterministic watermark-aware row emitter\n"
    "mode: agent-skill\n"
    "---\n\n"
    "# {name}\n\nProject bucket rows newer than the sync watermark.\n"
)


def _schema() -> dict:
    return {
        "type": "object",
        "required": ["source_trace_id", "source_unit_id", "summary"],
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "additionalProperties": False,
    }


def _install_sync_workflow(name: str) -> str:
    pkg = workflows_dir() / name
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    (pkg / "scripts" / "build_rows.py").write_text(_SYNC_BUILDER, encoding="utf-8")
    return load_workflow(name).digest


def _candidate(trace: str, written_at: str) -> dict:
    return {
        "written_at": written_at,
        "row": {
            "source_trace_id": trace,
            "source_unit_id": f"tu:{trace}:trace",
            "summary": f"Work on {trace}.",
        },
    }


def _write_candidates(candidates: list[dict]) -> None:
    path = paths.OPENTRACES_DIR / "sync_candidates.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for c in candidates:
            fh.write(json.dumps(c) + "\n")


def _create_sync_dataset(name: str) -> None:
    from opentraces.core.datasets import create_dataset

    digest = _install_sync_workflow(f"{name}-curator")
    create_dataset(
        name,
        workflow_skill=f"{name}-curator",
        workflow_digest=digest,
        row_schema=_schema(),
    )


def _cursor_watermark(name: str) -> dict | None:
    from opentraces.core.datasets import dataset_path

    path = dataset_path(name) / ".opentraces" / "cursors.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entry = (data.get("queries") or {}).get("default") or {}
    return entry.get("watermark")


def test_bucket_watermark_reads_manifest_digest_and_last_write_at(monkeypatch):
    """``bucket_watermark`` pairs the manifest digest with max status.written_at."""
    from opentraces.core import datasets

    fake = {
        "digest": "sha256:deadbeef",
        "traces": [
            {"trace_id": "a", "status": {"known": True, "written_at": "2026-01-01T00:00:00Z"}},
            {"trace_id": "b", "status": {"known": True, "written_at": "2026-03-01T00:00:00Z"}},
        ],
    }
    monkeypatch.setattr(
        datasets, "push_clearance_manifest", lambda: fake, raising=False
    )
    monkeypatch.setattr(
        "opentraces.core.bucket_store.bucket_manifest",
        lambda **_kw: fake,
    )
    wm = datasets.bucket_watermark()
    assert wm["manifest_digest"] == "sha256:deadbeef"
    assert wm["last_write_at"] == "2026-03-01T00:00:00Z"


def test_full_run_stamps_watermark_and_projects_everything(monkeypatch):
    from opentraces.core import workflow_runner

    _create_sync_dataset("sync-full")
    _write_candidates(
        [
            _candidate("trace-jan", "2026-01-01T00:00:00Z"),
            _candidate("trace-feb", "2026-02-01T00:00:00Z"),
        ]
    )
    monkeypatch.setattr(
        workflow_runner,
        "bucket_watermark",
        lambda: {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"},
    )

    result = workflow_runner.run_dataset_workflow(
        "sync-full", executor="script", scope={"scope": "all-projects"}
    )
    # Full run projects EVERYTHING (no watermark filter threaded).
    assert result.append_summary.appended_count == 2
    assert result.cursor_advanced is True
    # Existing write-only cursor behaviour is unchanged...
    from opentraces.core.datasets import dataset_path

    cursors = yaml.safe_load(
        (dataset_path("sync-full") / ".opentraces" / "cursors.yaml").read_text()
    )
    assert "last_successful_run_id" in cursors["queries"]["default"]
    # ...and the new watermark sub-block is stamped.
    assert _cursor_watermark("sync-full") == {
        "manifest_digest": "d0",
        "last_write_at": "2026-01-15T00:00:00Z",
    }


def test_sync_no_new_data_is_fast_noop_zero_candidates(monkeypatch):
    from opentraces.core import workflow_runner

    _create_sync_dataset("sync-noop")
    _write_candidates([_candidate("trace-jan", "2026-01-01T00:00:00Z")])
    monkeypatch.setattr(
        workflow_runner,
        "bucket_watermark",
        lambda: {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"},
    )
    # Establish the watermark with a full run.
    workflow_runner.run_dataset_workflow(
        "sync-noop", executor="script", scope={"scope": "all-projects"}
    )

    # --sync with the SAME manifest digest -> zero candidates, no execution.
    synced = workflow_runner.run_dataset_workflow(
        "sync-noop", executor="script", scope={"scope": "all-projects"}, sync=True
    )
    assert synced.append_summary.appended_count == 0
    assert synced.append_summary.emitted_count == 0
    assert synced.delta_scope is not None
    assert synced.delta_scope["bucket_changed"] is False
    assert synced.delta_scope["candidate_scope"] == "none"


def test_sync_projects_only_the_delta_after_new_trace(monkeypatch):
    from opentraces.core import workflow_runner

    _create_sync_dataset("sync-delta")
    _write_candidates([_candidate("trace-jan", "2026-01-01T00:00:00Z")])
    monkeypatch.setattr(
        workflow_runner,
        "bucket_watermark",
        lambda: {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"},
    )
    first = workflow_runner.run_dataset_workflow(
        "sync-delta", executor="script", scope={"scope": "all-projects"}
    )
    assert first.append_summary.appended_count == 1

    # A new trace lands: bucket digest advances AND a later candidate appears.
    _write_candidates(
        [
            _candidate("trace-jan", "2026-01-01T00:00:00Z"),
            _candidate("trace-feb", "2026-02-01T00:00:00Z"),
        ]
    )
    monkeypatch.setattr(
        workflow_runner,
        "bucket_watermark",
        lambda: {"manifest_digest": "d1", "last_write_at": "2026-02-15T00:00:00Z"},
    )
    synced = workflow_runner.run_dataset_workflow(
        "sync-delta", executor="script", scope={"scope": "all-projects"}, sync=True
    )
    # Only the delta (trace-feb) is projected: trace-jan is filtered by the
    # watermark threaded into the run packet, not merely deduped.
    assert synced.append_summary.emitted_count == 1
    assert synced.append_summary.appended_count == 1
    assert synced.delta_scope["bucket_changed"] is True
    assert synced.delta_scope["candidate_scope"] == "delta"
    # Watermark advanced to the new bucket position.
    assert _cursor_watermark("sync-delta")["manifest_digest"] == "d1"


def test_failed_sync_run_leaves_watermark_untouched(monkeypatch):
    import pytest

    from opentraces.core import workflow_runner
    from opentraces.core.workflow_runner import WorkflowScriptError

    _create_sync_dataset("sync-fail")
    _write_candidates([_candidate("trace-jan", "2026-01-01T00:00:00Z")])
    monkeypatch.setattr(
        workflow_runner,
        "bucket_watermark",
        lambda: {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"},
    )
    workflow_runner.run_dataset_workflow(
        "sync-fail", executor="script", scope={"scope": "all-projects"}
    )
    assert _cursor_watermark("sync-fail")["manifest_digest"] == "d0"

    # Force the next --sync run to fail (bucket digest changed so it does NOT
    # short-circuit and actually executes the -- now failing -- builder).
    (paths.OPENTRACES_DIR / "FAIL").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        workflow_runner,
        "bucket_watermark",
        lambda: {"manifest_digest": "d1", "last_write_at": "2026-02-15T00:00:00Z"},
    )
    with pytest.raises(WorkflowScriptError):
        workflow_runner.run_dataset_workflow(
            "sync-fail", executor="script", scope={"scope": "all-projects"}, sync=True
        )
    # The failed run left the watermark at the last successful position.
    assert _cursor_watermark("sync-fail")["manifest_digest"] == "d0"


def test_failed_script_run_preserves_child_stderr_in_log(monkeypatch):
    """A failed workflow script's REAL error survives in the run's log.txt.

    Regression (found dogfooding on a real bucket): ``run_dataset_workflow``'s
    failure handler overwrote the run's ``log.txt`` with the bare
    ``WorkflowScriptError`` message — whose text only points BACK at that same
    ``log.txt`` — clobbering the child stderr that ``_execute_script`` had just
    captured. A failed run must leave the actual diagnostic behind, or the user
    is told to "see log.txt" for a log that only repeats the pointer.
    """
    import pytest

    from opentraces.core import workflow_runner
    from opentraces.core.datasets import dataset_path
    from opentraces.core.workflow_runner import WorkflowScriptError

    _create_sync_dataset("sync-log")
    _write_candidates([_candidate("trace-jan", "2026-01-01T00:00:00Z")])
    monkeypatch.setattr(
        workflow_runner,
        "bucket_watermark",
        lambda: {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"},
    )
    # The _SYNC_BUILDER writes "forced sync failure" to stderr then exits 1.
    (paths.OPENTRACES_DIR / "FAIL").write_text("x", encoding="utf-8")
    with pytest.raises(WorkflowScriptError):
        workflow_runner.run_dataset_workflow(
            "sync-log", executor="script", scope={"scope": "all-projects"}
        )

    runs = sorted((dataset_path("sync-log") / ".opentraces" / "runs").iterdir())
    log = (runs[-1] / "log.txt").read_text(encoding="utf-8")
    # The child's real stderr is PRESERVED (not clobbered) ...
    assert "forced sync failure" in log
    # ... with the wrapper outcome appended, not replacing it.
    assert "WorkflowScriptError" in log
