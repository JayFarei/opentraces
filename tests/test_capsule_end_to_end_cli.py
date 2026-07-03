"""End-to-end CLI proof of the capsule-as-a-test loop (handoff test plan A).

The other capsule suites exercise the *library* (`run_capsule_test`, `freeze_capsule`,
`build_capsule_bundle`) directly. This one drives the real ``opentraces capsule``
command surface as a subprocess — exactly what a maintainer agent runs — through the
full troubleshoot loop on a genuine bug -> fix git repo:

  1. ``capsule open`` validates and returns the frozen envelope (the agent contract).
  2. ``capsule test --against <buggy>`` reproduces the failure (troubleshoot).
  3. ``capsule test --against <fixed>`` reports fixed (re-test).
  4. ``capsule test --from-bundle`` still reproduces AFTER THE REPO IS DELETED —
     the hermetic self-sufficiency proof.

Fully hermetic: a temp git repo, the venv's pytest as the in-fixture framework, no
network and no outward actions.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.core.capsule.contract import freeze_capsule
from opentraces.core.capsule.redaction import REDACTION_FLOOR
from opentraces.core.capsule.share import build_capsule_bundle, write_capsule_dir
from opentraces.core.capsule.test_extract import declared_test


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _rev(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _make_bug_fix_repo(root: Path) -> dict[str, str | Path]:
    """A tiny repo whose ``add`` subtracts, with a pytest that asserts add(2,3)==5."""

    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "buggy: add subtracts")
    buggy = _rev(repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: add adds")
    fixed = _rev(repo)
    return {"repo": repo, "buggy": buggy, "fixed": fixed}


def _build_capsule_with_bundle(repo: Path, pinned_sha: str) -> dict:
    """A complete, valid ``opentraces.capsule.v1`` envelope: pytest repro + bundle@sha."""

    bundle_meta, bundle_bytes = build_capsule_bundle(repo, pinned_sha)
    capsule = freeze_capsule(
        capsule_id="e2ecapsule000001",
        source={"project_slug": "p", "trace_id": "t", "step_index": 3, "agent": "codex-cli"},
        summary={"title": "add() subtracts", "what_happened": "add(2,3) returned -1",
                 "failure": "AssertionError", "is_failure": True, "scope": "2 commits"},
        test=declared_test(f"{sys.executable} -m pytest -q test_calc.py", None),
        environment={"dependencies": [], "language_ecosystem": "python", "setup": []},
        bundle=bundle_meta,
        intent={"headline": "make add() add", "most_substantive_spec": None, "trigger": None},
        failing_step={"index": 3, "type": "agent", "error_excerpt": "AssertionError", "had_error_marker": True},
        slice_payload={"slice_id": "s", "trace_id": "t", "steps": [], "limitations": []},
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": "n",
                               "system_layer": {"content": {"x": 1}}},
        trail_anchors=[],
        repo_pin={"remote_url": "https://github.com/o/r", "commit_sha": pinned_sha, "changed_files": ["calc.py"]},
        redaction={"manifest": {"floor": list(REDACTION_FLOOR), "floor_satisfied": True, "redactions_applied": 0}},
        render_state={"redaction": "redacted_ok", "closure": "closure_full", "replay": "replay_unverified"},
        limitations=[],
        created_with="opentraces e2e test",
    )
    return {"capsule": capsule, "bundle_bytes": bundle_bytes}


def _cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the real ``opentraces`` CLI as a subprocess (what a maintainer agent does)."""

    proc_env = dict(os.environ if env is None else env)
    return subprocess.run(
        [sys.executable, "-c", "from opentraces.cli import main; main()", *args],
        capture_output=True, text=True, env=proc_env,
    )


def _verdict(proc: subprocess.CompletedProcess) -> str:
    # `capsule test` prints the human line on stderr and the bare verdict word on stdout.
    return (proc.stdout.strip().splitlines() or [""])[-1].strip()


def test_capsule_cli_full_troubleshoot_loop(tmp_path):
    fix = _make_bug_fix_repo(tmp_path)
    repo, buggy, fixed = fix["repo"], fix["buggy"], fix["fixed"]

    built = _build_capsule_with_bundle(repo, buggy)  # bundle pinned at the BUGGY sha
    arts = write_capsule_dir(built["capsule"], tmp_path / "out", bundle_bytes=built["bundle_bytes"])
    capsule_json = arts["json"]
    assert "bundle" in arts and arts["bundle"].exists(), "sibling bundle must be written"

    # 1. CONSUME: `capsule open --json` validates + returns the full envelope.
    opened = _cli("capsule", "open", str(capsule_json), "--json")
    assert opened.returncode == 0, opened.stderr
    env = json.loads(opened.stdout)
    assert env["schema_version"] == "opentraces.capsule.v1"
    assert env["capsule_id"] == "e2ecapsule000001"
    assert env["test"]["command"].endswith("pytest -q test_calc.py")
    assert env["bundle"]["filename"] == "capsule.bundle.tar.gz"
    assert env["context_resume_packet"]["node_id"] == "n"

    # 2. TROUBLESHOOT: reproduce the failure at the buggy commit.
    repro = _cli("capsule", "test", str(capsule_json), "--against", buggy,
                 "--repo-dir", str(repo), "--unsafe-run-on-host", "--yes")
    assert repro.returncode == 0, repro.stderr
    assert _verdict(repro) == "reproduces", repro.stdout + repro.stderr
    assert "framework=pytest" in repro.stderr
    assert "🔴" in repro.stderr

    # 3. RE-TEST: the same repro reports fixed at the fix commit.
    refix = _cli("capsule", "test", str(capsule_json), "--against", fixed,
                 "--repo-dir", str(repo), "--unsafe-run-on-host", "--yes")
    assert refix.returncode == 0, refix.stderr
    assert _verdict(refix) == "fixed", refix.stdout + refix.stderr
    assert "🟢" in refix.stderr

    # 4. SELF-SUFFICIENCY: delete the repo entirely, then reproduce FROM THE BUNDLE.
    shutil.rmtree(repo)
    assert not repo.exists()
    hermetic = _cli("capsule", "test", str(capsule_json), "--from-bundle", "--unsafe-run-on-host", "--yes")
    assert hermetic.returncode == 0, hermetic.stderr
    assert _verdict(hermetic) == "reproduces", hermetic.stdout + hermetic.stderr
    assert "[bundle]" in hermetic.stderr, "must report it ran from the bundle, not git"


def test_capsule_cli_from_bundle_fixed_without_repo(tmp_path):
    # The symmetric half: a bundle pinned at the FIXED sha reports fixed with no repo.
    fix = _make_bug_fix_repo(tmp_path)
    built = _build_capsule_with_bundle(fix["repo"], fix["fixed"])  # bundle @ FIXED sha
    arts = write_capsule_dir(built["capsule"], tmp_path / "out", bundle_bytes=built["bundle_bytes"])
    shutil.rmtree(fix["repo"])

    proc = _cli("capsule", "test", str(arts["json"]), "--from-bundle", "--unsafe-run-on-host", "--yes")
    assert proc.returncode == 0, proc.stderr
    assert _verdict(proc) == "fixed", proc.stdout + proc.stderr
