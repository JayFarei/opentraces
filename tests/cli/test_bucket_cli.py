from __future__ import annotations

import json

from click.testing import CliRunner
from opentraces_schema import Agent, Step, TraceRecord

from opentraces.cli import main
from opentraces.core.bucket_store import write_trace_record
from opentraces.security import SECURITY_VERSION


def _trace(trace_id: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code"),
        task={"description": "Bucket status trace"},
        steps=[Step(step_index=1, role="user", content="Bucket status trace")],
    )


def test_bucket_status_and_fake_remote_cli(monkeypatch, tmp_path):
    record = _trace("trace-bucket-cli")
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        record,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    runner = CliRunner()

    status = runner.invoke(main, ["bucket", "status", "--json"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["bucket"]["trace_records"]["object_count"] == 1
    assert payload["bucket"]["sync"]["eligible"] is True

    remote_root = tmp_path / "remote-bucket"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))
    before = runner.invoke(main, ["bucket", "remote", "status", "--json"])
    assert before.exit_code == 0, before.output
    assert json.loads(before.output)["remote"]["state"] == "missing"

    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.output)["remote"]["state"] == "pushed"

    diff = runner.invoke(main, ["bucket", "remote", "diff", "--json"])
    assert diff.exit_code == 0, diff.output
    assert json.loads(diff.output)["remote"]["different"] is False

    pulled = runner.invoke(main, ["bucket", "remote", "pull", "--json"])
    assert pulled.exit_code == 0, pulled.output
    assert json.loads(pulled.output)["remote"]["state"] == "pulled"

    after = runner.invoke(main, ["bucket", "remote", "status", "--json"])
    assert after.exit_code == 0, after.output
    assert json.loads(after.output)["remote"]["state"] == "current"


def test_setup_bucket_defaults_to_private_remote_when_authenticated(monkeypatch):
    from opentraces.core.config import load_config

    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(
        "opentraces.cli._auth_identity",
        lambda token: {"name": "me"} if token else None,
    )
    runner = CliRunner()

    result = runner.invoke(main, ["setup", "bucket", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    bucket = payload["bucket"]
    assert bucket["storage"] == "remote"
    assert bucket["local_cache"] is True
    assert bucket["remote"]["enabled"] is True
    assert bucket["remote"]["provider"] == "huggingface"
    assert bucket["remote"]["visibility"] == "private"
    assert bucket["remote"]["sync_policy"] == "daemon"
    assert bucket["remote"]["url"] == "hf://me/opentraces-bucket"
    assert load_config().bucket.remote.url == "hf://me/opentraces-bucket"


def test_setup_bucket_local_only_opts_out():
    from opentraces.core.config import load_config

    runner = CliRunner()
    result = runner.invoke(main, ["setup", "bucket", "--local-only", "--json"])

    assert result.exit_code == 0, result.output
    bucket = json.loads(result.output)["bucket"]
    assert bucket["storage"] == "local"
    assert bucket["local_cache"] is True
    assert bucket["remote"]["enabled"] is False
    assert load_config().bucket.storage == "local"


def test_setup_bucket_fake_remote_feeds_bucket_remote_harness(tmp_path):
    record = _trace("trace-configured-fake-bucket")
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        record,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    runner = CliRunner()
    remote_root = tmp_path / "configured-fake-bucket"

    configured = runner.invoke(
        main,
        ["setup", "bucket", "--provider", "fake", "--fake-root", str(remote_root), "--json"],
    )
    assert configured.exit_code == 0, configured.output
    bucket = json.loads(configured.output)["bucket"]
    assert bucket["storage"] == "remote"
    assert bucket["remote"]["provider"] == "fake"
    assert bucket["remote"]["url"].startswith("file://")

    status = runner.invoke(main, ["bucket", "remote", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["remote"]["state"] == "missing"

    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.output)["remote"]["state"] == "pushed"

    diff = runner.invoke(main, ["bucket", "remote", "diff", "--json"])
    assert diff.exit_code == 0, diff.output
    assert json.loads(diff.output)["remote"]["different"] is False
