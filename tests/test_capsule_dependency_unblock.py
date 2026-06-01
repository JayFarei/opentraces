"""Dependency-unblock axis: the fix lives in a CONSUMED dependency (plan 089).

The client source is byte-identical across every run; only the consumed dependency
version changes. Proves at the runner level:
  * pin humanduration@v0.1.0 -> reproduces (story fails: 1h30m -> 3600)
  * --with humanduration=v0.2.0 -> fixed (story passes: 1h30m -> 5400)
  * a matrix sweep reports which version resolves the story (resolved_in)
  * a `service` consume injects its endpoint as the client's env var, and an
    override changes what the client sees — with no client-source change.

Hermetic: a local humanduration git repo (file:// install) + a local client repo.
The live HF + GitHub legs are U5/U6.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "capsule_dep_unblock"))
from world import build_world, client_head  # noqa: E402

from opentraces.core.capsule.contract import freeze_capsule  # noqa: E402
from opentraces.core.capsule.redaction import REDACTION_FLOOR  # noqa: E402
from opentraces.core.capsule.run import run_capsule_test  # noqa: E402
from opentraces.core.capsule.share import build_capsule_bundle  # noqa: E402
from opentraces.core.capsule.test_extract import declared_test  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build_world(tmp_path_factory.mktemp("dep_unblock_world"))


def _client_capsule(world, *, consumes, command, expect=None):
    client = world["client"]
    bmeta, bdata = build_capsule_bundle(client, client_head(client))
    capsule = freeze_capsule(
        capsule_id="depunblock00001",
        source={"project_slug": "client", "trace_id": "t", "step_index": 1, "agent": "demo"},
        summary={"title": "delay('1h30m') != 5400", "what_happened": "consumed dep returns 3600",
                 "failure": "AssertionError", "is_failure": True, "scope": "1 file"},
        test=declared_test(command, expect),
        environment={"dependencies": [], "language_ecosystem": "python", "setup": [],
                     "consumes": consumes},
        bundle=bmeta,
        intent={"headline": "schedule after 1h30m", "most_substantive_spec": None, "trigger": None},
        failing_step={"index": 1, "type": "agent", "error_excerpt": "assert 3600 == 5400",
                      "had_error_marker": True},
        slice_payload={"slice_id": "s", "trace_id": "t", "steps": [], "limitations": []},
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": "n",
                               "system_layer": {"content": {}}},
        trail_anchors=[],
        repo_pin={"remote_url": None, "commit_sha": client_head(client), "changed_files": []},
        redaction={"manifest": {"floor": list(REDACTION_FLOOR), "floor_satisfied": True,
                                "redactions_applied": 0}},
        render_state={"redaction": "redacted_ok", "closure": "closure_full",
                      "replay": "replay_unverified"},
        limitations=[],
        created_with="opentraces dep-unblock test",
    ), bdata
    return capsule


def _pkg_capsule(world):
    repo = world["humanduration_repo"]
    consumes = [{"kind": "package", "name": "humanduration",
                 "pin": f"git+file://{repo}@v0.1.0"}]
    cap, bdata = _client_capsule(world, consumes=consumes,
                                 command="python -m pytest -q test_delay.py")
    return cap, bdata


def _bundle_file(tmp_path, bdata) -> Path:
    p = tmp_path / "capsule.bundle.tar.gz"
    p.write_bytes(bdata)
    return p


def test_pinned_buggy_dependency_reproduces(world, tmp_path):
    cap, bdata = _pkg_capsule(world)
    res = run_capsule_test(cap, bundle_path=_bundle_file(tmp_path, bdata))
    assert res["verdict"] == "reproduces", res
    assert res["consumes_used"] == {"humanduration": "v0.1.0"}


def test_upgrading_dependency_flips_to_fixed(world, tmp_path):
    cap, bdata = _pkg_capsule(world)
    res = run_capsule_test(cap, bundle_path=_bundle_file(tmp_path, bdata),
                           with_overrides={"humanduration": "v0.2.0"})
    assert res["verdict"] == "fixed", res
    assert res["consumes_used"] == {"humanduration": "v0.2.0"}


def test_matrix_resolves_to_the_fixed_version(world, tmp_path):
    # Same loop the CLI --matrix will drive: first version that flips to fixed.
    cap, bdata = _pkg_capsule(world)
    bundle = _bundle_file(tmp_path, bdata)
    resolved_in = None
    verdicts = {}
    for ver in ("v0.1.0", "v0.2.0"):
        res = run_capsule_test(cap, bundle_path=bundle, with_overrides={"humanduration": ver})
        verdicts[ver] = res["verdict"]
        if res["verdict"] == "fixed" and resolved_in is None:
            resolved_in = ver
    assert verdicts == {"v0.1.0": "reproduces", "v0.2.0": "fixed"}
    assert resolved_in == "v0.2.0"


def test_client_source_is_unchanged_across_versions(world, tmp_path):
    # The whole thesis: the fix is NOT in the client. The bundle (client source) is
    # byte-identical; only the consumed dep override differs between the two runs.
    cap, bdata = _pkg_capsule(world)
    cap2, bdata2 = _pkg_capsule(world)
    assert bdata == bdata2  # same client bundle bytes
    bundle = _bundle_file(tmp_path, bdata)
    r1 = run_capsule_test(cap, bundle_path=bundle, with_overrides={"humanduration": "v0.1.0"})
    r2 = run_capsule_test(cap, bundle_path=bundle, with_overrides={"humanduration": "v0.2.0"})
    assert (r1["verdict"], r2["verdict"]) == ("reproduces", "fixed")


def _versioned_lib(dest: Path, *, fixed: bool) -> str:
    """A humanduration repo whose v0.1.0 tag holds either buggy or fixed code, both
    declaring version 0.1.0 — to reproduce pip's (name, version) cache collision."""

    import subprocess

    dest.mkdir(parents=True, exist_ok=True)
    g = lambda *a: subprocess.run(["git", "-C", str(dest), *a], check=True, capture_output=True)
    g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (dest / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=61"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="humanduration"\nversion="0.1.0"\n[tool.setuptools]\npackages=["humanduration"]\n')
    pkg = dest / "humanduration"; pkg.mkdir()
    rx = r"(\d+)([hms])" if fixed else r"(\d+)(h)"
    (pkg / "__init__.py").write_text(
        "import re\n_U={'h':3600,'m':60,'s':1}\n"
        f'def parse(t):\n    return sum(int(v)*_U[u] for v,u in re.findall(r"{rx}", t))\n')
    g("add", "-A"); g("commit", "-q", "-m", "v0.1.0"); g("tag", "v0.1.0")
    return f"git+file://{dest}@v0.1.0"


def test_no_cache_collision_between_same_version_sources(world, tmp_path):
    # Two different sources both versioned 0.1.0 (a fork / moved tag). pip caches
    # wheels by (name, version); without --no-cache-dir the second install would
    # serve the first's wheel and flip the verdict wrongly. The runner must build
    # the pinned source every time.
    buggy = _versioned_lib(tmp_path / "libA", fixed=False)   # 0.1.0 -> 3600
    fixed = _versioned_lib(tmp_path / "libB", fixed=True)    # 0.1.0 -> 5400 (same version!)
    consumes = [{"kind": "package", "name": "humanduration", "pin": buggy}]
    cap, bdata = _client_capsule(world, consumes=consumes,
                                 command="python -m pytest -q test_delay.py")
    bundle = _bundle_file(tmp_path, bdata)
    assert run_capsule_test(cap, bundle_path=bundle,
                            with_overrides={"humanduration": buggy})["verdict"] == "reproduces"
    # Same version string, fixed source: must be FIXED, not a cached 'reproduces'.
    assert run_capsule_test(cap, bundle_path=bundle,
                            with_overrides={"humanduration": fixed})["verdict"] == "fixed"


def test_service_consume_injects_endpoint_env(world, tmp_path):
    # A `service` consume injects its endpoint as the client's env var; an override
    # changes what the client reads. Test passes (fixed) only when the env == v2 url.
    v2 = "https://convert.example/v2"
    consumes = [{"kind": "service", "name": "convert-api", "env": "CONVERT_API_URL",
                 "endpoint": "https://convert.example/v1", "deploy": "dpl_v1"}]
    # sys.executable (absolute) — no venv here, so bare `python` isn't on PATH.
    cmd = (f'{sys.executable} -c "import os,sys; '
           "sys.exit(0 if os.environ.get('CONVERT_API_URL')=='" + v2 + "' else 1)\"")
    # expect=None -> declared_test uses a nonzero-exit oracle.
    cap, bdata = _client_capsule(world, consumes=consumes, command=cmd)
    bundle = _bundle_file(tmp_path, bdata)
    # default endpoint (v1) -> the v2 check fails -> reproduces
    assert run_capsule_test(cap, bundle_path=bundle)["verdict"] == "reproduces"
    # override to v2 -> env matches -> exit 0 -> fixed
    res = run_capsule_test(cap, bundle_path=bundle, with_overrides={"convert-api": v2})
    assert res["verdict"] == "fixed", res
    assert res["consumes_used"] == {"convert-api": v2}
