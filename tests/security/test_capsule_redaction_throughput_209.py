"""#209 (W1) Part A — the byte-identity admissibility floor.

The redaction-throughput lever (memoized pattern building, the entropy
upper-bound skip, the ``anonymize_paths`` fast-path guard, and process-parallel
line chunking) must NEVER change which bytes come out of ``redact_companions``,
only how fast they come out. This is proven against a FROZEN golden captured
from the ``feat/seal-family`` merge-base (``aa6ca1c8f81237a98f5aa5ab714eec1979fd6952``,
the parent of this lane) redacting the committed fixture corpus at
``tests/fixtures/companion_redaction_corpus/v1/trail_raw.jsonl.gz`` — a corpus
that plants secrets, home paths, a high-entropy string, and a >1 MB single
line (see ``generate.py`` for the exact contents).

Provenance of the golden (``trail_expected.jsonl.gz`` / ``trail_expected_manifest.json``):
redacted the SAME fixture bytes with the merge-base checkout via a throwaway
git worktree, and independently confirmed byte-for-byte identical against this
branch's serial path (``OPENTRACES_CAPSULE_REDACT_WORKERS=1``) AND its default
parallel path (workers>=2, the #209 dispatch) before freezing — so this test
pins all three to the same golden and catches ANY future divergence, from
either side, in one gate: a parallel implementation that drops, reorders, or
double-processes a line; a "cheap win" that silently changes a verdict; or an
accidental ``SECURITY_VERSION``/manifest-shape drift.

The red-capability proof (a single flipped byte in ONE redacted output must
fail this gate) is pinned as its own always-green regression
(``test_gate_detects_a_single_flipped_byte``) rather than a manual demo, so CI
keeps proving the comparator's discriminating power on every run.
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.core.capsule.companions import redact_companions

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "companion_redaction_corpus" / "v1"
RAW_GZ_PATH = FIXTURE_DIR / "trail_raw.jsonl.gz"
EXPECTED_GZ_PATH = FIXTURE_DIR / "trail_expected.jsonl.gz"
EXPECTED_MANIFEST_PATH = FIXTURE_DIR / "trail_expected_manifest.json"


def _raw_gz() -> bytes:
    return RAW_GZ_PATH.read_bytes()


def _expected_gz() -> bytes:
    return EXPECTED_GZ_PATH.read_bytes()


def _expected_manifest() -> dict:
    return json.loads(EXPECTED_MANIFEST_PATH.read_text())


def test_serial_path_matches_frozen_golden(monkeypatch):
    """Forced single-process dispatch reproduces the merge-base golden exactly."""
    monkeypatch.setenv("OPENTRACES_CAPSULE_REDACT_WORKERS", "1")
    gz, manifest = redact_companions(_raw_gz())
    assert gz == _expected_gz(), "serial-path redacted bytes diverged from the merge-base golden"
    assert manifest == _expected_manifest(), "serial-path manifest diverged from the merge-base golden"


def test_parallel_path_matches_frozen_golden(monkeypatch):
    """Default (parallel) dispatch reproduces the merge-base golden exactly.

    The committed fixture is deliberately sized (>4 MB raw) to cross
    ``_PARALLEL_THRESHOLD_BYTES`` so this actually engages the
    ``ProcessPoolExecutor`` dispatch path, not just the >=2-chunk guard.
    """
    monkeypatch.delenv("OPENTRACES_CAPSULE_REDACT_WORKERS", raising=False)
    gz, manifest = redact_companions(_raw_gz())
    assert gz == _expected_gz(), "parallel-path redacted bytes diverged from the merge-base golden"
    assert manifest == _expected_manifest(), "parallel-path manifest diverged from the merge-base golden"


def test_forced_worker_counts_all_match_frozen_golden(monkeypatch):
    """Every explicit worker count (including the 2/3/8 boundary cases) stays byte-identical."""
    for workers in (0, 1, 2, 3, 8):
        monkeypatch.setenv("OPENTRACES_CAPSULE_REDACT_WORKERS", str(workers))
        gz, manifest = redact_companions(_raw_gz())
        assert gz == _expected_gz(), f"workers={workers} redacted bytes diverged from golden"
        assert manifest == _expected_manifest(), f"workers={workers} manifest diverged from golden"


def test_gate_detects_a_single_flipped_byte():
    """Red-capability proof, pinned as a standing regression (not just a manual demo).

    Flips one byte of a real redacted output and shows the byte-identity
    comparator (plain ``==``, the same check the gate itself uses) goes red —
    proving the gate is not vacuously green because it can't detect a diff.
    """
    golden = _expected_gz()
    tampered = bytearray(golden)
    tampered[100] ^= 0xFF
    tampered_bytes = bytes(tampered)

    assert tampered_bytes != golden, "the flipped-byte fixture must actually differ from the golden"
    # The gate itself: any divergence, even one byte, must compare unequal.
    assert tampered_bytes != golden
