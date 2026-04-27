"""Trace Trails Phase 7 lineage consumers and search coverage."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.config import _write_marker
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    append_exact_patch_trail,
    build_trail_query_projection,
    reconcile_commit_anchors,
)
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    _write_marker(repo, uuid.uuid4().hex, {})
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _oid(repo: Path, rev_path: str) -> dict[str, str]:
    return {"algo": "sha1", "hex": _git(repo, "rev-parse", rev_path)}


def _run(repo: Path, args: list[str]):
    return CliRunner().invoke(main, args + ["--project", str(repo)])


def _run_json(repo: Path, args: list[str]) -> dict:
    result = _run(repo, args + ["--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _append_anchored_patch(
    repo: Path,
    *,
    trace_id: str = "tr-phase7",
    step_index: int = 3,
    file_path: str = "app.py",
    authored: str = "    return 'phase-seven-lineage-consumer'\n",
    message: str = "phase seven patch",
) -> tuple[str, dict]:
    (repo / file_path).parent.mkdir(parents=True, exist_ok=True)
    if file_path == "app.py":
        (repo / file_path).write_text("def value():\n" + authored)
    else:
        (repo / file_path).write_text("def value():\n" + authored)
    commit_sha = _commit(repo, message)
    append_exact_patch_trail(
        repo,
        trace_id=trace_id,
        step_index=step_index,
        file_path=file_path,
        authored_text=authored,
        commit_ref=commit_sha,
        writer="test-fixture",
        capture_method=["hook_posttooluse"],
    )
    explain = _run_json(repo, ["trail", "explain", "--commit", commit_sha])
    patch = explain["trace_patches"][0]
    patch["commit_sha"] = commit_sha
    return commit_sha, patch


def _assert_same_anchor(expected: dict, actual: dict) -> None:
    fields = (
        "trace_id",
        "step_id",
        "trace_patch_id",
        "git_anchor_id",
        "containing_segment_id",
        "commit_sha",
        "file_path",
    )
    for field in fields:
        assert actual.get(field) == expected.get(field), field
    assert actual["line_origin"]["path"] == expected["file_path"]
    assert actual["line_origin"]["line"] == expected["affected_range"]["start_line"]
    lineage_key = actual["lineage_key"]
    assert lineage_key["trace"]["id"] == expected["trace_id"]
    assert lineage_key["trace"]["kind"] == "trace"
    assert lineage_key["step"]["id"] == expected["step_id"]
    assert lineage_key["step"]["kind"] == "trace_step"
    assert lineage_key["trace_patch"]["id"] == expected["trace_patch_id"]
    assert lineage_key["trace_patch"]["kind"] == "trace_patch"
    assert lineage_key["trace_patch"]["ref"].startswith("ot://trace-patch/sha256/")
    assert lineage_key["git_anchor"]["id"] == expected["git_anchor_id"]
    assert lineage_key["git_anchor"]["kind"] == "git_anchor"
    assert lineage_key["git_anchor"]["ref"].startswith("ot://git-anchor/sha256/")
    assert lineage_key["commit"]["id"] == expected["commit_sha"]
    assert lineage_key["commit"]["kind"] == "git_commit"
    assert actual["origin"]["kind"] == "trace_step"
    assert actual["origin"]["step_id"] == expected["step_id"]
    assert actual["trace_patch"]["kind"] == "trace_patch"
    assert actual["trace_patch"]["trace_patch_id"] == expected["trace_patch_id"]
    assert actual["trace_patch"]["ref"]["id"] == expected["trace_patch_id"]
    assert actual["trace_patch"]["ranges"][0]["side"] == "trace_patch"
    assert actual["landing"]["kind"] == "git_anchor"
    assert actual["landing"]["relation"] == "landed_in_commit"
    assert actual["landing"]["git_anchor_id"] == expected["git_anchor_id"]
    assert actual["landing"]["ref"]["id"] == expected["git_anchor_id"]
    assert actual["landing"]["ranges"][0]["side"] == "landing"
    assert actual["evidence"]["tier"] == "exact_range_hash"
    assert actual["evidence"]["label"] == "exact range match"
    assert actual["evidence"]["firmness"] == "firm_observed"
    assert "committed_range_hash_matches_trace_patch_range_hash" in actual["evidence"][
        "reason_codes"
    ]
    assert actual["current_state"]["survival_state"] in {
        "alive_on_path",
        "reverted",
    }
    assert actual["current_state"]["observed_at_commit"]
    assert actual["line_origin_refs"][0]["resource_ref"].startswith("ot://file/")


def test_lineage_consumers_and_search_read_same_trail_projection(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    commit_sha, expected = _append_anchored_patch(tmp_path)

    trace_explain = _run_json(
        tmp_path,
        [
            "trail",
            "explain",
            "--trace",
            expected["trace_id"],
            "--step",
            str(expected["step_index"]),
        ],
    )
    assert trace_explain["trace_patch_id"] == expected["trace_patch_id"]
    resolve = _run_json(
        tmp_path,
        ["trail", "resolve", trace_explain["resource_refs"]["trace_patch_trail"]],
    )
    assert resolve["schema_version"] == "opentraces.trail.resolve.v1"
    assert resolve["trace_patch_id"] == expected["trace_patch_id"]
    assert resolve["lineage_key"]["trace_patch"]["id"] == expected["trace_patch_id"]
    assert resolve["current_state"]["survival_state"] == "alive_on_path"
    assert {node["kind"] for node in resolve["nodes"]} >= {
        "trace",
        "trace_step",
        "trace_patch",
        "git_anchor",
        "git_commit",
    }
    assert {
        edge["relation"] for edge in resolve["edges"]
    } >= {"contains_step", "created_trace_patch", "anchored_in_git", "landed_in_commit"}

    blame = _run_json(tmp_path, ["blame", commit_sha])
    assert blame["projection_limitations"] == ["attribution_cache_missing_trail_events_used"]
    assert len(blame["trailEvidence"]) == 1
    _assert_same_anchor(expected, blame["trailEvidence"][0])

    line_blame = _run_json(
        tmp_path,
        ["blame", f"{expected['file_path']}:{expected['affected_range']['start_line']}"],
    )
    _assert_same_anchor(expected, line_blame["trailEvidence"][0])

    search = _run_json(tmp_path, ["trail", "search", "--commit", commit_sha])
    assert search["query"]["type"] == "anchors_per_commit"
    assert len(search["results"]) == 1
    _assert_same_anchor(expected, search["results"][0])

    graph = _run(tmp_path, ["graph", "--limit", "1", "--no-color"])
    assert graph.exit_code == 0, graph.output
    assert expected["trace_id"][:8] in graph.output
    assert commit_sha[:7] in graph.output

    graph_json = _run_json(tmp_path, ["graph", "--limit", "1", "--no-color"])
    graph_edge = graph_json["commits"][0]["traces"][0]["trail_evidence"][0]
    _assert_same_anchor(expected, graph_edge)

    graph_trace = _run(
        tmp_path,
        ["graph", "--trace", expected["trace_id"], "--limit", "1", "--no-color"],
    )
    assert graph_trace.exit_code == 0, graph_trace.output
    assert commit_sha[:7] in graph_trace.output

    trace_blame = _run_json(tmp_path, ["blame", f"t:{expected['trace_id']}"])
    assert trace_blame["commits"][0]["sha"] == commit_sha
    assert trace_blame["commits"][0]["source"] == "trail_events"


def test_trail_search_finds_patches_for_trace(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit_sha, expected = _append_anchored_patch(tmp_path)

    payload = _run_json(tmp_path, ["trail", "search", "--trace", expected["trace_id"]])

    assert payload["schema_version"] == "opentraces.trail.search.v1"
    assert payload["projection_version"] == "v1"
    assert payload["projection"]["name"] == "trace_trail"
    assert payload["projection"]["high_watermark"]["events_seen"] >= 1
    assert payload["project"]["project_id"]
    assert "result" not in payload
    assert payload["query"]["type"] == "patches_per_trace"
    assert payload["query"]["semantic_type"] == "trace_to_patches"
    assert payload["results"][0]["trace_patch_id"] == expected["trace_patch_id"]
    assert payload["results"][0]["git_anchor_id"] == expected["git_anchor_id"]
    assert payload["results"][0]["commit_sha"] == expected["commit_sha"]


def test_trail_search_trace_human_output_uses_lineage_graph_language(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _commit_sha, expected = _append_anchored_patch(
        tmp_path,
        trace_id="tr-phase7-human",
    )

    result = _run(tmp_path, ["trail", "search", "--trace", expected["trace_id"]])

    assert result.exit_code == 0, result.output
    assert "Trace trail for t:tr-phase" in result.output
    assert "1 Trace Patch anchored in Git" in result.output
    assert "╭◆ t:tr-phase" in result.output
    assert "╰◇ tp:" in result.output
    assert "│ evidence: exact range match · firm" in result.output
    assert "╰● c:" in result.output
    assert "landed_exact" in result.output
    assert "alive_on_path" in result.output
    assert "exact range match" in result.output
    assert "Next:" in result.output


def test_trail_search_commit_human_output_resolves_ref_and_shows_trace_patch_node(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    commit_sha, _expected = _append_anchored_patch(tmp_path)

    result = _run(tmp_path, ["trail", "search", "--commit", "HEAD"])

    assert result.exit_code == 0, result.output
    assert "Trace trail evidence in HEAD" in result.output
    assert f"Resolved HEAD -> c:{commit_sha[:8]}" in result.output
    assert "1 anchored Trace Patch · 1 trace · 1 file" in result.output
    assert "● c:" in result.output
    assert "╰◇ tp:" in result.output
    assert "╰◆ t:" in result.output
    assert "exact range match · firm" in result.output


def test_trail_search_table_mode_is_compact_for_many_results(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_anchored_patch(tmp_path, trace_id="tr-phase7-human", file_path="a.py")
    _append_anchored_patch(
        tmp_path,
        trace_id="tr-phase7-human",
        file_path="b.py",
        authored="    return 'second phase seven patch'\n",
        message="second phase seven patch",
    )

    result = _run(tmp_path, ["trail", "search", "--trace", "tr-phase7-human", "--table"])

    assert result.exit_code == 0, result.output
    assert "2 Trace Patches" in result.output
    assert "TRACE PATCH  STEP   FILE" in result.output
    assert result.output.count("tp:") == 2


def test_trail_search_defaults_to_graph_for_multiple_trace_patches(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _append_anchored_patch(tmp_path, trace_id="tr-phase7-human", file_path="a.py")
    _append_anchored_patch(
        tmp_path,
        trace_id="tr-phase7-human",
        file_path="b.py",
        authored="    return 'second phase seven patch'\n",
        message="second phase seven patch",
    )

    result = _run(tmp_path, ["trail", "search", "--trace", "tr-phase7-human"])

    assert result.exit_code == 0, result.output
    assert "2 Trace Patches anchored in Git" in result.output
    assert "├◇ tp:" in result.output
    assert "╰◇ tp:" in result.output
    assert result.output.count("evidence: exact range match · firm") == 2
    assert result.output.count("╰● c:") == 2


def test_trail_search_empty_state_teaches_instead_of_plain_zero(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)

    result = _run(tmp_path, ["trail", "search", "--trace", "missing-trace"])

    assert result.exit_code == 0, result.output
    assert "No committed Trace Patches found." in result.output
    assert "Possible reasons:" in result.output
    assert "TrailEvents are unavailable for this project" in result.output


def test_trail_search_finds_anchors_for_commit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    commit_sha, expected = _append_anchored_patch(tmp_path)

    payload = _run_json(tmp_path, ["trail", "search", "--commit", "HEAD"])

    assert payload["query"]["commit_sha"] == commit_sha
    assert payload["results"][0]["git_anchor_id"] == expected["git_anchor_id"]


def test_trail_search_finds_patches_touching_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit_sha, expected = _append_anchored_patch(tmp_path, file_path="src/search_demo.py")

    payload = _run_json(tmp_path, ["trail", "search", "--path", "src/search_demo.py"])

    assert payload["query"]["type"] == "patches_touching_file"
    assert payload["query"]["semantic_type"] == "path_to_patches"
    assert payload["query"]["path"] == "src/search_demo.py"
    assert payload["results"][0]["trace_patch_id"] == expected["trace_patch_id"]


def test_trail_search_finds_reverted_patch_trails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    commit_sha, expected = _append_anchored_patch(
        tmp_path,
        trace_id="tr-reverted-phase7",
        authored="    return 'phase-seven-reverted'\n",
    )
    subprocess.run(["git", "revert", "--no-edit", commit_sha], cwd=tmp_path, check=True)

    payload = _run_json(tmp_path, ["trail", "search", "--survival", "reverted"])

    assert payload["query"]["type"] == "patch_trails_by_survival"
    assert payload["query"]["semantic_type"] == "survival_to_patch_trails"
    assert payload["query"]["survival"] == "reverted"
    assert payload["results"][0]["trace_patch_id"] == expected["trace_patch_id"]
    assert payload["results"][0]["current_survival"]["survival_state"] == "reverted"

    blame = _run_json(tmp_path, ["blame", commit_sha])
    assert blame["trailEvidence"][0]["current_survival"]["survival_state"] == "reverted"

    graph = _run_json(
        tmp_path,
        ["graph", "--trace", "tr-reverted-phase7", "--all", "--no-color"],
    )
    evidence = graph["commits"][0]["traces"][0]["trail_evidence"][0]
    assert evidence["current_survival"]["survival_state"] == "reverted"


def test_trail_index_rebuild_is_deterministic(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    commit_sha, expected = _append_anchored_patch(tmp_path)

    first = build_trail_query_projection(tmp_path)
    second = build_trail_query_projection(tmp_path)

    assert first.to_summary() == second.to_summary()
    assert first.patches_for_trace(expected["trace_id"]) == second.patches_for_trace(
        expected["trace_id"]
    )
    assert first.anchors_for_commit(commit_sha) == second.anchors_for_commit(commit_sha)


def test_partial_commit_unknown_patch_not_promoted_to_blame_graph_or_search(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    commit_sha, expected = _append_anchored_patch(
        tmp_path,
        trace_id="knowntracephase7",
        authored="    return 'known phase seven patch'\n",
    )
    unknown_authored = "print('unknown phase seven patch')\n"
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="unknowntracephase7",
                step_index=4,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": sha256_text("phase7-unknown").split(":", 1)[1],
                    "file_path": "uncommitted.py",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": unknown_authored,
                    "raw_authored_hash": sha256_text(unknown_authored),
                    "git_clean_hash": sha256_text(" ".join(unknown_authored.split())),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )

    blame = _run_json(tmp_path, ["blame", commit_sha])
    assert [row["trace_patch_id"] for row in blame["trailEvidence"]] == [
        expected["trace_patch_id"]
    ]

    search = _run_json(tmp_path, ["trail", "search", "--commit", commit_sha])
    assert [row["trace_patch_id"] for row in search["results"]] == [
        expected["trace_patch_id"]
    ]

    graph = _run(tmp_path, ["graph", "--limit", "1", "--no-color"])
    assert graph.exit_code == 0, graph.output
    assert "knowntr" in graph.output
    assert "unknown" not in graph.output

    unknown = _run_json(tmp_path, ["trail", "search", "--trace", "unknowntracephase7"])
    assert unknown["results"][0]["relation"] == "unknown"
    assert "git_anchor_unknown" in unknown["results"][0]["limitations"]


def test_trail_query_normalizes_legacy_prefixed_patch_id_with_new_anchor(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    seed_sha = _git(tmp_path, "rev-parse", "HEAD")
    patch_id = sha256_text("legacy-query-patch").split(":", 1)[1]
    authored = "    return 'legacy query anchor bridge'\n"
    append_event_batch(
        tmp_path,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id="legacyqueryphase7",
                step_index=7,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "before_blob_id": _oid(tmp_path, f"{seed_sha}:app.py"),
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )
    (tmp_path / "app.py").write_text("def value():\n" + authored)
    commit_sha = _commit(tmp_path, "legacy query patch")
    created = reconcile_commit_anchors(
        tmp_path,
        commit_sha,
        writer="post-commit-correlator",
    )
    assert [anchor["trace_patch_id"] for anchor in created] == [patch_id]

    search = _run_json(tmp_path, ["trail", "search", "--trace", "legacyqueryphase7"])

    assert search["results"][0]["trace_patch_id"] == patch_id
    assert search["results"][0]["git_anchor_id"] == created[0]["git_anchor_id"]
    assert search["results"][0]["relation"] == "anchored_in_git"
    assert "git_anchor_unknown" not in search["results"][0]["limitations"]
    assert search["results"][0]["lineage_key"]["trace_patch"]["id"] == patch_id

    blame = _run_json(tmp_path, ["blame", commit_sha])
    assert blame["trailEvidence"][0]["trace_patch_id"] == patch_id
    assert blame["trailEvidence"][0]["git_anchor_id"] == created[0]["git_anchor_id"]


def test_legacy_notes_only_project_still_renders_blame_without_trail_events(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "legacy.py").write_text("x = 1\n")
    commit_sha = _commit(tmp_path, "legacy notes only")
    from opentraces.enrichment.git import notes_store

    notes_store.append(
        commit_sha,
        [notes_store.format_link("legacytracephase7")],
        tmp_path,
    )

    blame = _run_json(tmp_path, ["blame", commit_sha])

    assert blame["trailEvidence"] == []
    assert blame["hookLinked"][0]["trace_id"] == "legacytracephase7"
