"""Faultpoint seam contract (otbox 2.0 phase 6).

The seam itself: armed only under BOTH env vars; unknown sites raise; the
otbox mutation layer recognizes faultpoint: faults and refuses to cache a
faulted world. The first faultpoint KILL (redaction) is deferred (see
guarantees.toml) pending an ingest-arming investigation — this pins the
infrastructure so that kill can land without re-deriving the safety rails.

Second section (release-gate claim CAP-6): hook fail-open fault
injection. The capture hook ENTRYPOINTS — the four installed Claude Code
hook scripts, the eight Codex CLI hook modules, and the git post-commit
shell shim — must exit 0 and keep stderr traceback-free under injected
faults: corrupt ``~/.opentraces/config.json``, garbage/empty stdin, an
unwritable transcript path, a missing ``opentraces`` package (the
lazy-import contract, pinned for ALL FOUR Claude Code hook scripts), and
a dangling pinned interpreter with no ``opentraces`` binary on PATH (git
shim). These drive the REAL installed artifacts as subprocesses, the way
the agent harness would — not in-process ``main()`` calls
(tests/capture/test_hooks.py owns those).

The former KNOWN CRASH WINDOW (module-level ``opentraces``/``mmh3``
imports in ``on_pre_tool_use.py`` / ``on_tool_use.py`` exiting 1 with a
traceback before ``main()`` when the pinned venv broke) is CLOSED: both
scripts now lazy-import inside ``main()`` like ``on_stop`` /
``on_compact``, and the missing-package sweep below covers all four.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.core.faultpoints import SITES, armed, armed_site
from .mutations import is_faultpoint, known_fault


def test_disarmed_by_default(monkeypatch):
    monkeypatch.delenv("OT_FAULTPOINT", raising=False)
    monkeypatch.delenv("OT_OTBOX_FAULT_OK", raising=False)
    assert armed_site() is None
    for site in SITES:
        assert not armed(site)


def test_requires_both_env_vars(monkeypatch):
    site = next(iter(SITES))
    monkeypatch.setenv("OT_FAULTPOINT", site)
    monkeypatch.delenv("OT_OTBOX_FAULT_OK", raising=False)
    assert not armed(site), "OT_FAULTPOINT alone must not arm"
    monkeypatch.setenv("OT_OTBOX_FAULT_OK", "1")
    assert armed(site)
    assert armed_site() == site


def test_only_named_site_arms(monkeypatch):
    sites = sorted(SITES)
    assert len(sites) >= 2
    monkeypatch.setenv("OT_FAULTPOINT", sites[0])
    monkeypatch.setenv("OT_OTBOX_FAULT_OK", "1")
    assert armed(sites[0])
    assert not armed(sites[1])


def test_unknown_site_raises_loudly(monkeypatch):
    monkeypatch.setenv("OT_FAULTPOINT", "no-such-site")
    monkeypatch.setenv("OT_OTBOX_FAULT_OK", "1")
    with pytest.raises(RuntimeError, match="not a registered faultpoint"):
        armed(next(iter(SITES)))


def test_mutations_recognizes_faultpoint_faults():
    assert is_faultpoint("faultpoint:redaction-skipped") == "redaction-skipped"
    assert is_faultpoint("disable-security-tools") is None
    assert known_fault("disable-security-tools")
    assert known_fault("faultpoint:redaction-skipped")
    assert not known_fault("nonsense")
    with pytest.raises(KeyError):
        is_faultpoint("faultpoint:unknown-site")


# ---------------------------------------------------------------------------
# Hook fail-open fault injection (release-gate claim CAP-6)
# ---------------------------------------------------------------------------
#
# tests/conftest.py's autouse fixture points $HOME at an isolated tmp dir,
# so the subprocess-driven hooks below resolve ~/.opentraces and
# ~/.claude inside the sandbox — corrupt-config injection never touches
# the developer's real world.

# Claude Code event -> script map; imported (not copied) so a new hook
# event automatically joins this sweep.
from opentraces.capture.claude_code.install import EVENT_SCRIPTS as _CLAUDE_EVENT_SCRIPTS  # noqa: E402
from opentraces.capture.codex_cli.install import EVENT_SCRIPTS as _CODEX_EVENT_SCRIPTS  # noqa: E402

_HOOK_TIMEOUT_S = 60


def _isolated_home() -> Path:
    return Path(os.environ["HOME"])


def _install_claude_hook_scripts() -> dict[str, Path]:
    """Materialize the REAL installed hook artifacts in the isolated home."""
    from opentraces.capture.claude_code import install as cc_install

    home = _isolated_home()
    result = cc_install.install(
        hooks_dir=home / ".claude" / "hooks",
        settings_file=home / ".claude" / "settings.json",
    )
    return {event: Path(path) for event, path in result.installed.items()}


def _run_hook_subprocess(argv: list[str], stdin_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=_HOOK_TIMEOUT_S,
        cwd=str(_isolated_home()),
    )


def _valid_hook_payload(tmp_path: Path, *, transcript: str | None = None) -> str:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return json.dumps({
        "cwd": str(project),
        "session_id": "cap6-fault",
        "transcript_path": transcript or str(tmp_path / "transcript.jsonl"),
        "tool_name": "Read",
        "tool_input": {"file_path": "README.md"},
    })


def _corrupt_global_config() -> None:
    cfg = _isolated_home() / ".opentraces" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('corrupt{{{not-json')


_HOOK_FAULTS = [
    "corrupt_global_config",
    "garbage_stdin",
    "empty_stdin",
    "transcript_path_is_directory",
]


@pytest.mark.parametrize("script_name", sorted(set(_CLAUDE_EVENT_SCRIPTS.values())))
@pytest.mark.parametrize("fault", _HOOK_FAULTS)
def test_claude_hook_entrypoint_fails_open(tmp_path, script_name, fault):
    """Every installed Claude Code hook script exits 0 under injected faults."""
    scripts = _install_claude_hook_scripts()
    script = next(p for p in scripts.values() if p.name.endswith(script_name))

    stdin_text = _valid_hook_payload(tmp_path)
    if fault == "corrupt_global_config":
        _corrupt_global_config()
    elif fault == "garbage_stdin":
        stdin_text = "not-json{{{"
    elif fault == "empty_stdin":
        stdin_text = ""
    elif fault == "transcript_path_is_directory":
        unwritable = tmp_path / "transcript-is-a-dir"
        unwritable.mkdir()
        stdin_text = _valid_hook_payload(tmp_path, transcript=str(unwritable))

    proc = _run_hook_subprocess([sys.executable, str(script)], stdin_text)
    assert proc.returncode == 0, (
        f"{script_name} under {fault} exited {proc.returncode} — a non-zero "
        f"hook exit surfaces in the agent session: {proc.stderr[-400:]}"
    )
    assert "Traceback" not in proc.stderr, (
        f"{script_name} under {fault} dumped a traceback into the agent "
        f"session: {proc.stderr[-400:]}"
    )


@pytest.mark.parametrize("script_name", sorted(set(_CLAUDE_EVENT_SCRIPTS.values())))
def test_lazy_import_hooks_survive_missing_opentraces_package(tmp_path, script_name):
    """EVERY installed Claude Code hook script must exit 0 even when the
    pinned interpreter can no longer import opentraces (or mmh3) — the
    lazy-import fail-open contract. ``python -S`` skips site-packages,
    which is exactly the broken/deleted-venv failure mode.

    Release-gate CAP-6: on_pre_tool_use/on_tool_use previously had
    module-level ``opentraces``/``mmh3`` imports and exited 1 with a
    traceback into the agent session; this sweep pins the fix.
    """
    scripts = _install_claude_hook_scripts()
    script = next(p for p in scripts.values() if p.name.endswith(script_name))

    proc = _run_hook_subprocess(
        [sys.executable, "-S", str(script)], _valid_hook_payload(tmp_path)
    )
    assert proc.returncode == 0, (
        f"{script_name} without an importable opentraces exited "
        f"{proc.returncode}: {proc.stderr[-400:]}"
    )
    assert "Traceback" not in proc.stderr, proc.stderr[-400:]


@pytest.mark.parametrize("module_name", sorted(s[:-3] for s in _CODEX_EVENT_SCRIPTS.values()))
def test_codex_hook_module_fails_open(tmp_path, module_name):
    """Every Codex hook module exits 0 on garbage stdin + corrupt config."""
    _corrupt_global_config()
    proc = _run_hook_subprocess(
        [sys.executable, "-m", f"opentraces.capture.codex_cli.hooks.{module_name}"],
        "not-json{{{",
    )
    assert proc.returncode == 0, (
        f"codex {module_name} exited {proc.returncode}: {proc.stderr[-400:]}"
    )
    assert "Traceback" not in proc.stderr, proc.stderr[-400:]


def test_git_post_commit_shim_never_blocks_commit(tmp_path):
    """The git shim's exit-0 guarantee holds at the SHELL level: a dangling
    pinned interpreter plus no ``opentraces`` on PATH must not block
    ``git commit`` (the strongest fail-open of the three hook families)."""
    from opentraces.capture.git import install as git_install

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "PATH": "/usr/bin:/bin"}

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-c", "user.email=cap6@otbox", "-c", "user.name=cap6",
             "-c", "commit.gpgsign=false", *args],
            cwd=repo, capture_output=True, text=True, env=env,
            timeout=_HOOK_TIMEOUT_S,
        )

    assert _git("init", "-q").returncode == 0
    (repo / "f.txt").write_text("one\n")
    assert _git("add", "-A").returncode == 0
    assert _git("commit", "-q", "-m", "seed").returncode == 0

    assert git_install.install(repo) is True
    owned = repo / ".git" / "hooks" / "opentraces-post-commit"
    # Simulate the deleted-venv failure mode: the baked interpreter path
    # dangles, and the minimal PATH above has no `opentraces` fallback.
    owned.write_text(owned.read_text().replace(sys.executable, "/nonexistent/venv/bin/python"))
    assert "/nonexistent/venv/bin/python" in owned.read_text()

    (repo / "f.txt").write_text("two\n")
    assert _git("add", "-A").returncode == 0
    commit = _git("commit", "-m", "commit with dangling hook interpreter")
    assert commit.returncode == 0, (
        f"git commit blocked by the post-commit shim: {commit.stderr[-400:]}"
    )
    assert "Traceback" not in commit.stderr
    head = _git("log", "--oneline")
    assert head.returncode == 0 and len(head.stdout.strip().splitlines()) == 2
