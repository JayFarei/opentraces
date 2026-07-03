"""Workflow-isolation regressions from the M1 engine collapse (#185/#188).

Two defects that pass CI on a small/empty bucket but bite real users:

* DEFECT A — a new default 180s wall-clock timeout (``run_isolated``'s default)
  is applied to every workflow script because ``_execute_script`` never passed a
  ``timeout``. A legitimate large-bucket projection that runs > 3 min is killed
  (rc=124 -> ``WorkflowScriptError``). On origin/main ``subprocess.run`` had NO
  timeout. The cure plumbs a configurable timeout through ``execute_workflow``
  down to ``run_isolated`` and raises the default well above 180s.
* DEFECT B — an external-directory (untrusted) workflow gets NO
  ``OT_OPENTRACES_DIR``, so its ``paths.OPENTRACES_DIR`` resolves inside the
  isolated throwaway HOME -> an empty bucket -> zero rows with rc=0 and no
  diagnostic. The cure surfaces a warning naming the cause (no bucket access)
  instead of a silent empty dataset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces.core.workflow_runner import (
    DEFAULT_WORKFLOW_TIMEOUT,
    WorkflowScriptError,
    execute_workflow,
)
from opentraces.core.workflows import resolve_workflow_reference, workflows_dir


_SKILL_MD = "---\nname: {name}\ndescription: regression fixture\nmode: agent-skill\n---\n\n# {name}\n"

# Sleeps ``OT_SLEEP`` seconds then writes one row. Used to prove the timeout is
# configurable (a short limit kills it; a generous limit lets it finish).
_SLEEP_BUILD_ROWS = '''\
import json, os, time
from pathlib import Path

time.sleep(float(os.environ.get("OT_SLEEP", "0")))
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    json.dumps({"ok": True}, sort_keys=True) + "\\n", encoding="utf-8"
)
'''

# Emits one row PER bucket trace dir it can see; with no bucket seam it writes
# zero rows (the DEFECT B silent-empty shape).
_BUCKET_BUILD_ROWS = '''\
import json, os
from pathlib import Path

ot_dir = os.environ.get("OT_OPENTRACES_DIR")
rows = []
if ot_dir:
    traces = Path(ot_dir) / "bucket" / "traces"
    if traces.exists():
        rows = [{"trace": p.name} for p in sorted(traces.iterdir())]
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    "".join(json.dumps(r, sort_keys=True) + "\\n" for r in rows), encoding="utf-8"
)
'''


def _write_package(root: Path, name: str, script: str) -> Path:
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    (root / "scripts" / "build_rows.py").write_text(script, encoding="utf-8")
    return root


def _install_package(name: str, script: str) -> Path:
    """Install a TRUSTED (by-provenance) workflow under the registry dir."""
    return _write_package(workflows_dir() / name, name, script)


# --- DEFECT A: configurable timeout -----------------------------------------


def test_default_workflow_timeout_is_well_above_180s():
    """The projection default must not inherit run_isolated's 180s floor."""
    assert DEFAULT_WORKFLOW_TIMEOUT > 180
    # A large-bucket projection needs real headroom; assert a generous floor.
    assert DEFAULT_WORKFLOW_TIMEOUT >= 1800


def test_workflow_timeout_is_configurable_short_limit_kills(tmp_path):
    """A script that outruns a short configured timeout is killed."""
    _install_package("slow-curator", _SLEEP_BUILD_ROWS)
    out = tmp_path / "rows.jsonl"
    with pytest.raises(WorkflowScriptError):
        execute_workflow(
            "slow-curator",
            scope={"scope": "adhoc"},
            output_path=out,
            executor="script",
            extra_env={"OT_SLEEP": "5"},
            timeout=0.5,
        )


def test_workflow_timeout_is_configurable_generous_limit_completes(tmp_path):
    """The same script completes under a generous configured timeout."""
    _install_package("slow-curator2", _SLEEP_BUILD_ROWS)
    out = tmp_path / "rows.jsonl"
    result = execute_workflow(
        "slow-curator2",
        scope={"scope": "adhoc"},
        output_path=out,
        executor="script",
        extra_env={"OT_SLEEP": "1"},
        timeout=30,
    )
    assert result.row_count == 1
    assert json.loads(out.read_text(encoding="utf-8").strip()) == {"ok": True}


# --- DEFECT B: untrusted empty-bucket zero rows is surfaced ------------------


def test_untrusted_zero_row_run_surfaces_bucket_access_warning(tmp_path):
    """An untrusted external workflow that produces zero rows because it has no
    bucket access must SURFACE the cause, not silently succeed with an empty
    dataset."""
    external = _write_package(tmp_path / "thirdparty", "thirdparty", _BUCKET_BUILD_ROWS)
    pkg = resolve_workflow_reference(str(external))
    out = tmp_path / "rows.jsonl"

    result = execute_workflow(
        "thirdparty",
        scope={"scope": "adhoc"},
        output_path=out,
        executor="script",
        workflow_package=pkg,
    )

    assert result.row_count == 0
    assert result.warnings, "untrusted zero-row run must surface a warning"
    joined = " ".join(result.warnings).lower()
    assert "bucket" in joined and "untrusted" in joined


def test_trusted_zero_row_run_does_not_warn(tmp_path):
    """A TRUSTED workflow with bucket access that legitimately finds zero rows
    must NOT emit the no-bucket-access warning (no false positive)."""
    _install_package("trusted-empty", _BUCKET_BUILD_ROWS)
    pkg = resolve_workflow_reference(str(workflows_dir() / "trusted-empty"))
    out = tmp_path / "rows.jsonl"

    result = execute_workflow(
        "trusted-empty",
        scope={"scope": "adhoc"},
        output_path=out,
        executor="script",
        workflow_package=pkg,
    )
    assert result.row_count == 0
    assert not result.warnings
