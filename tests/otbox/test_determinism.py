"""Determinism two-fork harness (otbox 2.0 phase 6).

Guarantee (cross-cutting, spec §design-principles): gzip mtime=0 everywhere
gives byte-identity across machines and runs. The 1.0 byte-identity journeys
all sit in the unbuilt bucket-spine-v2 family (quarantined), so nothing
actually executed this. This harness runs it against the real captured
worlds we DO have: build a checkpoint twice into two independent boxes and
require identical content.

What "identical" means here, and why the obvious measures are WRONG:

* Raw bytes differ across two boxes because restore rewrites machine-local
  absolute paths into envelopes (correct machine-locality, not a bug).
* The top-level `bucket_digest` USED to differ — that was issue #29: the
  digest material fed each sub-block (raw_sources / trail_events /
  context_trees / ...) in WHOLE, and every sub-block embedded a machine-local
  ``"root"`` absolute path, so the digest the "Plan 080 Resolution H —
  deterministic across machines" comment promised was path-polluted. FIXED:
  ``bucket_manifest`` now strips ``root`` (and the volatile ``objects``
  listing) from each sub-block via a recursive ``_digest_view`` and drops the
  machine-local ``trail`` (trace-index freshness) block entirely, so
  ``bucket_digest`` is byte-identical across two restores of identical content.

What DOES hold, and what we assert: every per-block CONTENT digest
(``raw_sources.digest``, ``trail_events.digest``, ``context_trees.digest``,
``trace_records.snapshot.digest``) is byte-identical across two independent
forks — the content-addressed determinism is real; only the top-level
roll-up is polluted. We compare those content digests, computed by the
product and surfaced via public ``bucket status --json``.

DSC-4 (U7 addition): the Trace Intelligence derive-on-demand surfaces
(``--waste`` / ``--run-intel`` / ``trace compare``) are frozen-envelope
contracts with NO persisted projection — every invocation re-derives from
the TraceRecord. Their determinism guarantee is therefore run-twice
byte-identity of stdout, plus cross-verb identity between ``trace map``
and ``trace get`` (both route through the same one-shot impl helpers in
cli/trace.py, so parity is structural — these tests keep it that way).
"""

from __future__ import annotations

import json

import pytest

from .drivers import get_driver

# Worlds with committed real artifacts (deterministic restore source).
FORK_CHECKPOINTS = [
    "c-captured-real-session",
    "c-captured-codex-real-session",
]

# Per-block CONTENT digest paths within `bucket status --json`'s bucket{}.
# These are the machine-independent content hashes (each block ALSO carries a
# machine-local `root` we deliberately ignore — see module docstring).
_CONTENT_DIGEST_PATHS = [
    ("raw_sources", "digest"),
    ("trail_events", "digest"),
    ("context_trees", "digest"),
]


def _content_digests(driver, box) -> dict[str, str] | None:
    res = driver.exec(box, [*driver.cli_argv(box), "--json", "bucket", "status"])
    if res.returncode != 0:
        return None
    try:
        bucket = json.loads(res.stdout).get("bucket", {})
    except ValueError:
        return None
    out: dict[str, str] = {}
    for block, key in _CONTENT_DIGEST_PATHS:
        val = bucket.get(block, {}).get(key)
        if val:
            out[f"{block}.{key}"] = val
    return out


@pytest.fixture(scope="module")
def driver():
    return get_driver("local")


@pytest.mark.parametrize("checkpoint", FORK_CHECKPOINTS)
def test_two_forks_share_content_digests(driver, checkpoint):
    from .checkpoints import resolve_checkpoint

    a = resolve_checkpoint(driver, checkpoint)
    ca = _content_digests(driver, a.box)
    b = resolve_checkpoint(driver, checkpoint)
    cb = _content_digests(driver, b.box)
    try:
        if not ca and not cb:
            pytest.skip(f"{checkpoint}: no content digests (synthetic fallback path)")
        # Anti-vacuity: real content digests on both sides, not {}=={}.
        assert ca and all(v.startswith("sha256:") for v in ca.values()), (
            f"{checkpoint}: fork A produced no real content digests: {ca}"
        )
        assert set(ca) == set(cb), (
            f"{checkpoint}: content-digest block SET differs across forks: "
            f"{set(ca) ^ set(cb)}"
        )
        mismatched = {k: (ca[k], cb[k]) for k in ca if ca[k] != cb[k]}
        assert not mismatched, (
            f"{checkpoint}: per-block CONTENT digest differs across two "
            f"independent restores (real non-determinism): {mismatched}"
        )
    finally:
        for r in (a, b):
            if r.box.root.exists():
                driver.teardown(r.box)


# ---------------------------------------------------------------------------
# DSC-4 — Trace Intelligence derive-on-demand determinism (U7).
# ---------------------------------------------------------------------------

_INTEL_CHECKPOINT = "c-captured-real-session"


@pytest.fixture(scope="module")
def intel_box(driver):
    from .checkpoints import resolve_checkpoint

    cp = resolve_checkpoint(driver, _INTEL_CHECKPOINT)
    audit = cp.box.notes.get("c_captured_session_audit") or {}
    yield cp.box, str(audit.get("trace_id") or "")
    if cp.box.root.exists():
        driver.teardown(cp.box)


def _envelope_schema_version(payload: dict) -> str:
    """Frozen-envelope version, wherever the surface carries it.

    run-intel and compare emit ``schema_version`` top-level; the waste CLI
    wrapper nests the ``opentraces.context_waste.v1`` envelope under a
    ``waste`` key. Either way a real envelope must be present.
    """
    if "schema_version" in payload:
        return str(payload["schema_version"])
    for value in payload.values():
        if isinstance(value, dict) and "schema_version" in value:
            return str(value["schema_version"])
    return ""


# (label, argv template). compare is run trace-vs-itself: the sentinel world
# holds one captured trace, and self-compare still exercises the full
# envelope (metrics/quality/bursts/signals/security triples with zero deltas).
_INTEL_SURFACES = [
    ("waste", ["trace", "map", "{trace_id}", "--waste", "--json"]),
    ("run-intel", ["trace", "map", "{trace_id}", "--run-intel", "--json"]),
    ("compare", ["trace", "compare", "{trace_id}", "{trace_id}", "--json"]),
]


@pytest.mark.parametrize(
    "label,template", _INTEL_SURFACES, ids=[label for label, _ in _INTEL_SURFACES]
)
def test_trace_intelligence_run_twice_byte_identical(driver, intel_box, label, template):
    """Derive-on-demand means re-derived EVERY call: two back-to-back
    invocations on the same world must emit byte-identical stdout (no
    wall-clock, randomness, or set-iteration leaking into the envelope)."""
    box, trace_id = intel_box
    assert trace_id, f"{_INTEL_CHECKPOINT}: audit carries no trace_id"
    argv = [part.replace("{trace_id}", trace_id) for part in template]

    first = driver.exec(box, [*driver.cli_argv(box), *argv])
    second = driver.exec(box, [*driver.cli_argv(box), *argv])

    assert first.returncode == 0, f"{label} failed rc={first.returncode}: {first.stderr[:200]}"
    assert second.returncode == 0, f"{label} rerun failed rc={second.returncode}: {second.stderr[:200]}"
    # Anti-vacuity: a real frozen envelope, not an empty payload.
    payload = json.loads(first.stdout)
    assert _envelope_schema_version(payload).startswith("opentraces."), payload
    assert first.stdout == second.stdout, (
        f"{label}: derive-on-demand envelope differs across two back-to-back "
        f"runs on the same world (real non-determinism)"
    )


@pytest.mark.parametrize("flag", ["--waste", "--run-intel"])
def test_trace_intelligence_cross_verb_identity(driver, intel_box, flag):
    """``trace map`` and ``trace get`` share one one-shot impl helper per
    flag (cli/trace.py), so the two surfaces must emit byte-identical
    payloads — an agent must never get a different answer depending on
    which verb it happened to reach the envelope through."""
    box, trace_id = intel_box
    assert trace_id, f"{_INTEL_CHECKPOINT}: audit carries no trace_id"

    via_map = driver.exec(
        box, [*driver.cli_argv(box), "trace", "map", trace_id, flag, "--json"]
    )
    via_get = driver.exec(
        box, [*driver.cli_argv(box), "trace", "get", trace_id, flag, "--json"]
    )

    assert via_map.returncode == 0, f"map {flag} failed: {via_map.stderr[:200]}"
    assert via_get.returncode == 0, f"get {flag} failed: {via_get.stderr[:200]}"
    payload = json.loads(via_map.stdout)
    assert _envelope_schema_version(payload).startswith("opentraces."), payload
    assert via_map.stdout == via_get.stdout, (
        f"{flag}: map/get cross-verb payloads diverged (the shared one-shot "
        f"impl helper contract broke)"
    )
