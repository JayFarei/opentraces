"""Cluster G — P1: refresh-time performance contracts on Trace Index.

The previous behavior of ``_build_trail_units`` called ``sync_patch`` per
anchor (via ``TrailQueryProjection.with_current_survival``). On a real
project with 315 anchors that turned a ~6 second refresh into a 10+
minute one. P1 defers survival enrichment to query time so refresh stays
O(units), not O(units × git ops).

These tests pin two contracts:

1. ``_build_trail_units`` makes zero ``sync_patch`` calls.
2. A 24-trace synthetic refresh completes well under 60 seconds.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
)
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _seed_patch_anchor(
    repo: Path,
    *,
    trace_id: str,
    patch_id: str,
    anchor_commit: str,
    file_path: str,
    text: str,
) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "file_path": file_path,
                    "affected_range": {"start_line": 1, "end_line": 2},
                    "authored_text": text,
                    "raw_authored_hash": sha256_text(text),
                    "git_clean_hash": sha256_text(text.strip()),
                    "limitations": [],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["manual_attach"],
                payload={
                    "git_anchor_id": f"gitanchor-sha256:{patch_id}",
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "commit_id": {"algo": "sha1", "hex": anchor_commit},
                    "path": file_path,
                    "range": {"start_line": 1, "end_line": 2},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )


def test_build_trail_units_does_not_call_sync_patch(tmp_path: Path) -> None:
    """``_build_trail_units`` must not call ``sync_patch`` per anchor.

    P1 contract: refresh becomes O(units), not O(units × git ops). We
    patch ``sync_patch`` to count invocations and assert zero calls
    during the trail-units build.
    """
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("alpha\nline\n")
    head = _commit(tmp_path, "seed")
    _seed_patch_anchor(
        tmp_path,
        trace_id="t-1",
        patch_id="abc12345",
        anchor_commit=head,
        file_path="a.py",
        text="alpha\nline\n",
    )

    from opentraces.core.trace_index import _build_trail_units

    with patch(
        "opentraces.core.trails.sync_patch",
        autospec=True,
    ) as sync_mock:
        units = _build_trail_units(tmp_path, project_slug="t-proj")

    assert sync_mock.call_count == 0, (
        f"_build_trail_units called sync_patch {sync_mock.call_count} times "
        "(P1 expected 0)."
    )
    # We should still produce git_anchor units — just without survival
    # enrichment baked in.
    git_anchor_units = [u for u in units if u.unit_type == "git_anchor"]
    assert git_anchor_units, "expected at least one git_anchor unit from build"
    # Each git_anchor unit should still carry a ``trail.survival_state``
    # facet, with the lazy default of ``unknown`` until cache populates.
    for unit in git_anchor_units:
        survival_facets = [
            f for f in unit.facets if f.name == "trail.survival_state"
        ]
        assert survival_facets, "git_anchor unit missing trail.survival_state facet"
        assert survival_facets[0].value == "unknown", (
            "P1 expected survival_state=unknown at refresh time; "
            f"got {survival_facets[0].value!r}"
        )


def test_refresh_index_under_60s_for_24_traces(tmp_path: Path) -> None:
    """A 24-trace × 13-anchor project must build trail units in <60s.

    The real ``~/.opentraces/projects/`` fixture had ~315 anchors and
    refresh blew past 10 minutes. With P1 the refresh is O(units) and
    a synthetic 24-trace × 13-anchor (~312 anchors) build should be
    well under the absolute 60-second threshold.
    """
    _init_repo(tmp_path)
    (tmp_path / "main.py").write_text("first\nsecond\n")
    head = _commit(tmp_path, "seed")

    # 24 traces × 13 anchors each → 312 anchors total.
    for trace_index in range(24):
        for anchor_index in range(13):
            file_path = f"file_{trace_index}_{anchor_index}.py"
            text = f"line a {trace_index}-{anchor_index}\nline b\n"
            (tmp_path / file_path).write_text(text)
            head = _commit(tmp_path, f"seed {trace_index}-{anchor_index}")
            _seed_patch_anchor(
                tmp_path,
                trace_id=f"t-{trace_index:02d}",
                patch_id=f"{trace_index:02d}{anchor_index:02d}{'0' * 56}"[:64],
                anchor_commit=head,
                file_path=file_path,
                text=text,
            )

    from opentraces.core.trace_index import _build_trail_units

    t0 = time.time()
    units = _build_trail_units(tmp_path, project_slug="proj")
    elapsed = time.time() - t0
    git_anchor_units = [u for u in units if u.unit_type == "git_anchor"]
    assert len(git_anchor_units) == 24 * 13, len(git_anchor_units)
    assert elapsed < 60.0, (
        f"P1 perf gate: _build_trail_units took {elapsed:.1f}s for "
        f"{len(git_anchor_units)} anchors (target <60s)."
    )
