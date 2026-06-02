"""Hermetic unit tests for the trace-capsule subsystem (plan 082, v1).

Covers the frozen contract, the redaction gate + manifest-leak guard (the
eng-critical sentinel test), the renderer, URL minting, and the open round-trip.
Export-from-bucket is exercised in ``test_capsule_export_integration.py``
(skip-guarded on local bucket data).
"""

from __future__ import annotations

import json

import pytest

from opentraces.core.capsule.contract import (
    CAPSULE_SCHEMA_VERSION,
    CapsuleSchemaAheadError,
    build_capsule_id,
    freeze_capsule,
    validate_capsule,
)
from opentraces.core.capsule.redaction import (
    REDACTION_FLOOR,
    RedactionGateError,
    assert_redaction_gate,
    redact_envelope,
)
from opentraces.core.capsule.render import render_capsule_markdown, render_issue_body
from opentraces.core.capsule.share import (
    human_capsule_url,
    load_capsule_file,
    mint_capsule_url,
    write_capsule_dir,
)


# A 48-char hex token — high entropy, reliably caught by the entropy detector.
SENTINEL_SECRET = "deadbeefcafef00d0123456789abcdef0011223344556677"


def _make_capsule(**overrides):
    base = dict(
        capsule_id="abc123def456abcd",
        source={"project_slug": "p", "trace_id": "t", "step_index": 3, "agent": "codex-cli"},
        summary={"title": "fix the parser", "what_happened": "hit a TypeError", "failure": "TypeError: x", "is_failure": True, "scope": "5 steps"},
        test=None,
        environment={"dependencies": [], "language_ecosystem": "python", "setup": []},
        bundle=None,
        intent={"headline": "agent tried to fix the parser", "most_substantive_spec": None, "trigger": None},
        failing_step={"index": 3, "type": "agent", "error_excerpt": "Traceback ...", "had_error_marker": True},
        slice_payload={"slice_id": "s", "trace_id": "t", "steps": [], "limitations": []},
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": "n", "system_layer": {"content": {"x": 1}}},
        trail_anchors=[],
        repo_pin={"remote_url": "https://github.com/o/r", "commit_sha": "abc", "changed_files": ["a.py"]},
        redaction={"manifest": {"floor": list(REDACTION_FLOOR), "floor_satisfied": True, "redactions_applied": 0}},
        render_state={"redaction": "redacted_ok", "closure": "closure_full", "replay": "replay_unverified"},
        limitations=[],
        created_with="opentraces test",
    )
    base.update(overrides)
    return freeze_capsule(**base)


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #


def test_capsule_id_is_deterministic():
    kw = dict(trace_id="t1", node_id="n1", start_step_index=2, end_step_index=8, repo_commit_sha="deadbeef")
    assert build_capsule_id(**kw) == build_capsule_id(**kw)
    other = build_capsule_id(**{**kw, "end_step_index": 9})
    assert build_capsule_id(**kw) != other


def test_freeze_sets_schema_version_and_untrusted_flag():
    cap = _make_capsule()
    assert cap["schema_version"] == CAPSULE_SCHEMA_VERSION
    assert cap["content_is_untrusted"] is True
    assert cap["embedded"]["context_resume"] == "opentraces.context_resume.v1"


def test_validate_roundtrips_a_good_capsule():
    cap = _make_capsule()
    assert validate_capsule(cap) is cap


def test_validate_rejects_missing_keys():
    cap = _make_capsule()
    del cap["repo_pin"]
    with pytest.raises(ValueError):
        validate_capsule(cap)


def test_validate_rejects_newer_schema_with_upgrade_hint():
    cap = _make_capsule()
    cap["schema_version"] = "opentraces.capsule.v2"
    with pytest.raises(CapsuleSchemaAheadError):
        validate_capsule(cap)


def test_validate_rejects_non_capsule():
    with pytest.raises(ValueError):
        validate_capsule({"schema_version": "something.else"})


# --------------------------------------------------------------------------- #
# Redaction gate + manifest-leak guard (eng-critical)
# --------------------------------------------------------------------------- #


def test_redaction_floor_runs_and_gate_passes():
    payload = {"a": "nothing secret here", "b": {"c": "also fine"}}
    _redacted, manifest = redact_envelope(payload)
    assert manifest["floor_satisfied"] is True
    assert set(REDACTION_FLOOR).issubset(set(manifest["tools_applied"]))
    assert_redaction_gate(manifest)  # must not raise


def test_secret_is_redacted_and_never_leaks_into_manifest():
    payload = {
        "context_resume_packet": {"messages": [{"role": "user", "text": f"token={SENTINEL_SECRET}"}]},
        "intent": {"headline": "ok"},
    }
    redacted, manifest = redact_envelope(payload)
    blob = json.dumps(redacted, ensure_ascii=False)
    manifest_blob = json.dumps(manifest, ensure_ascii=False)
    # 1. The secret is gone from the redacted envelope.
    assert SENTINEL_SECRET not in blob
    # 2. The manifest never carries the literal matched value, nor a matched_text key.
    assert SENTINEL_SECRET not in manifest_blob
    assert "matched_text" not in manifest_blob
    assert manifest["redactions_applied"] >= 1


def test_gate_blocks_when_floor_absent():
    fake_manifest = {"floor_satisfied": False, "tools_applied": []}
    with pytest.raises(RedactionGateError):
        assert_redaction_gate(fake_manifest)


def test_home_paths_are_scrubbed():
    payload = {"pin": {"path": "/Users/somebody/secret/repo/file.py"}}
    redacted, manifest = redact_envelope(payload)
    blob = json.dumps(redacted)
    assert "somebody" not in blob  # path-embedded username removed
    assert manifest["home_paths_scrubbed"] >= 1


def test_windows_path_username_is_scrubbed():
    payload = {"pin": {"path": r"C:\Users\Alice\AppData\app\creds.json"}}
    redacted, manifest = redact_envelope(payload)
    assert "Alice" not in json.dumps(redacted)
    assert manifest["home_paths_scrubbed"] >= 1


def test_bare_local_username_token_is_scrubbed():
    # The operator's real local username must not leak via git-author / whoami /
    # prose, even when it is not inside a path (the adversarial-verify finding).
    import getpass

    user = getpass.getuser()
    if len(user) < 3:
        pytest.skip("local username too short to word-boundary scrub safely")
    payload = {
        "intent": {"headline": f"agent run by {user}: build failed"},
        "ctx": {"steps": [{"content": f"$ whoami\n{user}\nERROR: permission denied for user {user}"}]},
    }
    redacted, manifest = redact_envelope(payload)
    blob = json.dumps(redacted)
    assert user not in blob
    assert manifest["home_paths_scrubbed"] >= 1


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #


def test_issue_body_has_marker_open_command_and_badge():
    cap = _make_capsule()
    url = "https://huggingface.co/datasets/o/r/resolve/abc/capsules/v1/abc123def456abcd/capsule.json"
    body = render_issue_body(cap, capsule_url=url)
    assert f"<!-- opentraces-capsule: {cap['capsule_id']} -->" in body
    assert f"opentraces capsule open {url} --json" in body
    assert "Redaction floor" in body
    assert "Not yet reproduced" in body  # replay_unverified state
    assert "untrusted" in body.lower()


def test_intent_only_closure_renders_a_note():
    cap = _make_capsule()
    cap["render_state"]["closure"] = "closure_intent_only"
    body = render_issue_body(cap, capsule_url=None)
    assert "context layer was unavailable" in body


def test_capsule_markdown_has_header_and_no_marker():
    cap = _make_capsule()
    md = render_capsule_markdown(cap)
    assert md.startswith("# Trace Capsule")
    assert "opentraces-capsule:" not in md  # marker is issue-only


# --------------------------------------------------------------------------- #
# Summary distiller (better-than-raw-TASK-dump)
# --------------------------------------------------------------------------- #


def test_distill_title_strips_scaffolding():
    from opentraces.core.capsule.summary import distill_title

    raw = (
        "$thermo-nuclear-review TASK: Design the simplest robust fix to make "
        '"trace spotlight" fast. EXPECTED OUTCOME: a design doc with sections.'
    )
    title = distill_title(raw)
    assert title.startswith("Design the simplest robust fix")
    assert "TASK:" not in title
    assert "EXPECTED OUTCOME" not in title
    assert "$thermo" not in title


def test_issue_h2_uses_distilled_summary_title_not_raw_dump():
    cap = _make_capsule(summary={
        "title": "fix the flaky parser", "what_happened": "hit a TypeError on empty input",
        "failure": "TypeError: NoneType", "is_failure": True, "scope": "5 steps",
    })
    body = render_issue_body(cap, capsule_url="https://hf.co/x")
    # is_failure=True + test=None (the _make_capsule default) → blocked-episode banner.
    assert "## 🧭 Agent Experience Report (blocked episode): fix the flaky parser" in body
    assert "**What happened:** hit a TypeError on empty input" in body


def test_replay_packet_carries_intent_context_and_oracle():
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _make_capsule()
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["schema_version"] == "opentraces.capsule_replay.v1"
    assert packet["capsule_id"] == cap["capsule_id"]
    assert packet["target_ref"] == "HEAD"
    assert packet["before_commit"] == cap["repo_pin"]["commit_sha"]
    assert packet["context_resume_packet"] == cap["context_resume_packet"]
    # is_failure=True + a real error → error_string_gone oracle
    assert packet["oracle"]["strategy"] == "error_string_gone"
    assert "fixed" in packet["oracle"]["verdict_values"]


def test_replay_oracle_is_intent_satisfied_for_non_failure():
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _make_capsule(summary={"title": "design X", "what_happened": "no error", "failure": None, "is_failure": False, "scope": "5 steps"})
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["oracle"]["strategy"] == "intent_satisfied"


def test_verdict_comment_is_marker_tagged():
    from opentraces.core.capsule.replay import render_verdict_comment

    body = render_verdict_comment(capsule_id="abc123", state="fixed", note="looks good", target_ref="HEAD", before_commit="deadbeef0000")
    assert "<!-- opentraces-capsule-verdict: abc123 state=fixed -->" in body
    assert "🟢" in body and "fixed" in body
    assert "looks good" in body


def test_parse_issue_ref_forms():
    from opentraces.core.capsule.share import parse_issue_ref

    assert parse_issue_ref("https://github.com/o/r/issues/8") == ("o/r", 8)
    assert parse_issue_ref("o/r#12") == ("o/r", 12)
    assert parse_issue_ref("5") == (None, 5)


def test_non_failure_summary_uses_session_label():
    cap = _make_capsule(summary={
        "title": "design a caching layer", "what_happened": "no explicit error captured",
        "failure": None, "is_failure": False, "scope": "10 steps",
    })
    body = render_issue_body(cap, capsule_url=None)
    assert "🧭 Agent Experience Report (usage episode): design a caching layer" in body


# --------------------------------------------------------------------------- #
# URL minting + write/load round-trip
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "repo_in",
    ["owner/repo", "hf://owner/repo", "https://huggingface.co/datasets/owner/repo"],
)
def test_mint_url_normalizes_repo_forms(repo_in):
    url = mint_capsule_url(repo_in, "cid123", revision="deadbeef")
    assert url == "https://huggingface.co/datasets/owner/repo/resolve/deadbeef/capsules/v1/cid123/capsule.json"


def test_human_url_points_at_markdown():
    url = human_capsule_url("owner/repo", "cid123", revision="main")
    assert url.endswith("/capsules/v1/cid123/capsule.md")
    assert "/blob/" in url


def test_write_then_load_roundtrip(tmp_path):
    cap = _make_capsule()
    arts = write_capsule_dir(cap, tmp_path)
    assert arts["json"].exists() and arts["md"].exists()
    loaded = load_capsule_file(arts["json"])
    assert loaded["capsule_id"] == cap["capsule_id"]
    assert loaded["schema_version"] == CAPSULE_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# New-environment self-sufficiency (consume with zero local opentraces state)
# --------------------------------------------------------------------------- #


def test_capsule_open_needs_no_home_or_bucket(tmp_path):
    """A maintainer in a brand-new environment can `capsule open` a capsule with
    no ~/.opentraces, no bucket, no project. Hermetic: a severed-HOME subprocess
    resolves a local capsule file and creates no opentraces state."""

    import os
    import subprocess
    import sys

    cap = _make_capsule()
    arts = write_capsule_dir(cap, tmp_path / "store")
    fresh_home = tmp_path / "fresh_home"
    fresh_home.mkdir()

    env = dict(os.environ)
    env["HOME"] = str(fresh_home)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    proc = subprocess.run(
        [sys.executable, "-c", "from opentraces.cli import main; main()",
         "capsule", "open", str(arts["json"]), "--json"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["capsule_id"] == cap["capsule_id"]
    assert out["schema_version"] == CAPSULE_SCHEMA_VERSION
    # The consume path must not materialize any opentraces state in the fresh HOME.
    assert not (fresh_home / ".opentraces").exists()
