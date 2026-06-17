"""`c-multi-install-mixed-runtime` — integrations wired to divergent installs.

Built on top of ``c-installed-source`` (opentraces editable in
``box.project/.testvenv``). The delta, ENTIRELY inside the box HOME (no real
host brew/pipx touched), manufactures two extra opentraces install roots and
points the integration runners at them:

  * a fake **pipx** root under ``<home>/.local/pipx/venvs/opentraces/...``
  * a fake **homebrew** root under ``<home>/brew/Cellar/opentraces/<ver>/libexec/...``

then patches the box's ``~/.codex/hooks.json`` + ``~/.claude/settings.json`` to
run the fake pipx python, the project's git ``opentraces-post-commit`` shim to
run the fake homebrew python, and the watcher worker shim to resolve the fake
homebrew python. The foreground CLI stays the editable ``.testvenv``.

Each fake "python" is a tiny ``#!/bin/sh`` wrapper that ignores its args and
echoes ``<module_file>\n<version>`` — exactly the two lines doctor's bounded
provenance probe (``<python> -c "import opentraces; print(__file__); print(
version)"``) reads. So the probe resolves a REAL, on-disk, distinct module file
per runner without needing a real venv (CI-safe, instant).

Result: ``opentraces doctor --json`` from the editable CLI reports
``runtime_provenance.state == "mixed_runtimes"`` with ≥2 discovered installs of
distinct ``source_kind`` / ``module_file`` and the codex/claude/git/watcher
runners all ``matches_current == false``.

HOME redirection is the box's ``isolated_env`` (``tests/otbox/env.py``), so the
doctor readers' ``Path.home()`` resolves into the box and the fake configs are
seen. Detection-only: doctor mutates nothing.
"""

from __future__ import annotations

import json

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, register

_PY_TAG = "python3.12"


def _wrapper_script(module_file: str, version: str) -> str:
    """A fake `python` that echoes the two probe lines and exits 0."""
    return f"#!/bin/sh\necho {module_file!r}\necho {version!r}\n"


def _multi_install_mixed_runtime_delta(driver: Driver, box: Box) -> None:
    from opentraces.core.integration_versions import current_cli_version

    version = current_cli_version()
    paths = driver.paths(box)
    home = paths["home"]
    project = paths["project"]
    opentraces_dir = paths["opentraces_dir"]

    # --- fake pipx install root -------------------------------------------
    pipx_site = f"{home}/.local/pipx/venvs/opentraces/lib/{_PY_TAG}/site-packages"
    pipx_module = f"{pipx_site}/opentraces/__init__.py"
    pipx_python = f"{home}/.local/pipx/venvs/opentraces/bin/python"
    driver.put_text(box, pipx_module, "# stub opentraces (fake pipx install)\n")
    driver.put_text(
        box,
        f"{pipx_site}/opentraces-{version}.dist-info/METADATA",
        f"Metadata-Version: 2.1\nName: opentraces\nVersion: {version}\n",
    )
    driver.put_text(box, pipx_python, _wrapper_script(pipx_module, version))
    driver.exec(box, ["chmod", "+x", pipx_python])

    # --- fake homebrew (Cellar) install root ------------------------------
    brew_libexec = f"{home}/brew/Cellar/opentraces/{version}/libexec"
    brew_site = f"{brew_libexec}/lib/{_PY_TAG}/site-packages"
    brew_module = f"{brew_site}/opentraces/__init__.py"
    brew_python = f"{brew_libexec}/bin/python"
    driver.put_text(box, brew_module, "# stub opentraces (fake homebrew install)\n")
    driver.put_text(
        box,
        f"{brew_site}/opentraces-{version}.dist-info/METADATA",
        f"Metadata-Version: 2.1\nName: opentraces\nVersion: {version}\n",
    )
    driver.put_text(box, brew_python, _wrapper_script(brew_module, version))
    driver.exec(box, ["chmod", "+x", brew_python])

    # --- point codex hooks at the fake pipx python ------------------------
    codex_cmd = f'"{pipx_python}" -m opentraces.capture.codex_cli.hooks.on_tool_use'
    driver.put_text(
        box,
        f"{home}/.codex/hooks.json",
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {"hooks": [{"type": "command", "command": codex_cmd}]}
                    ]
                }
            },
            indent=2,
        ),
    )

    # --- point claude hooks at the fake pipx python -----------------------
    claude_cmd = f'"{pipx_python}" -m opentraces.capture.claude_code.hooks.on_stop'
    driver.put_text(
        box,
        f"{home}/.claude/settings.json",
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": claude_cmd}]}
                    ]
                }
            },
            indent=2,
        ),
    )

    # --- point the git post-commit shim at the fake homebrew python -------
    git_hook = f"{project}/.git/hooks/opentraces-post-commit"
    driver.put_text(
        box,
        git_hook,
        "#!/usr/bin/env sh\n"
        "set +e\n"
        f'"{brew_python}" -m opentraces _run-post-commit-hook "$(pwd)" '
        ">/dev/null 2>&1 || true\n"
        "exit 0\n",
    )
    driver.exec(box, ["chmod", "+x", git_hook])

    # --- point the watcher worker shim at the fake homebrew python --------
    # Kept clean (run-sweep verb + matching version stamp + on-disk
    # interpreter) so the watcher-provenance exit gate stays quiet; the only
    # thing diverging is the install ROOT, which runtime_provenance reports
    # without gating.
    watcher_shim = f"{opentraces_dir}/bin/ot-watcher"
    driver.put_text(
        box,
        watcher_shim,
        "#!/usr/bin/env sh\n"
        f"# opentraces-version: {version}\n"
        f'exec "{brew_python}" -m opentraces setup watcher run-sweep "$@"\n',
    )
    driver.exec(box, ["chmod", "+x", watcher_shim])

    box.notes["c_multi_install_mixed_runtime_audit"] = {
        "version": version,
        "current_is_editable_testvenv": True,
        "pipx_module": pipx_module,
        "brew_module": brew_module,
        "pipx_python": pipx_python,
        "brew_python": brew_python,
    }


register(
    Checkpoint(
        name="c-multi-install-mixed-runtime",
        composed_from="c-installed-source",
        delta=_multi_install_mixed_runtime_delta,
        cache=True,
        description=(
            "c-installed-source + fake pipx/homebrew install roots with the "
            "codex/claude/git/watcher runners pointed at them, so doctor's "
            "runtime_provenance reports mixed_runtimes (issue #93)."
        ),
        provides={},
    )
)
