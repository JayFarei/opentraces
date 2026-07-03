"""M1 workflow integrity (#187): digested == installed == executed.

RED-first guards for the two halves of the integrity invariant:

* INSTALL-TIME — the digest excludes dot-prefixed paths (``_workflow_files``),
  but ``install_workflow`` used to ``copytree`` everything including dotfiles, so
  a planted ``scripts/.runtime/payload.py`` would land on disk and run yet stay
  invisible to ``compute_workflow_digest``. Install now rejects non-allowlisted
  dot-prefixed entries (templates ship none), so the installed tree is exactly
  the digested set.
* RUN-TIME — the run recomputes the workflow digest and compares it to the
  digest bound at pin time (``manifest.workflow.digest`` for datasets; the run
  packet's ``workflow_digest`` for the primitive seam). A post-install mutation
  is warned by default and made fatal (non-zero, before execution) under
  ``--strict``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opentraces.core.datasets import create_dataset
from opentraces.core.workflow_runner import execute_workflow, run_dataset_workflow
from opentraces.core.workflows import (
    WorkflowIntegrityError,
    compute_workflow_digest,
    install_workflow,
    load_workflow,
    verify_workflow_digest,
    workflows_dir,
)

_BUILD_ROWS = (
    "import json, os\n"
    "from pathlib import Path\n"
    "Path(os.environ['OT_DATASET_OUTPUT']).write_text(\n"
    "    json.dumps({'ok': True}, sort_keys=True) + '\\n', encoding='utf-8'\n"
    ")\n"
)

_SCOPE = {"scope": "all-projects"}


def _write_valid_source(root: Path, name: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: valid fixture\nmode: agent-skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "build_rows.py").write_text(_BUILD_ROWS, encoding="utf-8")
    (root / "examples").mkdir()
    (root / "examples" / "expected-row.json").write_text("{}\n", encoding="utf-8")


def _install_script_workflow(skill: str) -> str:
    pkg = workflows_dir() / skill
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: integrity fixture\nmode: agent-skill\n---\n\n# {skill}\n",
        encoding="utf-8",
    )
    (pkg / "scripts" / "build_rows.py").write_text(_BUILD_ROWS, encoding="utf-8")
    return load_workflow(skill).digest


def _new_pinned_dataset(name: str, skill: str, digest: str) -> None:
    create_dataset(
        name,
        workflow_skill=skill,
        workflow_digest=digest,
        row_schema={"type": "object", "additionalProperties": True},
    )


# --- (a) install rejects non-allowlisted dotfiles/dot-dirs ------------------


def test_planted_dot_dir_payload_rejected_at_install(tmp_path):
    """The exact #187 case: a package shipping ``scripts/.runtime/payload.py``
    is rejected at install (it would install undigested and run)."""
    source = tmp_path / "planted-pkg"
    _write_valid_source(source, "planted-pkg")
    runtime = source / "scripts" / ".runtime"
    runtime.mkdir()
    (runtime / "payload.py").write_text("raise SystemExit('pwned')\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        install_workflow(source)
    assert ".runtime" in str(excinfo.value)

    # Rejected BEFORE any copy: nothing landed on disk, so nothing can run.
    assert not (workflows_dir() / "planted-pkg").exists()


def test_planted_top_level_dotfile_rejected_at_install(tmp_path):
    source = tmp_path / "dotfile-pkg"
    _write_valid_source(source, "dotfile-pkg")
    (source / ".env").write_text("SECRET=1\n", encoding="utf-8")

    with pytest.raises(ValueError):
        install_workflow(source)
    assert not (workflows_dir() / "dotfile-pkg").exists()


# --- (b) installed tree recomputes to the exact source digest ---------------


def test_installed_tree_recomputes_to_source_digest(tmp_path):
    source = tmp_path / "clean-pkg"
    _write_valid_source(source, "clean-pkg")

    installed = install_workflow(source)

    assert compute_workflow_digest(installed.path) == compute_workflow_digest(source)
    # And the installed package carries that digest verbatim.
    assert installed.digest == compute_workflow_digest(source)


# --- (c) post-install mutation detected at run time -------------------------


def test_clean_run_does_not_warn_about_integrity(capsys):
    skill = "integrity-clean-curator"
    digest = _install_script_workflow(skill)
    _new_pinned_dataset("integrity-clean", skill, digest)

    run_dataset_workflow("integrity-clean", executor="script", scope=_SCOPE, dry_run=True)

    err = capsys.readouterr().err
    assert "digest mismatch" not in err


def test_post_install_mutation_warns_at_run_time(capsys):
    skill = "integrity-warn-curator"
    digest = _install_script_workflow(skill)
    _new_pinned_dataset("integrity-warn", skill, digest)

    # Mutate a digest-material (non-dotfile) file after the dataset pinned it.
    script = workflows_dir() / skill / "scripts" / "build_rows.py"
    script.write_text(_BUILD_ROWS + "# mutated after pin\n", encoding="utf-8")

    # Default (non-strict): warn on stderr, run still proceeds.
    run_dataset_workflow("integrity-warn", executor="script", scope=_SCOPE, dry_run=True)

    err = capsys.readouterr().err
    assert "digest mismatch" in err


def test_post_install_mutation_is_fatal_under_strict():
    skill = "integrity-strict-curator"
    digest = _install_script_workflow(skill)
    _new_pinned_dataset("integrity-strict", skill, digest)

    script = workflows_dir() / skill / "scripts" / "build_rows.py"
    script.write_text(_BUILD_ROWS + "# mutated after pin\n", encoding="utf-8")

    with pytest.raises(WorkflowIntegrityError):
        run_dataset_workflow(
            "integrity-strict", executor="script", scope=_SCOPE, strict=True, dry_run=True
        )


# --- verify helper + primitive seam guards ----------------------------------


def test_verify_workflow_digest_helper_semantics():
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64
    # Match → ok, no raise.
    assert verify_workflow_digest(digest_a, digest_a, workflow_name="w") is True
    # No pin → nothing to verify.
    assert verify_workflow_digest(digest_a, None, workflow_name="w") is True
    # A placeholder/unconfigured pin (not a well-formed digest) → skip, never a
    # fabricated mismatch.
    assert verify_workflow_digest(digest_a, "sha256:unconfigured", workflow_name="w") is True
    # Genuine mismatch of two real digests, non-strict → False (warn), no raise.
    assert verify_workflow_digest(digest_a, digest_b, workflow_name="w") is False
    # Genuine mismatch, strict → fatal.
    with pytest.raises(WorkflowIntegrityError):
        verify_workflow_digest(digest_a, digest_b, workflow_name="w", strict=True)


def test_execute_workflow_seam_rejects_stale_pin_under_strict(tmp_path):
    """The single execution seam re-verifies a supplied pinned digest before
    running the subprocess: a stale pin is fatal under strict, before any row
    is produced."""
    skill = "integrity-seam-curator"
    _install_script_workflow(skill)
    out = tmp_path / "rows.jsonl"
    # A well-formed but stale pin (the seam only asserts against real digests).
    stale_packet = {"workflow_digest": "sha256:" + "0" * 64}

    with pytest.raises(WorkflowIntegrityError):
        execute_workflow(
            skill,
            scope=_SCOPE,
            output_path=out,
            executor="script",
            run_dir=tmp_path,
            run_packet=stale_packet,
            strict=True,
        )
    # Fatal BEFORE execution: no rows were written.
    assert not out.exists() or out.read_text(encoding="utf-8") == ""
