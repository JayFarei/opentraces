"""#157 Part 1 — bundle secret-scan is a publish GATE (block on hit), NEVER a
trust factor. A capsule's source bundle is a raw ``git archive`` uploaded to a
PUBLIC HF dataset; a committed ``.env`` / key / hardcoded secret in a tracked
file would leak the moment publish completes. This suite proves:

  * archive hygiene drops committed ``.env`` / key / cert members before gzip;
  * the native trufflehog filesystem scan catches a hardcoded secret;
  * publish REFUSES a secret-bearing bundle by default (zero bytes out) and only
    proceeds with an explicit acknowledgement, which it stamps.

Mirrors ``tests/test_capsule_publish_redaction.py`` (planted-secret + monkeypatched
uploader byte-scan). trufflehog-gated REAL tests SKIP when the binary is absent —
CI never depends on trufflehog being installed.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
import types
from pathlib import Path

import pytest

from opentraces.core.capsule.contract import freeze_capsule
from opentraces.core.capsule.share import BUNDLE_FILENAME
from opentraces.security.trufflehog import find_trufflehog

# A marker used only as a monkeypatched finding's raw_match in the SYNTHETIC 4d
# (never a real detected secret there — the scan is patched).
AWS_SENTINEL_ID = "AKIAZ3MPUB6QX7FAKE99"

_HAS_TRUFFLEHOG = find_trufflehog() is not None
_HAS_OPENSSL = shutil.which("openssl") is not None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    return path


def _commit_all(repo: Path, msg: str = "c") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _extract_names(bundle_bytes: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as tf:
        return [m.name for m in tf.getmembers()]


def _capsule(bundle_meta: dict | None = None) -> dict:
    """A benign capsule (no secret) carrying the given bundle meta, built straight
    from ``freeze_capsule`` so the publish path's redaction + scan gates run."""

    return freeze_capsule(
        capsule_id="bundleseccap0001",
        source={"project_slug": "p", "trace_id": "t", "step_index": 1, "agent": "demo"},
        summary={"title": "ok", "what_happened": "did a thing", "failure": None,
                 "is_failure": False, "scope": "1"},
        test={"command": "pytest -q", "expected": {"kind": "nonzero_exit"},
              "cwd": "", "source": "declared"},
        environment={"dependencies": [], "language_ecosystem": "python", "setup": []},
        bundle=bundle_meta,
        intent={"headline": "x", "most_substantive_spec": None, "trigger": None},
        failing_step={"index": 1, "type": "agent", "error_excerpt": "", "had_error_marker": False},
        slice_payload={"slice_id": "s", "trace_id": "t", "steps": [], "limitations": []},
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": "n",
                               "system_layer": {"content": {}}},
        trail_anchors=[],
        repo_pin={"remote_url": None, "commit_sha": "abc", "changed_files": []},
        redaction={},
        render_state={"redaction": "unredacted", "closure": "closure_full", "replay": "x"},
        limitations=[],
        created_with="bundle-sec test",
    )


# --------------------------------------------------------------------------- #
# 4a — planted secret caught by the native filesystem scan (REAL, gated)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (_HAS_TRUFFLEHOG and _HAS_OPENSSL),
    reason="needs trufflehog + openssl for a deterministic offline detection",
)
def test_4a_planted_secret_caught_by_native_filesystem_scan(tmp_path):
    """A secret HARDCODED in a tracked source file (whose name evades the archive
    hygiene member-filter) is caught by the native trufflehog filesystem scan and
    BLOCKS. Uses the PrivateKey detector — a structural, offline, DETERMINISTIC
    signal. (The documented AWS example key is trufflehog-whitelisted → 0 findings,
    and the AWS detector is network-non-deterministic even under --no-verification,
    so a private key is the honest sentinel.) The throwaway key is generated at
    runtime into a temp repo; no key literal is committed anywhere in opentraces.
    """

    from opentraces.core.capsule.share import build_capsule_bundle, scan_bundle_bytes

    repo = _init_repo(tmp_path / "repo")
    pem = subprocess.run(
        ["openssl", "genrsa", "2048"], capture_output=True, text=True, check=True
    ).stdout
    # A tracked file whose NAME is not on the hygiene exclude-list (not id_rsa*,
    # *.pem, *.key), so it survives to be caught by content scanning — exactly the
    # "secret hardcoded in a tracked file" residual the ticket names.
    (repo / "credentials_backup.txt").write_text(pem, encoding="utf-8")
    (repo / "README.md").write_text("ordinary\n", encoding="utf-8")
    sha = _commit_all(repo, "plant secret")

    meta, bundle_bytes = build_capsule_bundle(repo, sha)
    # The hygiene filter must NOT have dropped the tracked source file.
    assert meta["excluded_members"] == []
    stamp, findings = scan_bundle_bytes(bundle_bytes)

    # Matched via raw_match (NOT raw) — the finding field on TruffleHogFinding.
    assert any("PRIVATE KEY" in f.raw_match for f in findings), findings
    assert stamp["blocked"] is True
    assert stamp["findings_count"] >= 1
    assert stamp["status"] == "blocked"
    assert stamp["trufflehog_version"]
    assert stamp["acknowledged"] is False


# --------------------------------------------------------------------------- #
# 4b — committed .env / keys excluded by archive hygiene (SYNTHETIC)
# --------------------------------------------------------------------------- #


def test_4b_committed_secrets_filtered_out(tmp_path):
    from opentraces.core.capsule.share import build_capsule_bundle

    repo = _init_repo(tmp_path / "repo")
    (repo / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    (repo / ".env.local").write_text("SECRET=hunter3\n", encoding="utf-8")
    (repo / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\nx\n", encoding="utf-8")
    (repo / "server.pem").write_text("-----BEGIN CERTIFICATE-----\ny\n", encoding="utf-8")
    (repo / "app.key").write_text("k\n", encoding="utf-8")
    (repo / ".netrc").write_text("machine x login y password z\n", encoding="utf-8")
    (repo / ".aws").mkdir()
    (repo / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    (repo / "README.md").write_text("ordinary\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    sha = _commit_all(repo, "with secrets")

    meta, bundle_bytes = build_capsule_bundle(repo, sha)
    names = _extract_names(bundle_bytes)
    bases = {Path(n).name for n in names}

    # Sensitive members are ABSENT from the shipped bytes.
    for gone in (".env", ".env.local", "id_rsa", "server.pem", "app.key", ".netrc"):
        assert gone not in bases, f"{gone} leaked into the bundle: {names}"
    assert not any(".aws" in Path(n).parts for n in names), names

    # Ordinary files survive.
    assert "README.md" in bases
    assert any(Path(n).name == "main.py" for n in names), names

    # Hygiene records what it dropped (additive provenance).
    assert set(meta["excluded_members"]) >= {".env", ".env.local", "id_rsa",
                                             "server.pem", "app.key", ".netrc"}

    # The re-tar path stays deterministic: two builds are byte-identical.
    m2, b2 = build_capsule_bundle(repo, sha)
    assert meta["sha256"] == m2["sha256"]
    assert bundle_bytes == b2


def test_4b_clean_bundle_is_byte_identical_to_raw_archive(tmp_path):
    """No sensitive members -> hygiene is a NO-OP and the bytes stay byte-identical
    to today's raw git-archive output (cross-machine reproducibility preserved)."""

    from opentraces.core.capsule.share import build_capsule_bundle

    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    sha = _commit_all(repo, "clean")

    raw = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", sha],
        capture_output=True, check=True,
    ).stdout
    import gzip

    expected = gzip.compress(raw, mtime=0)
    meta, bundle_bytes = build_capsule_bundle(repo, sha)
    assert bundle_bytes == expected
    assert meta["excluded_members"] == []


# --------------------------------------------------------------------------- #
# 4c — clean bundle passes and publishes (negative control)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _HAS_TRUFFLEHOG,
    reason="a clean-scan publish needs trufflehog to CERTIFY clean; without it the "
    "bundle is unscanned and #157 H3 fails the publish closed (see test_4e).",
)
def test_4c_clean_bundle_passes_and_publishes(tmp_path, monkeypatch):
    from opentraces.core.capsule.share import (
        build_capsule_bundle,
        publish_capsule,
        scan_bundle_bytes,
    )

    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("clean project\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    sha = _commit_all(repo, "clean")

    meta, bundle_bytes = build_capsule_bundle(repo, sha)
    stamp, findings = scan_bundle_bytes(bundle_bytes)
    assert stamp["blocked"] is False
    assert stamp["findings_count"] == 0
    assert stamp["status"] in ("clean", "skipped_no_trufflehog")

    captured: dict = {}

    class _FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, **kw):
            return None

        def upload_folder(self, *, folder_path, **kw):
            captured["capsule_json"] = (Path(folder_path) / "capsule.json").read_text("utf-8")
            captured["has_bundle"] = (Path(folder_path) / BUNDLE_FILENAME).exists()
            return types.SimpleNamespace(oid="rev123clean")

        def upload_file(self, **kw):
            captured["pinned"] = True

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    info = publish_capsule(
        _capsule(meta), repo_id="someone/caps", token="fake",
        bundle_bytes=bundle_bytes, require_clearance=False,
    )
    assert "capsule_json" in captured, "clean bundle failed to publish"
    assert captured["has_bundle"] is True
    body = json.loads(captured["capsule_json"])
    ss = body["bundle"]["secret_scan"]
    assert ss["blocked"] is False
    assert ss["status"] in ("clean", "skipped_no_trufflehog")
    assert info["capsule_id"] == "bundleseccap0001"


# --------------------------------------------------------------------------- #
# 4d — publish cannot emit a secret-bearing bundle (SYNTHETIC, forced finding)
# --------------------------------------------------------------------------- #


def test_4d_publish_cannot_emit_secret_bearing_bundle(tmp_path, monkeypatch):
    import opentraces.core.capsule.share as share_mod
    from opentraces.core.capsule.share import (
        BundleSecretFindingError,
        build_capsule_bundle,
        publish_capsule,
    )

    repo = _init_repo(tmp_path / "repo")
    # Hardcoded secret in a TRACKED source file (not a filtered member).
    (repo / "config.py").write_text(f'KEY = "{AWS_SENTINEL_ID}"\n', encoding="utf-8")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    sha = _commit_all(repo, "c")
    meta, bundle_bytes = build_capsule_bundle(repo, sha)

    # SYNTHETIC: force a finding regardless of trufflehog presence so the block
    # path is exercised on any host.
    class _F:
        detector_name = "AWS"
        raw_match = AWS_SENTINEL_ID

    def _fake_scan(bb, *, verify=False):
        return (
            {"status": "blocked", "blocked": True, "findings_count": 1,
             "scanned_at": "now", "trufflehog_version": "synthetic", "acknowledged": False},
            [_F()],
        )

    monkeypatch.setattr(share_mod, "scan_bundle_bytes", _fake_scan)

    captured: dict = {}

    class _FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, **kw):
            captured["create"] = True

        def upload_folder(self, *, folder_path, **kw):
            captured["uploaded"] = True
            captured["capsule_json"] = (Path(folder_path) / "capsule.json").read_text("utf-8")
            return types.SimpleNamespace(oid="revd")

        def upload_file(self, **kw):
            captured["pinned"] = True

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    # DEFAULT: publish REFUSES and moves zero bytes (uploader never called).
    with pytest.raises(BundleSecretFindingError):
        publish_capsule(
            _capsule(meta), repo_id="someone/caps", token="fake",
            bundle_bytes=bundle_bytes, require_clearance=False,
        )
    assert "uploaded" not in captured, "publish emitted a secret-bearing bundle!"

    # OVERRIDE: proceeds and stamps acknowledged=True on the shipped capsule.json.
    info = publish_capsule(
        _capsule(meta), repo_id="someone/caps", token="fake",
        bundle_bytes=bundle_bytes, require_clearance=False,
        i_accept_bundle_findings=True,
    )
    assert captured.get("uploaded") is True
    body = json.loads(captured["capsule_json"])
    ss = body["bundle"]["secret_scan"]
    assert ss["blocked"] is True
    assert ss["acknowledged"] is True
    assert ss["status"] == "blocked"
    assert info["capsule_id"] == "bundleseccap0001"


# --------------------------------------------------------------------------- #
# 4e — #157 H3: a scanner-absent bundle PUBLISH fails CLOSED. An UNSCANNED bundle
# cannot be certified free of secrets, so it does not leave to a PUBLIC dataset
# without an explicit override. (Non-publish paths never hit this gate; a capsule
# with NO bundle has nothing to scan and publishes normally.)
# --------------------------------------------------------------------------- #


def test_4e_publish_fails_closed_when_trufflehog_absent(tmp_path, monkeypatch):
    import opentraces.security.trufflehog as th_mod
    from opentraces.core.capsule.share import (
        BundleUnscannedError,
        build_capsule_bundle,
        publish_capsule,
    )

    # Force trufflehog ABSENT so the real scan returns skipped_no_trufflehog.
    monkeypatch.setattr(th_mod, "find_trufflehog", lambda: None)

    repo = _init_repo(tmp_path / "repo")
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    sha = _commit_all(repo, "clean")
    meta, bundle_bytes = build_capsule_bundle(repo, sha)

    captured: dict = {}

    class _FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, **kw):
            captured["create"] = True

        def upload_folder(self, *, folder_path, **kw):
            captured["uploaded"] = True
            captured["capsule_json"] = (Path(folder_path) / "capsule.json").read_text("utf-8")
            return types.SimpleNamespace(oid="rev4e")

        def upload_file(self, **kw):
            captured["pinned"] = True

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    # DEFAULT: a bundle-bearing capsule REFUSES (zero bytes: uploader never called).
    with pytest.raises(BundleUnscannedError):
        publish_capsule(
            _capsule(meta), repo_id="someone/caps", token="fake",
            bundle_bytes=bundle_bytes, require_clearance=False,
        )
    assert "uploaded" not in captured, "publish emitted an UNSCANNED bundle!"

    # OVERRIDE: proceeds and stamps acknowledged=True on the shipped capsule.json.
    info = publish_capsule(
        _capsule(meta), repo_id="someone/caps", token="fake",
        bundle_bytes=bundle_bytes, require_clearance=False,
        i_accept_bundle_findings=True,
    )
    assert captured.get("uploaded") is True
    body = json.loads(captured["capsule_json"])
    ss = body["bundle"]["secret_scan"]
    assert ss["status"] == "skipped_no_trufflehog"
    assert ss["acknowledged"] is True
    assert info["capsule_id"] == "bundleseccap0001"


def test_4e_no_bundle_publishes_normally_without_trufflehog(monkeypatch):
    # A capsule with NO bundle has nothing to scan; the scanner-absence gate never
    # applies and publish proceeds normally.
    import opentraces.security.trufflehog as th_mod
    from opentraces.core.capsule.share import publish_capsule

    monkeypatch.setattr(th_mod, "find_trufflehog", lambda: None)

    captured: dict = {}

    class _FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, **kw):
            return None

        def upload_folder(self, *, folder_path, **kw):
            captured["uploaded"] = True
            return types.SimpleNamespace(oid="revnb")

        def upload_file(self, **kw):
            captured["pinned"] = True

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    info = publish_capsule(
        _capsule(), repo_id="someone/caps", token="fake",
        bundle_bytes=None, require_clearance=False,
    )
    assert captured.get("uploaded") is True  # no bundle => publishes normally
    assert info["capsule_id"] == "bundleseccap0001"
