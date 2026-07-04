"""``core.dataset_facets`` — model/harness metadata as dataset scope facets (#212).

RED-first coverage (paste-worthy against ``origin/feat/seal-family``, where
neither ``dataset_facets.py`` nor ``dataset run --facet`` exist):

    $ ot dataset run demo --facet model=anthropic/claude-sonnet-4 --json
    Error: No such option: --facet Did you mean --trace?
    (exit 2)

This file covers the module in isolation: parsing/validation, exact-match
resolution against the persisted bucket manifest (including the honest
null-``agent.version`` exclusion), and the non-negotiable cost gate — opens of
``trace.json``/``current.json`` during resolution must be bounded by the
selected set, NOT by corpus size N. The cost-gate test proves its own
red-capability (a deliberately naive full-scan resolver is shown to explode
the open count) before asserting the real implementation stays at zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces_schema import Agent, Step, TraceRecord


def _trace(
    trace_id: str,
    *,
    agent_name: str,
    agent_version: str | None,
    agent_model: str | None,
) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name=agent_name, version=agent_version, model=agent_model),
        task={"description": f"Work on {trace_id}"},
        steps=[
            Step(step_index=1, role="user", content=f"Work on {trace_id}"),
            Step(step_index=2, role="agent", content="Done."),
        ],
    )


def _seed_world(tmp_path: Path, project_slug: str = "demo") -> None:
    """Seed a multi-model/multi-version world and persist the bucket manifest.

    Three traces across two models / two agent versions, plus one trace whose
    harness never captured a version (``agent_version=None`` — the honest
    limitation the null-version scope test exercises).
    """

    from opentraces.core.bucket_store import bucket_manifest, project_per_trace_exports

    project_per_trace_exports(
        tmp_path,
        project_slug=project_slug,
        trace_id="trace-claude-v1",
        record=_trace(
            "trace-claude-v1",
            agent_name="claude-code",
            agent_version="2.1.143",
            agent_model="anthropic/claude-sonnet-4",
        ),
    )
    project_per_trace_exports(
        tmp_path,
        project_slug=project_slug,
        trace_id="trace-claude-v2",
        record=_trace(
            "trace-claude-v2",
            agent_name="claude-code",
            agent_version="2.2.0",
            agent_model="anthropic/claude-sonnet-4",
        ),
    )
    project_per_trace_exports(
        tmp_path,
        project_slug=project_slug,
        trace_id="trace-codex",
        record=_trace(
            "trace-codex",
            agent_name="codex-cli",
            agent_version="0.31.0",
            agent_model="openai/gpt-5-codex",
        ),
    )
    project_per_trace_exports(
        tmp_path,
        project_slug=project_slug,
        trace_id="trace-hermes-no-version",
        record=_trace(
            "trace-hermes-no-version",
            agent_name="hermes-agent",
            agent_version=None,
            agent_model="z-ai/glm-5",
        ),
    )
    # Persist manifest.json once (the honest O(N) sweep, per plan-087's
    # documented boundary) so resolution below reads it O(1).
    bucket_manifest(write=True)


def test_parse_facet_filters_name_value() -> None:
    from opentraces.core.dataset_facets import parse_facet_filters

    parsed = parse_facet_filters(("model=anthropic/claude-sonnet-4", "agent.name=claude-code"))
    assert parsed == {"model": "anthropic/claude-sonnet-4", "agent.name": "claude-code"}


def test_parse_facet_filters_rejects_missing_equals() -> None:
    from opentraces.core.dataset_facets import parse_facet_filters

    with pytest.raises(ValueError, match="name=value"):
        parse_facet_filters(("missing-equals",))


def test_validate_facet_names_rejects_unsupported_name() -> None:
    from opentraces.core.dataset_facets import validate_facet_names

    with pytest.raises(ValueError, match="unsupported --facet name"):
        validate_facet_names({"survival.state": "reverted"})
    # Every documented name passes.
    validate_facet_names({"model": "x", "agent.name": "y", "agent.version": "z"})


def test_resolve_facet_candidates_matches_exact_set(tmp_path: Path) -> None:
    from opentraces.core.dataset_facets import resolve_facet_candidates

    _seed_world(tmp_path)

    by_model = resolve_facet_candidates({"model": "anthropic/claude-sonnet-4"})
    assert {row["trace_id"] for row in by_model} == {"trace-claude-v1", "trace-claude-v2"}

    by_agent = resolve_facet_candidates({"agent.name": "codex-cli"})
    assert [row["trace_id"] for row in by_agent] == ["trace-codex"]

    # Case-insensitive value match.
    by_agent_upper = resolve_facet_candidates({"agent.name": "CODEX-CLI"})
    assert [row["trace_id"] for row in by_agent_upper] == ["trace-codex"]


def test_resolve_facet_candidates_version_scope_excludes_null_version(
    tmp_path: Path,
) -> None:
    """A trace whose harness never captured a version must not match a
    version scope — an honest limitation, not a bug."""

    from opentraces.core.dataset_facets import resolve_facet_candidates

    _seed_world(tmp_path)

    matches = resolve_facet_candidates({"agent.version": "2.1.143"})
    assert [row["trace_id"] for row in matches] == ["trace-claude-v1"]

    # The null-version trace never matches ANY version scope, even one that
    # would otherwise be a substring/empty-string edge case.
    all_trace_ids = {
        row["trace_id"] for row in resolve_facet_candidates({"agent.name": "hermes-agent"})
    }
    assert "trace-hermes-no-version" in all_trace_ids  # exists in the corpus
    version_matches = {
        row["trace_id"]
        for v in ("2.1.143", "2.2.0", "0.31.0")
        for row in resolve_facet_candidates({"agent.version": v})
    }
    assert "trace-hermes-no-version" not in version_matches


def test_resolve_facet_candidates_composes_with_project_scope(tmp_path: Path) -> None:
    from opentraces.core.bucket_store import bucket_manifest, project_per_trace_exports
    from opentraces.core.dataset_facets import resolve_facet_candidates

    _seed_world(tmp_path, project_slug="proj-a")
    project_per_trace_exports(
        tmp_path,
        project_slug="proj-b",
        trace_id="trace-other-project",
        record=_trace(
            "trace-other-project",
            agent_name="codex-cli",
            agent_version="0.31.0",
            agent_model="openai/gpt-5-codex",
        ),
    )
    bucket_manifest(write=True)

    matches = resolve_facet_candidates({"agent.name": "codex-cli"}, project_slug="proj-a")
    assert [row["trace_id"] for row in matches] == ["trace-codex"]

    unscoped = resolve_facet_candidates({"agent.name": "codex-cli"})
    assert {row["trace_id"] for row in unscoped} == {"trace-codex", "trace-other-project"}


def test_resolve_facet_candidates_empty_facets_returns_empty(tmp_path: Path) -> None:
    from opentraces.core.dataset_facets import resolve_facet_candidates

    _seed_world(tmp_path)
    assert resolve_facet_candidates({}) == []


def test_bucket_manifest_digest_unchanged_by_facet_resolution(tmp_path: Path) -> None:
    """Digest gate (never red): resolving facets is read-only. The persisted
    ``bucket/manifest.json`` digest before and after a faceted resolution over
    the same world is byte-identical -- out of scope item confirms this
    module never touches ``digest_material``."""

    from opentraces.core.bucket_store import bucket_manifest
    from opentraces.core.dataset_facets import resolve_facet_candidates

    _seed_world(tmp_path)
    digest_before = bucket_manifest(write=False, include_objects=False)["digest"]

    resolve_facet_candidates({"model": "anthropic/claude-sonnet-4"})
    resolve_facet_candidates({"agent.version": "2.1.143"})
    resolve_facet_candidates({"agent.name": "does-not-exist"})

    digest_after = bucket_manifest(write=False, include_objects=False)["digest"]
    assert digest_after == digest_before


# ---------------------------------------------------------------------------
# The cost gate: O(manifest), never O(corpus) trace.json/current.json opens.
# ---------------------------------------------------------------------------


def _count_per_trace_file_reads(monkeypatch) -> list[int]:
    """Install a counter at the reader boundary for BOTH per-trace stores:
    the plan-080 v2 ``trace.json`` (``iter_traces_v2``'s direct
    ``Path.read_text``) and the ``trace_records/v1`` ``current.json`` object
    store (``read_trace_record_object``). Returns a single-element mutable
    list so the count is visible to the caller after each run.
    """

    count = [0]
    real_read_text = Path.read_text

    def _counting_read_text(self: Path, *args, **kwargs):
        if self.name in ("trace.json", "current.json"):
            count[0] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _counting_read_text)
    return count


def test_resolve_facet_candidates_never_opens_per_trace_files(
    tmp_path: Path, monkeypatch
) -> None:
    """The persisted-manifest read path opens ZERO ``trace.json``/
    ``current.json`` files, regardless of corpus size — the #87 anti-pattern
    this family cured for reads must not regress here."""

    from opentraces.core.dataset_facets import resolve_facet_candidates

    _seed_world(tmp_path)  # 4 traces persisted to manifest.json

    count = _count_per_trace_file_reads(monkeypatch)
    matches = resolve_facet_candidates({"model": "anthropic/claude-sonnet-4"})
    assert {row["trace_id"] for row in matches} == {"trace-claude-v1", "trace-claude-v2"}
    assert count[0] == 0, (
        "resolve_facet_candidates opened a per-trace file; it must resolve "
        "entirely against the persisted manifest"
    )


def test_resolve_facet_candidates_cost_is_independent_of_corpus_size(
    tmp_path: Path, monkeypatch
) -> None:
    """Prove the bound holds AS N GROWS, not just at N=4: opens stay at zero
    when the corpus is 4x larger, and the matched set is still exact."""

    from opentraces.core.bucket_store import bucket_manifest, project_per_trace_exports
    from opentraces.core.dataset_facets import resolve_facet_candidates

    _seed_world(tmp_path)  # 4 traces

    count_small = _count_per_trace_file_reads(monkeypatch)
    resolve_facet_candidates({"agent.name": "codex-cli"})
    small_opens = count_small[0]

    # Grow the corpus 4x (16 more unrelated traces) and re-persist.
    for i in range(16):
        project_per_trace_exports(
            tmp_path,
            project_slug="demo",
            trace_id=f"trace-filler-{i}",
            record=_trace(
                f"trace-filler-{i}",
                agent_name="filler-agent",
                agent_version="9.9.9",
                agent_model="some/other-model",
            ),
        )
    bucket_manifest(write=True)

    count_large = _count_per_trace_file_reads(monkeypatch)
    matches = resolve_facet_candidates({"agent.name": "codex-cli"})
    assert [row["trace_id"] for row in matches] == ["trace-codex"]
    assert count_large[0] == small_opens == 0


def test_resolve_facet_candidates_red_capability_naive_scan_would_explode(
    tmp_path: Path, monkeypatch
) -> None:
    """Prove the counter WOULD catch a regression to the #87 anti-pattern:
    monkeypatch a deliberately naive resolver that opens every trace's
    ``current.json`` to filter (instead of reading the persisted manifest),
    watch the open count explode to corpus size, then confirm the real
    implementation is restored and stays bounded."""

    from opentraces.core import dataset_facets
    from opentraces.core.bucket_trace_records import iter_trace_record_objects

    _seed_world(tmp_path)  # 4 traces

    real_resolve = dataset_facets.resolve_facet_candidates

    def _naive_full_scan_resolve(facets, *, project_slug=None, trace_id=None):
        # The anti-pattern: open EVERY trace's per-trace object to check its
        # facets, instead of reading the persisted manifest once.
        dataset_facets.validate_facet_names(facets)
        matches = []
        for obj in iter_trace_record_objects(project_slug=project_slug):
            record = obj.record
            row = {
                "agent_name": record.agent.name,
                "agent_version": record.agent.version,
                "agent_model": record.agent.model,
            }
            if dataset_facets._row_matches_facets(row, facets):
                matches.append({"project_slug": obj.project_slug, "trace_id": obj.trace_id})
        return matches

    # The naive path reads via ``iter_trace_record_objects`` — the OTHER
    # per-trace store (``trace_records/v1/current.json``), not the one our
    # seed helper populates (``traces/v1/trace.json``). To make the
    # red-capability demonstration land on THIS corpus, seed that store too
    # via the real capture write path, then prove the naive resolver's open
    # count scales with it while the real resolver's does not.
    from opentraces.core.bucket_trace_records import write_trace_record

    for trace_id in (
        "trace-claude-v1",
        "trace-claude-v2",
        "trace-codex",
        "trace-hermes-no-version",
    ):
        write_trace_record(
            _trace(
                trace_id,
                agent_name="codex-cli" if trace_id == "trace-codex" else "other",
                agent_version="0.31.0",
                agent_model="openai/gpt-5-codex",
            ),
            project_slug="demo",
            source_layer="canonical",
        )

    monkeypatch.setattr(dataset_facets, "resolve_facet_candidates", _naive_full_scan_resolve)
    count_naive = _count_per_trace_file_reads(monkeypatch)
    naive_matches = dataset_facets.resolve_facet_candidates({"agent.name": "codex-cli"})
    assert len(naive_matches) == 1
    assert count_naive[0] == 4, (
        "expected the naive full-scan resolver to open every trace's "
        "current.json (red-capability proof); the counter would catch this "
        "regression"
    )

    # Restore the real implementation and confirm it stays bounded on the
    # SAME corpus that just made the naive path explode.
    monkeypatch.setattr(dataset_facets, "resolve_facet_candidates", real_resolve)
    count_real = _count_per_trace_file_reads(monkeypatch)
    real_matches = dataset_facets.resolve_facet_candidates({"agent.name": "codex-cli"})
    assert [row["trace_id"] for row in real_matches] == ["trace-codex"]
    assert count_real[0] == 0


# ---------------------------------------------------------------------------
# External review of issue #212 (finding 2): the resolver must NEVER recompute
# the manifest as a fallback (the #87 anti-pattern), not just avoid it on the
# happy path. An absent/oversized/unreadable persisted manifest must fail
# LOUDLY with a clear maintenance error and open ZERO per-trace files.
# ---------------------------------------------------------------------------


def test_resolve_facet_candidates_absent_manifest_raises_clear_error_and_opens_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """Traces exist in the object store (a fresh capture), but
    ``bucket/manifest.json`` was never persisted (no ``bucket_manifest(write=
    True)`` sweep yet) -- the exact world the pre-fix code silently degraded
    on by calling ``bucket_manifest(write=False)``, which hydrates every
    trace's ``current.json``. Resolution must instead fail with a clear,
    actionable error and never open a single per-trace file."""

    from opentraces.core.bucket_store import project_per_trace_exports
    from opentraces.core.dataset_facets import (
        FacetResolutionUnavailableError,
        resolve_facet_candidates,
    )

    project_per_trace_exports(
        tmp_path,
        project_slug="demo",
        trace_id="trace-claude-v1",
        record=_trace(
            "trace-claude-v1",
            agent_name="claude-code",
            agent_version="2.1.143",
            agent_model="anthropic/claude-sonnet-4",
        ),
    )
    # Deliberately NOT calling bucket_manifest(write=True) -- manifest.json
    # does not exist yet.

    count = _count_per_trace_file_reads(monkeypatch)
    with pytest.raises(FacetResolutionUnavailableError, match="persisted bucket manifest"):
        resolve_facet_candidates({"model": "anthropic/claude-sonnet-4"})
    assert count[0] == 0, (
        "resolve_facet_candidates fell back to a per-trace scan on an absent "
        "manifest instead of failing loudly (the #87 anti-pattern this fix "
        "closes)"
    )


def test_resolve_facet_candidates_unreadable_manifest_raises_clear_error_and_opens_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """Same contract for a corrupt/unreadable persisted manifest (not just an
    absent one) -- both are ``read_persisted_manifest_capped`` states
    (``"error"`` / ``"too_large"``) that must never trigger a full recompute."""

    from opentraces.core.bucket_store import bucket_manifest, bucket_manifest_path
    from opentraces.core.dataset_facets import (
        FacetResolutionUnavailableError,
        resolve_facet_candidates,
    )

    _seed_world(tmp_path)  # persists a valid manifest.json first
    # Corrupt it in place -- simulates an unreadable/malformed persisted file.
    bucket_manifest_path().write_text("not valid json {{{", encoding="utf-8")

    count = _count_per_trace_file_reads(monkeypatch)
    with pytest.raises(FacetResolutionUnavailableError, match="persisted bucket manifest"):
        resolve_facet_candidates({"model": "anthropic/claude-sonnet-4"})
    assert count[0] == 0
