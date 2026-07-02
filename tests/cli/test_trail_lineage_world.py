"""Coverage for the #165 trail two-timeline surface.

The bare-noun ``trail <trace>`` (lineage overview) and ``trail <trace>:<step>``
(world at a step) reads, the ``trail track`` survival schema_version, and the
lifted ``trail pr`` verb. These are the net-new session-timeline half of the
trail family — the mirror of ctx's addressing.

Two hard contracts are asserted directly here (mirroring #165 V2 / V3):

* The lineage card is **survival-free** — no key anywhere in the payload
  contains ``survival``, and the card does not scale with step count.
* The world read leads with the outcome (the per-step hunk) plus a stable
  ``ot://`` world reference; raw ``tree_id`` pointers appear only under
  ``--full``.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.config import _write_marker
from opentraces.core.trails import append_exact_patch_trail, parse_trail_ref


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    _write_marker(repo, uuid.uuid4().hex, {})
    (repo / "app.py").write_text("def value():\n    return 'seed'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _seed_patch(repo: Path, trace_id: str, step: int, body: str) -> str:
    """Commit ``body`` and record an exact anchored patch at ``step``."""
    (repo / "app.py").write_text("def value():\n" + body)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"step-{step}"], cwd=repo, check=True)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    append_exact_patch_trail(
        repo,
        trace_id=trace_id,
        step_index=step,
        file_path="app.py",
        authored_text=body,
        commit_ref=sha,
        writer="test-fixture",
        capture_method=["hook_posttooluse"],
    )
    return sha


def _run(repo: Path, args: list[str]):
    return CliRunner().invoke(main, args + ["--project", str(repo)])


def _iter_keys(node: Any):
    """Recursively yield every dict key in a nested JSON structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_keys(item)


# --------------------------------------------------------------------------- #
# bare lineage — the survival-free card (V2)
# --------------------------------------------------------------------------- #
def test_bare_lineage_card_envelope_and_survival_free(tmp_path):
    _init_repo(tmp_path)
    _seed_patch(tmp_path, "tr-lin", 1, "    return 'one'\n")
    _seed_patch(tmp_path, "tr-lin", 2, "    return 'two'\n")

    res = _run(tmp_path, ["trail", "tr-lin", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)

    assert payload["status"] == "ok"
    assert payload["schema_version"] == "opentraces.trail.lineage.v1"
    assert payload["trace_id"] == "tr-lin"
    assert payload["patch_count"] == 2
    assert payload["files"] == [{"path": "app.py", "patches": 2}]
    # position axis is the CHEAP git-position signal (reachability), not survival.
    assert set(payload["position"]) == {"merged", "committed_unmerged", "unanchored"}
    assert payload["position"]["merged"] == 2
    assert set(payload["freshness"]) >= {"anchored", "understates"}
    assert payload["next_steps"], "the card must end in runnable next moves"

    # The positive contract: NO key anywhere contains 'survival'. A substring
    # scan of the raw JSON is the anti-gaming oracle (a surv_state rename fails).
    assert "survival" not in res.output.lower()
    assert not [k for k in _iter_keys(payload) if "survival" in k.lower()]


def test_bare_lineage_card_does_not_scale_with_step_count(tmp_path):
    """The card is a bounded summary: its key set is identical whether the
    trace has 2 patches or many. (Size oracle, #165 V2.)"""
    _init_repo(tmp_path)
    _seed_patch(tmp_path, "tr-small", 1, "    return 'x'\n")
    small = json.loads(_run(tmp_path, ["trail", "tr-small", "--json"]).output)

    big_repo = tmp_path / "big"
    big_repo.mkdir()
    _init_repo(big_repo)
    for step in range(1, 12):
        _seed_patch(big_repo, "tr-big", step, f"    return 'v{step}'\n")
    big = json.loads(_run(big_repo, ["trail", "tr-big", "--json"]).output)

    # Same top-level key set regardless of step count; files list is capped.
    assert set(small) == set(big)
    assert len(big["files"]) <= 8
    assert big["patch_count"] == 11


def test_bare_lineage_unknown_trace_errors_cleanly(tmp_path):
    _init_repo(tmp_path)
    res = _run(tmp_path, ["trail", "does-not-exist", "--json"])
    assert res.exit_code == 6, res.output
    payload = json.loads(res.output)
    assert payload["status"] == "error"
    assert payload["schema_version"] == "opentraces.trail.lineage.v1"
    assert payload["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# world read — outcome + hunk (V3)
# --------------------------------------------------------------------------- #
def test_world_read_carries_hunk_and_gates_tree_id(tmp_path):
    _init_repo(tmp_path)
    _seed_patch(tmp_path, "tr-w", 1, "    return 'one'\n")
    _seed_patch(tmp_path, "tr-w", 2, "    return 'two'\n")

    res = _run(tmp_path, ["trail", "tr-w:2", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)

    assert payload["schema_version"] == "opentraces.trail.world.v1"
    assert payload["step_index"] == 2
    assert re.fullmatch(r"ot://trace/.+/steps/2/world", payload["world_ref"])
    delta = payload["delta"]
    assert delta and delta["hunk"], "the world read must lead with the hunk"
    assert any(f["path"] == "app.py" for f in delta["files"])
    # Raw substrate is hidden by default, surfaced under --full (like ctx's
    # content_hash).
    assert "tree_id" not in payload

    full = json.loads(_run(tmp_path, ["trail", "tr-w:2", "--json", "--full"]).output)
    assert "tree_id" in full

    # world_ref re-derives byte-identically across runs.
    again = json.loads(_run(tmp_path, ["trail", "tr-w:2", "--json"]).output)
    assert again["world_ref"] == payload["world_ref"]


def test_world_read_missing_step_degrades_honestly(tmp_path):
    _init_repo(tmp_path)
    _seed_patch(tmp_path, "tr-w", 2, "    return 'two'\n")

    res = _run(tmp_path, ["trail", "tr-w:99", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["schema_version"] == "opentraces.trail.world.v1"
    assert payload["delta"] is None
    assert any("no_snapshot" in lim for lim in payload["limitations"])


# --------------------------------------------------------------------------- #
# reserved address forms — honest deferral (span / origin)
# --------------------------------------------------------------------------- #
def test_reserved_address_forms_refuse_cleanly(tmp_path):
    _init_repo(tmp_path)
    _seed_patch(tmp_path, "tr-r", 1, "    return 'one'\n")
    # ``:last`` is resolvable on trace get / ctx but deferred on trail — it
    # must degrade honestly (rc=2) instead of silently falling through to the
    # bare-trace lineage card (F1 required fix).
    for addr in ("tr-r:1-1", "tr-r:origin", "tr-r:last"):
        res = _run(tmp_path, ["trail", addr, "--json"])
        assert res.exit_code == 2, (addr, res.output)
        payload = json.loads(res.output)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "UNSUPPORTED_ADDRESS"


def test_parse_trail_ref_shapes():
    assert parse_trail_ref("abc") == ("abc", None, None, None)
    assert parse_trail_ref("abc:5") == ("abc", 5, None, None)
    assert parse_trail_ref("abc:3-7") == ("abc", None, (3, 7), "span")
    assert parse_trail_ref("abc:origin") == ("abc", None, None, "origin")
    assert parse_trail_ref("abc:nope") == ("abc", None, None, "invalid")
    # F1: the one grammar delta — ``:last`` moves from "invalid" to its own
    # reserved slot. Every other tuple above is pinned byte-identical.
    assert parse_trail_ref("abc:last") == ("abc", None, None, "last")


# --------------------------------------------------------------------------- #
# track survival scope — now self-describing (#165 item 4)
# --------------------------------------------------------------------------- #
def test_track_survival_scope_has_schema_version(tmp_path):
    _init_repo(tmp_path)
    sha = _seed_patch(tmp_path, "tr-t", 2, "    return 'two'\n")
    focus = json.loads(
        _run(tmp_path, ["trail", "track", "tr-t", "--step", "2", "--json"]).output
    )
    patch_id = focus["trace_patch_id"]
    anchor_id = focus["git_anchor_id"]

    patch = json.loads(
        _run(tmp_path, ["trail", "track", "--patch", patch_id, "--json"]).output
    )
    anchor = json.loads(
        _run(tmp_path, ["trail", "track", "--anchor", anchor_id, "--json"]).output
    )
    assert patch["schema_version"] == "opentraces.trail.track.v1"
    assert anchor["schema_version"] == "opentraces.trail.track.v1"
    # Additive only — the survival payload's own keys are unchanged.
    assert "current_survival" in patch or "relation" in patch


# --------------------------------------------------------------------------- #
# surface — hidden != removed, pr lifted, blame direct (V1 / V10)
# --------------------------------------------------------------------------- #
def test_trail_pr_is_top_level_and_blame_pr_stays_callable():
    runner = CliRunner()
    top = runner.invoke(main, ["trail", "pr", "--help"])
    assert top.exit_code == 0, top.output
    compat = runner.invoke(main, ["trail", "blame", "pr", "--help"])
    assert compat.exit_code == 0, compat.output


def test_trail_pr_visible_blame_pr_hidden_in_help():
    out = CliRunner().invoke(main, ["trail", "--help"]).output
    assert re.search(r"^\s*pr\b", out, re.MULTILINE), out
    blame_help = CliRunner().invoke(main, ["trail", "blame", "--help"]).output
    # `commit` is the visible blame subcommand; `pr` is the hidden compat mount.
    assert "commit" in blame_help
    assert re.search(r"^\s*pr\b", blame_help, re.MULTILINE) is None, blame_help


def test_hidden_substrate_verbs_still_callable():
    runner = CliRunner()
    for verb in ("explain", "timeline", "resolve", "search", "diff"):
        res = runner.invoke(main, ["trail", verb, "--help"])
        assert res.exit_code == 0, (verb, res.output)


def test_trail_blame_sha_resolves_directly(tmp_path):
    _init_repo(tmp_path)
    sha = _seed_patch(tmp_path, "tr-b", 2, "    return 'two'\n")
    direct = _run(tmp_path, ["trail", "blame", sha[:8], "--json"])
    nested = _run(tmp_path, ["trail", "blame", "commit", sha[:8], "--json"])
    assert direct.exit_code == 0, direct.output
    assert nested.exit_code == 0, nested.output
    # Both address forms route to the same commit-mode attribution reader.
    assert json.loads(direct.output) == json.loads(nested.output)
