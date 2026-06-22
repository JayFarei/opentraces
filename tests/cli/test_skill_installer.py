"""CLI tests for the shared opentraces skill installer."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main


def _parse_last_json(output: str) -> dict:
    text = output.strip()
    if "---OPENTRACES_JSON---" in text:
        _, _, after = text.rpartition("---OPENTRACES_JSON---")
        return json.loads(after.strip())
    return json.loads(text)


def _redirect_skill_paths(monkeypatch, home: Path) -> None:
    from opentraces.capture.skill import install as skill_install

    canonical = home / ".agents" / "skills" / "opentraces"
    harness_dirs = {
        "claude-code": home / ".claude" / "skills" / "opentraces",
        "codex-cli": home / ".codex" / "skills" / "opentraces",
    }
    monkeypatch.setattr(skill_install, "CANONICAL", canonical)
    monkeypatch.setattr(skill_install, "HARNESS_DIRS", harness_dirs)


def test_default_skill_harnesses_include_codex() -> None:
    from opentraces.capture.skill.install import HARNESS_DIRS, SkillInstaller

    assert "claude-code" in HARNESS_DIRS
    assert "codex-cli" in HARNESS_DIRS
    assert HARNESS_DIRS["codex-cli"].parts[-3:] == (".codex", "skills", "opentraces")
    assert "codex-cli" in SkillInstaller().harnesses


def test_setup_skill_supports_codex_harness(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _redirect_skill_paths(monkeypatch, home)

    result = CliRunner().invoke(
        main,
        ["--json", "setup", "skill", "--harness", "codex-cli"],
    )

    assert result.exit_code == 0, result.output
    payload = _parse_last_json(result.output)
    state = payload["state"]
    codex_link = home / ".codex" / "skills" / "opentraces"
    claude_link = home / ".claude" / "skills" / "opentraces"

    assert codex_link.is_symlink()
    assert codex_link.resolve() == (home / ".agents" / "skills" / "opentraces")
    assert not claude_link.exists()
    assert state["harnesses"]["codex-cli"]["canonical"] is True
    assert state["harnesses"]["claude-code"]["present"] is False
    assert str(codex_link) in result.output


def test_doctor_reports_codex_skill_harness(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _redirect_skill_paths(monkeypatch, home)
    runner = CliRunner()

    setup = runner.invoke(main, ["setup", "skill"])
    assert setup.exit_code == 0, setup.output

    doctor = runner.invoke(main, ["--json", "doctor"])
    assert doctor.exit_code == 0, doctor.output
    payload = _parse_last_json(doctor.output)
    skill = next(
        h for h in payload["doctor"]["hooks"] if h["installer"] == "skill"
    )

    assert set(skill["harnesses"]) >= {"claude-code", "codex-cli"}
    assert skill["harnesses"]["codex-cli"]["canonical"] is True


def test_setup_skill_remove_single_harness_preserves_canonical(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _redirect_skill_paths(monkeypatch, home)
    runner = CliRunner()

    setup = runner.invoke(main, ["setup", "skill"])
    assert setup.exit_code == 0, setup.output

    remove = runner.invoke(
        main,
        ["--json", "setup", "skill", "--remove", "--harness", "codex-cli"],
    )

    assert remove.exit_code == 0, remove.output
    payload = _parse_last_json(remove.output)
    canonical = home / ".agents" / "skills" / "opentraces"
    claude_link = home / ".claude" / "skills" / "opentraces"
    codex_link = home / ".codex" / "skills" / "opentraces"

    assert payload["removed"] == ["codex-cli"]
    assert canonical.exists()
    assert claude_link.is_symlink()
    assert not codex_link.exists()


def test_skill_status_drift_is_reason_list(tmp_path: Path, monkeypatch) -> None:
    """Skill drift is a reason LIST (version-missing / version-drift), the same
    shape every other installer emits — so doctor's soft/hard drift classifier
    treats the skill uniformly. Regression: the skill emitted a coarse bool,
    which masked version-drift as 'soft' (exit 0) while other installers'
    version-drift stayed hard (exit 3)."""
    from opentraces import __version__
    from opentraces.capture.skill import install as skill_install

    home = tmp_path / "home"
    _redirect_skill_paths(monkeypatch, home)
    canonical = skill_install.CANONICAL
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    installer = skill_install.SkillInstaller()

    # present but unstamped -> version-missing (benign)
    assert installer.status()["drift"] == ["version-missing"]

    # stamped at an older version -> version-drift (a real mismatch)
    (canonical / skill_install.VERSION_FILE).write_text("0.0.1\n", encoding="utf-8")
    assert installer.status()["drift"] == ["version-drift"]

    # stamped at the current version -> no drift
    (canonical / skill_install.VERSION_FILE).write_text(
        __version__ + "\n", encoding="utf-8"
    )
    assert installer.status()["drift"] == []
