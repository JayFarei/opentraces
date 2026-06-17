"""Unit + subprocess coverage for ``opentraces setup uninstall`` (issue #85).

The deterministic reversers are unit-tested in-process; the full orchestrator
is exercised end-to-end via a subprocess with a fresh ``$HOME`` (the only way
to isolate ``settings_patcher.DEFAULT_SETTINGS_PATH`` / ``lifecycle.*``, which
bind to the real home at import).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.capture.otlp import lifecycle, settings_patcher
from opentraces.cli import completions as completions_mod
from opentraces.core import uninstall


# ---------------------------------------------------------------------------
# purge_git_refs (path-parameterized helper extracted from `remove --all`)
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.co")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-qm", "init")
    return repo


def test_purge_git_refs_deletes_opentraces_and_notes(git_repo: Path):
    head = _git(git_repo, "rev-parse", "HEAD")
    _git(git_repo, "update-ref", "refs/opentraces/local/events/v1", head)
    _git(git_repo, "update-ref", "refs/notes/opentraces", head)

    result = uninstall.purge_git_refs(git_repo)

    assert result["git_not_found"] is False
    assert set(result["purged_refs"]) == {
        "refs/opentraces/local/events/v1", "refs/notes/opentraces"}
    assert result["failed_refs"] == []
    # Refs are actually gone.
    assert subprocess.run(["git", "show-ref", "--verify",
                           "refs/opentraces/local/events/v1"],
                          cwd=str(git_repo), capture_output=True).returncode != 0


def test_purge_git_refs_idempotent_and_non_git(tmp_path: Path, git_repo: Path):
    # No refs -> empty, not an error.
    assert uninstall.purge_git_refs(git_repo) == {
        "purged_refs": [], "failed_refs": [], "git_not_found": False}
    # Non-git dir -> still no crash (git for-each-ref just yields nothing).
    plain = tmp_path / "plain"
    plain.mkdir()
    out = uninstall.purge_git_refs(plain)
    assert out["purged_refs"] == []


# ---------------------------------------------------------------------------
# Ownership-safe uninstall_otel_env (prerequisite fix #2)
# ---------------------------------------------------------------------------
@pytest.fixture
def patched_raw_dir(tmp_path, monkeypatch):
    rb = tmp_path / "raw-bodies"
    monkeypatch.setattr(settings_patcher, "DEFAULT_RAW_BODIES_DIR", rb)
    return rb


def _expected(monkeypatched=True) -> dict:
    return settings_patcher._expected_env(
        settings_patcher.DEFAULT_OTLP_ENDPOINT,
        settings_patcher.DEFAULT_RAW_BODIES_DIR,
    )


def _write_settings(path: Path, env: dict) -> None:
    path.write_text(json.dumps({"env": env}, indent=2))


def test_ownership_safe_removes_only_our_keys_no_backup(tmp_path, patched_raw_dir):
    settings = tmp_path / "settings.json"
    expected = _expected()
    # opentraces-written keys + two user-owned keys, NO backup present.
    env = {**expected, "OTEL_RESOURCE_ATTRIBUTES": "service.name=app", "EDITOR": "vim"}
    _write_settings(settings, env)

    res = settings_patcher.uninstall_otel_env(
        settings_path=settings, ownership_safe=True, delete_backup=True)

    assert sorted(res.keys_skipped) == sorted(settings_patcher.OTEL_ENV_KEYS)
    after = json.loads(settings.read_text())["env"]
    # User keys survive; all 12 opentraces keys are gone.
    assert after == {"OTEL_RESOURCE_ATTRIBUTES": "service.name=app", "EDITOR": "vim"}
    assert "no-backup" in (res.reason or "")


def test_ownership_safe_preserves_user_modified_value(tmp_path, patched_raw_dir):
    settings = tmp_path / "settings.json"
    expected = _expected()
    env = dict(expected)
    env["OTEL_LOGS_EXPORTER"] = "otlp-grpc"  # user-modified (!= our "otlp")
    _write_settings(settings, env)

    res = settings_patcher.uninstall_otel_env(settings_path=settings, ownership_safe=True)

    assert "OTEL_LOGS_EXPORTER" not in res.keys_skipped  # preserved
    after = json.loads(settings.read_text())["env"]
    assert after.get("OTEL_LOGS_EXPORTER") == "otlp-grpc"


def test_ownership_safe_preserves_key_present_in_backup(tmp_path, patched_raw_dir):
    settings = tmp_path / "settings.json"
    backup = settings.with_name(settings.name + settings_patcher.DEFAULT_BACKUP_SUFFIX)
    expected = _expected()
    _write_settings(settings, dict(expected))
    # The user already owned CLAUDE_CODE_ENABLE_TELEMETRY (it is in the backup),
    # even though its value matches ours -> must be preserved.
    _write_settings(backup, {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"})

    res = settings_patcher.uninstall_otel_env(settings_path=settings, ownership_safe=True)

    assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in res.keys_skipped
    after = json.loads(settings.read_text())["env"]
    assert after.get("CLAUDE_CODE_ENABLE_TELEMETRY") == "1"


def test_default_uninstall_still_pops_all_twelve(tmp_path, patched_raw_dir):
    """Legacy callers + the capture-otlp --uninstall journey rely on pop-all."""
    settings = tmp_path / "settings.json"
    env = {**_expected(), "OTEL_RESOURCE_ATTRIBUTES": "x"}
    _write_settings(settings, env)

    res = settings_patcher.uninstall_otel_env(settings_path=settings)  # default

    assert sorted(res.keys_skipped) == sorted(settings_patcher.OTEL_ENV_KEYS)
    after = json.loads(settings.read_text())["env"]
    assert after == {"OTEL_RESOURCE_ATTRIBUTES": "x"}


def test_ownership_safe_removes_custom_raw_bodies_install(tmp_path, patched_raw_dir):
    """A custom --raw-bodies-dir value we wrote must still be removed (codex #2).

    With a pre-install backup, ownership is "we added it" (absent pre-install),
    value-agnostic — so a non-default OTEL_LOG_RAW_API_BODIES we installed is
    removed, not preserved as 'user-modified'.
    """
    settings = tmp_path / "settings.json"
    # settings.json pre-exists with a user key -> install writes a backup.
    _write_settings(settings, {"EDITOR": "vim"})
    custom = tmp_path / "custom-raw"
    settings_patcher.install_otel_env(settings_path=settings, raw_bodies_dir=custom)
    installed = json.loads(settings.read_text())["env"]
    assert installed["OTEL_LOG_RAW_API_BODIES"] == f"file:{custom.resolve()}"

    res = settings_patcher.uninstall_otel_env(settings_path=settings, ownership_safe=True)

    assert "OTEL_LOG_RAW_API_BODIES" in res.keys_skipped  # removed despite custom value
    after = json.loads(settings.read_text())["env"]
    assert "OTEL_LOG_RAW_API_BODIES" not in after
    assert after.get("EDITOR") == "vim"  # user key preserved


def test_ownership_safe_no_backup_removes_file_scheme_raw_bodies(tmp_path, patched_raw_dir):
    """No backup + a custom file: raw-body sink -> still recognized as ours."""
    settings = tmp_path / "settings.json"
    env = dict(_expected())
    env["OTEL_LOG_RAW_API_BODIES"] = "file:/some/other/dir"  # custom, no backup
    _write_settings(settings, env)
    res = settings_patcher.uninstall_otel_env(settings_path=settings, ownership_safe=True)
    assert "OTEL_LOG_RAW_API_BODIES" in res.keys_skipped


def test_ownership_safe_delete_backup_only_after_clean_strip(tmp_path, patched_raw_dir):
    settings = tmp_path / "settings.json"
    backup = settings.with_name(settings.name + settings_patcher.DEFAULT_BACKUP_SUFFIX)
    _write_settings(settings, dict(_expected()))
    backup.write_text("{}")

    settings_patcher.uninstall_otel_env(
        settings_path=settings, ownership_safe=True, delete_backup=True)
    assert not backup.exists()  # clean strip -> backup removed


# ---------------------------------------------------------------------------
# lifecycle.uninstall_autostart honesty fix (prerequisite fix #1)
# ---------------------------------------------------------------------------
def test_uninstall_autostart_failure_returns_not_ok_and_keeps_unit(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(lifecycle, "_plat", lambda: "darwin")
    monkeypatch.setattr(lifecycle, "LAUNCHD_PLIST_PATH", plist)
    monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: (1, "Operation not permitted"))

    res = lifecycle.uninstall_autostart()

    assert res.ok is False
    assert res.reason == "launchctl-unload-failed"
    assert plist.exists()  # NOT unlinked on failure (so a retry can find it)


def test_uninstall_autostart_success_unlinks_unit(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(lifecycle, "_plat", lambda: "darwin")
    monkeypatch.setattr(lifecycle, "LAUNCHD_PLIST_PATH", plist)
    monkeypatch.setattr(lifecycle, "_run", lambda *a, **k: (0, ""))

    res = lifecycle.uninstall_autostart()

    assert res.ok is True
    assert not plist.exists()


def test_uninstall_autostart_absent_unit_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "_plat", lambda: "darwin")
    monkeypatch.setattr(lifecycle, "LAUNCHD_PLIST_PATH", tmp_path / "missing.plist")
    res = lifecycle.uninstall_autostart()
    assert res.ok is True


# ---------------------------------------------------------------------------
# completions reusable helper (prerequisite fix #4)
# ---------------------------------------------------------------------------
def test_uninstall_completions_removes_script_and_rc_line(monkeypatch, tmp_path):
    # _home() reads $HOME at call time; the autouse fixture isolates it.
    home = Path(os.environ["HOME"])
    script = completions_mod._script_path("zsh")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# completion")
    rc = completions_mod._rc_path("zsh")
    line = completions_mod._source_line("zsh", script)
    rc.write_text(f"export FOO=1\n{line}\n")

    res = completions_mod.uninstall_completions("zsh")

    assert not script.exists()
    assert line not in rc.read_text()
    assert "export FOO=1" in rc.read_text()  # untouched
    assert res["removed"]
    # Idempotent: second call removes nothing.
    assert completions_mod.uninstall_completions("zsh")["removed"] == []


def test_uninstall_completions_rejects_bad_shell():
    with pytest.raises(ValueError):
        completions_mod.uninstall_completions("powershell")


def test_uninstall_completions_fish_ownership_guard(monkeypatch):
    """setup uninstall fans out across every shell; a user's own ot.fish that
    opentraces never wrote must NOT be clobbered (codex #4)."""
    rc = completions_mod._rc_path("fish")
    rc.parent.mkdir(parents=True, exist_ok=True)

    # Foreign file (no opentraces signature) -> preserved.
    rc.write_text("complete -c ot -a 'mytool'\n")
    assert completions_mod.uninstall_completions("fish")["removed"] == []
    assert rc.exists()

    # Foreign file that merely MENTIONS opentraces (but lacks our generated
    # header) -> still preserved (a loose substring check would wrongly delete).
    rc.write_text("# my own ot wrapper around opentraces\ncomplete -c ot\n")
    assert completions_mod.uninstall_completions("fish")["removed"] == []
    assert rc.exists()

    # opentraces-written file -> removed.
    rc.write_text(completions_mod.FISH_SCRIPT)
    assert completions_mod.uninstall_completions("fish")["removed"]
    assert not rc.exists()


def test_pid_is_receiver_rejects_non_receiver():
    """The current python test process is not an OTLP receiver -> never a kill
    target via the PID-file fallback (codex #3)."""
    assert uninstall._pid_is_receiver(os.getpid()) is False


# ---------------------------------------------------------------------------
# End-to-end orchestrator via subprocess (fresh $HOME isolates every path).
# ---------------------------------------------------------------------------
def _run_cli(home: Path, repo: Path, *args: str):
    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-m", "opentraces", "--json", "setup", "uninstall", *args],
        cwd=str(repo), env=env, capture_output=True, text=True,
    )
    blob = proc.stdout
    if "{" not in blob:
        raise AssertionError(f"no JSON envelope in: {proc.stdout!r} / {proc.stderr!r}")
    start = blob.index("{")
    depth = 0
    for i in range(start, len(blob)):
        if blob[i] == "{":
            depth += 1
        elif blob[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(blob[start:i + 1]), proc.returncode
    raise AssertionError(f"unterminated JSON envelope in: {proc.stdout!r}")


@pytest.fixture
def installed_world(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "opentraces", *args],
                              cwd=str(repo), env=env, capture_output=True, text=True)

    def git(*args):
        return subprocess.run(["git", *args], cwd=str(repo),
                              capture_output=True, text=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t.co")
    git("config", "user.name", "t")
    git("commit", "--allow-empty", "-qm", "init")
    cli("init", "--start-fresh", "--agent", "claude-code")
    cli("setup", "git")
    return home, repo, cli, git


def test_e2e_default_preserves_data_and_refs(installed_world):
    home, repo, cli, git = installed_world
    head = git("rev-parse", "HEAD").stdout.strip()
    git("update-ref", "refs/opentraces/local/events/v1", head)
    bucket = home / ".opentraces" / "bucket"
    bucket.mkdir(parents=True)
    (bucket / "blob.json").write_text("{}")
    staging = home / ".opentraces" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "trace.jsonl").write_text('{"t":1}')

    envelope, rc = _run_cli(home, repo)

    assert rc == 0 and envelope["ok"] is True
    assert "git" in envelope["removed_names"]
    assert "refs/opentraces/local/events/v1" in envelope["refs_preserved"]
    # PRESERVED: bucket + staging-root + the canonical events ref.
    assert bucket.exists() and (bucket / "blob.json").exists()
    assert (staging / "trace.jsonl").exists()
    assert git_ref_exists(repo, "refs/opentraces/local/events/v1")
    # The git hook is gone.
    assert not (repo / ".git" / "hooks" / "opentraces-post-commit").exists()


def test_e2e_idempotent_second_run_removes_nothing(installed_world):
    home, repo, cli, git = installed_world
    _run_cli(home, repo)  # first run removes git
    envelope, rc = _run_cli(home, repo)
    assert rc == 0 and envelope["ok"] is True
    assert "git" not in envelope["removed_names"]


def test_e2e_dry_run_mutates_nothing(installed_world):
    home, repo, cli, git = installed_world
    hook = repo / ".git" / "hooks" / "opentraces-post-commit"
    assert hook.exists()
    envelope, rc = _run_cli(home, repo, "--dry-run")
    assert rc == 0 and envelope["dry_run"] is True
    assert hook.exists()  # untouched
    assert "git" in envelope["removed_names"]  # would-remove


def test_e2e_purge_deletes_data_refs_and_marker(installed_world):
    home, repo, cli, git = installed_world
    head = git("rev-parse", "HEAD").stdout.strip()
    git("update-ref", "refs/opentraces/local/events/v1", head)
    git("update-ref", "refs/notes/opentraces", head)
    bucket = home / ".opentraces" / "bucket"
    bucket.mkdir(parents=True)
    (bucket / "blob.json").write_text("{}")
    staging = home / ".opentraces" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "trace.jsonl").write_text('{"t":1}')

    envelope, rc = _run_cli(home, repo, "--purge", "--yes")

    assert rc == 0 and envelope["ok"] is True
    assert "refs/opentraces/local/events/v1" in envelope["refs_purged"]
    assert not bucket.exists()
    assert not (staging / "trace.jsonl").exists()  # B2: staging root purged
    assert not git_ref_exists(repo, "refs/opentraces/local/events/v1")
    assert not (repo / ".opentraces.json").exists()  # marker deleted


def test_e2e_project_purge_preserves_global_data(installed_world):
    """`--project <repo> --purge` deletes that repo's refs + marker but PRESERVES
    the cross-repo captured corpus (codex R2)."""
    home, repo, cli, git = installed_world
    head = git("rev-parse", "HEAD").stdout.strip()
    git("update-ref", "refs/opentraces/local/events/v1", head)
    bucket = home / ".opentraces" / "bucket"
    bucket.mkdir(parents=True)
    (bucket / "blob.json").write_text("{}")

    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, "-m", "opentraces", "--json", "setup", "uninstall",
         "--project", str(repo), "--purge", "--yes"],
        cwd=str(repo), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # Repo refs + marker gone...
    assert not git_ref_exists(repo, "refs/opentraces/local/events/v1")
    assert not (repo / ".opentraces.json").exists()
    # ...but the global bucket survives a project-scoped purge.
    assert bucket.exists() and (bucket / "blob.json").exists()


def git_ref_exists(repo: Path, ref: str) -> bool:
    return subprocess.run(["git", "show-ref", "--verify", ref],
                          cwd=str(repo), capture_output=True).returncode == 0
