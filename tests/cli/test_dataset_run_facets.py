"""``ot dataset run --facet name=value`` — metadata scope facets (#212).

RED (against ``origin/feat/seal-family``, i.e. before this lane):

    $ ot dataset run demo-facet-test --facet model=anthropic/claude-sonnet-4 --json
    Usage: cli dataset run [OPTIONS] NAME
    Try 'cli dataset run --help' for help.

    Error: No such option: --facet Did you mean --trace?
    (exit 2)

GREEN below: ``--facet`` (repeatable) and the ``--model``/``--agent``
convenience aliases exist, compose with ``--scope``/``--project``/``--trace``,
resolve against the persisted bucket manifest, and the exact matched trace set
is asserted against the real CLI JSON output (``run.facet_resolution``) plus
the rows the run actually appends. A trace whose harness never captured a
version is excluded from a version scope and included in an unfaceted run.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.workflows import load_workflow, workflows_dir
from opentraces_schema import Agent, Step, TraceRecord


_BUILDER = r'''
import json, os, sys

otdir = os.environ.get("OT_OPENTRACES_DIR", "")
packet = json.load(open(os.environ["OT_RUN_PACKET"], encoding="utf-8"))

full_path = os.path.join(otdir, "facet_candidates.jsonl")
full = []
if os.path.exists(full_path):
    for line in open(full_path, encoding="utf-8"):
        line = line.strip()
        if line:
            full.append(json.loads(line))

candidate_ids = packet.get("candidate_trace_ids")
if candidate_ids is not None:
    wanted = {(c["project_slug"], c["trace_id"]) for c in candidate_ids}
    rows = [c for c in full if (c["project_slug"], c["source_trace_id"]) in wanted]
else:
    rows = full

with open(os.environ["OT_DATASET_OUTPUT"], "w", encoding="utf-8") as fh:
    for r in rows:
        out = {
            "source_trace_id": r["source_trace_id"],
            "source_unit_id": f"tu:{r['source_trace_id']}:trace",
            "summary": f"Work on {r['source_trace_id']}.",
        }
        fh.write(json.dumps(out, sort_keys=True) + "\n")
'''


# External review of issue #212 (finding 1): mirrors the bundled skill-opt-v1
# workflow's pre-fix bug -- it DELIBERATELY IGNORES the run packet's
# ``candidate_trace_ids`` and emits every candidate regardless of the
# resolved facet scope. Proves the runner enforces scoping itself rather than
# trusting the workflow to cooperate.
_BUILDER_IGNORES_SCOPE = r'''
import json, os, sys

otdir = os.environ.get("OT_OPENTRACES_DIR", "")

full_path = os.path.join(otdir, "facet_candidates.jsonl")
full = []
if os.path.exists(full_path):
    for line in open(full_path, encoding="utf-8"):
        line = line.strip()
        if line:
            full.append(json.loads(line))

# Deliberately never reads packet["candidate_trace_ids"] -- emits every
# candidate the workflow can see, regardless of the run's facet scope.
with open(os.environ["OT_DATASET_OUTPUT"], "w", encoding="utf-8") as fh:
    for r in full:
        out = {
            "source_trace_id": r["source_trace_id"],
            "source_unit_id": f"tu:{r['source_trace_id']}:trace",
            "summary": f"Work on {r['source_trace_id']}.",
        }
        fh.write(json.dumps(out, sort_keys=True) + "\n")
'''


_SKILL_MD = (
    "---\n"
    "name: {name}\n"
    "description: Deterministic facet-aware row emitter\n"
    "mode: agent-skill\n"
    "---\n\n# {name}\n\nProject rows from `candidate_trace_ids` when present.\n"
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


def _install_workflow(name: str, builder: str = _BUILDER) -> str:
    pkg = workflows_dir() / name
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    (pkg / "scripts" / "build_rows.py").write_text(builder, encoding="utf-8")
    return load_workflow(name).digest


def _trace(
    trace_id: str,
    *,
    agent_name: str,
    agent_version: str | None,
    agent_model: str | None,
) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name=agent_name, version=agent_version, model=agent_model),
        task={"description": f"Work on {trace_id}"},
        steps=[
            Step(step_index=1, role="user", content=f"Work on {trace_id}"),
            Step(step_index=2, role="agent", content="Done."),
        ],
    )


def _seed_world(project_slug: str = "demo") -> None:
    """Seed a multi-model/multi-version world: 4 traces, 2 models, 2 agent
    versions, one trace with no captured version. Persists the bucket
    manifest once (the honest O(N) sweep) so faceted runs read it O(1)."""

    from opentraces.core import paths
    from opentraces.core.bucket_store import bucket_manifest, write_trace_record

    specs = [
        ("trace-claude-v1", "claude-code", "2.1.143", "anthropic/claude-sonnet-4"),
        ("trace-claude-v2", "claude-code", "2.2.0", "anthropic/claude-sonnet-4"),
        ("trace-codex", "codex-cli", "0.31.0", "openai/gpt-5-codex"),
        ("trace-hermes-no-version", "hermes-agent", None, "z-ai/glm-5"),
    ]
    for trace_id, agent_name, agent_version, agent_model in specs:
        write_trace_record(
            _trace(
                trace_id,
                agent_name=agent_name,
                agent_version=agent_version,
                agent_model=agent_model,
            ),
            project_slug=project_slug,
            source_layer="canonical",
        )
    bucket_manifest(write=True)

    candidates_path = paths.OPENTRACES_DIR / "facet_candidates.jsonl"
    with candidates_path.open("w", encoding="utf-8") as fh:
        for trace_id, *_ in specs:
            fh.write(json.dumps({"project_slug": project_slug, "source_trace_id": trace_id}) + "\n")


def _create_facet_dataset(
    name: str, *, builder: str = _BUILDER, candidate_query: dict | None = None
) -> None:
    from opentraces.core.datasets import create_dataset

    digest = _install_workflow(f"{name}-curator", builder=builder)
    create_dataset(
        name,
        workflow_skill=f"{name}-curator",
        workflow_digest=digest,
        row_schema=_schema(),
        candidate_query=candidate_query,
    )


def _run(name: str, *extra_args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(
        main,
        ["dataset", "run", name, "--executor", "script", "--json", *extra_args],
    )
    try:
        payload = json.loads(result.output) if result.output.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw_output": result.output}
    return result.exit_code, payload


def test_facet_option_selects_exactly_matching_traces() -> None:
    _seed_world()
    _create_facet_dataset("facet-model-run")

    code, payload = _run(
        "facet-model-run", "--facet", "model=anthropic/claude-sonnet-4"
    )
    assert code == 0, payload
    resolution = payload["run"]["facet_resolution"]
    assert resolution["facets"] == {"model": "anthropic/claude-sonnet-4"}
    assert resolution["matched_count"] == 2
    assert {row["trace_id"] for row in resolution["matched"]} == {
        "trace-claude-v1",
        "trace-claude-v2",
    }
    # The appended rows mirror the resolved match set exactly.
    appended_ids = set()
    dataset_dir_rows = _read_appended_rows("facet-model-run")
    for row in dataset_dir_rows:
        appended_ids.add(row["source_trace_id"])
    assert appended_ids == {"trace-claude-v1", "trace-claude-v2"}


def test_version_scope_excludes_null_version_trace() -> None:
    _seed_world()
    _create_facet_dataset("facet-version-run")

    code, payload = _run(
        "facet-version-run", "--facet", "agent.version=2.1.143"
    )
    assert code == 0, payload
    resolution = payload["run"]["facet_resolution"]
    assert resolution["matched_count"] == 1
    assert [row["trace_id"] for row in resolution["matched"]] == ["trace-claude-v1"]
    appended_ids = {row["source_trace_id"] for row in _read_appended_rows("facet-version-run")}
    assert appended_ids == {"trace-claude-v1"}
    assert "trace-hermes-no-version" not in appended_ids


def test_unfaceted_run_includes_the_null_version_trace() -> None:
    _seed_world()
    _create_facet_dataset("facet-unfaceted-run")

    code, payload = _run("facet-unfaceted-run")
    assert code == 0, payload
    assert payload["run"].get("facet_resolution") is None
    appended_ids = {row["source_trace_id"] for row in _read_appended_rows("facet-unfaceted-run")}
    assert appended_ids == {
        "trace-claude-v1",
        "trace-claude-v2",
        "trace-codex",
        "trace-hermes-no-version",
    }


def test_faceted_run_leaves_bucket_manifest_digest_unchanged() -> None:
    """Digest gate: a faceted ``dataset run`` (a read over the manifest) must
    not perturb ``bucket_manifest``'s digest for the same world."""

    from opentraces.core.bucket_store import bucket_manifest

    _seed_world()
    _create_facet_dataset("facet-digest-run")
    digest_before = bucket_manifest(write=False, include_objects=False)["digest"]

    code, payload = _run("facet-digest-run", "--facet", "model=openai/gpt-5-codex")
    assert code == 0, payload

    digest_after = bucket_manifest(write=False, include_objects=False)["digest"]
    assert digest_after == digest_before


def test_model_and_agent_convenience_aliases() -> None:
    _seed_world()
    _create_facet_dataset("facet-alias-run")

    code, payload = _run("facet-alias-run", "--model", "openai/gpt-5-codex")
    assert code == 0, payload
    assert payload["run"]["facet_resolution"]["matched_count"] == 1
    assert [row["trace_id"] for row in payload["run"]["facet_resolution"]["matched"]] == [
        "trace-codex"
    ]

    _create_facet_dataset("facet-alias-run-2")
    code2, payload2 = _run("facet-alias-run-2", "--agent", "hermes-agent")
    assert code2 == 0, payload2
    assert [row["trace_id"] for row in payload2["run"]["facet_resolution"]["matched"]] == [
        "trace-hermes-no-version"
    ]


def test_unsupported_facet_name_is_a_loud_cli_error() -> None:
    """External review of issue #212 (finding 4c): the error must say WHY the
    name is rejected (not resolvable at O(manifest) cost) and point at
    ``trace query --facet`` for the full vocabulary -- not just a bare
    rejection."""
    _seed_world()
    _create_facet_dataset("facet-bad-name-run")

    code, payload = _run("facet-bad-name-run", "--facet", "survival.state=reverted")
    assert code == 2
    assert "unsupported --facet name" in payload["raw_output"]
    assert "not resolvable at O(manifest) cost" in payload["raw_output"]
    assert "trace query --facet" in payload["raw_output"]


def test_malformed_facet_flag_is_a_loud_cli_error() -> None:
    _seed_world()
    _create_facet_dataset("facet-bad-syntax-run")

    code, payload = _run("facet-bad-syntax-run", "--facet", "missing-equals")
    assert code == 2
    assert "name=value" in payload["raw_output"]


def test_scope_ignoring_workflow_rows_are_rejected_not_appended() -> None:
    """External review of issue #212 (finding 1, criticals a/c): facet scoping
    must be ENFORCED by the runner, not merely advisory. A workflow that
    ignores ``candidate_trace_ids`` and scans/emits every candidate anyway
    (the bundled skill-opt-v1 bug the review found) must still only append
    rows inside the resolved facet scope -- the runner validates every
    emitted row's source trace id against the allowed set and rejects
    (never appends) anything outside it."""

    _seed_world()
    _create_facet_dataset("facet-enforce-run", builder=_BUILDER_IGNORES_SCOPE)

    code, payload = _run(
        "facet-enforce-run", "--facet", "model=anthropic/claude-sonnet-4"
    )
    assert code == 0, payload
    resolution = payload["run"]["facet_resolution"]
    assert resolution["matched_count"] == 2

    # The builder emitted ALL 4 rows regardless of scope; the runner must
    # have rejected the 2 outside the facet match, appending only the 2 in
    # scope.
    appended_ids = {row["source_trace_id"] for row in _read_appended_rows("facet-enforce-run")}
    assert appended_ids == {"trace-claude-v1", "trace-claude-v2"}
    assert payload["run"]["facet_rejected_count"] == 2


def test_persisted_candidate_query_facets_are_honored_without_runtime_flag() -> None:
    """External review of issue #212 (finding 1, critical a): the canonical
    candidate-query resolution path must read a facet scope PERSISTED on the
    dataset's ``candidate_query`` (set at ``dataset new`` / schedule time),
    not just a runtime ``--facet`` CLI flag -- a scheduled run with no CLI
    flags at all must still narrow by its persisted facets."""

    _seed_world()
    _create_facet_dataset(
        "facet-persisted-run",
        candidate_query={
            "name": "facet-persisted-query",
            "scope": "all-projects",
            "facets": {"agent.name": "codex-cli"},
        },
    )

    # No --facet flag on the CLI invocation at all.
    code, payload = _run("facet-persisted-run")
    assert code == 0, payload
    resolution = payload["run"]["facet_resolution"]
    assert resolution is not None, "persisted candidate_query.facets was ignored"
    assert resolution["matched_count"] == 1
    assert [row["trace_id"] for row in resolution["matched"]] == ["trace-codex"]

    appended_ids = {
        row["source_trace_id"] for row in _read_appended_rows("facet-persisted-run")
    }
    assert appended_ids == {"trace-codex"}


def _read_appended_rows(name: str) -> list[dict]:
    from opentraces.core.datasets import dataset_path

    path = dataset_path(name) / "data" / "train.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows
