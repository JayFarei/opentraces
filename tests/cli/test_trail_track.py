"""Coverage for the new ``ot trail track`` umbrella verb.

``track`` collapses ``timeline``, ``sync``, and ``explain --trace --step``
into a single multi-scope command. The old verbs remain callable as
hidden aliases — see ``test_old_verbs_still_callable_as_hidden_aliases``.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.config import _write_marker
from opentraces.core.trails import append_exact_patch_trail


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    _write_marker(repo, uuid.uuid4().hex, {})
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _seed_anchored_patch(repo: Path) -> dict[str, str]:
    """Create a single anchored Trace Patch and return its identifiers."""
    authored = "    return 'tracked'\n"
    (repo / "app.py").write_text("def value():\n" + authored)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "track-fixture"], cwd=repo, check=True)
    commit_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    append_exact_patch_trail(
        repo,
        trace_id="tr-track",
        step_index=2,
        file_path="app.py",
        authored_text=authored,
        commit_ref=commit_sha,
        writer="test-fixture",
        capture_method=["hook_posttooluse"],
    )
    runner = CliRunner()
    explain = runner.invoke(
        main,
        ["trail", "track", "tr-track", "--step", "2", "--json", "--project", str(repo)],
    )
    assert explain.exit_code == 0, explain.output
    payload = json.loads(explain.output)
    return {
        "trace_id": "tr-track",
        "patch_id": payload["trace_patch_id"],
        "anchor_id": payload["git_anchor_id"],
        "commit_sha": commit_sha,
    }


def _run(repo: Path, args: list[str]):
    return CliRunner().invoke(main, args + ["--project", str(repo)])


def test_track_full_trace_renders_rail_diagram(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)

    result = _run(tmp_path, ["trail", "track", handles["trace_id"]])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Trace timeline for t:tr-track" in out
    assert "step:s2" in out
    # Snapshot/patch glyphs from the rail rendering
    assert "◇" in out or "tp:" in out


def test_track_full_trace_json_returns_timeline_payload(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)

    result = _run(tmp_path, ["trail", "track", handles["trace_id"], "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["trace_id"] == handles["trace_id"]
    assert "timeline" in payload
    assert any(
        item.get("event_type") == "trace_patch_created" for item in payload["timeline"]
    )


def test_track_step_renders_explain_output(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)

    result = _run(tmp_path, ["trail", "track", handles["trace_id"], "--step", "2"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Trace tr-track step_2" in out
    assert "anchored in git:" in out
    assert "evidence: exact_range_hash" in out


def test_track_patch_renders_sync_summary(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)

    result = _run(tmp_path, ["trail", "track", "--patch", handles["patch_id"]])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Trail track" in out
    assert "survival:" in out
    # Patch handle uses tp: prefix
    assert "tp:" in out


def test_track_anchor_renders_sync_summary(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)

    result = _run(tmp_path, ["trail", "track", "--anchor", handles["anchor_id"]])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Trail track" in out
    assert "ga:" in out


def test_track_silent_suppresses_output_but_succeeds(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)

    result = _run(tmp_path, ["trail", "track", handles["trace_id"], "--silent"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_track_rejects_no_selector(tmp_path):
    _init_repo(tmp_path)
    result = _run(tmp_path, ["trail", "track"])
    assert result.exit_code == 2
    assert "Provide TRACE_ID, --patch, or --anchor" in result.output


def test_track_rejects_multiple_selectors(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)
    result = _run(
        tmp_path,
        [
            "trail",
            "track",
            handles["trace_id"],
            "--patch",
            handles["patch_id"],
        ],
    )
    assert result.exit_code == 2
    assert "Provide only one of TRACE_ID, --patch, or --anchor" in result.output


def test_track_step_without_trace_id_errors(tmp_path):
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)
    result = _run(tmp_path, ["trail", "track", "--patch", handles["patch_id"], "--step", "2"])
    assert result.exit_code == 2
    assert "--step requires a TRACE_ID" in result.output


def test_old_verbs_still_callable_as_hidden_aliases(tmp_path):
    """``timeline``, ``explain``, ``sync``, ``search`` keep working —
    they're hidden in --help but still dispatch to the same behavior so
    existing scripts and tests don't break."""
    _init_repo(tmp_path)
    handles = _seed_anchored_patch(tmp_path)

    timeline = _run(tmp_path, ["trail", "timeline", handles["trace_id"]])
    assert timeline.exit_code == 0, timeline.output

    explain = _run(
        tmp_path,
        ["trail", "explain", "--trace", handles["trace_id"], "--step", "2"],
    )
    assert explain.exit_code == 0, explain.output

    sync = _run(tmp_path, ["trail", "sync", "--patch", handles["patch_id"]])
    assert sync.exit_code == 0, sync.output

    search = _run(tmp_path, ["trail", "search", "--trace", handles["trace_id"]])
    assert search.exit_code == 0, search.output


def test_help_listing_hides_old_verbs(tmp_path):
    """`ot trail --help` should list the 4 visible verbs only:
    track, blame, graph, teleport."""
    runner = CliRunner()
    result = runner.invoke(main, ["trail", "--help"], color=False)
    assert result.exit_code == 0, result.output
    # Visible verbs
    for verb in ("track", "blame", "graph", "teleport"):
        assert f"  {verb}" in result.output, f"missing verb: {verb}"
    # Hidden verbs must not appear in the listing
    listing_block = result.output.split("Commands:", 1)[-1]
    for verb in ("timeline", "explain", "sync", "search"):
        # row pattern: leading spaces then verb name as a word
        # avoid matching "explain" as substring of other help text by anchoring
        bad_prefix = f"  {verb} "
        bad_only = f"  {verb}\n"
        assert bad_prefix not in listing_block and bad_only not in listing_block, (
            f"hidden verb {verb!r} leaked into help: {listing_block!r}"
        )
