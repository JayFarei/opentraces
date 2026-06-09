"""Neutral box-prep helpers for the otbox simulated-user drivers.

Extracted verbatim from ``runner.py`` (Stage 2 of the
"unify-on-terminal-control" refactor) so the prep sequence can be shared
by BOTH the tmux runner (``runner.py``) and the termctrl drive core
(``drive.py``) without creating an import cycle.

This module is deliberately neutral: it imports from ``..env`` and the
standard library only. It must NEVER import ``runner`` or
``footage_runner`` (those import *from here*). ``runner.py`` re-exports
these names so existing in-module uses, external references like
``footage_runner._runner._prep_agent_home``, and the test monkeypatches
that target ``tests.otbox.simulated_users.runner.<name>`` keep resolving
the same objects.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..env import Box, isolated_env

_CODEX_AGENTS = {"codex", "codex-cli"}
_CLAUDE_AGENTS = {"claude", "claude-code"}
_PI_AGENTS = {"pi"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _detect_binary_version(binary: str) -> str:
    """Return the first non-empty line of ``<binary> --version``.

    Returns ``""`` if the call fails or produces no parseable output.
    Bounded with a 5-second timeout so a broken binary cannot wedge
    the runner before the real session even starts.
    """
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _build_env_prefix(box: Box, env_extra: dict[str, str] | None) -> list[str]:
    """Pinned ``env K=V ...`` prefix for the tmux-spawned command.

    Mirrors the journey ``tmux`` step pattern: only the keys that
    DIFFER from the ambient environment need to be pinned, which keeps
    the command line short while still defending against a running
    tmux server inheriting the developer's HOME.
    """
    full_env = isolated_env(box, env_extra)
    overrides = {
        k: v for k, v in full_env.items() if os.environ.get(k) != v
    }
    return ["env"] + [f"{k}={v}" for k, v in sorted(overrides.items())]


def _copy_initial_state(initial_state_dir: Path, project: Path) -> None:
    """Recursively copy template contents into ``project``.

    Files are overlaid onto the existing project directory (the
    project may already contain a git repo from the base checkpoint);
    existing files with the same name are overwritten so the template
    has the last word.
    """
    project.mkdir(parents=True, exist_ok=True)
    for entry in initial_state_dir.iterdir():
        target = project / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target)


def _ensure_box_project_git_repo(box: Box) -> str | None:
    """Ensure the scenario project is a box-local git repo.

    ``c-installed-source`` intentionally installs opentraces into
    ``box.project/.testvenv`` but does not seed an application repo.
    Real-agent capture scenarios need the opposite shape: a tiny
    project with a local Git history, while the installed CLI remains
    ignored implementation detail. Without this, Git can walk out of
    ``.otbox`` into the developer's checkout.
    """
    project = Path(box.project)
    project.mkdir(parents=True, exist_ok=True)
    Path(box.home).mkdir(parents=True, exist_ok=True)
    gitignore = project / ".gitignore"
    ignore_lines = [
        ".testvenv/",
        ".agents/",
        ".opentraces/",
        ".opentraces.json",
        "__pycache__/",
        ".pytest_cache/",
    ]
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8").splitlines()
    else:
        existing = []
    missing = [line for line in ignore_lines if line not in existing]
    if missing:
        body = "\n".join([*existing, *missing]).strip()
        gitignore.write_text(f"{body}\n", encoding="utf-8")

    env = isolated_env(box)

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=str(project),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    if not (project / ".git").is_dir():
        init = run(["git", "init", "--quiet", "--initial-branch=main"])
        if init.returncode != 0:
            init = run(["git", "init", "--quiet"])
        if init.returncode != 0:
            return f"git init failed: {(init.stderr or init.stdout).strip()[:200]}"

    for key, value in (
        ("user.email", "otbox-agent@opentraces.local"),
        ("user.name", "otbox agent"),
    ):
        config = run(["git", "config", key, value])
        if config.returncode != 0:
            return (
                f"git config {key} failed: "
                f"{(config.stderr or config.stdout).strip()[:200]}"
            )

    add = run(["git", "add", "-A"])
    if add.returncode != 0:
        return f"git add failed: {(add.stderr or add.stdout).strip()[:200]}"

    diff = run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        return None
    if diff.returncode != 1:
        return f"git diff --cached failed: {(diff.stderr or diff.stdout).strip()[:200]}"

    commit = run(["git", "commit", "-q", "-m", "seed: initial project"])
    if commit.returncode != 0:
        return f"git commit failed: {(commit.stderr or commit.stdout).strip()[:200]}"
    return None


def _agent_name(agent: str | None) -> str | None:
    if agent in _CODEX_AGENTS:
        return "codex-cli"
    if agent in _CLAUDE_AGENTS:
        return "claude"
    if agent in _PI_AGENTS:
        return "pi"
    return agent


def _strip_codex_project_tables(config_text: str) -> str:
    """Return host Codex config without per-project history tables."""
    kept: list[str] = []
    skipping_project = False
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping_project = (
                stripped.startswith("[projects.")
                or stripped.startswith("[[projects.")
            )
        if not skipping_project:
            kept.append(line)
    body = "\n".join(kept).strip()
    return f"{body}\n" if body else ""


def _toml_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _prep_codex_project_trust(box_home: Path, project_dir: Path) -> None:
    """Add a trusted-project entry to the box-local Codex config."""
    codex_dir = box_home / ".codex"
    config_path = codex_dir / "config.toml"
    codex_dir.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("", encoding="utf-8")

    body = config_path.read_text(encoding="utf-8")
    project = str(project_dir.resolve())
    variants = {project}
    if project.startswith("/private"):
        variants.add(project.removeprefix("/private"))
    else:
        variants.add(f"/private{project}")

    additions: list[str] = []
    for variant in sorted(variants):
        table = f'[projects."{_toml_quote(variant)}"]'
        if table not in body:
            additions.append(f'{table}\ntrust_level = "trusted"\n')
    if additions:
        separator = "\n" if body and not body.endswith("\n\n") else ""
        config_path.write_text(
            f"{body}{separator}" + "\n".join(additions),
            encoding="utf-8",
        )


def _prep_agent_home(box_home: Path, agent: str | None) -> str | None:
    """Seed the box's isolated HOME with already-onboarded agent state.

    Without this, a real agent binary (e.g. ``claude``) launched into a
    virgin HOME shows first-run UX — theme picker, login prompt, terms
    acceptance, MCP-server trust prompts — and never reaches a state
    where the runner can drive it. The fix is to copy the host operator's
    already-onboarded state into the box, the same state
    ``capture-refresh`` requires anyway (the operator has to be logged
    in for the real agent to do anything useful).

    What gets seeded for ``claude``:

      * ``$HOME/.claude/.credentials.json`` — copied verbatim from the
        host so the API session is authenticated.
      * ``$HOME/.claude.json`` — copied from the host, then sanitised:
        ``editorMode`` is forced to ``"emacs"`` (PTY callers send keys
        in literal-text mode which breaks under vim), the ``projects``
        dict is wiped (Claude rebuilds per-project entries on demand
        and the host's 300KB+ of project history is irrelevant and
        privacy-leaky in a box copy), and any MCP-server prompts the
        host already approved are pre-acknowledged for the box's
        project path.

    The MCP-prompt suppression is the critical bit: when ``claude``
    launches inside the box's project dir it walks the cwd ancestry
    looking for ``.mcp.json`` files and surfaces a trust prompt for
    each user-scope server it finds. The host operator's
    ``~/.mcp.json`` is one such file and the box's runner cannot
    dismiss that prompt. Seeding ``hasTrustDialogAccepted: true``
    for the box's project path plus ``enabledMcpjsonServers: []``
    short-circuits the prompt; for safety the runner *also* passes
    ``--strict-mcp-config`` on the spawn line (defence in depth).

    The host operator's vim preference, full project history, and
    MCP enable list stay untouched on disk — only the box copy is
    normalised.

    Returns ``None`` on success or no-op (echo, unknown agent, etc.),
    or an error string the caller turns into a SKIP verdict if the
    operator's host state isn't usable.
    """
    normalized_agent = _agent_name(agent)
    if normalized_agent == "codex-cli":
        host_home = Path(os.path.expanduser("~"))
        host_codex = host_home / ".codex"
        host_auth = host_codex / "auth.json"
        host_config = host_codex / "config.toml"
        box_codex = box_home / ".codex"
        if not host_auth.is_file():
            return (
                f"agent prep: host {host_auth} not found — run `codex login` "
                "or authenticate Codex CLI first"
            )
        box_codex.mkdir(parents=True, exist_ok=True)
        shutil.copy2(host_auth, box_codex / "auth.json")
        if host_config.is_file():
            try:
                sanitized = _strip_codex_project_tables(
                    host_config.read_text(encoding="utf-8")
                )
            except OSError as exc:
                return f"agent prep: failed to read host {host_config}: {exc}"
            (box_codex / "config.toml").write_text(sanitized, encoding="utf-8")
        else:
            (box_codex / "config.toml").write_text("", encoding="utf-8")
        return None

    if normalized_agent == "pi":
        host_home = Path(os.path.expanduser("~"))
        host_pi = host_home / ".pi" / "agent"
        host_auth = host_pi / "auth.json"
        host_settings = host_pi / "settings.json"
        host_models = host_pi / "models.json"
        if not host_auth.is_file():
            return (
                f"agent prep: host {host_auth} not found — run `pi /login` "
                "or authenticate Pi first"
            )
        if not host_settings.is_file():
            return f"agent prep: host {host_settings} not found — run pi once to onboard"
        box_pi = box_home / ".pi" / "agent"
        box_pi.mkdir(parents=True, exist_ok=True)
        shutil.copy2(host_auth, box_pi / "auth.json")
        if host_models.is_file():
            shutil.copy2(host_models, box_pi / "models.json")
        try:
            import json as _json
            settings = _json.loads(host_settings.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - corrupt host config shouldn't crash the runner
            return f"agent prep: failed to parse host {host_settings}: {exc}"
        # Keep provider/model preferences so the box can run without first-run
        # prompts, but drop host package/resource lists. `_install_opentraces_hooks_in_box`
        # adds only the repo-local opentraces-pi package after this copy.
        sanitized = {
            key: settings[key]
            for key in (
                "defaultProvider",
                "defaultModel",
                "defaultThinkingLevel",
                "enabledModels",
                "theme",
                "lastChangelogVersion",
            )
            if key in settings
        }
        sanitized["packages"] = []
        (box_pi / "settings.json").write_text(
            _json.dumps(sanitized, indent=2), encoding="utf-8"
        )
        return None

    if normalized_agent != "claude":
        return None
    host_home = Path(os.path.expanduser("~"))
    host_settings = host_home / ".claude.json"
    host_creds = host_home / ".claude" / ".credentials.json"
    if not host_settings.is_file():
        return f"agent prep: host {host_settings} not found — run claude once to onboard"
    if not host_creds.is_file():
        return f"agent prep: host {host_creds} not found — run `claude login` first"
    box_home.mkdir(parents=True, exist_ok=True)
    (box_home / ".claude").mkdir(parents=True, exist_ok=True)
    try:
        import json as _json
        settings = _json.loads(host_settings.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - corrupt host config shouldn't crash the runner
        return f"agent prep: failed to parse host {host_settings}: {exc}"
    settings["editorMode"] = "emacs"
    # Drop the host's per-project history — Claude rebuilds per-project
    # entries on first contact, and the host's 186+ project entries
    # (300KB+) leak ambient state into every box. Replace with an empty
    # dict so the box starts with a clean project ledger.
    settings["projects"] = {}
    (box_home / ".claude.json").write_text(
        _json.dumps(settings, indent=2), encoding="utf-8"
    )
    shutil.copy2(host_creds, box_home / ".claude" / ".credentials.json")
    return None


def _prep_claude_project_trust(box_home: Path, project_dir: Path) -> None:
    """Pre-acknowledge MCP + trust prompts for the box's project path.

    ``claude`` walks the cwd ancestry looking for ``.mcp.json`` files
    when it boots and surfaces a per-server trust prompt for any
    user-scope server it finds. The host operator's ``~/.mcp.json``
    almost always declares servers, so the prompt fires by default
    even though the box's HOME is otherwise isolated. The PTY runner
    cannot dismiss that prompt (it requires keyboard interaction
    during boot when the alternate-screen buffer is empty to a
    ``tmux capture-pane`` reader).

    The defence is to seed an entry in ``$HOME/.claude.json`` under
    ``projects[<box.project absolute path>]`` that pre-acks the trust
    dialog and declares an empty enabled-MCP-server list for that
    path. ``claude --strict-mcp-config`` on the spawn line is the
    second layer of defence — even if the trust seeding drifts, the
    flag forces Claude to ignore everything outside the explicit
    config flag.

    Callable safely on any HOME — no-op if ``.claude.json`` is missing.
    """
    settings_path = box_home / ".claude.json"
    if not settings_path.is_file():
        return
    try:
        import json as _json
        settings = _json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt config shouldn't crash the runner
        return
    projects = settings.setdefault("projects", {})
    key = str(project_dir.resolve())
    # Pre-ack the trust dialog and declare zero enabled MCP servers for
    # this exact project path. Claude treats both the literal path and
    # any /private-prefixed Darwin variant as distinct keys, so seed
    # both forms — the cost is two small dict entries.
    for variant in {key, f"/private{key}" if not key.startswith("/private") else key.removeprefix("/private")}:
        entry = projects.setdefault(variant, {})
        entry["hasTrustDialogAccepted"] = True
        entry["enabledMcpjsonServers"] = []
        entry["disabledMcpjsonServers"] = []
        entry["mcpServers"] = {}
        entry["hasCompletedProjectOnboarding"] = True
    settings_path.write_text(_json.dumps(settings, indent=2), encoding="utf-8")


def _install_opentraces_hooks_in_box(box: Box, agent: str | None = "claude") -> str | None:
    """Install opentraces hooks into the box's HOME for one agent.

    The capture-refresh contract is to produce a real trace artifact,
    which requires opentraces' agent hooks to be wired into the box
    HOME BEFORE the agent boots. The box ships with an editable
    opentraces install at ``box.project/.testvenv/bin/opentraces`` (the
    ``c-installed-source`` checkpoint provisions it), so we invoke
    ``opentraces setup <agent>`` against the isolated HOME via the box
    CLI.

    Also runs ``opentraces init`` in the project (so the project has
    a registered ``project-<slug>`` state dir under
    ``$HOME/.opentraces/projects/``) and ``opentraces setup git``
    (post-commit correlator hook that powers ``trail blame``).

    Returns ``None`` on success or a non-fatal error string. The
    caller decides whether to fail the run on a non-None return.
    """
    from ..env import isolated_env

    normalized_agent = _agent_name(agent)
    if normalized_agent in (None, "echo", "hermes"):
        return None

    testvenv_cli = Path(box.project) / ".testvenv" / "bin" / "opentraces"
    if not testvenv_cli.is_file():
        return (
            f"box CLI not found at {testvenv_cli} — checkpoint "
            f"`c-installed-source` did not run?"
        )
    env = isolated_env(box)
    if normalized_agent == "claude":
        setup_agent = "claude-code"
        init_agent = "claude-code"
    elif normalized_agent == "codex-cli":
        setup_agent = "codex-cli"
        init_agent = "codex-cli"
    elif normalized_agent == "pi":
        setup_agent = "pi"
        init_agent = "pi"
    else:
        return None

    # 1. Install agent hooks into the box's isolated HOME. Pi capture-refresh
    # runs before npm publication, so load the repo-local package instead of
    # resolving npm:opentraces-pi from the public registry.
    setup_args = [str(testvenv_cli), "setup", setup_agent]
    if normalized_agent == "pi":
        setup_args.append("--local")
    setup_hooks = subprocess.run(
        setup_args,
        cwd=str(box.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if setup_hooks.returncode != 0:
        return (
            f"opentraces setup {setup_agent} failed (rc={setup_hooks.returncode}): "
            f"{(setup_hooks.stderr or setup_hooks.stdout).strip()[:200]}"
        )
    # 2. Install the skill into the harness namespace the real agent reads.
    setup_skill = subprocess.run(
        [str(testvenv_cli), "setup", "skill", "--harness", setup_agent],
        cwd=str(box.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if setup_skill.returncode != 0:
        return (
            f"opentraces setup skill --harness {setup_agent} failed "
            f"(rc={setup_skill.returncode}): "
            f"{(setup_skill.stderr or setup_skill.stdout).strip()[:200]}"
        )
    # 3. Register the project so traces have somewhere to land.
    init = subprocess.run(
        [str(testvenv_cli), "init", "--start-fresh", "--agent", init_agent],
        cwd=str(box.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if init.returncode != 0:
        return (
            f"opentraces init --agent {init_agent} failed (rc={init.returncode}): "
            f"{(init.stderr or init.stdout).strip()[:200]}"
        )
    # 4. Install the post-commit hook so trail-blame anchors mature.
    setup_git = subprocess.run(
        [str(testvenv_cli), "setup", "git"],
        cwd=str(box.project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if setup_git.returncode != 0:
        return (
            f"opentraces setup git failed (rc={setup_git.returncode}): "
            f"{(setup_git.stderr or setup_git.stdout).strip()[:200]}"
        )
    return None


def _rebuild_trace_search_snapshot_in_box(box: Box) -> str | None:
    """Run the explicit trace search maintenance command inside a box."""
    from ..env import isolated_env

    testvenv_cli = Path(box.project) / ".testvenv" / "bin" / "opentraces"
    if not testvenv_cli.is_file():
        return (
            f"box CLI not found at {testvenv_cli} — checkpoint "
            f"`c-installed-source` did not run?"
        )
    result = subprocess.run(
        [str(testvenv_cli), "trace", "index"],
        cwd=str(box.project),
        env=isolated_env(box),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return (
            f"opentraces trace index failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:200]}"
        )
    return None


__all__ = [
    "_agent_name",
    "_detect_binary_version",
    "_copy_initial_state",
    "_ensure_box_project_git_repo",
    "_prep_agent_home",
    "_prep_codex_project_trust",
    "_prep_claude_project_trust",
    "_install_opentraces_hooks_in_box",
    "_rebuild_trace_search_snapshot_in_box",
    "_build_env_prefix",
    "_strip_codex_project_tables",
    "_toml_quote",
]
