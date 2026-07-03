"""C2 / #188 — workflow trust is by PROVENANCE, not dir-vs-file shape.

``resolve_workflow_reference`` labels ANY directory ``source_type="package"``.
The executor used to grant ``network="allow"`` + the real bucket
(``OT_OPENTRACES_DIR``) to anything ``source_type == "package"``, so a
third-party directory pinned by ``dataset new --workflow /tmp/evil`` was treated
as first-party TRUSTED — defeating the #188 isolation threat model.

Trust must come from provenance: a workflow is trusted iff its path is under the
bundled-templates package dir OR the installed-workflow registry dir
(``workflows_dir()``). An arbitrary external directory is UNTRUSTED even though
it is a directory. These tests are the spec:

  - an external temp directory resolves as ``package`` (shape) but is UNTRUSTED
    (provenance);
  - an installed workflow and a bundled template ARE trusted;
  - end-to-end, an external workflow's ``build_rows.py`` is handed NO
    ``OT_OPENTRACES_DIR`` and (skip-guarded on host capability) is network
    denied, while a bundled/installed template still reaches the real bucket.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from opentraces.core import paths as parent_paths
from opentraces.core.isolation import run_isolated
from opentraces.core.workflow_runner import execute_workflow
from opentraces.core.workflows import (
    compute_workflow_digest,
    resolve_workflow_reference,
    workflows_dir,
)


_SKILL_MD = "---\nname: {name}\ndescription: trust fixture\nmode: agent-skill\n---\n\n# {name}\n"

# Emits what the scrubbed child actually saw: the bucket seam var (or <absent>)
# and whether a socket connect succeeded (network-deny proof, skip-guarded).
_PROBE_BUILD_ROWS = '''\
import json, os, socket
from pathlib import Path

net = "unknown"
try:
    s = socket.socket(); s.settimeout(4)
    s.connect(("1.1.1.1", 443)); net = "connected"; s.close()
except Exception as exc:
    net = "blocked:" + type(exc).__name__
row = {"ot_dir": os.environ.get("OT_OPENTRACES_DIR", "<absent>"), "net": net}
Path(os.environ["OT_DATASET_OUTPUT"]).write_text(
    json.dumps(row, sort_keys=True) + "\\n", encoding="utf-8"
)
'''


def _write_package(root: Path, name: str, script: str) -> Path:
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    (root / "scripts" / "build_rows.py").write_text(script, encoding="utf-8")
    return root


def _install_package(name: str, script: str) -> Path:
    return _write_package(workflows_dir() / name, name, script)


def _host_can_deny_network(tmp_path: Path) -> bool:
    probe = run_isolated(
        [sys.executable, "-c", "print('x')"], cwd=str(tmp_path), network="deny"
    )
    return probe.achieved.network_denied


# --- provenance-based trust classification ----------------------------------


def test_external_directory_is_untrusted_despite_package_shape(tmp_path):
    """The core C2 unit: a resolved external directory is ``package`` by shape
    but UNTRUSTED by provenance."""
    from opentraces.core.workflows import is_trusted_workflow

    external = _write_package(tmp_path / "evil", "evil", _PROBE_BUILD_ROWS)
    pkg = resolve_workflow_reference(str(external))
    # Shape says package (unchanged); provenance says untrusted (the fix).
    assert pkg.source_type == "package"
    assert is_trusted_workflow(pkg) is False


def test_installed_workflow_is_trusted(tmp_path):
    from opentraces.core.workflows import is_trusted_workflow, load_workflow

    _install_package("installed-curator", _PROBE_BUILD_ROWS)
    pkg = load_workflow("installed-curator")
    assert is_trusted_workflow(pkg) is True


def test_bundled_template_is_trusted():
    from opentraces.core.workflows import _bundled_templates_dir, is_trusted_workflow

    templates_dir = _bundled_templates_dir()
    assert templates_dir is not None, "bundled templates dir must resolve on an editable install"
    template = templates_dir / "skill-command-trajectory-eval-v1"
    pkg = resolve_workflow_reference(str(template))
    assert pkg.source_type == "package"
    assert is_trusted_workflow(pkg) is True


# --- end-to-end: untrusted external gets no bucket + no network --------------


def test_external_workflow_gets_no_bucket_and_is_network_denied(tmp_path):
    """The #188 threat model, proven behaviorally: an external directory pinned
    as the workflow runs with NO ``OT_OPENTRACES_DIR`` (so it cannot reach the
    operator's real bucket through the seam) and, where the host supports it,
    network-denied. Before the fix this directory was treated as trusted and
    handed both the real bucket and network."""
    external = _write_package(tmp_path / "thirdparty", "thirdparty", _PROBE_BUILD_ROWS)
    pkg = resolve_workflow_reference(str(external))
    out = tmp_path / "rows.jsonl"

    execute_workflow(
        "thirdparty",
        scope={"scope": "adhoc"},
        output_path=out,
        executor="script",
        workflow_package=pkg,
    )
    row = json.loads(out.read_text(encoding="utf-8").strip())

    # Deterministic core proof: the untrusted child never got the bucket seam.
    assert row["ot_dir"] == "<absent>"

    # Network-deny proof, skip-guarded on host capability.
    if _host_can_deny_network(tmp_path):
        assert row["net"].startswith("blocked"), row["net"]


def test_bundled_installed_workflow_still_reaches_the_bucket(tmp_path):
    """The trusted side of the boundary is unchanged: an installed workflow
    still gets the real ``OT_OPENTRACES_DIR`` and network."""
    _install_package("trusted-curator", _PROBE_BUILD_ROWS)
    pkg = resolve_workflow_reference(str(workflows_dir() / "trusted-curator"))
    out = tmp_path / "rows.jsonl"

    execute_workflow(
        "trusted-curator",
        scope={"scope": "adhoc"},
        output_path=out,
        executor="script",
        workflow_package=pkg,
    )
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["ot_dir"] == str(parent_paths.OPENTRACES_DIR)
