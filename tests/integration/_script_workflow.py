"""Test helper: install a deterministic ``script``-executor workflow.

The M1 engine collapse (#185) removed the permanently-dead
``claude-code-headless`` executor and its fake-rows environment injection hook.
Dataset tests that used to fake rows through that hook now install a real
workflow package whose ``scripts/build_rows.py`` writes fixed JSONL rows through
the single subprocess execution seam (``_execute_script`` via
``execute_workflow``).

Install AFTER ``dataset new`` (so ``dataset new`` pins the caller's explicit
``--workflow-digest`` instead of the fixture's recomputed digest) and BEFORE
``dataset run --executor script``. ``workflows_dir()`` resolves under whatever
``OPENTRACES_DIR`` is active, so this works both under the autouse per-test HOME
isolation and inside a ``_temporary_opentraces_home`` block.
"""

from __future__ import annotations

from pathlib import Path

from opentraces.core.workflows import workflows_dir

_SKILL_MD = (
    "---\n"
    "name: {name}\n"
    "description: Deterministic test row emitter\n"
    "mode: agent-skill\n"
    "---\n\n"
    "# {name}\n\n"
    "Emit fixed rows for tests.\n"
)


def _write_skill(pkg: Path, name: str) -> None:
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")


def install_rows_workflow(name: str, rows: str) -> Path:
    """Install a workflow package under ``name`` whose script writes ``rows``.

    ``rows`` is already-serialized JSONL text (possibly empty). The generated
    ``build_rows.py`` reproduces the old headless hook's write semantics exactly:
    it writes ``rows.rstrip("\\n") + "\\n"`` to ``$OT_DATASET_OUTPUT`` (an empty
    ``rows`` becomes a single newline, i.e. zero data rows).
    """

    pkg = workflows_dir() / name
    _write_skill(pkg, name)
    (pkg / "scripts" / "build_rows.py").write_text(
        "import os\n"
        f"ROWS = {rows!r}\n"
        "with open(os.environ['OT_DATASET_OUTPUT'], 'w', encoding='utf-8') as fh:\n"
        "    fh.write(ROWS.rstrip('\\n') + '\\n')\n",
        encoding="utf-8",
    )
    return pkg


def install_scriptless_workflow(name: str) -> Path:
    """Install a package with a SKILL.md but no ``scripts/build_rows.py``.

    Running it with the ``script`` executor raises ``ExecutorUnavailableError``
    (the builder is missing) — the modern stand-in for the old "executor
    unavailable" failure path.
    """

    pkg = workflows_dir() / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    return pkg
