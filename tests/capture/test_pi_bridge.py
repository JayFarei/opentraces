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


def test_bridge_skips_capture_until_project_is_enabled(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()

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
