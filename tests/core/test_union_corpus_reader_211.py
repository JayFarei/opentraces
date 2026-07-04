"""Union corpus reader for the six consumer sites (issue #211).

RED-first coverage for the seal-family follow-up lane W3. ``iter_trace_record_objects``
(``bucket_trace_records.py``) only globs the bucket tier (v2 object + plan-079 legacy
mirror). It never looks at the two project-local JSONL layers ``trace_corpus`` treats
as first-class corpus members: project JSONL (``projects/<slug>/traces/<id>.jsonl``)
and staging JSONL. A trace can land there ONLY -- never getting a bucket object --
because ingest writes the JSONL before the bucket object as separate non-atomic
writes (``ingest.py``); a ``write_trace_record`` failure leaves the JSONL orphaned.

Six consumer call sites silently dropped these traces before this fix:

* ``core/datasets.py`` (``_bucket_record_ref``, dataset-row provenance backref)
* ``core/trace_index.py`` (``list_skill_invocation_units_from_records``)
* ``consumers/skill_intelligence/pipeline.py``
* ``consumers/verifier_factory/mining.py``
* ``cli/skill_verifier.py``
* the bundled ``pr-intent-summary-v1`` + ``skill-opt-v1`` workflow ``build_rows.py`` scripts

The cure is a sibling reader, ``iter_corpus_trace_records`` (routes through
``trace_corpus.iter_sources`` + ``load_record``), NOT a widening of
``iter_trace_record_objects`` itself -- the manifest/digest/prune/security-sweep
callers must stay bucket-object-only or the digest invariant and prune semantics
break.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentraces_schema import Agent, Step, TraceRecord

PROJECT_ONLY_TRACE_ID = "a1a1a1a1-2222-4333-8444-555555555555"

# The six call sites named by issue #211 (repo-relative paths).
SIX_CALL_SITE_MODULES = [
    "opentraces.core.datasets",
    "opentraces.core.trace_index",
    "opentraces.consumers.skill_intelligence.pipeline",
    "opentraces.consumers.verifier_factory.mining",
    "opentraces.cli.skill_verifier",
]
# The two bundled workflow scripts aren't importable modules (subprocess-run),
# addressed by filesystem path instead.
WORKFLOW_BUILD_ROWS_SCRIPTS = [
    "workflow_templates/pr-intent-summary-v1/scripts/build_rows.py",
    "workflow_templates/skill-opt-v1/scripts/build_rows.py",
]


def _trace(trace_id: str, content: str = "Project-local-only trace (#211)") -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": content},
        steps=[Step(step_index=1, role="user", content=content)],
        outcome={"success": True, "committed": False},
    )


def _enroll_project(project_dir: Path, project_id: str) -> str:
    """Write the opt-in marker and create the project's traces dir.

    Returns the real ``project_slug`` (``get_project_dir(project_dir).name``) --
    NOT ``project_dir.name``, which the slug hashes together with the
    ``project_id`` (:func:`opentraces.core.config._make_slug`).
    """
    from opentraces.core.config import get_project_dir, get_project_traces_dir

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )
    get_project_traces_dir(project_dir).mkdir(parents=True, exist_ok=True)
    return get_project_dir(project_dir).name


def _write_project_local_only_trace(project_dir: Path, record: TraceRecord) -> Path:
    """Seed a trace ONLY in the project-local JSONL layer -- no bucket object,
    no legacy mirror. This is exactly the artifact of a non-atomic ingest write
    where the JSONL landed but ``write_trace_record`` never ran (#211).
    """
    from opentraces.core.config import get_project_traces_dir

    trace_path = get_project_traces_dir(project_dir) / f"{record.trace_id}.jsonl"
    trace_path.write_text(record.model_dump_json() + "\n")
    return trace_path


# ---------------------------------------------------------------------------
# 1. RED-documented boundary: iter_trace_record_objects stays bucket-only.
# ---------------------------------------------------------------------------


def test_project_local_only_trace_absent_from_bucket_object_enumerator(tmp_path):
    """Documents the boundary (issue scope): ``iter_trace_record_objects`` is
    deliberately NOT widened and must never see a project-local-only trace,
    both before and after the union reader ships.
    """
    from opentraces.core.bucket_store import iter_trace_record_objects

    project = tmp_path / "proj"
    _enroll_project(project, "1111111111111111aaaaaaaaaaaaaaaa")
    _write_project_local_only_trace(project, _trace(PROJECT_ONLY_TRACE_ID))

    objects = iter_trace_record_objects()
    assert PROJECT_ONLY_TRACE_ID not in {o.trace_id for o in objects}


def test_project_local_only_trace_absent_from_bucket_record_ref_without_union_reader(tmp_path):
    """RED-documents the pre-fix symptom directly against the narrow enumerator:
    a naive ``{o.trace_id: o.record for o in iter_trace_record_objects(...)}``
    lookup style (what every one of the six call sites used before routing to
    the union reader) never resolves a project-local-only trace.
    """
    from opentraces.core.bucket_store import iter_trace_record_objects

    project = tmp_path / "proj"
    _enroll_project(project, "0101010101010101bbbbbbbbbbbbbbbb")
    _write_project_local_only_trace(project, _trace(PROJECT_ONLY_TRACE_ID))

    narrow_records = {o.trace_id: o.record for o in iter_trace_record_objects(project_slug=None)}
    assert PROJECT_ONLY_TRACE_ID not in narrow_records


# ---------------------------------------------------------------------------
# 2. GREEN: the union corpus reader sees it.
# ---------------------------------------------------------------------------


def test_project_local_only_trace_visible_in_union_corpus_reader(tmp_path):
    from opentraces.core.bucket_store import iter_corpus_trace_records

    project = tmp_path / "proj"
    slug = _enroll_project(project, "2222222222222222bbbbbbbbbbbbbbbb")
    _write_project_local_only_trace(project, _trace(PROJECT_ONLY_TRACE_ID))

    objects = iter_corpus_trace_records()
    assert PROJECT_ONLY_TRACE_ID in {o.trace_id for o in objects}

    # Also visible with an explicit project_slug filter (the shape every
    # routed call site actually calls with).
    scoped = iter_corpus_trace_records(project_slug=slug)
    assert PROJECT_ONLY_TRACE_ID in {o.trace_id for o in scoped}


def test_project_local_only_trace_visible_in_skill_invocation_units(tmp_path):
    """Consumer call site #2 (``core/trace_index.py``): a project-local-only
    trace carrying a skill invocation surfaces as a ``skill_invocation`` unit.
    """
    from opentraces.core import trace_index

    project = tmp_path / "proj"
    slug = _enroll_project(project, "3333333333333333cccccccccccccccc")
    record = _trace(PROJECT_ONLY_TRACE_ID)
    record.metadata["skill_invocations"] = [
        {"name": "demo-skill", "source": "metadata", "step_index": 1}
    ]
    _write_project_local_only_trace(project, record)

    units = trace_index.list_skill_invocation_units_from_records(project_slug=slug)
    assert any(unit.trace_id == PROJECT_ONLY_TRACE_ID for unit in units)


def test_project_local_only_trace_visible_in_verifier_mining(tmp_path):
    """Consumer call site #4 (``consumers/verifier_factory/mining.py``)."""
    from opentraces.core.bucket_store import iter_corpus_trace_records

    project = tmp_path / "proj"
    slug = _enroll_project(project, "4444444444444444dddddddddddddddd")
    _write_project_local_only_trace(project, _trace(PROJECT_ONLY_TRACE_ID))

    # Mirrors ``mine_verifier_candidates``'s record load: a dict of
    # trace_id -> record built from the union reader, scoped to a project.
    records = {o.trace_id: o.record for o in iter_corpus_trace_records(project_slug=slug)}
    assert PROJECT_ONLY_TRACE_ID in records


def test_project_local_only_trace_visible_in_real_dataset_run(tmp_path, monkeypatch):
    """Acceptance bar #1: a project-local-only trace is visible in a real
    ``opentraces dataset run`` against the bundled ``skill-opt-v1`` workflow --
    the actual bundled ``build_rows.py`` script, subprocess-executed with a
    real run packet, not an internal mock.

    ``execute_workflow`` runs the script through ``core.isolation.run_isolated``,
    which builds the child's env from an allowlist that deliberately excludes
    ``PYTHONPATH`` (a real hardening property -- the isolated child must not
    inherit the parent's import path). In this dev worktree the installed
    interpreter's editable ``opentraces`` link points at the sibling checkout,
    not this branch, so the child must be handed ``PYTHONPATH`` explicitly for
    THIS branch's code to be what actually executes; that is what
    ``_ENV_ALLOW_EXACT`` is widened for here, test-locally only.
    """
    from opentraces.core import isolation
    from opentraces.core.workflow_runner import execute_workflow
    from opentraces.core.workflows import create_workflow

    monkeypatch.setattr(
        isolation, "_ENV_ALLOW_EXACT", isolation._ENV_ALLOW_EXACT | {"PYTHONPATH"}
    )

    project = tmp_path / "proj"
    _enroll_project(project, "5555555555555555eeeeeeeeeeeeeeee")
    _write_project_local_only_trace(project, _trace(PROJECT_ONLY_TRACE_ID))

    create_workflow("skill-opt-v1", template="skill-opt-v1", replace=True)
    out = tmp_path / "rows.jsonl"
    result = execute_workflow("skill-opt-v1", scope={"kind": "skill_opt"}, output_path=out)
    assert result.row_count >= 1

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    row_trace_ids = {row.get("trace_id") for row in rows}
    assert PROJECT_ONLY_TRACE_ID in row_trace_ids


# ---------------------------------------------------------------------------
# 3. Digest invariance + prune semantics gate (must never go red).
# ---------------------------------------------------------------------------


def test_bucket_manifest_digest_unaffected_by_project_local_only_trace(tmp_path):
    """A project-local-only trace must never be visible to, or perturb, the
    bucket manifest / ``bucket_digest`` -- that scan stays bucket-object-only
    by construction (the deliberately-not-widened ``iter_trace_record_objects``).
    """
    from opentraces.core.bucket_store import bucket_manifest, write_trace_record

    write_trace_record(
        _trace("bucket-object-trace-1"),
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    manifest_before = bucket_manifest(write=True)
    digest_before = manifest_before.get("bucket_digest") or manifest_before.get("digest")
    assert digest_before is not None

    project = tmp_path / "proj2"
    _enroll_project(project, "6666666666666666ffffffffffffffff")
    _write_project_local_only_trace(project, _trace(PROJECT_ONLY_TRACE_ID))

    manifest_after = bucket_manifest(write=True)
    digest_after = manifest_after.get("bucket_digest") or manifest_after.get("digest")

    assert digest_after == digest_before
    trace_ids_in_manifest = {row.get("trace_id") for row in manifest_after.get("traces", [])}
    assert PROJECT_ONLY_TRACE_ID not in trace_ids_in_manifest


def test_bucket_prune_never_touches_a_project_local_only_trace(tmp_path):
    """``bucket_prune`` (reachability-based orphan cleanup) only ever reasons
    about the bucket tier; a project-local-only trace's JSONL file is not
    reachable from ``traces/v1`` and must survive untouched, never treated as
    an orphan.
    """
    from opentraces.core.bucket_maintenance import bucket_prune

    project = tmp_path / "proj3"
    _enroll_project(project, "7777777777777777aaaaaaaabbbbbbbb")
    trace_path = _write_project_local_only_trace(project, _trace(PROJECT_ONLY_TRACE_ID))

    result = bucket_prune(dry_run=False)

    assert trace_path.exists()
    assert trace_path.read_text(encoding="utf-8").strip() != ""
    # The orphan sweep must not have reasoned about (or deleted) anything
    # under the project-local JSONL tree.
    for blob in result.get("blobs", []):
        assert "proj3" not in blob


# ---------------------------------------------------------------------------
# 4. Tamper guard: the six call sites reference the union reader.
# ---------------------------------------------------------------------------


def _module_source(module_name: str) -> str:
    import importlib

    module = importlib.import_module(module_name)
    return Path(module.__file__).read_text(encoding="utf-8")


def _workflow_script_source(relative_path: str) -> str:
    import opentraces

    root = Path(opentraces.__file__).resolve().parent
    return (root / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("module_name", SIX_CALL_SITE_MODULES)
def test_call_site_modules_do_not_call_the_narrow_enumerator(module_name):
    """Import-level tamper guard: none of the five importable call-site modules
    may call ``iter_trace_record_objects(`` directly. Two of them (``datasets``,
    ``skill_intelligence.pipeline``) route through the corpus-aware
    ``read_bucket_record_for_trace`` bounded reader instead; the rest call
    ``iter_corpus_trace_records`` directly.
    """
    source = _module_source(module_name)
    assert "iter_trace_record_objects(" not in source, (
        f"{module_name} must not call the bucket-object-only enumerator directly "
        "-- route through iter_corpus_trace_records / read_bucket_record_for_trace"
    )

    routes_via_union_reader = "iter_corpus_trace_records" in source
    routes_via_bounded_reader = "read_bucket_record_for_trace" in source
    assert routes_via_union_reader or routes_via_bounded_reader, (
        f"{module_name} must reference the union corpus reader (directly or via "
        "the bounded per-trace reader that itself resolves through trace_corpus)"
    )


@pytest.mark.parametrize("script_path", WORKFLOW_BUILD_ROWS_SCRIPTS)
def test_workflow_build_rows_scripts_use_the_union_reader(script_path):
    source = _workflow_script_source(script_path)
    assert "iter_trace_record_objects(" not in source
    assert "iter_corpus_trace_records" in source


def test_bucket_maintenance_and_security_callers_stay_on_the_narrow_enumerator():
    """The keep-narrow list (issue #211 scope) must NOT have been touched:
    manifest build, security sweep, and prune all stay bucket-object-only.
    """
    for module_name in (
        "opentraces.core.bucket_store",
        "opentraces.core.bucket_security",
        "opentraces.core.bucket_maintenance",
        "opentraces.core.bucket_trace_records",
    ):
        source = _module_source(module_name)
        assert "iter_trace_record_objects(" in source, (
            f"{module_name} must still call the bucket-object-only enumerator "
            "-- it is on the deliberate keep-narrow list"
        )
