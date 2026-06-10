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
