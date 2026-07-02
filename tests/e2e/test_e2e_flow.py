"""End-to-end smoke tests for the current local opentraces flow.

Tests: init -> _capture -> status -> captured trace validation.
Uses real Claude Code sessions from this project.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from opentraces.cli import main


THIS_PROJECT_DIR = Path(os.environ["OPENTRACES_TEST_PROJECT_DIR"]) if "OPENTRACES_TEST_PROJECT_DIR" in os.environ else None


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _restore_cwd():
    previous = Path.cwd()
    try:
        yield
    finally:
        os.chdir(previous)


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal project directory for testing."""
    proj = tmp_path / "test-project"
    proj.mkdir()
    return proj


@pytest.fixture
def initialized_project(runner, project_dir):
    """Run init on a temp project directory and return the path."""
    with runner.isolated_filesystem(temp_dir=project_dir.parent):
        os.chdir(str(project_dir))
        result = runner.invoke(main, ["init", "--start-fresh"])
        assert result.exit_code == 0, f"init failed: {result.output}"
    return project_dir


@pytest.fixture
def real_session_file():
    """Find a real JSONL session file from this project's Claude Code sessions."""
    if THIS_PROJECT_DIR is None or not THIS_PROJECT_DIR.exists():
        pytest.skip("Set OPENTRACES_TEST_PROJECT_DIR to run session tests")
    sessions = list(THIS_PROJECT_DIR.glob("*.jsonl"))
    if not sessions:
        pytest.skip("No session files found")
    # Pick a mid-sized file (smallest may have 0 tool calls and get filtered)
    sorted_by_size = sorted(sessions, key=lambda p: p.stat().st_size)
    # Use the median file, not the smallest
    return sorted_by_size[len(sorted_by_size) // 2]


class TestInit:
    """Test the init command."""

    def test_init_creates_config_and_staging(self, runner, project_dir):
        """init creates .opentraces.json marker and global traces dir."""
        from opentraces.core.config import get_project_traces_dir

        os.chdir(str(project_dir))
        result = runner.invoke(main, ["init", "--start-fresh"])

        assert result.exit_code == 0
        marker = project_dir / ".opentraces.json"
        assert marker.exists(), ".opentraces.json marker not created"
        traces_dir = get_project_traces_dir(project_dir)
        assert traces_dir.exists(), "global traces dir not created"

        content = json.loads(marker.read_text())
        assert content["review_policy"] == "review"
        assert "project_id" in content

    def test_init_idempotent(self, runner, project_dir):
        """Running init twice does not error."""
        os.chdir(str(project_dir))
        runner.invoke(main, ["init", "--start-fresh"])
        result = runner.invoke(main, ["init", "--start-fresh"])
        assert result.exit_code == 0
        assert "Already initialized" in result.output


class TestCapture:
    """Test the _capture command."""

    def test_capture_stages_trace(
        self, runner, initialized_project, real_session_file, tmp_path
    ):
        """_capture with a real session JSONL produces a trace in staging/."""
        # Copy the real session file into a temp session dir
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        shutil.copy2(real_session_file, session_dir / real_session_file.name)

        os.chdir(str(initialized_project))
        result = runner.invoke(
            main,
            [
                "_capture",
                "--session-dir",
                str(session_dir),
                "--project-dir",
                str(initialized_project),
            ],
        )

        # _capture prints to stderr, check staging dir
        from opentraces.core.config import get_project_traces_dir
        staging = get_project_traces_dir(initialized_project)
        staged_files = list(staging.glob("*.jsonl"))
        assert len(staged_files) >= 1, (
            f"Expected at least 1 staged trace, got {len(staged_files)}. "
            f"Output: {result.output}"
        )


class TestStatus:
    """Test the status command."""

    def test_status_shows_project_info(self, runner, initialized_project):
        """status-inbox shows mode info and session count (the transitional
        per-project inbox verb; top-level `status` is now the fleet dashboard)."""
        os.chdir(str(initialized_project))
        result = runner.invoke(main, ["status-inbox"])

        assert result.exit_code == 0
        assert "review" in result.output  # review_policy visible in the header banner
        assert "sessions in inbox" in result.output or "session" in result.output

    def test_status_shows_remote(self, runner, initialized_project):
        """status-inbox shows that no dataset remote has been configured yet."""
        os.chdir(str(initialized_project))
        result = runner.invoke(main, ["status-inbox"])

        assert result.exit_code == 0
        assert "remote: not set" in result.output


class TestCapturedTrace:
    """Test review by loading staged traces programmatically."""

    def test_staged_trace_has_expected_fields(
        self, runner, initialized_project, real_session_file, tmp_path
    ):
        """A staged trace remains raw by default and still carries metrics."""
        from opentraces_schema import TraceRecord

        # Capture first
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        shutil.copy2(real_session_file, session_dir / real_session_file.name)

        os.chdir(str(initialized_project))
        runner.invoke(
            main,
            [
                "_capture",
                "--session-dir",
                str(session_dir),
                "--project-dir",
                str(initialized_project),
            ],
        )

        from opentraces.core.config import get_project_traces_dir
        staging = get_project_traces_dir(initialized_project)
        staged_files = list(staging.glob("*.jsonl"))
        if not staged_files:
            pytest.skip("No traces staged")

        # Load and validate the first staged trace
        data = staged_files[0].read_text().strip()
        record = TraceRecord.model_validate_json(data)

        assert record.security.scanned is False, "security should be opt-in by default"
        assert record.metadata["security"]["tools_applied"] == []
        assert record.metrics.total_steps > 0, "metrics.total_steps is 0"


class TestLoginMock:
    """Test login with mocked device code flow."""

    @patch("opentraces.cli._validate_and_save")
    @patch("opentraces.cli.load_config")
    def test_device_code_polling(self, mock_config, mock_validate, runner):
        """Mock the device code flow endpoints, verify polling works."""
        mock_cfg = MagicMock()
        mock_cfg.hf_token = None
        mock_config.return_value = mock_cfg

        # Mock requests to simulate the device code flow
        with patch("requests.post") as mock_post:
            # First call: device code request
            device_resp = MagicMock()
            device_resp.json.return_value = {
                "device_code": "test-device-code",
                "user_code": "TEST-CODE",
                "verification_uri": "https://huggingface.co/device",
                "interval": 0,  # no delay in tests
                "expires_in": 10,
            }
            device_resp.raise_for_status = MagicMock()

            # Second call: token response (success)
            token_resp = MagicMock()
            token_resp.json.return_value = {
                "access_token": "hf_test_access_token",
            }

            mock_post.side_effect = [device_resp, token_resp]

            with patch("webbrowser.open"):
                with patch("time.sleep"):
                    result = runner.invoke(main, ["auth", "login"])

            assert mock_validate.called or "TEST-CODE" in result.output
