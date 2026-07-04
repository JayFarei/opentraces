"""Acceptance harness for #143: the planted-sentinel tail-probe leak count.

Ported from ``kb/projects/opentraces/capsule-flow-prototype/{run_p2_f3.py,
p2-planted.json}`` (prototype artifacts, COPIED here — not importable src).

Two mandatory corrections vs the shipped prototype (see the security reader R2):
  (a) probe the path TAILS (``/secret/.env``, ``/work/acme``, ``/.ssh/id_rsa``),
      NOT the verbatim full-path probes ``run_p2_f3.py`` greps — the old
      ``_scrub_identity`` rewrote the username segment while LEAKING the tail, so
      the verbatim probes already read CAUGHT and hide the real structure leak;
  (b) the S1 AWS sentinel is a canonical 20-char key (``AKIA`` + 16) so it
      redacts wholly and stays a clean non-regression control — the shipped
      fixture's 28-char key left a ``YEXAMPLE`` tail that inflated the count.

The frozen self-contained input is built below (no ``resume_packet_from_bucket``
bucket dependency); the OUT path is dropped (assert in memory). The gate is the
TAIL-probe number: **5/8 leaks today → 1/8 after this work (0/8 NER-gated)**.
"""

from __future__ import annotations

import json

from opentraces.security.pipeline import COMPANION_FLOOR, sanitize_companion_dict
from opentraces.security.tools.llm_pii_tool import LLMPIIDetectorTool


# --- The 8 unique sentinels (so survivors are grep-able) -------------------
S = {
    "S1": "AKIAZZ7QF3SENTINEL1X",                          # AWS key id (20-char canonical, trace-core)
    "S2": "jane.SENTINEL2@acme.com",                       # email (trace-core)
    "S3": "sk-live-SENTINEL3aaaaaaaaaaaaaaaaaaaaaaaa",      # hyphenated sk- token (ctx messages)
    "S4_name": "SENTINEL4 Doe",                            # person name (ctx messages, NER-only)
    "S4_email": "x@y.com",                                 # co-located email (regex-caught)
    "S5": "/Users/SENTINEL5/secret/.env",                  # home path in prose (ctx messages)
    "S6": "/Users/SENTINEL6/work/acme",                    # home path cwd (ctx runtime_state)
    "S7": 'API_KEY = "ghp_SENTINEL7aaaaaaaaaaaaaaaaaaaaaa"',  # code secret (trail authored_text)
    "S8": 'path = "/Users/SENTINEL8/.ssh/id_rsa"',         # home path in code (trail authored_text)
}

# Probe set = TAIL-probe (the security-meaningful definition). S5/S6/S8 assert
# on the revealing TAIL, not the (already-rewritten) full path.
PROBES = {
    "S1": "SENTINEL1",                 # control: AWS regex redacts the whole key
    "S2": "jane.SENTINEL2@acme.com",   # control: email regex
    "S3": "sk-live-SENTINEL3",         # widened sk- regex
    "S4": "SENTINEL4 Doe",             # residual on stock (name → NER only)
    "S5": "/secret/.env",              # TAIL probe (path_anonymizer tail-consume)
    "S6": "/work/acme",                # TAIL probe
    "S7": "ghp_SENTINEL7",             # control: github regex
    "S8": "/.ssh/id_rsa",              # TAIL probe
}


def _planted_envelope() -> dict:
    """The full-fidelity (otel, completeness=full) capsule envelope that inlines
    TraceRecord-core + ctx (context_resume_packet) + trail (trail_anchors)."""
    return {
        "schema_version": "opentraces.capsule.v1",
        "capsule_id": "cap-p2-f3-demo",
        "trace": {
            "trace_id": "19f1a2f4-4fc6-48bc-ad10-84306fb2b268",
            "title": "release prep for the CLI/TUI capacity manager",
            "summary": {
                "what_happened": (
                    "Reviewed the project for OSS release. Rotated the deploy key "
                    + S["S1"] + " and pinged " + S["S2"]
                    + " to confirm the new IAM policy."
                ),
                "outcome": "prepared a pull request",
            },
        },
        "context_resume_packet": {
            "schema_version": "opentraces.context_resume.v1",
            "node_id": "sha256:9e76558fe4f2981d8b71b113a7ee60c68424d6ec71c7471192b1cc9073bb4d1e",
            "trace_id": "19f1a2f4-4fc6-48bc-ad10-84306fb2b268",
            "step_index": 0,
            "branch_type": "root",
            "source": "bucket_companion",
            "system_layer": {
                "layer_type": "system",
                "content": {
                    "claude_md_set": [
                        {"load_reason": "user_global", "path": "/Users/jayfarei/.claude/CLAUDE.md", "scope": "user"}
                    ],
                    "static_core_ref": "claude_code:unknown",
                },
            },
            "messages_layer": {
                "layer_type": "messages",
                "completeness": "full",
                "capture_method": "otel",
                "content": {
                    "is_summary": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Here is the live key " + S["S3"] + " for staging. "
                                "Contact " + S["S4_name"] + " <" + S["S4_email"] + "> owns it. "
                                "It is written to " + S["S5"] + " on the box."
                            ),
                        },
                        {"role": "assistant", "content": "Acknowledged, I will not echo the credential back."},
                    ],
                },
            },
            "tool_registry_layer": {
                "layer_type": "tool_registry",
                "content": {"deferred_tools": [], "tools": []},
            },
            "runtime_state_layer": {
                "layer_type": "runtime_state",
                "completeness": "full",
                "capture_method": "otel",
                "content": {"cwd": S["S6"], "model": "claude-fable-5", "permission_mode": "default"},
            },
            "limitations": [],
        },
        "trail_anchors": [
            {
                "anchor_id": "ot://git-anchor/anc-0001",
                "trace_id": "19f1a2f4-4fc6-48bc-ad10-84306fb2b268",
                "patch_id": "patch-0001",
                "file_path": "config/settings.py",
                "survival_state": "alive_on_path",
                "authored_text": S["S7"],
            },
            {
                "anchor_id": "ot://git-anchor/anc-0002",
                "trace_id": "19f1a2f4-4fc6-48bc-ad10-84306fb2b268",
                "patch_id": "patch-0002",
                "file_path": "scripts/deploy.sh",
                "survival_state": "alive_transformed",
                "authored_text": S["S8"],
            },
        ],
    }


def _leaks(redacted: dict) -> set[str]:
    blob = json.dumps(redacted, ensure_ascii=False)
    return {key for key, probe in PROBES.items() if probe in blob}


class _FakeNER:
    """Deterministic stand-in for an availability-gated NER (privacy_filter /
    llm_pii). Flags the planted person name only — proves the honest band
    without a live model. The stock floor never has it, so S4 leaks on stock."""

    def detect_for_path(self, path, text, field_type_label, siblings):
        return [("PERSON", S["S4_name"])] if S["S4_name"] in text else []


# --------------------------------------------------------------------------- #
# The headline gate
# --------------------------------------------------------------------------- #


def test_stock_install_drops_tailprobe_leaks_to_one_of_eight():
    """STOCK (no optional deps): 5/8 → 1/8. The three path TAILS (S5/S6/S8) and
    the conversational token (S3) close; only the person name (S4) residual."""
    redacted, manifest = sanitize_companion_dict(_planted_envelope())
    leaks = _leaks(redacted)

    # The three path tails are ABSENT (the load-bearing headline).
    assert "/secret/.env" not in json.dumps(redacted)   # S5
    assert "/work/acme" not in json.dumps(redacted)      # S6
    assert "/.ssh/id_rsa" not in json.dumps(redacted)    # S8
    # The hyphenated sk- token is ABSENT (widened regex).
    assert "sk-live-SENTINEL3" not in json.dumps(redacted)  # S3

    # Exactly one residual on a stock install: the person name.
    assert leaks == {"S4"}, f"expected only S4 to leak on stock, got {leaks}"

    # Non-regression controls: the regex-shaped secrets stay caught.
    for control in ("S1", "S2", "S7"):
        assert control not in leaks

    # The manifest stamps what ran (path_anonymizer + the detector floor).
    assert manifest["floor_satisfied"] is True
    assert "path_anonymizer" in manifest["tools_applied"]
    assert manifest["home_paths_scrubbed"] >= 1


def test_person_name_closes_only_when_ner_present():
    """S4 closes to 0/8 ONLY in an availability-gated branch where an NER tool is
    present (injected deterministically here). On stock it leaks — the honest
    band. Never asserts a name catch on a stock floor."""
    ner = LLMPIIDetectorTool().with_detector(_FakeNER())
    redacted, _ = sanitize_companion_dict(
        _planted_envelope(), tools=[*COMPANION_FLOOR, ner]
    )
    leaks = _leaks(redacted)
    assert leaks == set(), f"with NER present all 8 should close, got leak {leaks}"
    assert "SENTINEL4 Doe" not in json.dumps(redacted)


def test_manifest_never_carries_matched_text():
    """The companion manifest is counts + types only — never a literal secret."""
    _redacted, manifest = sanitize_companion_dict(_planted_envelope())
    blob = json.dumps(manifest, ensure_ascii=False)
    assert "matched_text" not in blob
    assert S["S1"] not in blob
    assert "sk-live-SENTINEL3" not in blob


def test_sanitize_is_idempotent():
    """Re-sanitizing already-sanitized bytes is a byte-identical no-op, so
    re-sync / re-seal is deterministic."""
    once, _ = sanitize_companion_dict(_planted_envelope())
    twice, _ = sanitize_companion_dict(once)
    assert twice == once


def test_negative_control_sk_test_placeholder_survives():
    """A synthetic ``sk-test-…`` placeholder is NOT flagged after the sk- widening
    (the allowlist exclusions still hold)."""
    payload = {"trace": {"summary": {"note": "example token sk-test-aaaaaaaaaaaaaaaaaaaa is fine"}}}
    redacted, _ = sanitize_companion_dict(payload)
    assert "sk-test-aaaaaaaaaaaaaaaaaaaa" in json.dumps(redacted)


def test_classifier_is_not_run_as_a_detector():
    """The classifier is a Judge/envelope-gate; it must NEVER appear in the
    companion detector loop (it never mutates a leaf)."""
    _redacted, manifest = sanitize_companion_dict(_planted_envelope())
    assert "classifier" not in manifest["tools_applied"]
