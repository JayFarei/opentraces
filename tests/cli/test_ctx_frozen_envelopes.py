"""Shape-pin for the FROZEN ctx consumer envelopes (adversarial-review #2).

``ctx show / tree / step / list / info`` emit frozen ``opentraces.*`` consumer
contracts whose byte-shape downstream consumers (replay / RL / viewer /
dashboards / Slack / PR) lock against. The version tag IS the shape, so a field
may not be added without a schema_version bump — in particular the L5 uniform
``status`` header must NOT ride these payloads.

The B3a envelope sweep (``test_json_surface_sweep``) can only reach zero-arg
commands, so ``ctx show/tree/step/info`` (which need a ref) escape it. This test
runs them on a small fixture trace and pins the exact top-level key SET, so a
future silent mutation (e.g. re-injecting ``status``) is caught here even
though the sweep cannot see it.
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
from opentraces.cli.ctx import FROZEN_ENVELOPE_SCHEMA_VERSIONS, ctx_group
from opentraces.core.bucket_store import bucket_manifest, write_trace_record
from opentraces.core.context_tree.query import build_context_tree_projection
from opentraces.security import SECURITY_VERSION
from opentraces_schema import Agent, Step, TraceRecord

FIXTURES_DIR = Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"


# The frozen top-level key SETS. NONE carries the L5 ``status`` header — these
# stay byte-identical to origin/main. A new key here is a contract change that
# requires a schema_version bump.
FROZEN_TREE_SCHEMA = "opentraces.context_tree.v1"
EXPECTED_KEYS = {
    "tree": {
        "active_leaf_id",
        "nodes",
        "orphan_branch_roots",
        "root_node_id",
        "schema_version",
        "subagent_session_ids",
        "trace_id",
    },
    "show": {"layers", "node", "schema_version"},
    "step": {"layers", "node", "node_id", "schema_version"},
    "list": {"schema_version", "traces"},
    "info": {"info", "schema_version"},
}


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.fixture
def frozen_fixture(tmp_path, _isolate_opentraces_global_state):
    """A project with a captured linear context tree AND a bucket trace.

    Returns ``(project_dir, trace_id, node_id)``. ``ctx tree/show/step`` read
    the project projection (``--project``); ``ctx list/info`` read the isolated
    bucket seeded here.
    """
    _init_git_repo(tmp_path)
    base = FIXTURES_DIR / "context-tree-linear"
    spec = importlib.util.spec_from_file_location("_ctxfrozen_linear", base / "session.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)

    trace_id = "trace-context-tree-linear"
    record = TraceRecord(
        trace_id=trace_id,
        session_id="sess-linear",
        agent=Agent(name="claude-code", version="fixture-1.0"),
        steps=[],
    )
    emit_context_tree_events_from_record(
        project_dir=tmp_path,
        final_record=record,
        transcript_path=transcript,
    )
    projection = build_context_tree_projection(tmp_path)
    node_id = next(iter(projection.nodes_by_id))
    step_node = next(
        n for n in projection.nodes_by_id.values() if n.step_index is not None
    )

    # Seed a bucket trace so ``ctx list`` / ``ctx info`` resolve.
    bucket_trace = "t-frozen-bucket"
    rec = TraceRecord(
        trace_id=bucket_trace,
        session_id="s",
        agent=Agent(name="claude-code"),
        task={"description": "d"},
        steps=[Step(step_index=1, role="user", content="x")],
        outcome={"success": True, "committed": False},
    )
    rec.security.scanned = True
    rec.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        rec,
        project_slug="frozen-demo",
        source_layer="canonical",
        legacy_mirror=False,
        privacy_tier="medium",
    )
    bucket_manifest(write=True, heal=True)
    return tmp_path, trace_id, bucket_trace, node_id, step_node.step_index


def _run(args):
    result = CliRunner().invoke(ctx_group, args)
    assert result.exit_code == 0, f"args={args} rc={result.exit_code}\n{result.output}"
    return json.loads(result.output)


def test_ctx_frozen_envelopes_have_exact_shape_and_no_status(frozen_fixture):
    project, trace_id, bucket_trace, node_id, step_index = frozen_fixture
    proj = ["--project", str(project)]

    docs = {
        "tree": _run(["tree", trace_id, *proj, "--json"]),
        "show": _run(["show", node_id, *proj, "--json"]),
        "step": _run(["step", trace_id, str(step_index), *proj, "--json"]),
        "list": _run(["list", "--json"]),
        "info": _run(["info", bucket_trace, "--json"]),
    }

    for verb, doc in docs.items():
        assert set(doc.keys()) == EXPECTED_KEYS[verb], (
            f"ctx {verb} top-level keys drifted: {sorted(doc.keys())} != "
            f"{sorted(EXPECTED_KEYS[verb])} (frozen contract — a change needs a "
            "schema_version bump)"
        )
        # The regression this pins: NO L5 status header on a frozen envelope.
        assert "status" not in doc, f"ctx {verb} re-injected a status header"
        assert doc["schema_version"] in FROZEN_ENVELOPE_SCHEMA_VERSIONS

    assert docs["tree"]["schema_version"] == FROZEN_TREE_SCHEMA
    assert docs["show"]["schema_version"] == FROZEN_TREE_SCHEMA
    assert docs["step"]["schema_version"] == FROZEN_TREE_SCHEMA
    assert docs["list"]["schema_version"] == "opentraces.ctx.list.v2"
    assert docs["info"]["schema_version"] == "opentraces.ctx.info.v2"
