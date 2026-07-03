"""Frozen ``opentraces.capsule.v1`` envelope contract.

The capsule is a flattened, denormalized PROJECTION of one failing session,
designed so a single ``GET`` of ``capsule.json`` gives a maintainer agent the
whole agent-consumable contract with zero bespoke parsing. The envelope is a
plain dict (NOT registered into ``opentraces-schema`` for v1 to avoid
VERSION-POLICY/migration overhead — promote when the shape stops churning).

Changing the envelope shape requires bumping ``CAPSULE_SCHEMA_VERSION``. The
capsule also pins the nested schema versions it embeds (``context_resume``,
``trace_slice``) under ``embedded`` so a nested bump does not silently change a
capsule's meaning without a capsule bump.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CAPSULE_SCHEMA_VERSION = "opentraces.capsule.v1"
REDACTION_MANIFEST_SCHEMA_VERSION = "opentraces.capsule.redaction.v1"
# #197 mini-bucket manifest + per-trace spine (additive; NOT the capsule envelope
# version — the mini-bucket is a sibling storage unit carried alongside the
# frozen capsule.json, so it mints its OWN frozen schema strings).
MINI_BUCKET_MANIFEST_SCHEMA_VERSION = "opentraces.capsule.mini_bucket.v1"
MINI_BUCKET_TRACE_SCHEMA_VERSION = "opentraces.capsule.mini_trace.v1"

# Nested envelope versions this capsule version embeds. A consumer can check
# these to know exactly which sub-contract shapes it is reading.
EMBEDDED_SCHEMAS = {
    "context_resume": "opentraces.context_resume.v1",
    "trace_slice": "opentraces.trace_slice.v1",
}

# Required top-level keys for a well-formed capsule envelope.
REQUIRED_KEYS = (
    "schema_version",
    "capsule_id",
    "content_is_untrusted",
    "source",
    "intent",
    "failing_step",
    "slice",
    "context_resume_packet",
    "trail_anchors",
    "repo_pin",
    "redaction",
    "render_state",
    "limitations",
    "embedded",
    "share",
)


class CapsuleSchemaAheadError(RuntimeError):
    """A capsule was written by a newer opentraces than this one understands."""


def _canonical_json(obj: Any) -> str:
    """Stable JSON for hashing / byte-identity (sorted keys, no whitespace drift)."""

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_capsule_id(
    *,
    trace_id: str,
    node_id: str | None,
    start_step_index: int,
    end_step_index: int,
    repo_commit_sha: str | None,
) -> str:
    """Deterministic capsule id (16 hex chars).

    Stable across re-exports of the same closure so ``issue create`` can find
    its own prior issue (idempotency key), and so two machines exporting the
    same bug produce the same id.
    """

    seed = _canonical_json(
        {
            "trace_id": trace_id,
            "node_id": node_id or "",
            "start": int(start_step_index),
            "end": int(end_step_index),
            "sha": repo_commit_sha or "",
        }
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def build_multi_trace_capsule_id(seeds: list[dict[str, Any]]) -> str:
    """Deterministic capsule id for a multi-trace capsule (16 hex chars).

    #197 story 26: a capsule can carry N traces. The single-trace ``capsule_id``
    is a 5-key seed; the N-trace id is the hash of the SORTED per-trace seeds, so
    it is stable across re-seals and INDEPENDENT of the order the traces were
    listed. Each seed is the same 5-key dict ``build_capsule_id`` hashes.
    """

    canonical_seeds = sorted(_canonical_json(seed) for seed in seeds)
    material = _canonical_json(canonical_seeds)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def compute_mini_bucket_digest(
    trace_companion_digests: dict[str, dict[str, str | None]],
) -> str:
    """Deterministic mini-bucket digest (16 hex chars) over per-trace content.

    #197: the digest is a PURE function of the per-trace companion CONTENT-digest
    map — ``{trace_id: {"trail": "sha256:..."|None, "context": ..., "sources":
    ...}}`` — where each value is the content hash of the UNCOMPRESSED redacted
    companion text. Deriving from content hashes (not the gzip bytes) makes the
    digest gzip/mtime/zlib-INDEPENDENT, so it is stable across two seals AND
    across machines given the same redacted content. 16 hex mirrors
    ``build_capsule_id`` and stays below the entropy-floor's 20-char span so the
    stamped digest survives the publish-time ``ensure_redacted`` pass intact.
    """

    material = _canonical_json(trace_companion_digests)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def freeze_capsule(
    *,
    capsule_id: str,
    source: dict[str, Any],
    summary: dict[str, Any],
    test: dict[str, Any] | None,
    environment: dict[str, Any] | None,
    bundle: dict[str, Any] | None,
    intent: dict[str, Any],
    failing_step: dict[str, Any],
    slice_payload: dict[str, Any],
    context_resume_packet: dict[str, Any],
    trail_anchors: list[dict[str, Any]],
    repo_pin: dict[str, Any],
    redaction: dict[str, Any],
    render_state: dict[str, Any],
    limitations: list[str],
    created_with: str,
    # Plan 090 (usage episode): additive, null-tolerant. ``product`` is the
    # optional grouping anchor (one consumed product/dependency); ``privacy_scope``
    # is the structural egress declaration. Both are OPTIONAL (defaulted) and are
    # NOT in REQUIRED_KEYS — older capsules omit them and still validate. They are
    # threaded through here because this builder has no ``**extra`` passthrough.
    product: dict[str, Any] | None = None,
    privacy_scope: dict[str, Any] | None = None,
    # #197 mini-bucket integrity claim: a deterministic digest over the redacted
    # mini-bucket carried alongside this capsule. Additive + optional (NOT in
    # REQUIRED_KEYS); ``None`` on a capsule built without the mini-bucket. The
    # export path stamps it AFTER redaction (a hash string is not a secret; a
    # 16-hex digest stays below the entropy floor so it survives ensure_redacted).
    mini_bucket_digest: str | None = None,
) -> dict[str, Any]:
    """Assemble the frozen ``opentraces.capsule.v1`` envelope dict."""

    return {
        "schema_version": CAPSULE_SCHEMA_VERSION,
        "capsule_id": capsule_id,
        "created_with": created_with,
        # #197 additive: mini-bucket digest (None until the mini-bucket is built).
        "mini_bucket_digest": mini_bucket_digest,
        # The captured content (prompts, tool output, intent) is
        # attacker-influenceable text. A maintainer agent MUST treat it as DATA,
        # never as instructions.
        "content_is_untrusted": True,
        "source": source,
        "summary": summary,
        # Usage-episode grouping anchor (one consumed product/dependency) or None.
        "product": product,
        # Runnable repro: command + expected failure signal, or None when the
        # session had no failing command (then the capsule replays by intent).
        "test": test,
        # How to set up the env to run the test (deps + setup commands), so the
        # verdict is reproducible, not host-coupled.
        "environment": environment or {},
        # Hermetic source bundle (sibling capsule.bundle.tar.gz) so the test runs
        # even when the pinned commit is unreachable. None = pin-only (git ref).
        "bundle": bundle,
        "intent": intent,
        "failing_step": failing_step,
        "slice": slice_payload,
        "context_resume_packet": context_resume_packet,
        "trail_anchors": trail_anchors,
        "repo_pin": repo_pin,
        "redaction": redaction,
        # Structural egress declaration (what is included + redaction floor). Never
        # carries a classifier verdict; reflects the developer's include/exclude choices.
        "privacy_scope": privacy_scope or {},
        "render_state": render_state,
        "limitations": sorted(set(limitations)),
        "embedded": dict(EMBEDDED_SCHEMAS),
        # Filled in by the share step once the capsule is published. ``viewer_url``
        # (#198) is the human microsite address, minted at publish alongside the
        # raw-blob capsule_url; additive within the share block.
        "share": {
            "capsule_url": None,
            "human_url": None,
            "viewer_url": None,
            "published_revision": None,
        },
    }


def validate_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    """Validate a parsed capsule. Raises on shape / version problems.

    Reject-newer mirrors the house ``RemoteSchemaAheadError`` pattern: a capsule
    with a newer major version than we understand exits with an upgrade hint
    rather than silently mis-parsing.
    """

    if not isinstance(capsule, dict):
        raise ValueError("capsule must be a JSON object")
    version = capsule.get("schema_version")
    if version != CAPSULE_SCHEMA_VERSION:
        # v1 is the only known version. Anything else that starts with the
        # capsule prefix is treated as "ahead"; otherwise it is not a capsule.
        if isinstance(version, str) and version.startswith("opentraces.capsule."):
            raise CapsuleSchemaAheadError(
                f"capsule schema_version {version!r} is newer than this "
                f"opentraces understands ({CAPSULE_SCHEMA_VERSION!r}). "
                "Run `opentraces setup upgrade`."
            )
        raise ValueError(
            f"not an opentraces capsule (schema_version={version!r})"
        )
    missing = [k for k in REQUIRED_KEYS if k not in capsule]
    if missing:
        raise ValueError(f"capsule missing required keys: {', '.join(missing)}")
    # Light nested-shape checks — the contract is more than a key list.
    if not isinstance(capsule.get("repo_pin"), dict):
        raise ValueError("capsule.repo_pin must be an object")
    if not isinstance(capsule.get("embedded"), dict):
        raise ValueError("capsule.embedded must be an object")
    test = capsule.get("test")
    if test is not None:
        cmd = test.get("command") if isinstance(test, dict) else None
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError("capsule.test, when present, must carry a non-empty string command")
        exp = test.get("expected")
        if not isinstance(exp, dict) or exp.get("kind") not in ("error_string", "nonzero_exit"):
            raise ValueError("capsule.test.expected.kind must be 'error_string' or 'nonzero_exit'")
    return capsule
