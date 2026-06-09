from __future__ import annotations

import json
import shutil

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


def test_setup_bucket_json_applies_recommended_default_security(monkeypatch):
    """Code-review #10: non-interactive / --json setup of a real HF bucket must
    not configure a remote-syncing bucket with zero redaction; it applies the
    safe 'recommended' baseline."""
    from opentraces.core.config import load_config

    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(
        "opentraces.cli._auth_identity",
        lambda token: {"name": "me"} if token else None,
    )
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "bucket", "--json"])

    assert result.exit_code == 0, result.output
    enabled = json.loads(result.output)["security_tools"]["enabled"]
    assert enabled == ["regex", "entropy", "business_logic", "path_anonymizer", "classifier"]
    assert load_config().security.regex.enabled is True


def test_setup_bucket_explicit_flags_override_default(tmp_path):
    """An explicit --enable-security-tool suppresses the recommended default."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "setup", "bucket", "--provider", "fake", "--fake-root", str(tmp_path / "r"),
            "--enable-security-tool", "regex", "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["security_tools"]["enabled"] == ["regex"]


def test_setup_bucket_requires_huggingface_auth(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    runner = CliRunner()

    result = runner.invoke(main, ["setup", "bucket", "--json"])

    assert result.exit_code == 3
    assert "opentraces auth login" in result.output


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


def test_setup_bucket_security_tool_flags_toggle_config(tmp_path):
    from opentraces.core.config import load_config

    runner = CliRunner()
    remote_root = tmp_path / "configured-fake-bucket"

    result = runner.invoke(
        main,
        [
            "setup",
            "bucket",
            "--provider",
            "fake",
            "--fake-root",
            str(remote_root),
            "--enable-security-tool",
            "regex",
            "--enable-security-tool",
            "business_logic",
            "--disable-security-tool",
            "entropy",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["security_tool_changes"]["enabled"] == ["regex", "business_logic"]
    assert payload["security_tool_changes"]["disabled"] == ["entropy"]
    assert payload["security_tools"]["enabled"] == ["regex", "business_logic"]
    cfg = load_config()
    assert cfg.security.regex.enabled is True
    assert cfg.security.business_logic.enabled is True
    assert cfg.security.entropy.enabled is False


def test_bucket_security_command_sets_policy_and_tools():
    from opentraces.core.config import load_config

    runner = CliRunner()

    policy = runner.invoke(main, ["bucket", "security", "--policy", "basic", "--json"])
    assert policy.exit_code == 0, policy.output
    payload = json.loads(policy.output)
    assert payload["security"]["scope"] == "bucket"
    assert payload["security"]["policy"] == "basic"
    assert payload["security"]["enabled"] == ["regex", "entropy"]

    disable = runner.invoke(
        main,
        ["bucket", "security", "--tool", "entropy", "--disable", "--json"],
    )
    assert disable.exit_code == 0, disable.output
    payload = json.loads(disable.output)
    assert payload["changes"]["disabled"] == ["entropy"]
    assert payload["security"]["enabled"] == ["regex"]
    # An enabled set that matches no named bundle reports as 'custom'.
    assert payload["security"]["policy"] == "custom"
    cfg = load_config()
    assert cfg.security.regex.enabled is True
    assert cfg.security.entropy.enabled is False


def test_bucket_security_reads_state_without_mutation():
    """Bare `bucket security` is a read-only inspector (no flags)."""
    runner = CliRunner()
    result = runner.invoke(main, ["bucket", "security", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["security"]["scope"] == "bucket"
    assert payload["security"]["policy"] == "off"
    assert payload["security"]["enabled"] == []
    assert payload["changes"] == {"enabled": [], "disabled": []}


def test_bucket_security_guard_errors_exit_2():
    """Mutually-exclusive / malformed flag combinations exit 2."""
    runner = CliRunner()
    bad_combos = [
        ["bucket", "security", "--enable", "--disable", "--tool", "regex"],
        ["bucket", "security", "--policy", "basic", "--tool", "regex", "--enable"],
        ["bucket", "security", "--policy", "basic", "--enable"],
        ["bucket", "security", "--tool", "regex"],
        ["bucket", "security", "--enable"],
    ]
    for argv in bad_combos:
        result = runner.invoke(main, argv)
        assert result.exit_code == 2, (argv, result.output)


def test_setup_bucket_interactive_policy_prompt_applies_basic(monkeypatch):
    from opentraces.core.config import load_config

    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setattr(
        "opentraces.cli._auth_identity",
        lambda token: {"name": "me"} if token else None,
    )
    monkeypatch.setattr("opentraces.cli._is_interactive_terminal", lambda: True)
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["setup", "bucket"],
        input="2\n",
    )

    assert result.exit_code == 0, result.output
    assert "Bucket security policy" in result.output
    cfg = load_config()
    assert cfg.bucket.remote.url == "hf://me/opentraces-bucket"
    assert cfg.security.regex.enabled is True
    assert cfg.security.entropy.enabled is True
    assert cfg.security.business_logic.enabled is False


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


def test_bucket_remote_reports_ahead_and_blocks_unsafe_push(monkeypatch, tmp_path):
    record = _trace("trace-conflict-base")
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        record,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    runner = CliRunner()
    remote_root = tmp_path / "remote-bucket"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.output)["remote"]["state"] == "pushed"

    remote_manifest = json.loads((remote_root / "manifest.json").read_text())
    remote_manifest["digest"] = "sha256:" + "a" * 64
    (remote_root / "manifest.json").write_text(json.dumps(remote_manifest), encoding="utf-8")

    remote_ahead = runner.invoke(main, ["bucket", "remote", "status", "--json"])
    assert remote_ahead.exit_code == 0, remote_ahead.output
    assert json.loads(remote_ahead.output)["remote"]["state"] == "remote_ahead"

    blocked = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert blocked.exit_code == 3
    assert "remote bucket has changes" in blocked.output

    forced = runner.invoke(main, ["bucket", "remote", "push", "--force", "--json"])
    assert forced.exit_code == 0, forced.output
    assert json.loads(forced.output)["remote"]["state"] == "pushed"


def test_bucket_remote_reports_diverged_and_blocks_unsafe_pull(monkeypatch, tmp_path):
    record = _trace("trace-diverged-base")
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        record,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    runner = CliRunner()
    remote_root = tmp_path / "remote-bucket"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output

    local = _trace("trace-diverged-local")
    local.security.scanned = True
    local.security.classifier_version = SECURITY_VERSION
    write_trace_record(
        local,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    remote_manifest = json.loads((remote_root / "manifest.json").read_text())
    remote_manifest["digest"] = "sha256:" + "b" * 64
    (remote_root / "manifest.json").write_text(json.dumps(remote_manifest), encoding="utf-8")

    diverged = runner.invoke(main, ["bucket", "remote", "status", "--json"])
    assert diverged.exit_code == 0, diverged.output
    assert json.loads(diverged.output)["remote"]["state"] == "diverged"

    blocked = runner.invoke(main, ["bucket", "remote", "pull", "--json"])
    assert blocked.exit_code == 3
    assert "local bucket has changes" in blocked.output


def test_setup_bucket_fake_remote_push_now(tmp_path):
    record = _trace("trace-setup-push-now")
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
        [
            "setup",
            "bucket",
            "--provider",
            "fake",
            "--fake-root",
            str(remote_root),
            "--push-now",
            "--json",
        ],
    )

    assert configured.exit_code == 0, configured.output
    payload = json.loads(configured.output)
    assert payload["remote_sync"]["state"] == "pushed"
    assert (remote_root / "manifest.json").exists()


def test_setup_bucket_fake_remote_pull_now(tmp_path, monkeypatch):
    from opentraces.core import paths
    from opentraces.core.bucket_store import iter_trace_record_objects

    record = _trace("trace-setup-pull-now")
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
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))
    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output

    shutil_root = paths.bucket_dir()
    shutil.rmtree(shutil_root)

    configured = runner.invoke(
        main,
        [
            "setup",
            "bucket",
            "--provider",
            "fake",
            "--fake-root",
            str(remote_root),
            "--pull-now",
            "--json",
        ],
    )

    assert configured.exit_code == 0, configured.output
    payload = json.loads(configured.output)
    assert payload["remote_sync"]["state"] == "pulled"
    assert [obj.trace_id for obj in iter_trace_record_objects()] == ["trace-setup-pull-now"]
