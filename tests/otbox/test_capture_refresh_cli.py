"""Tests for `./otbox capture-refresh` (plan 071 R4 + R5, Agent C surface).

These tests drive the CLI subcommand through three states:

  1. ``--dry-run`` — never touches a box, just reports the plan.
  2. Full run against the in-tree echo scenario — produces an artifact
     under ``tests/otbox/captures/echo-meta/``. Cleans up after itself.
  3. SKIP semantics when the scenario's binary is not on PATH.

The CLI surface is the only thing under test here. Agent A's runner +
Agent B's scenario module are dependencies — if they have not landed
yet, the tests SKIP cleanly so this file can ship alongside them and
go green once the merge happens.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURES_ROOT = REPO_ROOT / "tests" / "otbox" / "captures"


def _venv_python() -> str:
    """Prefer the repo venv so the editable opentraces install resolves."""
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def _run_cli(*argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [_venv_python(), "-m", "tests.otbox", "capture-refresh", *argv]
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def _require_simulated_users():
    """Skip the test if Agents A + B's modules are not yet present."""
    try:
        importlib.import_module("tests.otbox.simulated_users.runner")
        importlib.import_module("tests.otbox.simulated_users.scenario")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"simulated_users modules not present yet (Agents A + B): {exc}"
        )


def _require_echo_scenario():
    """Skip if the echo-meta scenario itself hasn't shipped yet."""
    _require_simulated_users()
    scenario_mod = importlib.import_module("tests.otbox.simulated_users.scenario")
    try:
        scenario_mod.load_scenario("echo-meta")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"echo-meta scenario not present yet: {exc}")


def _cleanup_capture_dir(name: str) -> None:
    target = CAPTURES_ROOT / name
    if target.exists() and target.is_dir():
        # Guard rail: only delete inside the captures tree, only the two
        # artifact files we know we wrote. Never touch README.md / __init__.py.
        for filename in ("snapshot.tar.gz", "metadata.json"):
            f = target / filename
            if f.exists():
                f.unlink()
        try:
            target.rmdir()
        except OSError:
            # Directory not empty (operator may have left other files);
            # leave it alone.
            pass


# ---------------------------------------------------------------------------
# Test 1 — dry-run never produces an artifact
# ---------------------------------------------------------------------------
def test_dry_run_emits_summary():
    _require_echo_scenario()

    artifact_dir = CAPTURES_ROOT / "echo-meta"
    artifact_before = (artifact_dir / "snapshot.tar.gz").exists()

    proc = _run_cli("--scenario", "echo-meta", "--dry-run", "--json")
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"

    payload = json.loads(proc.stdout)
    assert payload["action"] == "capture-refresh"
    assert payload["status"] == "dry-run"
    assert payload["scenario"] == "echo-meta"
    assert payload["agent"] == "echo"
    assert payload["turn_count"] >= 1
    # The binary path field is set when the in-tree echo binary exists;
    # we don't assert its presence (Agent A owns the binary file).
    assert "binary_path" in payload
    assert "artifact_path" in payload
    assert "metadata_path" in payload

    # Dry-run must NOT have written an artifact.
    artifact_after = (artifact_dir / "snapshot.tar.gz").exists()
    assert artifact_after == artifact_before, "dry-run produced an artifact"


# ---------------------------------------------------------------------------
# Test 2 — full run against the echo scenario produces the artifact pair
# ---------------------------------------------------------------------------
def test_real_run_against_echo_produces_artifact():
    import shutil as _shutil

    _require_echo_scenario()
    # capture-refresh drives a real terminal via tmux + terminal-control; both
    # absent on CI runners -> skip cleanly rather than fail on rc=3 (surfaced
    # by the first on-main nightly).
    if not _shutil.which("tmux") or not _shutil.which("termctrl"):
        pytest.skip("tmux/termctrl not installed on PATH")

    # If the echo binary isn't on disk yet (Agent A), skip.
    echo_binary = (
        REPO_ROOT / "tests" / "otbox" / "simulated_users" / "_echo_binary.py"
    )
    if not echo_binary.exists():
        pytest.skip("echo binary not present yet (Agent A)")

    # Pre-clean so we know the artifact came from this run.
    _cleanup_capture_dir("echo-meta")

    try:
        proc = _run_cli("--scenario", "echo-meta", "--json")
        assert proc.returncode == 0, (
            f"capture-refresh failed: rc={proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        payload = json.loads(proc.stdout)
        assert payload["status"] == "ok", payload
        artifact_path = Path(payload["artifact_path"])
        metadata_path = Path(payload["metadata_path"])

        assert artifact_path.exists(), f"artifact missing: {artifact_path}"
        assert artifact_path.stat().st_size > 0, "artifact is empty"
        assert metadata_path.exists(), f"metadata missing: {metadata_path}"

        meta = json.loads(metadata_path.read_text())
        assert meta["scenario_name"] == "echo-meta"
        assert meta["agent"] == "echo"
        assert meta["turn_count"] >= 1
        assert meta["base_checkpoint"] == "c-installed-source"
        # captured_at must be ISO-shaped.
        assert "T" in meta["captured_at"]
        # Provenance fields must be present (values may be "unknown" in
        # extreme misconfigurations but the keys are non-optional).
        assert "scenario_digest" in meta
        assert "binary_version" in meta
        assert "opentraces_schema_version" in meta
        assert "opentraces_cli_version" in meta
    finally:
        _cleanup_capture_dir("echo-meta")


# ---------------------------------------------------------------------------
# Test 3 — missing binary on PATH → SKIP (exit 0), no artifact
# ---------------------------------------------------------------------------
def test_skip_when_real_binary_missing(tmp_path, monkeypatch):
    """Build a synthetic scenario whose binary_name is definitely absent
    from PATH, and assert capture-refresh SKIPs cleanly.

    We do not edit Agent B's scenarios/ catalogue. Instead we patch
    ``load_scenario`` at the CLI's import site to return a hand-rolled
    Scenario whose binary_name is unresolvable. This isolates the test
    from whatever real scenarios ship.
    """
    _require_simulated_users()
    scenario_mod = importlib.import_module("tests.otbox.simulated_users.scenario")

    # We do the patch in a subprocess via a small bootstrap script so the
    # patched load_scenario is the one the CLI sees.
    bootstrap = tmp_path / "run_with_missing_binary.py"
    bootstrap.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        # The bootstrap script runs from a tmp dir, so `tests` is not
        # on sys.path by default — add the repo root explicitly.
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from tests.otbox.simulated_users import scenario as _scn\n"
        "\n"
        "# Build a Scenario whose binary cannot exist on PATH.\n"
        "real_load = _scn.load_scenario\n"
        "\n"
        "def patched_load(name):\n"
        "    sc = real_load(name)\n"
        "    import dataclasses\n"
        "    return dataclasses.replace(\n"
        "        sc,\n"
        "        agent='claude',  # force PATH lookup, not echo special-case\n"
        "        binary_name='definitely-not-a-real-binary-xyz-471',\n"
        "    )\n"
        "\n"
        "_scn.load_scenario = patched_load\n"
        "# CLI imports load_scenario lazily inside cmd_capture_refresh,\n"
        "# so the patch is in place before resolution.\n"
        "from tests.otbox.cli import main\n"
        "sys.exit(main(['capture-refresh', '--scenario', 'echo-meta', '--json']))\n"
    )

    proc = subprocess.run(
        [_venv_python(), str(bootstrap)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    # SKIP path must exit 0, even though no agent ran.
    assert proc.returncode == 0, (
        f"missing-binary path should exit 0, got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    payload = json.loads(proc.stdout)
    assert payload["status"] == "skipped"
    assert "not found" in payload["reason"].lower()
    assert payload["binary_name"] == "definitely-not-a-real-binary-xyz-471"

    # No artifact should have been written.
    artifact = CAPTURES_ROOT / "echo-meta" / "snapshot.tar.gz"
    assert not artifact.exists(), "SKIP path produced an artifact"
