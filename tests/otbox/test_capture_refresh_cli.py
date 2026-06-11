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


# ---------------------------------------------------------------------------
# issue #49 — pre-snapshot contract-floor gate
# ---------------------------------------------------------------------------
def test_dry_run_envelope_frozen_and_human_text_surfaces_floors():
    """The dry-run --json envelope keeps its pre-#49 shape (hard rule: no
    shape changes to existing envelopes); the declared contract floors are
    surfaced in the HUMAN dry-run text instead."""
    _require_simulated_users()
    scenario_mod = importlib.import_module("tests.otbox.simulated_users.scenario")
    try:
        scenario_mod.load_scenario("claude-with-revert")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"claude-with-revert scenario not present: {exc}")

    proc = _run_cli("--scenario", "claude-with-revert", "--dry-run", "--json")
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    payload = json.loads(proc.stdout)
    assert "contract_floors" not in payload, (
        "dry-run --json envelope grew a new key; existing envelope shapes "
        "are frozen (grep journeys before changing)"
    )

    human = _run_cli("--scenario", "claude-with-revert", "--dry-run")
    assert human.returncode == 0, f"stderr: {human.stderr}"
    assert "contract floors" in human.stdout
    assert "require_revert_commit=True" in human.stdout


def test_expand_capture_refresh_turns_preserves_every_turn_field():
    """Template expansion must NOT strip Turn fields: the issue #49 per-turn
    contract (verify_command / verify_regex / expect_revert_commit /
    fresh_session) — and any field added later — has to survive the trip
    through ``_expand_capture_refresh_turns`` or the drive never sees it."""
    _require_simulated_users()
    from tests.otbox.cli import _expand_capture_refresh_turns
    from tests.otbox.simulated_users.runner import Turn

    turn = Turn(
        prompt="open {trace_id}",
        expect_regex="(?i)ok {trace_id}",
        timeout_s=42.5,
        verify_command=["git", "log", "--grep", "{trace_id}"],
        verify_regex="trace {trace_id}",
        expect_revert_commit=True,
        fresh_session=True,
    )
    (expanded,) = _expand_capture_refresh_turns(
        [turn], {"trace_id": "deadbeef"}
    )

    # String fields expand their placeholders.
    assert expanded.prompt == "open deadbeef"
    assert expanded.expect_regex == "(?i)ok deadbeef"
    assert expanded.verify_regex == "trace deadbeef"
    assert expanded.verify_command == ["git", "log", "--grep", "deadbeef"]
    # Every NON-expanded field rides through identically — introspective so
    # a future Turn field cannot be silently dropped again.
    expanded_fields = {"prompt", "expect_regex", "verify_regex", "verify_command"}
    for f in dataclasses.fields(Turn):
        if f.name in expanded_fields:
            continue
        assert getattr(expanded, f.name) == getattr(turn, f.name), (
            f"_expand_capture_refresh_turns dropped Turn.{f.name}"
        )


def _write_meta_scenario(name: str, body: str) -> Path:
    """Write a temp scenario TOML into the package scenarios dir (the only
    place ``load_scenario(name)`` resolves); caller unlinks in finally."""
    import textwrap

    scenarios_dir = (
        REPO_ROOT / "tests" / "otbox" / "simulated_users" / "scenarios"
    )
    path = scenarios_dir / f"{name}.toml"
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def _cleanup_meta_capture_dir(name: str) -> None:
    """Remove a temp meta-scenario's whole capture dir (incl. the gitignored
    footage/ byproduct). Guarded to the -meta namespace these tests own."""
    assert name.endswith("-meta"), name
    target = CAPTURES_ROOT / name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _teardown_box(payload: dict) -> None:
    """Tear down the box a FAIL/sub-contract run intentionally left up."""
    box_id = payload.get("box_id")
    if not box_id:
        return
    subprocess.run(
        [_venv_python(), "-m", "tests.otbox", "down", "--box", box_id],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _interactive_lane_available() -> bool:
    return bool(shutil.which("tmux")) and bool(shutil.which("termctrl"))


def test_real_run_sub_contract_floor_gate_blocks_artifact():
    """End-to-end through the REAL capture-refresh path (default-CI-safe):
    a scenario that declares ``min_traces = 2`` on the echo agent (which
    never mints a trace) must exit 4 with the sub-contract envelope and
    write NO artifact — proving the contract-floor gate actually runs."""
    _require_echo_scenario()
    if not _interactive_lane_available():
        pytest.skip("tmux/termctrl not installed on PATH")

    name = "echo-floor-gate-meta"
    path = _write_meta_scenario(
        name,
        f"""
        name = "{name}"
        description = "meta-test: contract-floor gate must reject a sub-contract capture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [initial_state]
        template = "single-file-python-project"

        [[turns]]
        prompt = "Add a farewell helper to src/app.py"
        expect_regex = "(?i)(I'll add|let me|adding)"
        timeout_s = 15

        [capture]
        artifact_dir = "{name}"
        expected_paths = ["src/app.py"]
        min_traces = 2
        """,
    )
    payload: dict = {}
    try:
        _cleanup_meta_capture_dir(name)
        proc = _run_cli("--scenario", name, "--json")
        assert proc.returncode == 4, (
            f"expected sub-contract rc=4, got {proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        payload = json.loads(proc.stdout)
        assert payload["status"] == "sub-contract", payload
        assert payload["artifact_written"] is False
        assert any("min_traces" in v for v in payload["violations"]), payload
        artifact = CAPTURES_ROOT / name / "snapshot.tar.gz"
        assert not artifact.exists(), "sub-contract run wrote an artifact"
    finally:
        path.unlink(missing_ok=True)
        _teardown_box(payload)
        _cleanup_meta_capture_dir(name)


def test_real_run_turn_verification_failure_fails_capture():
    """End-to-end through the REAL capture-refresh path (default-CI-safe):
    a turn whose ``verify_regex`` cannot match the box state must FAIL the
    run (rc=3) naming the assertion, and write NO artifact. This is the
    test that catches per-turn contract fields being stripped between the
    scenario TOML and the drive — a stripped contract would PASS here."""
    _require_echo_scenario()
    if not _interactive_lane_available():
        pytest.skip("tmux/termctrl not installed on PATH")

    name = "echo-verify-fail-meta"
    marker = "marker-that-cannot-exist-zzz-471"
    path = _write_meta_scenario(
        name,
        f"""
        name = "{name}"
        description = "meta-test: a failing per-turn verify_regex must sink the capture"
        agent = "echo"
        binary_name = "_echo_binary.py"

        [initial_state]
        template = "single-file-python-project"

        [[turns]]
        prompt = "Add a farewell helper to src/app.py"
        expect_regex = "(?i)(I'll add|let me|adding)"
        timeout_s = 15
        verify_command = ["cat", "src/app.py"]
        verify_regex = "{marker}"

        [capture]
        artifact_dir = "{name}"
        expected_paths = ["src/app.py"]
        """,
    )
    payload: dict = {}
    try:
        _cleanup_meta_capture_dir(name)
        proc = _run_cli("--scenario", name, "--json")
        assert proc.returncode == 3, (
            f"expected verify-FAIL rc=3, got {proc.returncode} — a PASS here "
            f"means the per-turn contract was stripped before the drive\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        payload = json.loads(proc.stdout)
        assert payload["status"] == "failed", payload
        assert marker in (payload.get("error") or ""), payload
        artifact = CAPTURES_ROOT / name / "snapshot.tar.gz"
        assert not artifact.exists(), "failed run wrote an artifact"
    finally:
        path.unlink(missing_ok=True)
        _teardown_box(payload)
        _cleanup_meta_capture_dir(name)


def test_evaluate_capture_floors_reports_violations():
    """The floor evaluator names each violated floor and is silent when
    every floor is satisfied."""
    from tests.otbox.cli import _evaluate_capture_floors
    from tests.otbox.simulated_users.scenario import CaptureSpec

    spec = CaptureSpec(
        artifact_dir_name="x",
        min_traces=3,
        require_security_fingerprints=True,
        require_revert_commit=True,
    )

    # Everything below the floor → three named violations.
    facts = {"trace_count": 1, "security_fingerprinted": False, "has_revert_commit": False}
    violations = _evaluate_capture_floors(facts, spec)
    joined = " | ".join(violations)
    assert len(violations) == 3, violations
    assert "min_traces" in joined and "3" in joined
    assert "security" in joined.lower()
    assert "revert" in joined.lower()

    # Everything at/above the floor → no violations.
    ok_facts = {"trace_count": 4, "security_fingerprinted": True, "has_revert_commit": True}
    assert _evaluate_capture_floors(ok_facts, spec) == []


def test_evaluate_capture_floors_baseline_is_permissive():
    """A default CaptureSpec (min_traces=1, no requirements) passes a
    one-trace, no-security, no-revert world."""
    from tests.otbox.cli import _evaluate_capture_floors
    from tests.otbox.simulated_users.scenario import CaptureSpec

    spec = CaptureSpec(artifact_dir_name="x")
    facts = {"trace_count": 1, "security_fingerprinted": False, "has_revert_commit": False}
    assert _evaluate_capture_floors(facts, spec) == []
    # Zero traces still fails the implicit min_traces=1.
    zero = {"trace_count": 0, "security_fingerprinted": False, "has_revert_commit": False}
    assert _evaluate_capture_floors(zero, spec) != []
