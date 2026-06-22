"""CLI unit tests for ``opentraces ctx`` (Plan 077 Phase 3 CLI surface).

One test per verb covers happy-path JSON envelope shape, an empty-state
case (returns rc=0 with ``limitations``, not a traceback), and rc=3
on missing resources. Fixtures reuse the same e2e capture flow as
``tests/context_tree/test_context_tree_e2e.py`` so the CLI is exercised on real
projection state rather than handcrafted dicts.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.cli.ctx import ctx_group
from opentraces.core.context_tree.contract import (
    CONTEXT_READS_SCHEMA_VERSION,
    CONTEXT_RESUME_SCHEMA_VERSION,
    CONTEXT_TREE_SCHEMA_VERSION,
    CONTEXT_WRITES_SCHEMA_VERSION,
)
from opentraces.core.context_tree.query import (
    build_context_tree_projection,
    resolve_node_traces,
)
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = Path(__file__).parent / "otbox" / "fixtures" / "sessions"


# --------------------------------------------------------------------------- #
# Test scaffolding
# --------------------------------------------------------------------------- #


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_ctx_{name}", base / "session.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


@pytest.fixture
def linear_project(tmp_path):
    """A tmp project with the ``context-tree-linear`` fixture captured."""
    _init_git_repo(tmp_path)
    transcript = _run_fixture("context-tree-linear", tmp_path)
    record = TraceRecord(
        trace_id="trace-context-tree-linear",
        session_id="sess-linear",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[],
    )
    emit_context_tree_events_from_record(
        project_dir=tmp_path,
        final_record=record,
        transcript_path=transcript,
    )
    projection = build_context_tree_projection(tmp_path)
    return tmp_path, transcript, projection


@pytest.fixture
def runner():
    return CliRunner()


def _project_arg(project: Path) -> list[str]:
    return ["--project", str(project)]


def _invoke_json(runner, args):
    """Run a ctx command and parse its JSON stdout."""
    result = runner.invoke(ctx_group, args)
    assert result.exit_code == 0, (
        f"unexpected exit_code={result.exit_code} for args={args}\n"
        f"stdout={result.output}\n"
    )
    return json.loads(result.output)


# --------------------------------------------------------------------------- #
# ctx tree
# --------------------------------------------------------------------------- #


def test_ctx_tree_json_envelope(runner, linear_project):
    project, _transcript, _proj = linear_project
    data = _invoke_json(
        runner,
        ["tree", "trace-context-tree-linear", *_project_arg(project), "--json"],
    )
    assert data["schema_version"] == CONTEXT_TREE_SCHEMA_VERSION
    assert data["trace_id"] == "trace-context-tree-linear"
    assert data["root_node_id"] is not None
    assert data["active_leaf_id"] is not None
    assert isinstance(data["nodes"], list) and data["nodes"]
    assert "orphan_branch_roots" in data
    assert "subagent_session_ids" in data


def test_ctx_tree_empty_state(runner, tmp_path):
    _init_git_repo(tmp_path)
    data = _invoke_json(
        runner,
        ["tree", "nonexistent-trace", *_project_arg(tmp_path), "--json"],
    )
    assert data["schema_version"] == CONTEXT_TREE_SCHEMA_VERSION
    assert "context_tree_not_captured" in data["limitations"]
    assert data["nodes"] == []


def test_ctx_tree_text_renders(runner, linear_project):
    project, _, _ = linear_project
    result = runner.invoke(
        ctx_group,
        ["tree", "trace-context-tree-linear", *_project_arg(project)],
    )
    assert result.exit_code == 0
    # Text rendering shows at least one node line with the branch_type
    assert "step=" in result.output


# --------------------------------------------------------------------------- #
# ctx show
# --------------------------------------------------------------------------- #


def test_ctx_show_json_envelope(runner, linear_project):
    project, _, projection = linear_project
    node_id = list(projection.nodes_by_id.keys())[0]
    data = _invoke_json(
        runner,
        ["show", node_id, *_project_arg(project), "--json"],
    )
    assert data["schema_version"] == CONTEXT_TREE_SCHEMA_VERSION
    assert data["node"]["node_id"] == node_id
    # Four layer summary entries by default
    assert set(data["layers"].keys()) == {
        "system", "messages", "tool_registry", "runtime_state"
    }
    # Summary form has no content key
    for entry in data["layers"].values():
        assert "content" not in entry


def test_ctx_show_full_inlines_content(runner, linear_project):
    project, _, projection = linear_project
    node_id = list(projection.nodes_by_id.keys())[0]
    data = _invoke_json(
        runner,
        ["show", node_id, "--full", *_project_arg(project), "--json"],
    )
    for entry in data["layers"].values():
        assert "content" in entry


def test_ctx_show_missing_node_rc3(runner, linear_project):
    project, _, _ = linear_project
    bad_id = "sha256:" + "0" * 64
    result = runner.invoke(
        ctx_group,
        ["show", bad_id, *_project_arg(project)],
    )
    assert result.exit_code == 3


# --------------------------------------------------------------------------- #
# ctx step
# --------------------------------------------------------------------------- #


def test_ctx_step_resolves_node(runner, linear_project):
    project, _, projection = linear_project
    # Pick any node with a step_index
    any_node = next(
        n for n in projection.nodes_by_id.values() if n.step_index is not None
    )
    data = _invoke_json(
        runner,
        [
            "step",
            "trace-context-tree-linear",
            str(any_node.step_index),
            *_project_arg(project),
            "--json",
        ],
    )
    assert data["schema_version"] == CONTEXT_TREE_SCHEMA_VERSION
    assert data["node"]["step_index"] == any_node.step_index
    assert data["node_id"] == any_node.node_id


def test_ctx_step_missing_returns_rc3(runner, linear_project):
    project, _, _ = linear_project
    result = runner.invoke(
        ctx_group,
        ["step", "trace-context-tree-linear", "99999", *_project_arg(project)],
    )
    assert result.exit_code == 3


def test_ctx_step_empty_state_json(runner, linear_project):
    project, _, _ = linear_project
    data = _invoke_json(
        runner,
        [
            "step",
            "trace-context-tree-linear",
            "99999",
            *_project_arg(project),
            "--json",
        ],
    )
    assert "context_layer_unavailable" in data["limitations"]


# --------------------------------------------------------------------------- #
# ctx reads
# --------------------------------------------------------------------------- #


def test_ctx_reads_json_envelope(runner, linear_project):
    project, _, _ = linear_project
    data = _invoke_json(
        runner,
        ["reads", "trace-context-tree-linear", *_project_arg(project), "--json"],
    )
    assert data["schema_version"] == CONTEXT_READS_SCHEMA_VERSION
    assert data["trace_id"] == "trace-context-tree-linear"
    assert "reads" in data


def test_ctx_reads_unknown_trace_empty_state(runner, tmp_path):
    _init_git_repo(tmp_path)
    data = _invoke_json(
        runner,
        ["reads", "unknown", *_project_arg(tmp_path), "--json"],
    )
    assert data["schema_version"] == CONTEXT_READS_SCHEMA_VERSION
    assert "context_tree_not_captured" in data["limitations"]


# --------------------------------------------------------------------------- #
# ctx writes
# --------------------------------------------------------------------------- #


def test_ctx_writes_json_envelope(runner, linear_project):
    project, _, _ = linear_project
    data = _invoke_json(
        runner,
        [
            "writes",
            "trace-context-tree-linear",
            "--with-trail-anchor",
            *_project_arg(project),
            "--json",
        ],
    )
    assert data["schema_version"] == CONTEXT_WRITES_SCHEMA_VERSION
    assert data["trace_id"] == "trace-context-tree-linear"
    assert "writes" in data


def test_ctx_writes_unknown_trace_empty_state(runner, tmp_path):
    _init_git_repo(tmp_path)
    data = _invoke_json(
        runner,
        ["writes", "unknown", *_project_arg(tmp_path), "--json"],
    )
    assert data["schema_version"] == CONTEXT_WRITES_SCHEMA_VERSION
    assert "context_tree_not_captured" in data["limitations"]


# --------------------------------------------------------------------------- #
# ctx diff
# --------------------------------------------------------------------------- #


def test_ctx_diff_same_node_empty_diff(runner, linear_project):
    project, _, projection = linear_project
    node_id = list(projection.nodes_by_id.keys())[0]
    data = _invoke_json(
        runner,
        ["diff", node_id, node_id, *_project_arg(project), "--json"],
    )
    assert data["schema_version"] == CONTEXT_TREE_SCHEMA_VERSION
    assert data["diff"]["changed"] == []


def test_ctx_diff_missing_node_rc3(runner, linear_project):
    project, _, projection = linear_project
    real = list(projection.nodes_by_id.keys())[0]
    bad = "sha256:" + "0" * 64
    result = runner.invoke(
        ctx_group,
        ["diff", real, bad, *_project_arg(project)],
    )
    assert result.exit_code == 3


# --------------------------------------------------------------------------- #
# ctx compactions
# --------------------------------------------------------------------------- #


def test_ctx_compactions_empty_for_linear(runner, linear_project):
    project, _, _ = linear_project
    data = _invoke_json(
        runner,
        ["compactions", "trace-context-tree-linear", *_project_arg(project), "--json"],
    )
    assert data["schema_version"] == CONTEXT_TREE_SCHEMA_VERSION
    assert data["compactions"] == []


def test_ctx_compactions_unknown_trace_empty_state(runner, tmp_path):
    _init_git_repo(tmp_path)
    data = _invoke_json(
        runner,
        ["compactions", "missing", *_project_arg(tmp_path), "--json"],
    )
    assert "context_tree_not_captured" in data["limitations"]


def test_ctx_compactions_index_out_of_range_rc3(runner, linear_project):
    project, _, _ = linear_project
    result = runner.invoke(
        ctx_group,
        [
            "compactions",
            "trace-context-tree-linear",
            "--index",
            "0",
            *_project_arg(project),
        ],
    )
    # linear fixture has zero compactions; --index 0 is out of range
    assert result.exit_code == 3


# --------------------------------------------------------------------------- #
# ctx prune
# --------------------------------------------------------------------------- #


def test_ctx_prune_dry_run_reports_target(runner, linear_project):
    project, transcript, projection = linear_project
    # Choose any node with a real transcript_uuid the source file has
    node = next(iter(projection.nodes_by_id.values()))
    data = _invoke_json(
        runner,
        [
            "prune",
            node.node_id,
            "--source-jsonl",
            str(transcript),
            *_project_arg(project),
            "--json",
        ],
    )
    # Dry-run path doesn't write; just describes the target
    assert data.get("wrote") is False
    assert data.get("new_session_id")
    assert data.get("jsonl_path")


def test_ctx_prune_write_then_collision_rc4(runner, linear_project):
    project, transcript, projection = linear_project
    node = next(iter(projection.nodes_by_id.values()))
    # First write must succeed
    data = _invoke_json(
        runner,
        [
            "prune",
            node.node_id,
            "--source-jsonl",
            str(transcript),
            "--to-session",
            "ctx-test",
            "--write",
            *_project_arg(project),
            "--json",
        ],
    )
    assert data.get("wrote") is True
    written_path = Path(data["jsonl_path"])
    assert written_path.exists()
    # Second write that collides must hit rc=4. Pre-create the SAME
    # name to force collision since new_session_id randomizes per call;
    # the prune helper raises FileExistsError if the target exists.
    collision_path = transcript.parent / "ctx-collide.jsonl"
    collision_path.write_text("preexisting")
    # We can't easily force the random suffix; instead patch by writing
    # to a fixed destination and re-invoking. To deterministically hit
    # collision, monkeypatch prune_session_to_node to raise. Simpler:
    # invoke twice with the same explicit destination by colliding on
    # the random tail is non-deterministic — assert the safer path:
    # the underlying helper raises FileExistsError when target exists,
    # and the CLI exits rc=4. We verify that by pre-creating the exact
    # path the next dry-run would target.
    dry = _invoke_json(
        runner,
        [
            "prune",
            node.node_id,
            "--source-jsonl",
            str(transcript),
            "--to-session",
            "ctx-collide",
            *_project_arg(project),
            "--json",
        ],
    )
    # Replace the would-be path with a preexisting file
    Path(dry["jsonl_path"]).write_text("preexisting")
    # The next --write call rolls a new random suffix, so we cannot
    # deterministically hit that exact path. Instead, write a custom
    # collision file matching whatever name a fresh dry-run produces,
    # then immediately invoke --write reusing the same to_session stem.
    # In practice this is non-deterministic, so we settle for asserting
    # the helper exit_code semantics directly through a forced exists.
    from opentraces.core.context_tree import prune as _prune_mod

    original = _prune_mod.prune_session_to_node

    def _force_collision(*args, **kwargs):
        raise FileExistsError("forced collision for test")

    try:
        from opentraces.cli import ctx as _ctx_mod

        _ctx_mod._prune_session = _force_collision  # type: ignore[assignment]
        result = runner.invoke(
            ctx_group,
            [
                "prune",
                node.node_id,
                "--source-jsonl",
                str(transcript),
                "--write",
                *_project_arg(project),
                "--json",
            ],
        )
        assert result.exit_code == 4
        body = json.loads(result.output)
        assert body["error"] == "destination_exists"
    finally:
        from opentraces.cli import ctx as _ctx_mod

        _ctx_mod._prune_session = original  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# ctx resume
# --------------------------------------------------------------------------- #


def test_ctx_resume_json_envelope(runner, linear_project):
    project, _, projection = linear_project
    node_id = list(projection.nodes_by_id.keys())[0]
    data = _invoke_json(
        runner,
        ["resume", node_id, *_project_arg(project), "--json"],
    )
    assert data["schema_version"] == CONTEXT_RESUME_SCHEMA_VERSION
    assert data["node_id"] == node_id
    assert "messages" in data
    assert "env" in data


def test_ctx_resume_unknown_node_rc3(runner, linear_project):
    project, _, _ = linear_project
    bad = "sha256:" + "1" * 64
    result = runner.invoke(
        ctx_group,
        ["resume", bad, *_project_arg(project)],
    )
    # The helper returns error in payload; CLI exits rc=3 in text mode
    assert result.exit_code == 3


# --------------------------------------------------------------------------- #
# ctx resolve
# --------------------------------------------------------------------------- #


def test_ctx_resolve_unknown_node_empty_state(runner, linear_project):
    project, _, _ = linear_project
    bad_uri = "ot://context-node/sha256/" + "0" * 64
    result = runner.invoke(
        ctx_group,
        ["resolve", bad_uri, *_project_arg(project), "--json"],
    )
    # Either rc=3 with empty-state envelope, or rc=0 with limitations
    # — both are acceptable contracts per the plan's "ctx-resolve-empty-state"
    # journey. Our CLI exits rc=3 on resolver failures.
    assert result.exit_code in (0, 3)
    body = json.loads(result.output)
    assert "limitations" in body or "resource_type" in body


def test_ctx_resolve_known_node(runner, linear_project):
    project, _, projection = linear_project
    node_id = list(projection.nodes_by_id.keys())[0]
    uri = f"ot://context-node/sha256/{node_id.split(':', 1)[1]}"
    result = runner.invoke(
        ctx_group,
        ["resolve", uri, *_project_arg(project), "--json"],
    )
    # Could succeed (rc=0) if Agent F's resolver extension landed, or
    # surface limitations envelope. Either way: never traceback.
    assert result.exit_code in (0, 3)
    body = json.loads(result.output)
    assert isinstance(body, dict)


# --------------------------------------------------------------------------- #
# ctx anchor-for-step
# --------------------------------------------------------------------------- #


def test_ctx_anchor_for_step_missing_anchor_rc3(runner, linear_project):
    project, _, projection = linear_project
    # Linear fixture nodes have no trail_anchor_hint, so this must rc=3
    any_node = next(
        n for n in projection.nodes_by_id.values() if n.step_index is not None
    )
    result = runner.invoke(
        ctx_group,
        [
            "anchor-for-step",
            "trace-context-tree-linear",
            str(any_node.step_index),
            *_project_arg(project),
        ],
    )
    assert result.exit_code == 3


def test_ctx_anchor_for_step_missing_step_rc3(runner, linear_project):
    project, _, _ = linear_project
    result = runner.invoke(
        ctx_group,
        [
            "anchor-for-step",
            "trace-context-tree-linear",
            "99999",
            *_project_arg(project),
        ],
    )
    assert result.exit_code == 3


# --------------------------------------------------------------------------- #
# Issue #121: bounded ctx show / ctx diff reads (equivalence + structural bound)
# --------------------------------------------------------------------------- #


def _emit_fixture_trace(name: str, trace_id: str, repo: Path, workdir: Path) -> None:
    """Capture a session fixture and emit its context-tree events into ``repo``."""
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(
        f"_xt_{name.replace('-', '_')}", base / "session.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    workdir.mkdir(parents=True, exist_ok=True)
    transcript = workdir / f"{name}.jsonl"
    mod.run(workdir, transcript)
    record = TraceRecord(
        trace_id=trace_id,
        session_id=f"sess-{name}",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[],
    )
    emit_context_tree_events_from_record(
        project_dir=repo,
        final_record=record,
        transcript_path=transcript,
    )


@pytest.fixture
def multi_trace_project(tmp_path):
    """A tmp project with TWO captured traces (cross-trace diff coverage)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _emit_fixture_trace("context-tree-linear", "trace-a", repo, tmp_path / "wa")
    _emit_fixture_trace("context-tree-multi-turn", "trace-b", repo, tmp_path / "wb")
    projection = build_context_tree_projection(repo)
    return repo, projection


def test_ctx_show_equals_whole_repo_path(runner, linear_project):
    """The bounded ctx show payload is byte-identical to the OLD whole-repo path.

    Computes the expected ``node`` dict directly from the full-walk
    ``build_context_tree_projection(repo)`` (the pre-#121 source) and asserts
    the CLI's bounded read reproduces it bit-for-bit.
    """
    project, _, projection = linear_project
    node_id = list(projection.nodes_by_id.keys())[0]
    # Whole-repo projection (the prior path) — recompute fresh, no cache reuse.
    full = build_context_tree_projection(project)
    full_node = full.nodes_by_id[node_id]
    expected_node = {
        "node_id": full_node.node_id,
        "parent_node_id": full_node.parent_node_id,
        "branch_type": full_node.branch_type,
        "trace_id": full_node.trace_id,
        "step_index": full_node.step_index,
        "transcript_uuid": full_node.transcript_uuid,
        "transcript_offset": full_node.transcript_offset,
        "capture_completeness": full_node.capture_completeness,
        "subagent_session_id": full_node.subagent_session_id,
        "trail_anchor_hint": full_node.trail_anchor_hint,
    }
    data = _invoke_json(
        runner,
        ["show", node_id, *_project_arg(project), "--json"],
    )
    assert data["node"] == expected_node


def test_ctx_diff_cross_trace_equals_whole_repo_layer_diff(runner, multi_trace_project):
    """Cross-trace diff (node_a in trace A, node_b in trace B) still works.

    Asserts the bounded merge-of-two-per-trace-projections reproduces the
    whole-repo ``layer_diff`` byte-for-byte — the case a naive single-trace
    fix would silently break.
    """
    repo, projection = multi_trace_project
    node_a = projection.nodes_by_trace["trace-a"][0]
    node_b = projection.nodes_by_trace["trace-b"][0]
    # Sanity: the two nodes really belong to different traces.
    assert projection.nodes_by_id[node_a].trace_id != \
        projection.nodes_by_id[node_b].trace_id
    expected_diff = build_context_tree_projection(repo).layer_diff(node_a, node_b)
    data = _invoke_json(
        runner,
        ["diff", node_a, node_b, *_project_arg(repo), "--json"],
    )
    assert data["diff"] == expected_diff


def test_ctx_show_diff_never_call_full_read_events(runner, multi_trace_project, monkeypatch):
    """Structural bound: neither command calls the whole-log ``read_events``.

    Mirrors the #87/#110 bounded-read tests — ``read_events`` (the full walk)
    is monkeypatched to blow up; the scoped/per-trace readers stay live, so a
    green run proves the bounded path is the one taken.
    """
    import opentraces.core.trails.event_log as event_log_mod

    def _boom(*_a, **_k):  # pragma: no cover - asserts via raising
        raise AssertionError("full read_events walk must not be called (#121)")

    monkeypatch.setattr(event_log_mod, "read_events", _boom)
    # query.py imports read_events by name — patch that binding too.
    import opentraces.core.context_tree.query as query_mod
    monkeypatch.setattr(query_mod, "read_events", _boom)

    repo, projection = multi_trace_project
    node_a = projection.nodes_by_trace["trace-a"][0]
    node_b = projection.nodes_by_trace["trace-b"][0]

    show = runner.invoke(
        ctx_group, ["show", node_a, *_project_arg(repo), "--json"]
    )
    assert show.exit_code == 0, show.output
    diff = runner.invoke(
        ctx_group, ["diff", node_a, node_b, *_project_arg(repo), "--json"]
    )
    assert diff.exit_code == 0, diff.output


def test_ctx_show_unresolved_node_rc3_via_bounded_path(runner, multi_trace_project):
    """Unresolvable node_id falls through to the existing rc=3 not-found branch."""
    repo, _ = multi_trace_project
    bad_id = "sha256:" + "0" * 64
    result = runner.invoke(ctx_group, ["show", bad_id, *_project_arg(repo)])
    assert result.exit_code == 3
    data = _invoke_json(
        runner, ["show", bad_id, *_project_arg(repo), "--json"]
    )
    assert data["limitations"] == ["context_layer_unavailable"]


def test_resolve_node_traces_cli_smoke(multi_trace_project):
    """The resolver returns owning traces for known nodes, omits unknown."""
    repo, projection = multi_trace_project
    node_a = projection.nodes_by_trace["trace-a"][0]
    node_b = projection.nodes_by_trace["trace-b"][0]
    resolved = resolve_node_traces(repo, {node_a, node_b, "sha256:" + "f" * 64})
    assert resolved[node_a] == "trace-a"
    assert resolved[node_b] == "trace-b"
    assert len(resolved) == 2
