"""Plan 081 — global/manual tracking mode + auto-enrollment.

These tests rely on the autouse ``_isolate_opentraces_global_state``
fixture (tests/conftest.py), which redirects HOME and ~/.opentraces to a
tmp dir so enrollment never touches the developer's real config.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from opentraces.core import config as cfgmod
from opentraces.core.config import (
    CONFIG_PATH,
    Config,
    ProjectConfig,
    auto_enroll_if_global,
    load_config,
    load_project_config,
    opted_in_projects,
    project_is_opted_in,
    save_config,
)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _set_mode(mode: str) -> None:
    cfg = load_config()
    cfg.capture.tracking_mode = mode
    save_config(cfg)


# --------------------------------------------------------------------------- #
# R1 — config field + migration default
# --------------------------------------------------------------------------- #


def test_default_tracking_mode_is_global():
    assert Config().capture.tracking_mode == "global"
    assert load_config().capture.tracking_mode == "global"


def test_old_config_without_key_loads_as_global():
    # Simulate a pre-plan-081 config: no capture block at all.
    CONFIG_PATH.write_text(
        json.dumps({"config_version": load_config().config_version})
    )
    assert load_config().capture.tracking_mode == "global"


def test_tracking_mode_round_trips():
    _set_mode("manual")
    assert load_config().capture.tracking_mode == "manual"
    _set_mode("global")
    assert load_config().capture.tracking_mode == "global"


# --------------------------------------------------------------------------- #
# R2 / R7 — auto-enroll under global mode, git and non-git
# --------------------------------------------------------------------------- #


def test_auto_enroll_global_git(tmp_path):
    _set_mode("global")
    proj = tmp_path / "repo"
    proj.mkdir()
    _git_init(proj)

    assert not project_is_opted_in(proj)
    assert auto_enroll_if_global(proj) is True
    assert project_is_opted_in(proj)
    # Marker written into the repo (R4 "always write marker" decision).
    assert (proj / ".opentraces.json").is_file()
    assert load_project_config(proj)["agents"] == ["claude-code", "codex-cli", "pi"]
    # Registered in the global registry.
    assert str(proj.resolve()) in opted_in_projects(load_config())


def test_auto_enroll_global_non_git(tmp_path):
    _set_mode("global")
    proj = tmp_path / "plain_dir"
    proj.mkdir()

    assert auto_enroll_if_global(proj) is True
    assert project_is_opted_in(proj)
    assert (proj / ".opentraces.json").is_file()


def test_auto_enroll_is_idempotent(tmp_path):
    _set_mode("global")
    proj = tmp_path / "repo"
    proj.mkdir()

    assert auto_enroll_if_global(proj) is True
    # Second call: already enrolled, no change.
    assert auto_enroll_if_global(proj) is False


def test_auto_enroll_repairs_marker_registry_drift(tmp_path):
    _set_mode("manual")
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "already-opted-in",
    }))

    assert project_is_opted_in(proj)
    assert str(proj.resolve()) not in opted_in_projects(load_config())
    assert auto_enroll_if_global(proj) is True
    assert str(proj.resolve()) in opted_in_projects(load_config())

    slug = load_config().projects[str(proj.resolve())].slug
    project_json = cfgmod.PROJECTS_DIR / slug / "project.json"
    assert project_json.is_file()
    assert json.loads(project_json.read_text())["path"] == str(proj.resolve())


def test_auto_enroll_global_repairs_legacy_agent_list(tmp_path):
    _set_mode("global")
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "legacy-global-project",
        "agents": ["claude-code"],
    }))

    assert auto_enroll_if_global(proj) is True
    assert load_project_config(proj)["agents"] == ["claude-code", "codex-cli", "pi"]


# --------------------------------------------------------------------------- #
# Per-project `excluded` opt-out beats global mode (release-gate CAP-4)
# --------------------------------------------------------------------------- #


def test_auto_enroll_never_mutates_bare_excluded_marker(tmp_path):
    """A bare committed ``{"excluded": true}`` marker is the documented
    per-project opt-out. Under global mode a hook fire must not mint a
    project_id, backfill agents, or register the project — the marker
    stays byte-identical."""
    _set_mode("global")
    proj = tmp_path / "repo"
    proj.mkdir()
    _git_init(proj)
    marker = proj / ".opentraces.json"
    marker.write_text('{"excluded": true}')
    marker_before = marker.read_bytes()

    assert auto_enroll_if_global(proj) is False
    assert marker.read_bytes() == marker_before
    assert str(proj.resolve()) not in opted_in_projects(load_config())


def test_auto_enroll_never_mutates_enrolled_excluded_marker(tmp_path):
    """An ENROLLED project that opted out (`config set excluded true
    --project`) keeps its marker byte-identical on the auto-enroll hot
    path: no codex-cli/pi agent backfill — backfilling `pi` would flip
    the Pi bridge's capture decision back on."""
    _set_mode("global")
    proj = tmp_path / "repo"
    proj.mkdir()
    marker = proj / ".opentraces.json"
    marker.write_text(json.dumps({
        "marker_version": "2",
        "project_id": "enrolled-excluded-project",
        "excluded": True,
        "review_policy": "review",
        "push_policy": "manual",
        "remotes": {},
        "active_remote": None,
        "agents": ["claude-code"],
    }))
    marker_before = marker.read_bytes()

    assert auto_enroll_if_global(proj) is False
    assert marker.read_bytes() == marker_before
    assert load_project_config(proj)["agents"] == ["claude-code"]
    assert str(proj.resolve()) not in opted_in_projects(load_config())


# --------------------------------------------------------------------------- #
# R3 — manual mode is a strict no-op
# --------------------------------------------------------------------------- #


def test_auto_enroll_manual_is_noop(tmp_path):
    _set_mode("manual")
    proj = tmp_path / "repo"
    proj.mkdir()
    _git_init(proj)

    assert auto_enroll_if_global(proj) is False
    assert not project_is_opted_in(proj)
    assert not (proj / ".opentraces.json").exists()
    assert str(proj.resolve()) not in opted_in_projects(load_config())


# --------------------------------------------------------------------------- #
# R6 — auto-enrolled projects default to private + review-required
# --------------------------------------------------------------------------- #


def test_auto_enrolled_policy_is_private_review(tmp_path):
    _set_mode("global")
    proj = tmp_path / "repo"
    proj.mkdir()
    auto_enroll_if_global(proj)

    policy = load_project_config(proj)
    assert policy["review_policy"] == "review"
    assert policy["push_policy"] == "manual"
    # The safety net: the effective project defaults are private.
    assert ProjectConfig().default_visibility == "private"


# --------------------------------------------------------------------------- #
# the enrollment marker must not perturb the Trace Trails worktree fingerprint
# --------------------------------------------------------------------------- #


def test_worktree_tree_invariant_to_marker(tmp_path):
    """Writing ``.opentraces.json`` must not change ``tree_id``.

    ``tree_id`` fingerprints the user's worktree for lineage anchoring and
    rewind materialization. opentraces' own enrollment marker is not user
    content, so enrolling a project must leave the fingerprint unchanged.
    This is the invariant that lets global-mode auto-enroll write the
    marker inside the capture hook without corrupting the snapshot the
    same hook takes.
    """
    from opentraces.core.trails import write_worktree_tree

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("print('x')\n")

    before = write_worktree_tree(tmp_path)
    (tmp_path / ".opentraces.json").write_text('{"project_id": "abc123"}')
    after = write_worktree_tree(tmp_path)

    assert before == after
    # Sanity: a real user-content change DOES move the fingerprint.
    (tmp_path / "app.py").write_text("print('y')\n")
    assert write_worktree_tree(tmp_path) != before


# --------------------------------------------------------------------------- #
# doctor surfaces the active mode
# --------------------------------------------------------------------------- #


def test_doctor_reports_tracking_mode():
    from opentraces.core import doctor

    _set_mode("manual")
    assert doctor.report(load_config()).get("tracking_mode") == "manual"
    _set_mode("global")
    assert doctor.report(load_config()).get("tracking_mode") == "global"


# --------------------------------------------------------------------------- #
# best-effort contract — never raises into the hook path
# --------------------------------------------------------------------------- #


def test_auto_enroll_swallows_errors(monkeypatch, tmp_path):
    _set_mode("global")
    proj = tmp_path / "repo"
    proj.mkdir()

    def _boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cfgmod, "register_project", _boom)
    # Must not propagate; returns False.
    assert auto_enroll_if_global(proj) is False
