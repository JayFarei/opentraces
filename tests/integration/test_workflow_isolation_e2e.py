"""#188 — workflow script executor through the shared isolation primitive.

End-to-end through ``dataset run``:

* a workflow script CANNOT read a planted parent-env token (env scrub);
* a BUNDLED template still runs green under the scrubbed env and reaches the
  bucket via the ``OT_OPENTRACES_DIR`` seam even though ``$HOME`` is isolated;
* a THIRD-PARTY (``source_type=="file"``) source gets NO bucket var (its bucket
  path resolves to the isolated empty HOME);
* run summaries and row provenance carry the honesty labels ``reconstructable``
  + isolation ``sandbox_tier``; the raw ``current-agent`` path is
  ``reconstructable: false``.
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.core import paths as parent_paths
from opentraces.core.datasets import (
    create_dataset,
    dataset_path,
    read_row_provenance,
)
from opentraces.core.workflow_runner import execute_workflow, run_dataset_workflow
from opentraces.core.workflows import WorkflowPackage, compute_workflow_digest, workflows_dir


_LEAK_BUILD_ROWS = '''\
import json, os
from pathlib import Path

leaked = os.environ.get("PARENT_ONLY_SECRET", "<absent>")
rows = [{"id": "row1", "leaked": leaked}]
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    "".join(json.dumps(r, sort_keys=True) + "\\n" for r in rows), encoding="utf-8"
)
'''

# Imports opentraces under the scrubbed env and reports the resolved bucket +
# the child HOME, proving both the OT_OPENTRACES_DIR seam and HOME isolation.
_BUCKET_PROBE_BUILD_ROWS = '''\
import json, os
from pathlib import Path
from opentraces.core import paths

rows = [{
    "id": "row1",
    "bucket": str(paths.OPENTRACES_DIR),
    "home": os.environ.get("HOME", ""),
}]
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    "".join(json.dumps(r, sort_keys=True) + "\\n" for r in rows), encoding="utf-8"
)
'''

_STATIC_BUILD_ROWS = '''\
import json, os
from pathlib import Path

rows = [{"id": "row1", "n": 1}]
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    "".join(json.dumps(r, sort_keys=True) + "\\n" for r in rows), encoding="utf-8"
)
'''


def _install_workflow(name: str, script: str) -> Path:
    pkg = workflows_dir() / name
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: isolation fixture\nmode: agent-skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (pkg / "scripts" / "build_rows.py").write_text(script, encoding="utf-8")
    return pkg


def _new_dataset(name: str, script: str) -> None:
    create_dataset(
        name,
        workflow_skill=f"{name}-curator",
        workflow_digest="sha256:isolation",
        row_schema={"type": "object", "additionalProperties": True},
    )
    _install_workflow(f"{name}-curator", script)


def _first_output_row(run_dir: Path) -> dict:
    text = (run_dir / "output_rows.jsonl").read_text(encoding="utf-8")
    return json.loads(text.strip().splitlines()[0])


# --- planted parent-env token is invisible ----------------------------------


def test_workflow_script_cannot_read_a_planted_parent_env_token(monkeypatch):
    secret = "SECRET-TOKEN-DO-NOT-LEAK-ZZZ99"
    monkeypatch.setenv("PARENT_ONLY_SECRET", secret)
    _new_dataset("iso-leak", _LEAK_BUILD_ROWS)

    result = run_dataset_workflow("iso-leak", executor="script")

    row = _first_output_row(result.run_dir)
    # The scrubbed child never inherited the parent secret.
    assert row["leaked"] == "<absent>"

    output_text = (result.run_dir / "output_rows.jsonl").read_text(encoding="utf-8")
    assert secret not in output_text
    train = (dataset_path("iso-leak") / "data" / "train.jsonl").read_text(encoding="utf-8")
    assert secret not in train


# --- bundled template green under scrub + OT_OPENTRACES_DIR seam -------------


def test_bundled_template_runs_green_and_reaches_the_bucket_under_scrub():
    _new_dataset("iso-bundled", _BUCKET_PROBE_BUILD_ROWS)
    expected_bucket = str(parent_paths.OPENTRACES_DIR)

    result = run_dataset_workflow("iso-bundled", executor="script", dry_run=True)

    # Green (no exception, status ok) and one row emitted.
    assert result.status == "ok"
    row = _first_output_row(result.run_dir)
    # The OT_OPENTRACES_DIR seam resolves the child's bucket to the REAL bucket,
    # even though HOME was scrubbed to a throwaway dir (import opentraces worked).
    assert row["bucket"] == expected_bucket
    # HOME isolation: the child's HOME differs from the operator's real HOME.
    assert row["home"] != parent_paths.OPENTRACES_DIR.parent.as_posix()
    assert row["home"] != ""


# --- third-party (file) source gets NO bucket var ---------------------------


def test_third_party_file_source_gets_no_bucket_var(tmp_path):
    # Build a workflow package on disk and drive it as a THIRD-PARTY source
    # (source_type="file") through the primitive. No OT_OPENTRACES_DIR is handed
    # over, so its bucket resolves to the isolated (throwaway) HOME, never the
    # operator's real bucket.
    pkg_dir = tmp_path / "thirdparty"
    (pkg_dir / "scripts").mkdir(parents=True)
    (pkg_dir / "SKILL.md").write_text(
        "---\nname: thirdparty\ndescription: file source\nmode: agent-skill\n---\n# thirdparty\n",
        encoding="utf-8",
    )
    (pkg_dir / "scripts" / "build_rows.py").write_text(
        _BUCKET_PROBE_BUILD_ROWS, encoding="utf-8"
    )
    pkg = WorkflowPackage(
        name="thirdparty",
        path=pkg_dir,
        digest=compute_workflow_digest(pkg_dir),
        source_type="file",
    )
    out = tmp_path / "rows.jsonl"
    execute_workflow(
        "thirdparty",
        scope={"scope": "adhoc"},
        output_path=out,
        executor="script",
        workflow_package=pkg,
    )
    row = json.loads(out.read_text(encoding="utf-8").strip())
    # No bucket var -> the child resolves paths.OPENTRACES_DIR under its isolated
    # HOME, NOT the operator's real bucket.
    assert row["bucket"] != str(parent_paths.OPENTRACES_DIR)
    assert row["bucket"].endswith("/.opentraces")


# --- honesty labels on run summary + row provenance -------------------------


def test_run_summary_and_row_provenance_carry_reconstructable_and_tier():
    _new_dataset("iso-labels", _STATIC_BUILD_ROWS)
    result = run_dataset_workflow("iso-labels", executor="script")

    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["reconstructable"] is True
    assert summary["isolation"]["sandbox_tier"] in {"none", "jail"}

    provenance = read_row_provenance("iso-labels")
    assert provenance, "expected at least one appended row's provenance"
    for row_prov in provenance.values():
        assert row_prov["reconstructable"] is True
        assert row_prov["isolation"]["sandbox_tier"] in {"none", "jail"}


def test_current_agent_path_is_labeled_not_reconstructable():
    create_dataset(
        "iso-current-agent",
        workflow_skill="iso-current-agent-curator",
        workflow_digest="sha256:current",
    )
    result = run_dataset_workflow("iso-current-agent", executor="current-agent")
    assert result.status == "instructions"
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    # Raw agent emission: the LLM output is not a recorded input, so it is
    # honestly labeled reconstructable=false with no isolation tier.
    assert summary["reconstructable"] is False
    assert summary["isolation"]["sandbox_tier"] == "none"
