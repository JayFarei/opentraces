"""Opt-in guarantees for current project-scoped local commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.config import (
    Config,
    opted_in_projects,
    project_is_opted_in,
    register_project,
    save_project_config,
    unregister_project,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Isolate ~/.opentraces for tests that mutate the registry."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    from opentraces.core import paths as _paths
    from opentraces.core import config as _config

    opentraces_dir = home / ".opentraces"
    opentraces_dir.mkdir()
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir()
    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
    return opentraces_dir


class TestProjectIsOptedIn:
    def test_returns_false_without_config(self, tmp_path) -> None:
        assert project_is_opted_in(tmp_path) is False

    def test_returns_true_after_save(self, tmp_path) -> None:
        save_project_config(tmp_path, {"review_policy": "review"})
        assert project_is_opted_in(tmp_path) is True


class TestRegistry:
    def test_register_is_idempotent(self, tmp_path) -> None:
        cfg = Config()
        assert register_project(cfg, tmp_path) is True
        assert register_project(cfg, tmp_path) is False
        assert str(tmp_path.resolve()) in opted_in_projects(cfg)
        slug = cfg.projects[str(tmp_path.resolve())].slug
        # Autouse tests monkeypatch PROJECTS_DIR per test; import it lazily so
        # this assertion follows the patched location.
        from opentraces.core.config import PROJECTS_DIR

        manifest = PROJECTS_DIR / slug / "project.json"
        assert json.loads(manifest.read_text())["path"] == str(tmp_path.resolve())

    def test_unregister_removes(self, tmp_path) -> None:
        cfg = Config()
        register_project(cfg, tmp_path)
        assert unregister_project(cfg, tmp_path) is True
        assert unregister_project(cfg, tmp_path) is False
        assert opted_in_projects(cfg) == []


class TestCaptureGate:
    def test_capture_noops_without_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        # A project dir that has NOT been initialized.
        project = tmp_path / "uninitialized"
        project.mkdir()
        result = runner.invoke(
            main, ["_capture", "--project-dir", str(project)]
        )
        assert result.exit_code == 0, result.output
        # Gate short-circuits before anything writes to disk.
        assert not (project / ".opentraces.json").exists()
        assert not (project / ".opentraces").exists()


class TestStatusGate:
    # The per-project opt-in gate + excluded-marker contract now lives on the
    # transitional ``status-inbox`` verb (#161 repurposed top-level ``status``
    # into the fleet bucket dashboard, which reads the global bucket and does
    # NOT require a project opt-in). These assert the inbox verb's gate verbatim.
    def test_status_inbox_refuses_without_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        # CliRunner's isolated_filesystem to chdir into an uninitialized dir.
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["status-inbox"])
            assert result.exit_code == 3
            assert "Not an opentraces project" in result.output

    def test_fleet_status_does_not_require_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        # The fleet dashboard reads the global bucket; an uninitialized cwd is
        # fine — it reports an empty bucket and exits 0 (never rc=3).
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(main, ["--json", "status"])
            assert result.exit_code == 0, result.output
            payload = json.loads(result.output.strip())
            assert payload["ok"] is True
            assert payload["schema_version"] == "opentraces.bucket.status.v1"
            assert payload["safety"]["verdict"] == "empty"

    def test_status_inbox_accepts_after_init(
        self, runner, isolated_home, tmp_path
    ) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            save_project_config(Path(td), {"review_policy": "review"})

            result = runner.invoke(main, ["status-inbox"])
            assert result.exit_code == 0, result.output
            assert "0 traces in inbox" in result.output

    def test_status_inbox_reports_bare_excluded_marker_cleanly(
        self, runner, isolated_home, tmp_path
    ) -> None:
        """A bare committed ``{"excluded": true}`` marker (no project_id)
        is a legal opt-out shape. ``status-inbox`` must report the excluded
        state, exit 0, and leave the marker byte-identical — no raw
        KeyError('project_id'), no minted id (release-gate CAP-4)."""
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            marker = Path(td) / ".opentraces.json"
            marker.write_text('{"excluded": true}')
            marker_before = marker.read_bytes()

            result = runner.invoke(main, ["status-inbox"])
            assert result.exit_code == 0, result.output
            assert "excluded" in result.output
            assert marker.read_bytes() == marker_before

    def test_status_inbox_json_reports_excluded_state(
        self, runner, isolated_home, tmp_path
    ) -> None:
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            (Path(td) / ".opentraces.json").write_text('{"excluded": true}')

            result = runner.invoke(main, ["--json", "status-inbox"])
            assert result.exit_code == 0, result.output
            # L3 (epic #129): explicit --json emits pure JSON, no sentinel.
            out = result.output
            blob = out.split("---OPENTRACES_JSON---", 1)[1] if "---OPENTRACES_JSON---" in out else out
            payload = json.loads(blob.strip())
            assert payload["status"] == "ok"
            assert payload["excluded"] is True


def _write_excluded_marker(project: Path) -> bytes:
    """Excluded-but-enrolled marker (carries a project_id); returns bytes."""
    marker = project / ".opentraces.json"
    marker.write_text(json.dumps({
        "marker_version": "2",
        "project_id": "excluded-0001",
        "excluded": True,
        "review_policy": "review",
    }, indent=2))
    return marker.read_bytes()


def _write_session_jsonl(session_dir: Path, session_id: str = "sess-cap-1") -> Path:
    """Minimal parseable Claude Code session JSONL (one full turn)."""
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{session_id}.jsonl"
    ts = "2026-04-15T07:00:01Z"
    lines = [
        {"type": "user", "sessionId": session_id, "timestamp": ts,
         "message": {"role": "user", "content": "prompt 1"}},
        {"type": "assistant", "sessionId": session_id, "timestamp": ts,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "tu_1", "name": "Read",
              "input": {"file_path": "x.py"}}],
             "usage": {"input_tokens": 10, "output_tokens": 10}}},
        {"type": "user", "sessionId": session_id, "timestamp": ts,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}]}},
    ]
    with path.open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


class TestCaptureExclusionGate:
    """Issue #60 item 3: `_capture` (and the `_capture_sessions_into_project`
    helper it calls) must honor the per-project exclusion gate, matching
    the ingest choke-point contract (plan 095 / CAP-4). An excluded marker
    still passes `project_is_opted_in` (marker-exists check), so without
    the gate `_capture` pollutes the bucket."""

    def test_capture_command_skips_excluded_project(
        self, runner, isolated_home, tmp_path
    ) -> None:
        project = tmp_path / "excluded-proj"
        project.mkdir()
        marker_before = _write_excluded_marker(project)
        session_dir = tmp_path / "sessions"
        _write_session_jsonl(session_dir)

        result = runner.invoke(
            main,
            ["_capture", "--session-dir", str(session_dir),
             "--project-dir", str(project)],
        )

        assert result.exit_code == 0, result.output
        # Nothing staged anywhere under the global projects dir.
        from opentraces.core.config import PROJECTS_DIR

        assert list(PROJECTS_DIR.glob("**/traces/*.jsonl")) == []
        # The marker is byte-identical — raw read, no migration write.
        assert (project / ".opentraces.json").read_bytes() == marker_before

    def test_capture_sessions_into_project_respects_exclusion(
        self, isolated_home, tmp_path
    ) -> None:
        from opentraces.cli import _capture_sessions_into_project

        project = tmp_path / "excluded-proj-direct"
        project.mkdir()
        marker_before = _write_excluded_marker(project)
        session_dir = tmp_path / "sessions-direct"
        _write_session_jsonl(session_dir, session_id="sess-cap-2")

        assert _capture_sessions_into_project(session_dir, project) == (0, 0)
        assert (project / ".opentraces.json").read_bytes() == marker_before


class TestBareMarkerTolerance:
    def test_load_project_config_tolerates_marker_without_project_id(
        self, tmp_path
    ) -> None:
        """Normalization must stay in memory for a marker with no
        project_id: persisting it would mean minting an id, and reads
        never mutate an opt-out marker."""
        from opentraces.core.config import load_project_config

        marker = tmp_path / ".opentraces.json"
        marker.write_text('{"excluded": true}')
        marker_before = marker.read_bytes()

        data = load_project_config(tmp_path)

        assert data["excluded"] is True
        assert data["review_policy"] == "review"
        assert marker.read_bytes() == marker_before
