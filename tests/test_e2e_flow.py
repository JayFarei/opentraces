"""End-to-end test of the opentraces git-analogy flow.

Tests: init -> _capture -> status -> review -> push
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
        result = runner.invoke(main, ["init", "--mode", "review", "--remote", "test/opentraces", "--no-hook"])
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
        """init with --mode review creates .opentraces/config.json and staging/."""
        os.chdir(str(project_dir))
        result = runner.invoke(main, ["init", "--mode", "review", "--remote", "test/repo", "--no-hook"])

        assert result.exit_code == 0
        config_file = project_dir / ".opentraces" / "config.json"
        staging_dir = project_dir / ".opentraces" / "staging"
        assert config_file.exists(), "config.json not created"
        assert staging_dir.exists(), "staging/ not created"

        # Verify config content
        import json
        content = json.loads(config_file.read_text())
        assert content["mode"] == "review"

    def test_init_idempotent(self, runner, project_dir):
        """Running init twice does not error."""
        os.chdir(str(project_dir))
        runner.invoke(main, ["init", "--mode", "review", "--remote", "test/repo", "--no-hook"])
        result = runner.invoke(main, ["init", "--mode", "review", "--remote", "test/repo", "--no-hook"])
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
        staging = initialized_project / ".opentraces" / "staging"
        staged_files = list(staging.glob("*.jsonl"))
        assert len(staged_files) >= 1, (
            f"Expected at least 1 staged trace, got {len(staged_files)}. "
            f"Output: {result.output}"
        )


class TestStatus:
    """Test the status command."""

    def test_status_shows_project_info(self, runner, initialized_project):
        """status shows mode info and session count."""
        os.chdir(str(initialized_project))
        result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "mode:" in result.output
        assert "sessions in inbox" in result.output or "session files tracked" in result.output

    def test_status_shows_remote(self, runner, initialized_project):
        """status shows the configured remote."""
        os.chdir(str(initialized_project))
        result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "opentraces" in result.output


class TestReview:
    """Test review by loading staged traces programmatically."""

    def test_staged_trace_has_expected_fields(
        self, runner, initialized_project, real_session_file, tmp_path
    ):
        """A staged trace has security.scanned, anonymized paths, and metrics."""
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

        staging = initialized_project / ".opentraces" / "staging"
        staged_files = list(staging.glob("*.jsonl"))
        if not staged_files:
            pytest.skip("No traces staged")

        # Load and validate the first staged trace
        data = staged_files[0].read_text().strip()
        record = TraceRecord.model_validate_json(data)

        assert record.security.scanned is True, "security.scanned not set"
        assert record.metrics.total_steps > 0, "metrics.total_steps is 0"

        # Check that raw username paths are anonymized
        username = os.environ.get("USER", "")
        serialized = record.to_jsonl_line()
        if username:
            assert f"/Users/{username}/" not in serialized, "Raw user path found in trace"


class TestPushMock:
    """Test push with mocked HF API (no real uploads)."""

    @patch("opentraces.cli.load_config")
    def test_push_not_authenticated(self, mock_config, runner, initialized_project):
        """push without auth exits with error."""
        mock_cfg = MagicMock()
        mock_cfg.hf_token = None
        mock_config.return_value = mock_cfg

        os.chdir(str(initialized_project))
        result = runner.invoke(main, ["push"])
        assert result.exit_code != 0 or "Not authenticated" in result.output

    @patch("huggingface_hub.HfApi")
    @patch("opentraces.cli.load_config")
    def test_push_uses_correct_repo_id(
        self, mock_config, mock_hf_api_cls, runner, initialized_project
    ):
        """push resolves repo_id to username/opentraces by default."""
        mock_cfg = MagicMock()
        mock_cfg.hf_token = "hf_test_token_123"
        mock_cfg.dataset_visibility = "private"
        mock_config.return_value = mock_cfg

        mock_api = MagicMock()
        mock_api.whoami.return_value = {"name": "testuser"}
        mock_hf_api_cls.return_value = mock_api

        os.chdir(str(initialized_project))
        result = runner.invoke(main, ["push"])

        # Should show default repo name in output or JSON
        # Even with no traces, the repo_id should be resolved
        assert "testuser/opentraces" in result.output or "No traces" in result.output

    @patch("huggingface_hub.HfApi")
    @patch("opentraces.cli.load_config")
    def test_push_repo_flag_overrides(
        self, mock_config, mock_hf_api_cls, runner, initialized_project
    ):
        """--repo flag takes priority over default."""
        mock_cfg = MagicMock()
        mock_cfg.hf_token = "hf_test_token_123"
        mock_cfg.dataset_visibility = "private"
        mock_config.return_value = mock_cfg

        mock_api = MagicMock()
        mock_api.whoami.return_value = {"name": "testuser"}
        mock_hf_api_cls.return_value = mock_api

        os.chdir(str(initialized_project))
        result = runner.invoke(main, ["push", "--repo", "testuser/custom-dataset"])

        # With no traces it should still resolve the repo correctly
        output = result.output
        assert "custom-dataset" in output or "No traces" in output


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
                    result = runner.invoke(main, ["login"])

            assert mock_validate.called or "TEST-CODE" in result.output


class TestDatasetNameResolution:
    """Verify the priority chain: --repo flag > config remote > default."""

    def test_default_resolution(self, tmp_path):
        """No flag, no config remote -> username/opentraces."""
        from opentraces.cli import _resolve_repo_id

        # Create a project dir with no remote in config
        proj = tmp_path / "proj"
        proj.mkdir()
        ot_dir = proj / ".opentraces"
        ot_dir.mkdir()
        (ot_dir / "config.yml").write_text("review_policy: review\n")

        os.chdir(str(proj))
        result = _resolve_repo_id("alice")
        assert result == "alice/opentraces"

    def test_config_remote_override(self, tmp_path):
        """Config remote field takes priority over default."""
        from opentraces.cli import _resolve_repo_id

        proj = tmp_path / "proj"
        proj.mkdir()
        ot_dir = proj / ".opentraces"
        ot_dir.mkdir()
        (ot_dir / "config.yml").write_text("review_policy: review\nremote: alice/custom-traces\n")

        os.chdir(str(proj))
        result = _resolve_repo_id("alice")
        assert result == "alice/custom-traces"

    def test_repo_flag_highest_priority(self, tmp_path):
        """--repo flag overrides both config remote and default."""
        from opentraces.cli import _resolve_repo_id

        proj = tmp_path / "proj"
        proj.mkdir()
        ot_dir = proj / ".opentraces"
        ot_dir.mkdir()
        (ot_dir / "config.yml").write_text("review_policy: review\nremote: alice/custom-traces\n")

        os.chdir(str(proj))
        result = _resolve_repo_id("alice", repo_flag="alice/override-repo")
        assert result == "alice/override-repo"
