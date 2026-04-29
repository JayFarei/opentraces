"""Cluster A — candidate-kind surface tests (MERGED-A items A4, A5).

These cover three guarantees:

* A4 — ``trace query --candidate-kind patch --since 12h`` returns at least
  one candidate against a fixture trace that has at least one TrailEvent
  ``trace_patch_created`` row, with the ``patch`` candidate kind populated.
* A4 — every ``git_anchor`` candidate carries a ``trail.survival_state``
  facet sourced from the projection's ``current_survival.survival_state``
  (falling back to ``unknown``).
* A5 — no candidate has ``candidate_kind: null`` for tool-less fixture
  traces (e.g. a single-prompt trace).
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opentraces_schema import Agent, Step, TraceRecord


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _register_project(project_dir: Path, project_id: str) -> str:
    from opentraces.core.config import (
        Config,
        ProjectRegistration,
        _make_slug,
        get_project_traces_dir,
        save_config,
    )

    _enroll_project(project_dir, project_id)
    get_project_traces_dir(project_dir).mkdir(parents=True, exist_ok=True)
    slug = _make_slug(project_dir.name, project_id)
    save_config(
        Config(
            projects={
                str(project_dir.resolve()): ProjectRegistration(
                    project_id=project_id,
                    slug=slug,
                )
            }
        )
    )
    return slug


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(
        record.model_dump_json() + "\n"
    )


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "a@b.c"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    authored_text = "    return 'kind probe'\n"
    (repo / "app.py").write_text("def value():\n" + authored_text)
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "patch"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def _seed_patch_trail(project: Path, *, commit_sha: str, trace_id: str) -> None:
    from opentraces.core.trails import append_exact_patch_trail

    append_exact_patch_trail(
        project,
        trace_id=trace_id,
        step_index=3,
        file_path="app.py",
        authored_text="    return 'kind probe'\n",
        commit_ref=commit_sha,
        writer="test",
        capture_method=["hook_posttooluse"],
    )


# ---------------------------------------------------------------- A4 patch
def test_trace_query_returns_patch_candidates_within_since_window(tmp_path):
    """A4 — ``--candidate-kind patch --since 12h`` returns >=1 patch candidate."""
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    _register_project(project, uuid.uuid4().hex)
    _seed_patch_trail(
        project,
        commit_sha=commit_sha,
        trace_id="trace-patch-since-window",
    )

    rebuild_index()

    packets = query_index(candidate_kind="patch", since="12h")
    assert packets, "expected at least one patch candidate within --since 12h"
    assert all(p.unit_type == "patch" for p in packets)
    assert any(
        p.candidate_kind == "patch" for p in packets
    ), "patch units must surface candidate_kind='patch' (not None)"


def test_trace_query_returns_git_anchor_candidates_within_since_window(tmp_path):
    """A4 — ``--candidate-kind git_anchor --since 12h`` returns anchored rows."""
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    _register_project(project, uuid.uuid4().hex)
    _seed_patch_trail(
        project,
        commit_sha=commit_sha,
        trace_id="trace-anchor-since-window",
    )

    rebuild_index()

    packets = query_index(candidate_kind="git_anchor", since="12h")
    assert packets, "expected at least one git_anchor candidate within --since 12h"
    assert all(p.unit_type == "git_anchor" for p in packets)
    assert all(p.candidate_kind == "git_anchor" for p in packets)


# ---------------------------------------------------- A4 survival facet
def test_git_anchor_candidates_carry_trail_survival_state_facet(tmp_path):
    """A4 — every ``git_anchor`` packet exposes ``trail.survival_state``."""
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    _register_project(project, uuid.uuid4().hex)
    _seed_patch_trail(
        project,
        commit_sha=commit_sha,
        trace_id="trace-anchor-survival-facet",
    )

    rebuild_index()

    packets = query_index(candidate_kind="git_anchor")
    assert packets, "fixture should produce at least one git_anchor candidate"
    for packet in packets:
        survival_facet_values = {
            str(facet.value)
            for facet in packet.facets
            if facet.name == "trail.survival_state"
        }
        assert survival_facet_values, (
            f"git_anchor packet {packet.unit_id!r} missing trail.survival_state facet"
        )
        # Vocabulary fall-back: at minimum we surface 'unknown' if the syncer
        # could not derive a richer state.
        recognised = {
            "alive_on_path",
            "alive_transformed",
            "alive_moved",
            "partially_preserved",
            "repaired",
            "reverted",
            "lost",
            "unknown",
        }
        assert survival_facet_values <= recognised, (
            f"unexpected survival vocabulary: {survival_facet_values}"
        )


# --------------------------------------------------- A5 trace baseline
def _toolless_prompt_trace(trace_id: str) -> TraceRecord:
    """A trace with no tool calls: just a user prompt that pastes a URL."""
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code"),
        task={"description": "Quick read of a URL, no tools."},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Please summarise https://example.test/notes",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Acknowledged, no tools required.",
            ),
        ],
    )


def test_no_candidate_has_null_kind_for_toolless_trace(tmp_path):
    """A5 — every candidate has a non-null ``candidate_kind``."""
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    slug = _register_project(project, uuid.uuid4().hex)
    _write_project_trace(project, _toolless_prompt_trace("trace-toolless-baseline"))

    rebuild_index()

    # Use a generic filter that returns the trace unit (not a child kind).
    packets = query_index(project=slug)
    assert packets, "tool-less trace should still be queryable by --project"
    for packet in packets:
        assert packet.candidate_kind is not None, (
            f"candidate_kind must never be null; saw None for unit_id={packet.unit_id}"
        )
        assert packet.candidate_kind, (
            f"candidate_kind must never be empty; saw {packet.candidate_kind!r}"
        )
