"""#157 Part 2 — sandbox v1: block FOREIGN capsules, enforce the bundle hash gate,
and stamp an HONEST ``sandbox_tier`` label. v1 buys the GATE + the LABEL, NOT real
containment: an S0 run's env-scrub + ``$HOME`` redirect provide NO filesystem
isolation, so the only honest tier is ``none``. The cardinal sin is OVER-CLAIMING
containment — an escape under S0 is EXPECTED and is NOT a failure.

Drives the real ``run.py`` execution path (mirrors ``tests/test_capsule_test_runner.py``).
``sandbox_tier`` is stamped at the top-level ``capsule["sandbox_tier"]`` key the U1
clamp reads (``core/capsule/replay.py::_read_trust_factors``), so a foreign-capsule
verdict floors.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.core.capsule.replay import build_replay_packet
from opentraces.core.capsule.run import (
    BundleHashMismatchError,
    SandboxNotOwnedError,
    run_capsule_test,
)
from opentraces.core.capsule.share import build_capsule_bundle
from opentraces.core.capsule.test_extract import declared_test


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def calc_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return {"repo": repo, "sha": sha}


def _capsule(sha: str, *, slug: str | None = None, command: str | None = None,
             meta: dict | None = None) -> dict:
    cmd = command or f"{sys.executable} -c \"from calc import add; assert add(2, 3) == 5\""
    cap: dict = {
        "schema_version": "opentraces.capsule.v1",
        "capsule_id": "sandboxcap00001",
        "repo_pin": {"commit_sha": sha},
        "test": declared_test(cmd, None),
    }
    if slug is not None:
        cap["source"] = {"project_slug": slug}
    if meta is not None:
        cap["bundle"] = meta
    return cap


# --------------------------------------------------------------------------- #
# 5a — S0 strips host env + redirects $HOME (drives real run.py)
# --------------------------------------------------------------------------- #


def test_5a_s0_strips_host_env_and_redirects_home(calc_repo, monkeypatch):
    monkeypatch.setenv("MY_HOST_SECRET_TOKEN", "supersecret-xyz-5a")
    real_home = os.environ.get("HOME", "")
    py = sys.executable
    cmd = (
        f"{py} -c \"import os;"
        "print('HOME='+os.environ.get('HOME',''));"
        "print('SEC='+os.environ.get('MY_HOST_SECRET_TOKEN','<absent>'))\""
    )
    cap = _capsule(calc_repo["sha"], command=cmd)
    res = run_capsule_test(cap, repo_dir=calc_repo["repo"], target_ref=calc_repo["sha"])

    out = res["output_excerpt"]
    assert "SEC=<absent>" in out, out                       # host secret scrubbed
    assert "supersecret-xyz-5a" not in out
    home_line = next(line for line in out.splitlines() if line.startswith("HOME="))
    assert home_line != f"HOME={real_home}"                 # $HOME redirected
    assert res["sandbox_tier"] == "none"                    # honest label
    assert cap["sandbox_tier"] == "none"


# --------------------------------------------------------------------------- #
# 5b — foreign capsule BLOCKED by default; override stamps honest tier + floor
# --------------------------------------------------------------------------- #


def test_5b_foreign_capsule_blocked_then_honest_tier(calc_repo, tmp_path):
    meta, data = build_capsule_bundle(calc_repo["repo"], calc_repo["sha"])
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(data)

    cap = _capsule(calc_repo["sha"], slug="some-foreign-project", meta=meta)
    with pytest.raises(SandboxNotOwnedError):
        run_capsule_test(cap, bundle_path=bundle, local_slug="my-local-project")

    cap2 = _capsule(calc_repo["sha"], slug="some-foreign-project", meta=meta)
    res = run_capsule_test(
        cap2, bundle_path=bundle, local_slug="my-local-project", unsafe_run_on_host=True
    )
    assert res["runnable"] is True
    assert res["sandbox_tier"] == "none"
    assert cap2["sandbox_tier"] == "none"                   # stamped at U1's read path

    packet = build_replay_packet(cap2, target_ref="HEAD")
    assert packet["trust_factors"]["sandbox_tier"] == "none"
    assert packet["verdict_trust"] == "floor"


# --------------------------------------------------------------------------- #
# 5c — own-capsule self-test WARNS-not-blocks (frictionless re-test loop)
# --------------------------------------------------------------------------- #


def test_5c_own_capsule_warns_not_blocks(calc_repo, tmp_path):
    meta, data = build_capsule_bundle(calc_repo["repo"], calc_repo["sha"])
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(data)

    cap = _capsule(calc_repo["sha"], slug="my-local-project", meta=meta)
    # NO override, own repo (slug matches) -> runs, still honestly tier none.
    res = run_capsule_test(cap, bundle_path=bundle, local_slug="my-local-project")
    assert res["runnable"] is True
    assert res["sandbox_tier"] == "none"
    assert cap["sandbox_tier"] == "none"


# --------------------------------------------------------------------------- #
# 5d — tampered bundle REFUSED before extraction (hash gate)
# --------------------------------------------------------------------------- #


def test_5d_tampered_bundle_refused_before_extract(calc_repo, tmp_path):
    meta, data = build_capsule_bundle(calc_repo["repo"], calc_repo["sha"])
    tampered = bytearray(data)
    tampered[-1] ^= 0xFF                                    # flip one byte
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(bytes(tampered))

    cap = _capsule(calc_repo["sha"], meta=meta)            # cap.bundle.sha256 == ORIGINAL
    with pytest.raises(BundleHashMismatchError):
        run_capsule_test(cap, bundle_path=bundle)


# --------------------------------------------------------------------------- #
# 5e — escape attempt matches the DECLARED tier (REAL, opt-in OT_REAL_SANDBOX)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not os.environ.get("OT_REAL_SANDBOX"), reason="opt-in real S0-escape proof"
)
def test_5e_s0_escape_matches_declared_none(calc_repo, tmp_path):
    secret_path = tmp_path / "host_secret.txt"
    secret_path.write_text("ESCAPE-SECRET-5e\n", encoding="utf-8")
    py = sys.executable
    cmd = f"{py} -c \"print(open(r'{secret_path}').read())\""
    cap = _capsule(calc_repo["sha"], command=cmd)

    res = run_capsule_test(cap, repo_dir=calc_repo["repo"], target_ref=calc_repo["sha"])

    # S0 does NOT contain an absolute-path read: the escape SUCCEEDS. EXPECTED.
    assert "ESCAPE-SECRET-5e" in res["output_excerpt"]     # escape happened
    # The ONLY failure would be over-claiming containment. tier stays honest.
    assert res["sandbox_tier"] == "none"
    assert cap["sandbox_tier"] == "none"
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["verdict_trust"] == "floor"
