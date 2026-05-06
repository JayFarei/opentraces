"""Acceptance coverage for projection-backed trace query.

The Plan-59 parity suite proves Trace Index filters against a JSONL crawl.
This module adds the local-bucket contract: the immutable search projection
must answer the same trace-id set for the supported exact filters, and the
semantic query path must find traces from dependency/service evidence.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.search_projection import (
    query_search_projection_page,
    search_projection_status,
)

from tests.integration._trace_query_corpus import (
    fixture_project_slug,
    write_corpus,
)
from tests.integration._trace_query_oracle import (
    ensure_search_projection_built,
    load_oracle,
    run_indexed_query,
    run_projected_query,
)


PROJECTION_FILTER_CASES: list[tuple[str, list[str]]] = [
    ("lex:parser", ["--lex", "parser"]),
    ("skill:grill-me", ["--skill", "grill-me"]),
    ("tool:Bash", ["--tool", "Bash"]),
    ("files:src-py", ["--files", "src/*.py"]),
    ("file-kind:py", ["--file-kind", "py"]),
    ("file-op:edit", ["--file-op", "edit"]),
    ("provider:openai", ["--provider", "openai"]),
    ("cmd-family:pytest", ["--cmd-family", "pytest"]),
    ("bash-action:test", ["--bash-action", "test"]),
    ("test:pytest", ["--test", "pytest"]),
    ("service:hub.huggingface.co", ["--service", "hub.huggingface.co"]),
    ("service-channel:https", ["--service-channel", "https"]),
    ("dependency:pytest", ["--dependency", "pytest"]),
    ("git-tier:overlapping", ["--git-tier", "overlapping"]),
    ("survival:lost", ["--survival", "lost"]),
    ("success:true", ["--success"]),
    ("committed:true", ["--committed"]),
    ("source-layer:staging", ["--facet", "source_layer=staging"]),
    ("candidate-kind:bug_fix", ["--candidate-kind", "bug_fix"]),
    (
        "signal:tested_successful_fix_candidate",
        ["--signal", "tested_successful_fix_candidate"],
    ),
    ("metadata:session", ["--metadata", "session_id=parity-distinct-1"]),
    ("project:canonical", ["--project", fixture_project_slug()]),
]


@pytest.fixture
def projection_corpus() -> dict[str, dict]:
    write_corpus()
    ensure_search_projection_built()
    return load_oracle(force=True)


@pytest.mark.parametrize(
    "name,args",
    PROJECTION_FILTER_CASES,
    ids=[case[0] for case in PROJECTION_FILTER_CASES],
)
def test_search_projection_matches_index_for_supported_filters(
    projection_corpus: dict[str, dict],
    name: str,
    args: list[str],
) -> None:
    index_ids, _, _ = run_indexed_query(args)
    projection_ids, _, _ = run_projected_query(args)

    assert index_ids, f"{name} did not exercise the index"
    assert projection_ids == index_ids


def test_search_projection_paginates_without_losing_candidates(
    projection_corpus: dict[str, dict],
) -> None:
    full = query_search_projection_page(lex="parser", limit=100)
    assert full.total > 1

    first = query_search_projection_page(lex="parser", limit=1)
    assert first.next_page_token is not None
    rest = query_search_projection_page(
        lex="parser",
        limit=100,
        page_token=first.next_page_token,
    )

    full_ids = [candidate.trace_id for candidate in full.candidates]
    paged_ids = [first.candidates[0].trace_id] + [
        candidate.trace_id for candidate in rest.candidates
    ]
    assert paged_ids == full_ids


def test_semantic_query_finds_service_from_dependency_evidence(
    projection_corpus: dict[str, dict],
) -> None:
    raw = projection_corpus["p59-c-022-mongo"]
    prompt_material = json.dumps(
        {
            "task": raw.get("task"),
            "steps": raw.get("steps"),
        },
        sort_keys=True,
    ).lower()
    assert "mongodb" not in prompt_material

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "query", "--semantic", "mongodb", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == "projection"
    assert payload["semantic_query"]["concept_ids"] == ["service:mongodb"]
    status = search_projection_status()
    assert status["state"] == "ok"
    assert status["manifest"]["capabilities"]["semantic_ready"] is True

    candidates = {candidate["trace_id"]: candidate for candidate in payload["candidates"]}
    assert "p59-c-022-mongo" in candidates
    candidate = candidates["p59-c-022-mongo"]
    assert candidate["score_parts"]["projection_semantic"] > 0
    assert candidate["matched_fields"]["semantic"] == ["mongodb"]
