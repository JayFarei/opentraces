"""Doctor Trace Trails event-log integrity panel (plan 054 phase 1)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from opentraces.core import doctor
from opentraces.core.doctor import _trail_event_log_status, exit_code
from opentraces.core.trails import TrailEventDraft, append_event_batch
from opentraces.core.trails.event_log import EVENT_LOG_REF


def _git(repo: Path, *args: str, input: str | None = None, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        input=input,
        text=True,
        env=env,
    ).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _append_fixture_event(repo: Path) -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id="tr-doctor",
                step_index=1,
                capture_method=["doctor_probe"],
                payload={"snapshot_id": "doctor-snapshot", "limitations": []},
            ),
        ],
        writer="doctor-test",
    )


def _tamper_first_event(repo: Path) -> None:
    head = _git(repo, "rev-parse", EVENT_LOG_REF)
    raw = _git(repo, "show", f"{head}:events/000000000001.json")
    event = json.loads(raw)
    event["payload"]["snapshot_id"] = "tampered"
    tampered = json.dumps(event, sort_keys=True, indent=2) + "\n"

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "opentraces",
            "GIT_AUTHOR_EMAIL": "opentraces@local",
            "GIT_COMMITTER_NAME": "opentraces",
            "GIT_COMMITTER_EMAIL": "opentraces@local",
            "GIT_INDEX_FILE": str(repo / ".git" / "opentraces-doctor-test-index"),
        }
    )
    _git(repo, "read-tree", head, env=env)
    blob = _git(repo, "hash-object", "-w", "--stdin", input=tampered, env=env)
    _git(
        repo,
        "update-index",
        "--cacheinfo",
        f"100644,{blob},events/000000000001.json",
        env=env,
    )
    tree = _git(repo, "write-tree", env=env)
    commit = _git(repo, "commit-tree", tree, "-m", "tamper event log", env=env)
    _git(repo, "update-ref", EVENT_LOG_REF, commit, head)


def test_trail_event_log_status_missing_when_ref_absent(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    status = _trail_event_log_status(tmp_path)

    assert status["state"] == "missing"
    assert status["ref"] == EVENT_LOG_REF
    assert status["exists"] is False
    assert status["event_count"] == 0
    assert exit_code({"trail_event_log": status}) == 0


def test_trail_event_log_status_ok_for_valid_event_log(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_fixture_event(tmp_path)

    status = _trail_event_log_status(tmp_path)

    assert status["state"] == "ok"
    assert status["exists"] is True
    assert status["batch_count"] == 1
    assert status["event_count"] == 1
    assert status["batch_parents_linear"] is True
    assert status["content_hashes_valid"] is True
    assert status["event_chain_valid"] is True
    assert status["errors"] == []
    assert exit_code({"trail_event_log": status}) == 0


def test_trail_event_log_status_invalid_for_tampered_event_content(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_fixture_event(tmp_path)
    _tamper_first_event(tmp_path)

    status = _trail_event_log_status(tmp_path)

    assert status["state"] == "invalid"
    assert status["exists"] is True
    assert status["event_count"] == 1
    assert status["content_hashes_valid"] is False
    assert "event 1: content_hash mismatch" in status["errors"]
    assert "event 1: event_id mismatch" in status["errors"]
    assert exit_code({"trail_event_log": status}) == 3


def test_doctor_report_includes_trail_event_log_payload(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    marker = {"state": "missing", "ref": EVENT_LOG_REF}
    cfg = SimpleNamespace(
        security=SimpleNamespace(
            trufflehog=SimpleNamespace(enabled=False),
            llm_review=SimpleNamespace(),
        ),
        projects={},
        hf_token=None,
    )
    monkeypatch.setattr(doctor, "find_trufflehog", lambda: None)
    monkeypatch.setattr(doctor, "_review_llm_status", lambda _cfg: {})
    monkeypatch.setattr(doctor, "_project_review_policy", lambda _cwd: "auto")
    monkeypatch.setattr(doctor, "_security_tiers", lambda *_args: [])
    monkeypatch.setattr(doctor, "_schema_version", lambda: "test-schema")
    monkeypatch.setattr(doctor, "_post_processors", lambda _cwd: [])
    monkeypatch.setattr(doctor, "_entity_parser_status", lambda: {})
    monkeypatch.setattr(doctor, "_attribution_status", lambda _cwd: {})
    monkeypatch.setattr(doctor, "_watcher_status", lambda: {})
    monkeypatch.setattr(doctor, "_hook_installers", lambda: [])
    monkeypatch.setattr(doctor, "_post_commit_hook_status", lambda _cwd: {})
    monkeypatch.setattr(doctor, "_trail_event_log_status", lambda _cwd: marker)

    payload = doctor.report(cfg, tmp_path)

    assert payload["trail_event_log"] == marker
