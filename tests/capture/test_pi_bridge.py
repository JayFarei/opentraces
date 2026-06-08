from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces.capture.pi import bridge


def _enable_pi_capture(project: Path) -> None:
    project.mkdir(exist_ok=True)
    (project / ".opentraces.json").write_text(json.dumps({"project_id": "test", "agents": ["pi"]}), encoding="utf-8")


def test_bridge_writes_validated_sidecar_and_drops_raw_body_by_default(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    _enable_pi_capture(project)
    spawned: list[dict] = []
    monkeypatch.setattr(bridge, "spawn_ingest", lambda envelope: spawned.append(envelope) or True)

    result = bridge.record_event({
        "event": "provider_request",
        "session_id": "pi-session",
        "session_file": str(tmp_path / "session.jsonl"),
        "cwd": str(project),
        "sequence": 1,
        "data": {
            "messages": [{"role": "user", "content": "hello"}],
            "raw_provider_payload": {"secret": "sk-test"},
        },
    })

    assert result.ok is True
    sidecar = Path(result.sidecar_path or "")
    row = json.loads(sidecar.read_text().splitlines()[0])
    assert row["schema_version"] == "opentraces.pi.sidecar.v1"
    assert row["data"]["messages"][0]["role"] == "user"
    assert "raw_provider_payload" not in row["data"]
    assert row["data"]["raw_provider_body_refs"]["raw_provider_payload"]["retained"] is False
    assert spawned == []


def test_bridge_retains_raw_body_only_when_opted_in(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    _enable_pi_capture(project)
    monkeypatch.setenv("OPENTRACES_PI_RETAIN_RAW_PROVIDER_BODIES", "1")

    result = bridge.record_event({
        "event": "provider_request",
        "session_id": "pi-session",
        "cwd": str(project),
        "data": {"raw_provider_payload": {"keep": True}},
    })

    assert result.ok is True
    assert result.raw_provider_body_retained is True
    row = json.loads(Path(result.sidecar_path or "").read_text().splitlines()[0])
    ref = row["data"]["raw_provider_body_refs"]["raw_provider_payload"]
    assert ref["retained"] is True
    assert ref["local_only"] is True
    assert Path(ref["path"]).exists()


def _set_tracking_mode(mode: str) -> None:
    from opentraces.core.config import load_config, save_config

    cfg = load_config()
    cfg.capture.tracking_mode = mode
    save_config(cfg)


def test_bridge_skips_capture_in_manual_mode_without_marker(tmp_path: Path) -> None:
    _set_tracking_mode("manual")
    project = tmp_path / "repo"
    project.mkdir()

    result = bridge.record_event({"event": "session_start", "cwd": str(project), "session_id": "s"})

    assert result.ok is True
    assert result.status == "capture_disabled"
    assert not (project / ".opentraces" / "pi" / "events").exists()


def test_bridge_captures_by_default_under_global_tracking(tmp_path: Path, monkeypatch) -> None:
    # Global tracking is the default; Pi capture is opt-out, consistent with
    # Claude/Codex. An un-init'd project auto-enrolls (private + review-required)
    # and captures, and the marker is backfilled with the pi agent.
    _set_tracking_mode("global")
    monkeypatch.setattr(bridge, "spawn_ingest", lambda envelope: True)
    project = tmp_path / "repo"
    project.mkdir()

    result = bridge.record_event({
        "event": "provider_request",
        "session_id": "s",
        "cwd": str(project),
        "data": {"messages": [{"role": "user", "content": "hi"}]},
    })

    assert result.ok is True
    assert result.status == "ok"
    assert bridge.capture_enabled_for_project(project) is True
    marker = json.loads((project / ".opentraces.json").read_text())
    assert "pi" in {str(agent).lower() for agent in marker.get("agents", [])}


def test_bridge_honors_explicit_project_exclusion_under_global(tmp_path: Path) -> None:
    _set_tracking_mode("global")
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".opentraces.json").write_text(
        json.dumps({"project_id": "x", "excluded": True}), encoding="utf-8"
    )

    result = bridge.record_event({"event": "session_start", "cwd": str(project), "session_id": "s"})

    assert result.ok is True
    assert result.status == "capture_disabled"
    assert not (project / ".opentraces" / "pi" / "events").exists()


def test_bridge_dedupes_replayed_extension_payloads(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _enable_pi_capture(project)
    payload = {"event": "session_start", "cwd": str(project), "session_id": "s", "sequence": 1, "data": {"reason": "start"}}

    first = bridge.record_event(payload)
    second = bridge.record_event(payload)

    assert first.status == "ok"
    assert second.status == "duplicate"
    assert len(Path(first.sidecar_path or "").read_text().splitlines()) == 1


def test_bridge_fail_open_for_malformed_event(tmp_path: Path) -> None:
    result = bridge.record_event({"event": "not-real", "cwd": str(tmp_path), "session_id": "s"})

    assert result.ok is False
    assert result.status == "error"
    assert "unsupported" in result.errors[0]


def test_bridge_strict_mode_raises_for_malformed_event(tmp_path: Path) -> None:
    with pytest.raises(bridge.PiBridgeError):
        bridge.record_event({"event": "not-real", "cwd": str(tmp_path), "session_id": "s"}, fail_open=False)


def test_bridge_agent_end_spawns_ingest(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    _enable_pi_capture(project)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("{}\n")
    spawned: list[dict] = []
    monkeypatch.setattr(bridge, "spawn_ingest", lambda envelope: spawned.append(envelope) or True)

    result = bridge.record_event({
        "event": "agent_end",
        "session_id": "pi-session",
        "session_file": str(transcript),
        "cwd": str(project),
        "data": {},
    })

    assert result.ok is True
    assert result.ingest_spawned is True
    assert spawned[0]["event"] == "agent_end"


def test_bridge_status_reports_cli_and_project_state(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _enable_pi_capture(project)
    status = bridge.bridge_status(project)

    assert status["schema_version"] == "opentraces.pi.bridge_status.v1"
    assert status["project"]["init_command"] == "opentraces init --agent pi"
    assert status["project"]["capture_enabled"] is True
    assert status["capture"]["raw_provider_bodies_default"] == "off"
    assert any(step["state"] == "needs_terminal" for step in status["checklist"])


def test_pi_setup_plan_git_hook_reflects_real_state(tmp_path: Path) -> None:
    """The git_hook step mirrors the real hook, not a static needs_terminal.

    Regression: setup_plan_json hardcoded git_hook to ``needs_terminal`` and
    never marked it optional, so the Pi startup status reported "1 setup step
    missing" forever even after ``opentraces setup git`` had installed the hook.
    """
    from opentraces.capture.git import install as git_hook
    from opentraces.capture.pi.install import setup_plan_json

    project = tmp_path / "repo"
    _enable_pi_capture(project)

    def _git_step(plan: dict) -> dict:
        return next(s for s in plan["steps"] if s["name"] == "git_hook")

    # No git repo / no hook yet -> actionable needs_terminal.
    assert _git_step(setup_plan_json(project_dir=project))["state"] == "needs_terminal"

    # Initialize a repo and install the post-commit hook.
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    assert git_hook.install(project) is True

    # Now the step reports ok and stops counting as missing.
    assert _git_step(setup_plan_json(project_dir=project))["state"] == "ok"
