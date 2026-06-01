"""Plan 090 — Trace Capsules generalised as privacy-bounded usage episodes.

End-to-end proof that a ``test=null`` usage episode (an "Agent Experience Report"):

* exports / previews / opens / renders with NO dependency on a test existing (R2);
* ``capsule preview`` shows the redaction manifest + privacy_scope and writes nothing (R3);
* internal infra (URL / hostname / DB string) is REDACTED as spans, and prompt-bearing
  fields (system_layer, reasoning_content) are EXCLUDED by default, included on opt-in (R4);
* the additive keys ``product`` / ``summary.outcome_taxonomy`` / ``privacy_scope`` thread
  through ``freeze_capsule`` while ``REQUIRED_KEYS`` / ``CAPSULE_SCHEMA_VERSION`` stay frozen (R1);
* publish sha-pins the PUBLISHED ``capsule.json`` and no classifier ``matched_text`` leaks (R5).

Two layers: a real-pipeline layer (``export_capsule`` + the ``capsule preview`` CLI over a
seeded trace) and a hermetic layer (``redact_envelope`` / ``freeze_capsule`` / mocked publish).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from opentraces_schema import Agent, Step, TraceRecord

from opentraces.core.capsule.contract import (
    CAPSULE_SCHEMA_VERSION,
    REQUIRED_KEYS,
    freeze_capsule,
    validate_capsule,
)
from opentraces.core.capsule.export import export_capsule
from opentraces.core.capsule.redaction import REDACTION_FLOOR, redact_envelope
from opentraces.core.capsule.render import render_issue_body
from opentraces.core.capsule.share import resolve_capsule, write_capsule_dir
from opentraces.security.tools.capsule_scope_tool import DEFAULT_PROMPT_EXCLUDE

# Planted, distinctive sentinels.
INTERNAL_URL = "https://jira.acme-internal.com/browse/SEC-42"
INTERNAL_HOST = "buildbox01.corp"
DB_CONN = "jdbc:postgresql://orders-db.internal:5432/customers"
SECRET = "deadbeefcafef00d0123456789abcdef0011223344556677"  # high-entropy
SYS_PROMPT = "SYSTEM-PROMPT-SENTINEL act exactly as instructed"
REASONING = "REASONING-SENTINEL private chain of thought"
PRODUCT = "acme-sdk"


# --------------------------------------------------------------------------- #
# Seeding a real-shaped trace into the (conftest-isolated) bucket
# --------------------------------------------------------------------------- #


def _episode_record(trace_id: str = "ue-trace-1") -> TraceRecord:
    """A successful (test=null) usage episode around one consumed product. Every
    step references the product so the product_episode slice covers them; step 2
    carries the planted infra signals + a reasoning trace."""

    return TraceRecord(
        trace_id=trace_id,
        session_id=f"s-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": f"Integrate the {PRODUCT} payments client into checkout"},
        dependencies=[PRODUCT, "requests"],
        steps=[
            Step(step_index=1, role="user", content=f"Integrate {PRODUCT} into checkout"),
            Step(
                step_index=2,
                role="agent",
                content=(
                    f"Using {PRODUCT}. Refs: {INTERNAL_URL} on host {INTERNAL_HOST}; "
                    f"connected {DB_CONN} with token {SECRET}"
                ),
                reasoning_content=REASONING,
            ),
            Step(step_index=3, role="agent", content=f"{PRODUCT} integrated; checkout works"),
        ],
        outcome={"success": True, "committed": False},
    )


def _enroll(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": "0123456789abcdef0123456789abcdef"})
    )
    from opentraces.core.config import get_project_traces_dir

    get_project_traces_dir(project_dir).mkdir(parents=True, exist_ok=True)


def _seed(project_dir: Path, record: TraceRecord) -> str:
    from opentraces.core.bucket_store import write_trace_record
    from opentraces.core.config import get_project_dir

    _enroll(project_dir)
    slug = get_project_dir(project_dir).name
    write_trace_record(record, project_slug=slug, source_layer="capture")
    return slug


# --------------------------------------------------------------------------- #
# R2 + R4 — real pipeline: a test=null usage episode redacts + excludes
# --------------------------------------------------------------------------- #


def test_usage_episode_exports_redacts_and_excludes(tmp_path):
    project = tmp_path / "shop"
    record = _episode_record()
    _seed(project, record)

    capsule = export_capsule(project_dir=project, trace_id=record.trace_id, product=PRODUCT)

    # test=null is first-class: validates, renders, resolves — no path needs a test.
    validate_capsule(capsule)
    assert capsule["test"] is None
    assert capsule["schema_version"] == CAPSULE_SCHEMA_VERSION

    blob = json.dumps(capsule)
    # R4 — internal infra REDACTED as spans (gone from the envelope).
    for needle in (INTERNAL_URL, INTERNAL_HOST, "orders-db.internal", SECRET, REASONING):
        assert needle not in blob, f"{needle!r} leaked into the capsule"
    manifest = capsule["redaction"]["manifest"]
    assert manifest["by_tool"].get("business_logic", 0) >= 1, manifest["by_tool"]
    assert manifest["floor"] == list(REDACTION_FLOOR)
    assert "matched_text" not in json.dumps(manifest)

    # R4 — reasoning_content EXCLUDED by default (marker present, count recorded).
    assert manifest["fields_excluded"] >= 1
    assert any(p.endswith("reasoning_content") for p in manifest["excluded_field_paths"])
    assert "[EXCLUDED:" in blob

    # Additive episode keys are present + honest.
    assert capsule["product"]["name"] == PRODUCT
    assert capsule["summary"]["outcome_taxonomy"] == "completed"
    assert capsule["privacy_scope"]["system_prompt_included"] is False
    assert "product_inferred_not_captured" in capsule["limitations"]

    # Renders as a usage episode (presentation reframe), resolves round-trip.
    body = render_issue_body(capsule, capsule_url="https://hf.co/x")
    assert "🧭 Agent Experience Report (usage episode)" in body
    arts = write_capsule_dir(capsule, tmp_path / "out")
    assert resolve_capsule(str(arts["json"]))["capsule_id"] == capsule["capsule_id"]


def test_include_prompts_opts_reasoning_back_in(tmp_path):
    project = tmp_path / "shop"
    record = _episode_record()
    _seed(project, record)

    capsule = export_capsule(
        project_dir=project, trace_id=record.trace_id, product=PRODUCT, include_prompts=True,
    )
    blob = json.dumps(capsule)
    # Opt-in INCLUSION: the reasoning trace is now present (still floor-redacted).
    assert REASONING in blob
    assert capsule["privacy_scope"]["reasoning_included"] is True
    assert capsule["redaction"]["manifest"]["fields_excluded"] == 0
    # Infra signals are STILL redacted regardless of the prompt choice.
    assert INTERNAL_URL not in blob and SECRET not in blob


# --------------------------------------------------------------------------- #
# R3 — `capsule preview` shows manifest + privacy_scope, writes/publishes NOTHING
# --------------------------------------------------------------------------- #


def _cli(*args: str) -> subprocess.CompletedProcess:
    # Subprocess inherits the conftest-isolated HOME, so it reads the same bucket.
    return subprocess.run(
        [sys.executable, "-c", "from opentraces.cli import main; main()", *args],
        capture_output=True, text=True, env=dict(os.environ),
    )


def test_capsule_preview_is_read_only(tmp_path):
    project = tmp_path / "shop"
    record = _episode_record()
    _seed(project, record)

    capsules_dir = project / ".opentraces" / "capsules"
    proc = _cli("capsule", "preview", record.trace_id, "--project", str(project),
                "--product", PRODUCT, "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)

    assert payload["writes_anything"] is False
    assert payload["redaction"]["by_field_path"]  # WHERE redactions landed
    assert payload["redaction"]["floor"] == list(REDACTION_FLOOR)
    assert "system_prompt_included" in payload["privacy_scope"]
    assert payload["business_logic"]["findings"] >= 1
    # The developer-approval checkpoint writes NOTHING under .opentraces/capsules.
    assert not capsules_dir.exists(), "preview must not write a capsule dir"


# --------------------------------------------------------------------------- #
# R4 (hermetic) — system_layer + reasoning_content excluded; spans on infra
# --------------------------------------------------------------------------- #


def test_redact_envelope_excludes_prompts_and_spans_infra():
    payload = {
        "context_resume_packet": {"node_id": "n", "system_layer": {"content": {"system_prompt": SYS_PROMPT}}},
        "slice": {"steps": [
            {"index": 0, "content": f"see {INTERNAL_URL} host {INTERNAL_HOST}", "reasoning_content": REASONING},
            {"index": 1, "content": f"db {DB_CONN} token {SECRET}"},
        ]},
    }
    red, man = redact_envelope(payload, exclude_paths=list(DEFAULT_PROMPT_EXCLUDE))
    blob = json.dumps(red)
    assert SYS_PROMPT not in blob and REASONING not in blob  # excluded
    assert "[EXCLUDED:context_resume_packet.system_layer]" in blob
    assert INTERNAL_URL not in blob and INTERNAL_HOST not in blob  # business_logic spans
    assert SECRET not in blob and "orders-db.internal" not in blob
    assert man["by_tool"].get("business_logic", 0) >= 2
    assert set(man["excluded_field_paths"]) >= {
        "context_resume_packet.system_layer", "slice.steps.0.reasoning_content",
    }
    assert "matched_text" not in json.dumps(man)

    # Without exclusion the prompts survive (opt-in inclusion), infra still redacted.
    red2, man2 = redact_envelope(payload, exclude_paths=None)
    blob2 = json.dumps(red2)
    assert SYS_PROMPT in blob2 and REASONING in blob2
    assert man2["fields_excluded"] == 0
    assert INTERNAL_URL not in blob2


# --------------------------------------------------------------------------- #
# R1 — additivity: the frozen contract is byte-unchanged
# --------------------------------------------------------------------------- #


def test_required_keys_and_version_are_frozen():
    assert CAPSULE_SCHEMA_VERSION == "opentraces.capsule.v1"
    assert REQUIRED_KEYS == (
        "schema_version", "capsule_id", "content_is_untrusted", "source", "intent",
        "failing_step", "slice", "context_resume_packet", "trail_anchors", "repo_pin",
        "redaction", "render_state", "limitations", "embedded", "share",
    )
    # The additive usage-episode keys are NOT required (older capsules omit them).
    for k in ("product", "privacy_scope", "summary", "test", "environment", "bundle"):
        assert k not in REQUIRED_KEYS

    # freeze_capsule still works WITHOUT the new kwargs (old callers), defaulting them.
    base = dict(
        capsule_id="z" * 16, source={}, summary={"is_failure": False}, test=None,
        environment=None, bundle=None, intent={"headline": "x"}, failing_step={},
        slice_payload={}, context_resume_packet={}, trail_anchors=[], repo_pin={},
        redaction={"manifest": None}, render_state={}, limitations=[], created_with="t",
    )
    cap = freeze_capsule(**base)
    assert cap["product"] is None and cap["privacy_scope"] == {}
    validate_capsule(cap)  # test=None passes


# --------------------------------------------------------------------------- #
# R5 — publish sha-pins the PUBLISHED capsule.json; no classifier matched_text
# --------------------------------------------------------------------------- #


_OID_A = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"  # data/folder commit
_OID_B = "b0a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1"  # re-stamp commit (serves the pinned capsule.json)


def _publishable_capsule():
    return freeze_capsule(
        capsule_id="usageepisode001",
        source={"project_slug": "p", "trace_id": "t", "step_index": 1, "agent": "claude-code"},
        summary={"title": "use acme-sdk", "what_happened": "ok", "failure": None,
                 "is_failure": False, "scope": "3", "outcome_taxonomy": "completed"},
        test=None,  # the usage-episode majority
        environment={"dependencies": [PRODUCT], "language_ecosystem": "python", "setup": []},
        bundle=None,
        intent={"headline": "integrate acme-sdk", "most_substantive_spec": None, "trigger": None},
        failing_step={"index": 2, "type": "agent", "error_excerpt": None, "had_error_marker": False},
        slice_payload={"slice_id": "s", "trace_id": "t", "steps": [], "limitations": []},
        context_resume_packet={"schema_version": "opentraces.context_resume.v1", "node_id": "n",
                               "system_layer": {"content": {}}},
        trail_anchors=[], repo_pin={"remote_url": None, "commit_sha": "abc", "changed_files": []},
        redaction={}, render_state={"redaction": "redacted_ok", "closure": "closure_full", "replay": "x"},
        limitations=[], created_with="t",
        product={"name": PRODUCT, "binding": "inferred"},
        privacy_scope={"system_prompt_included": False, "developer_approved": False},
    )


def test_published_capsule_json_is_sha_pinned_40hex(monkeypatch):
    # Model HF commit snapshots: a `/resolve/<oid>/` URL serves the blob AS IT
    # EXISTED at that commit. The pinned capsule.json lives at the SECOND commit;
    # resolving the FIRST would still return the stale folder version. We assert on
    # the RESOLVED handed URL (the real consumer path), not the in-memory bytes.
    snapshots: dict[str, str] = {}      # oid -> capsule.json content at that commit
    tree: dict[str, str] = {}

    class _FakeApi:
        def __init__(self, token=None):
            pass

        def create_repo(self, **kw):
            return None

        def upload_folder(self, *, repo_id, repo_type, folder_path, path_in_repo, commit_message):
            tree["capsule.json"] = (Path(folder_path) / "capsule.json").read_text(encoding="utf-8")
            snapshots[_OID_A] = tree["capsule.json"]
            return types.SimpleNamespace(oid=_OID_A)

        def upload_file(self, *, path_or_fileobj, path_in_repo, repo_id, repo_type=None,
                        commit_message=None, **kw):
            data = path_or_fileobj
            data = bytes(data).decode("utf-8") if isinstance(data, (bytes, bytearray)) else data
            tree["capsule.json"] = data
            snapshots[_OID_B] = data
            return types.SimpleNamespace(oid=_OID_B)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    from opentraces.core.capsule.share import publish_capsule

    info = publish_capsule(_publishable_capsule(), repo_id="someone/opentraces-capsules", token="fake")

    # The HANDED url must resolve at the re-stamp commit, so a third party fetching it
    # gets the SHA-pinned capsule.json — not the stale main/None folder version.
    rev = re.search(r"/resolve/([^/]+)/", info["capsule_url"]).group(1)
    assert rev == _OID_B, "handed URL must point at the re-stamp commit, not the folder commit"
    resolved = json.loads(snapshots[rev])
    assert re.fullmatch(r"[0-9a-f]{40}", resolved["share"]["revision"])  # 40-hex, not "main"
    assert resolved["share"]["published_revision"] == _OID_A  # non-null immutable marker
    assert "main" not in resolved["share"]["capsule_url"]
    assert "matched_text" not in snapshots[rev]
    assert info["published_revision"] == _OID_A
    # The stale folder commit still exists, but is NOT the artifact we hand out.
    assert json.loads(snapshots[_OID_A])["share"]["published_revision"] is None


# --------------------------------------------------------------------------- #
# U0 — the classifier verdict payload never carries matched_text
# --------------------------------------------------------------------------- #


def test_classifier_flags_carry_no_matched_text():
    from opentraces.security.tools import ToolContext
    from opentraces.security.tools.classifier_tool import ClassifierJudge

    record = TraceRecord(
        trace_id="cf-1",
        session_id="s-cf-1",
        agent=Agent(name="claude-code"),
        task={"description": f"reach {INTERNAL_HOST} and {INTERNAL_URL}"},
        steps=[Step(step_index=1, role="user", content=f"host {INTERNAL_HOST} url {INTERNAL_URL}")],
        outcome={"success": True},
    )

    class _Cfg:
        class security:  # noqa: N801 - mimic cfg.security.classifier
            class classifier:
                enabled = True
                sensitivity = "high"

    verdict = ClassifierJudge().judge(record, ToolContext(cfg=_Cfg()))
    flags = verdict.payload["flags"]
    assert flags, "expected classifier flags for the planted internal infra"
    for f in flags:
        assert set(f.keys()) == {"pattern", "severity"}
        assert "matched_text" not in f and "reason" not in f
    # The literal infra values never appear anywhere in the verdict payload.
    assert INTERNAL_HOST not in json.dumps(verdict.payload)
    assert "jira.acme-internal.com" not in json.dumps(verdict.payload)
