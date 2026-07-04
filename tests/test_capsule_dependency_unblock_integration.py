"""End-to-end integration: real capture → export → consumed-dep upgrade flips the
verdict (plan 089 U7). Hermetic — a file:// humanduration repo and the autouse
``_isolate_opentraces_global_state`` bucket isolation; no network, no outward calls.

This is the committed regression for the library axis proven live against
github.com/JayFarei/humanduration + HF + issue #1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "capsule_dep_unblock"))
from capture_client_session import capture_client_trace  # noqa: E402
from world import build_humanduration_repo  # noqa: E402

from opentraces.core.capsule.export import export_capsule  # noqa: E402
from opentraces.core.capsule.run import run_capsule_test  # noqa: E402


def _run_own(cap, **kwargs):
    """Re-test an OWN capsule (#208 fail-closed foreign gate): pass ``local_slug``
    == the capsule's own ``source.project_slug``, as the CLI does from the repo."""

    slug = (cap.get("source") or {}).get("project_slug")
    return run_capsule_test(cap, local_slug=slug, **kwargs)
from opentraces.core.capsule.share import build_capsule_bundle  # noqa: E402


def _client_head(client: Path) -> str:
    import subprocess
    return subprocess.run(["git", "-C", str(client), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_real_capture_to_dependency_unblock(tmp_path):
    # 1. A real captured client session -> a genuine bucket TraceRecord.
    client, result = capture_client_trace(tmp_path)
    assert result.action in ("new", "refreshed", "new_generation"), result
    trace_id = result.trace_id
    assert trace_id

    # 2. Export it as a capsule pinning the CONSUMED library at the buggy version
    #    (file:// so the test is hermetic — the live proof used the github URL).
    lib = build_humanduration_repo(tmp_path / "humanduration")
    pin = f"git+file://{lib['repo']}@v0.1.0"
    capsule = export_capsule(
        project_dir=client, trace_id=trace_id,
        test_command="python -m pytest -q test_delay.py",
        consumes=[{"kind": "package", "name": "humanduration", "pin": pin}],
    )
    # The consume entry is recorded (the local-path username is identity-scrubbed by
    # the redaction floor — the live proof used a github URL with no username).
    assert capsule["environment"]["consumes"][0]["name"] == "humanduration"
    assert capsule["source"]["trace_id"] == trace_id  # really from the captured trace

    # 3. Embed the hermetic source bundle of the real client repo.
    bmeta, bdata = build_capsule_bundle(client, _client_head(client))
    capsule["bundle"] = bmeta
    bundle = tmp_path / "capsule.bundle.tar.gz"
    bundle.write_bytes(bdata)

    # 4. The consumed-dependency upgrade — NOT a client change — flips the verdict.
    #    Overrides carry the full file:// spec (applied verbatim at run time, never
    #    stored/redacted), mirroring a maintainer naming the version to test.
    spec = {v: f"git+file://{lib['repo']}@{v}" for v in ("v0.1.0", "v0.2.0")}
    r_bug = _run_own(capsule, bundle_path=bundle, with_overrides={"humanduration": spec["v0.1.0"]})
    r_fix = _run_own(capsule, bundle_path=bundle, with_overrides={"humanduration": spec["v0.2.0"]})
    assert r_bug["verdict"] == "reproduces", r_bug
    assert r_fix["verdict"] == "fixed", r_fix

    # 5. Matrix resolves to the fixed version (what `--matrix` reports as resolved_in).
    resolved_in = next(
        (v for v in ("v0.1.0", "v0.2.0")
         if _run_own(capsule, bundle_path=bundle,
                             with_overrides={"humanduration": spec[v]})["verdict"] == "fixed"),
        None,
    )
    assert resolved_in == "v0.2.0"
