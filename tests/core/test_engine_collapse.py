"""M1 engine collapse (#185): one runner, one versioned packet, dead executor gone.

RED-first structural guards + a golden byte-identity comparison. These name the
contract the collapse must hold:

* the permanently-dead ``claude-code-headless`` executor and its fake-rows
  environment hook are removed with no orphaned references in ``src/``;
* exactly one code path executes a workflow script (``_execute_script``, reached
  through ``execute_workflow``); the dataset runner calls that seam;
* every run writes ONE versioned run packet (``opentraces.workflow.run_packet.v1``)
  and the dataset packet is a SUPERSET carrying the fields templates + the
  lifecycle read (the unversioned dict is gone);
* the ``current-agent`` lifecycle-only path is preserved;
* the three bundled-template modes / consumers produce rows byte-identical to
  pre-collapse (a committed golden, captured from the base engine).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from opentraces.core import workflow_runner
from opentraces.core.datasets import create_dataset
from opentraces.core.workflow_runner import execute_workflow, run_dataset_workflow
from opentraces.core.workflows import workflows_dir

_SRC_ROOT = Path(workflow_runner.__file__).resolve().parents[2]
_FAKE_ROWS_HOOK = "OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS"

_GOLDEN_ROWS = (
    Path(__file__).resolve().parent / "goldens" / "engine_collapse_rows.jsonl"
).read_text(encoding="utf-8")

# Deterministic row emitter: rows are a pure function of ``scope["items"]`` only
# (never run_id/timestamps), so both engine entry points must reproduce them
# byte-for-byte. This is the same fixture used to capture the committed golden.
_GOLDEN_SCOPE = {"scope": "golden", "items": ["alpha", "beta", "gamma"]}
_FIXTURE_BUILD_ROWS = (
    "import json, os\n"
    "from pathlib import Path\n"
    "packet = json.loads(Path(os.environ['OT_RUN_PACKET']).read_text(encoding='utf-8'))\n"
    "scope = packet.get('scope') or {}\n"
    "items = list(scope.get('items') or [])\n"
    "rows = [{'n': i, 'label': str(item)} for i, item in enumerate(items)]\n"
    "Path(os.environ['OT_DATASET_OUTPUT']).write_text(\n"
    "    ''.join(json.dumps(r, sort_keys=True) + '\\n' for r in rows), encoding='utf-8'\n"
    ")\n"
)


def _install_fixture(name: str) -> None:
    pkg = workflows_dir() / name
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: golden fixture\nmode: agent-skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (pkg / "scripts" / "build_rows.py").write_text(_FIXTURE_BUILD_ROWS, encoding="utf-8")


def _new_golden_dataset(name: str) -> None:
    create_dataset(
        name,
        workflow_skill=f"{name}-curator",
        workflow_digest="sha256:golden",
        row_schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}, "label": {"type": "string"}},
            "additionalProperties": True,
        },
    )
    _install_fixture(f"{name}-curator")


# --- dead executor + hook removed -------------------------------------------


def test_dead_headless_executor_is_gone():
    assert not hasattr(workflow_runner, "_execute_claude_code_headless")


def test_fake_rows_hook_unreferenced_in_src():
    hits = [
        path
        for path in _SRC_ROOT.rglob("*.py")
        if _FAKE_ROWS_HOOK in path.read_text(encoding="utf-8")
    ]
    assert hits == [], f"{_FAKE_ROWS_HOOK} still referenced in: {hits}"


def test_headless_executor_rejected_by_the_runner_allowlist():
    _new_golden_dataset("collapse-allowlist")
    try:
        run_dataset_workflow(
            "collapse-allowlist", executor="claude-code-headless", scope=_GOLDEN_SCOPE
        )
    except ValueError as exc:
        assert "unsupported executor" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("claude-code-headless must no longer be a runnable executor")


# --- exactly one script-execution seam --------------------------------------


def test_exactly_one_subprocess_script_seam():
    source = Path(workflow_runner.__file__).read_text(encoding="utf-8")
    # _execute_script is the ONE script-execution seam. #188 moved the actual
    # subprocess boundary into the shared isolation primitive, so the module no
    # longer calls subprocess.run directly and instead drives exactly one
    # run_isolated( call.
    assert "subprocess.run(" not in source
    assert source.count("run_isolated(") == 1


def test_dataset_runner_calls_execute_workflow_not_execute_script_directly():
    runner_src = inspect.getsource(run_dataset_workflow)
    assert "execute_workflow(" in runner_src
    # The dataset runner delegates the subprocess boundary to execute_workflow;
    # it must not call the low-level script executor itself.
    assert "_execute_script(" not in runner_src


# --- one versioned run packet (superset), unversioned shape gone -------------


def test_dataset_run_packet_is_versioned_v1_superset():
    _new_golden_dataset("collapse-packet")
    result = run_dataset_workflow(
        "collapse-packet", executor="script", scope=_GOLDEN_SCOPE, dry_run=True
    )
    packet = json.loads((result.run_dir / "run_packet.json").read_text(encoding="utf-8"))
    # Versioned (the pre-collapse dataset dict had no schema_version at all).
    assert packet["schema_version"] == "opentraces.workflow.run_packet.v1"
    # Superset: base versioned keys + the dataset fields templates/lifecycle read.
    for key in (
        "scope",
        "candidate_query",
        "source_provenance",
        "security",
        "privacy_tier",
        "trail_freshness",
        "trail_freshness_policy",
        "workflow",
        "dataset_name",
        "run_id",
    ):
        assert key in packet, f"superset packet missing {key!r}"


def test_primitive_run_packet_is_versioned_v1():
    _install_fixture("collapse-primitive-curator")
    out = workflows_dir().parent / "prim_rows.jsonl"
    execute_workflow(
        "collapse-primitive-curator", scope=_GOLDEN_SCOPE, output_path=out, executor="script"
    )
    packet = json.loads(
        (out.parent / "run_packet.json").read_text(encoding="utf-8")
    )
    assert packet["schema_version"] == "opentraces.workflow.run_packet.v1"
    # Primitive packet stays the minimal shape (no dataset-only keys leaked in).
    assert "candidate_query" not in packet
    assert "dataset_name" not in packet


# --- current-agent lifecycle path preserved ---------------------------------


def test_current_agent_path_is_preserved_as_lifecycle_only():
    create_dataset(
        "collapse-current-agent",
        workflow_skill="collapse-current-agent-curator",
        workflow_digest="sha256:workflow",
    )
    result = run_dataset_workflow("collapse-current-agent", executor="current-agent")
    assert result.status == "instructions"
    assert result.append_summary.emitted_count == 0
    # It writes RUN.md instructions and executes no script.
    assert (result.run_dir / "RUN.md").exists()


# --- golden byte-identity across both engine entry points -------------------


def test_both_engine_paths_reproduce_the_committed_golden_rows():
    # Primitive path.
    _install_fixture("collapse-golden-primitive")
    primitive_out = workflows_dir().parent / "golden_primitive.jsonl"
    execute_workflow(
        "collapse-golden-primitive",
        scope=_GOLDEN_SCOPE,
        output_path=primitive_out,
        executor="script",
    )
    primitive_rows = primitive_out.read_text(encoding="utf-8")

    # Dataset path (dry-run avoids append/cursor side effects; output_rows.jsonl
    # is the raw executor output the golden captures).
    _new_golden_dataset("collapse-golden")
    dataset_result = run_dataset_workflow(
        "collapse-golden", executor="script", scope=_GOLDEN_SCOPE, dry_run=True
    )
    dataset_rows = (dataset_result.run_dir / "output_rows.jsonl").read_text(encoding="utf-8")

    assert primitive_rows == _GOLDEN_ROWS
    assert dataset_rows == _GOLDEN_ROWS
    # The whole point of the collapse: both entry points are the one engine.
    assert primitive_rows == dataset_rows
