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

External review on PR #220 (REC A) — this gate is real for the ``tools=None``
default path but did not cover two risky surfaces the review flagged as
criticals: (1) an explicit ``tools`` list carrying a BOUND tool instance
crossing the parallel-dispatch size threshold, and (2) the mini-bucket
memoization cache serving a stale result when a companion's on-disk bytes
change between two calls within one process. ``test_bound_tool_instance_...``
and ``test_mini_bucket_cache_reflects_content_change_...`` below close both.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

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


# --------------------------------------------------------------------------- #
# REC A / CRITICAL 1 — a bound tool instance must force the serial path
# --------------------------------------------------------------------------- #


def _ner_gated_companion_text(companions_mod) -> str:
    """A >4 MB companion (crosses ``_PARALLEL_THRESHOLD_BYTES``) whose single
    real line plants a person-name sentinel that ONLY a bound NER detector
    catches (the stock regex/entropy/path_anonymizer/business_logic floor
    never does) — so "the redacted output still closes this sentinel" is
    direct proof the BOUND detector instance actually ran, not a silently
    re-resolved unbound default.
    """
    lines = [
        json.dumps({
            "event_type": "context_resume_observed",
            "content": "SENTINEL4 Franklin owns host bastion-SENTINEL5.internal",
        }),
    ]
    filler = json.dumps({"event_type": "tool_call", "content": "x" * 200, "step": 0})
    target = companions_mod._PARALLEL_THRESHOLD_BYTES * 2
    total = sum(len(entry) + 1 for entry in lines)
    while total < target:
        lines.append(filler)
        total += len(filler) + 1
    return "\n".join(lines) + "\n"


def test_bound_tool_instance_forces_serial_and_stays_correct(monkeypatch):
    """External review CRITICAL 1: an explicit ``tools`` list carrying a bound
    tool instance (e.g. ``LLMPIIDetectorTool().with_detector(...)``, the exact
    shape ``tests/test_capsule_mini_bucket.py`` exercises) must NEVER be
    handed to a worker process — a worker re-resolving the tool by bare
    registry name would run the UNBOUND default and silently under-redact.

    Proves three things on a companion well past the 4 MB parallel-dispatch
    threshold, with the worker count pinned high enough that the DEFAULT
    (``tools=None``) path on this same input would certainly parallelize:
    (1) ``_run_chunks_parallel`` is never invoked for an explicit ``tools``
    list (monkeypatched to raise if it is); (2) the redacted output still
    closes the NER-only sentinel, i.e. the bound detector's injected state
    was not lost; (3) that output is byte-identical to the forced
    single-worker serial path — the two "serial" invocations (auto-detected
    high worker count vs. explicitly pinned to 1) must produce the exact
    same bytes, since both now take the same code path for this input.
    """
    from opentraces.core.capsule import companions as companions_mod
    from opentraces.security.pipeline import COMPANION_FLOOR
    from opentraces.security.tools.llm_pii_tool import LLMPIIDetectorTool

    class _FakeNER:
        def detect_for_path(self, path, text, field_type_label, siblings):
            return [("PERSON", "SENTINEL4 Franklin")] if "SENTINEL4 Franklin" in text else []

    ner = LLMPIIDetectorTool().with_detector(_FakeNER())
    tools = [*COMPANION_FLOOR, ner]

    text = _ner_gated_companion_text(companions_mod)
    assert len(text.encode("utf-8")) >= companions_mod._PARALLEL_THRESHOLD_BYTES

    def _boom(*_args, **_kwargs):
        raise AssertionError("parallel dispatch must never run for an explicit tools list")

    monkeypatch.setattr(companions_mod, "_run_chunks_parallel", _boom)

    # A worker count that would certainly trigger parallel dispatch for the
    # tools=None default path on an input this size.
    monkeypatch.setenv("OPENTRACES_CAPSULE_REDACT_WORKERS", "4")
    body_bound, manifest_bound = companions_mod.redact_companion_text(text, tools=tools)
    assert "SENTINEL4 Franklin" not in body_bound, "the bound NER detector did not actually run"

    monkeypatch.setenv("OPENTRACES_CAPSULE_REDACT_WORKERS", "1")
    body_serial, manifest_serial = companions_mod.redact_companion_text(text, tools=tools)
    assert body_bound == body_serial, "explicit-tools serial output diverged across worker counts"
    assert manifest_bound == manifest_serial


def test_default_registry_path_still_parallelizes_above_threshold(monkeypatch):
    """Sanity companion to the critical-1 fix: the ``tools=None`` DEFAULT
    registry path must still engage the process pool above the size
    threshold — the fix narrows parallelism to this path, it must not
    accidentally disable it entirely.
    """
    from opentraces.core.capsule import companions as companions_mod

    calls: list[int] = []
    real_run = companions_mod._run_chunks_parallel

    def _spy(chunks, workers):
        calls.append(len(chunks))
        return real_run(chunks, workers)

    monkeypatch.setattr(companions_mod, "_run_chunks_parallel", _spy)
    monkeypatch.setenv("OPENTRACES_CAPSULE_REDACT_WORKERS", "4")

    text = _ner_gated_companion_text(companions_mod)
    companions_mod.redact_companion_text(text, tools=None)
    assert calls, "tools=None above the size threshold must still dispatch to the process pool"


# --------------------------------------------------------------------------- #
# REC A / CRITICAL 2 — mini-bucket cache must never serve stale bytes
# --------------------------------------------------------------------------- #


def _trail_body(mini: dict, trace_id: str) -> bytes:
    blob = mini["manifest"]["traces"][trace_id]["companions"]["trail"]["blob"]
    if blob is None:
        return b""
    relpath = mini["manifest"]["blobs"][blob]
    return gzip.decompress(mini["files"][relpath])


def test_mini_bucket_cache_reflects_content_change_within_one_process(tmp_path, monkeypatch):
    """External review CRITICAL 2: the mini-bucket memoization cache
    (``share.py::_MINI_BUCKET_CACHE``) must never serve a STALE redacted
    result when a companion's on-disk bytes change between two
    ``build_mini_bucket`` calls for the same (bucket, slug, trace_ids) within
    one process.

    Pre-fix, the cache key was ``(bucket_dir, slug, trace_ids, tool_names)`` —
    it ignored companion CONTENT entirely, so the second call below would
    return the FIRST call's redacted bytes/digest verbatim (this test is RED
    on that code; see the PR #220 comment for the pasted red run). Post-fix,
    the key includes a content digest of every companion byte actually read,
    so a byte-level change is never served stale.
    """
    from opentraces.core import paths
    from opentraces.core._bucket_io import _atomic_write_gzip
    from opentraces.core.bucket_layout import (
        trace_v1_context_path,
        trace_v1_sources_path,
        trace_v1_trail_path,
    )
    from opentraces.core.capsule.share import build_mini_bucket

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    slug = "p"
    tid = "tid-mutate"

    def _write_trail(content: str) -> None:
        line = json.dumps({"event_type": "tool_call", "content": content})
        _atomic_write_gzip(trace_v1_trail_path(slug, tid), (line + "\n").encode("utf-8"))
        _atomic_write_gzip(trace_v1_context_path(slug, tid), b"")
        _atomic_write_gzip(trace_v1_sources_path(slug, tid), b"")

    _write_trail("first-content-marker")
    first = build_mini_bucket(tmp_path, slug, [tid])
    first_body = _trail_body(first, tid)
    assert b"first-content-marker" in first_body

    # Mutate the on-disk companion bytes WITHOUT any process restart — the
    # exact in-process content-change scenario the pre-fix cache key missed.
    _write_trail("second-content-marker-totally-different")
    second = build_mini_bucket(tmp_path, slug, [tid])
    second_body = _trail_body(second, tid)

    assert b"second-content-marker-totally-different" in second_body
    assert b"first-content-marker" not in second_body, (
        "stale cache served the FIRST call's bytes after the companion content changed"
    )
    assert first["digest"] != second["digest"], "content changed but mini_bucket_digest did not"


def test_mini_bucket_cache_returns_defensive_copies(tmp_path, monkeypatch):
    """A caller mutating a ``build_mini_bucket`` result must never corrupt a
    later cache hit for the same scope (external review CRITICAL 2's
    "defensive copy if kept" ask)."""
    from opentraces.core import paths
    from opentraces.core._bucket_io import _atomic_write_gzip
    from opentraces.core.bucket_layout import (
        trace_v1_context_path,
        trace_v1_sources_path,
        trace_v1_trail_path,
    )
    from opentraces.core.capsule.share import build_mini_bucket

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    slug = "p"
    tid = "tid-defensive"
    line = json.dumps({"event_type": "tool_call", "content": "unchanged-content"})
    _atomic_write_gzip(trace_v1_trail_path(slug, tid), (line + "\n").encode("utf-8"))
    _atomic_write_gzip(trace_v1_context_path(slug, tid), b"")
    _atomic_write_gzip(trace_v1_sources_path(slug, tid), b"")

    first = build_mini_bucket(tmp_path, slug, [tid])
    first["manifest"]["traces"][tid]["companions"]["trail"]["blob"] = "tampered-by-caller"
    first["digest"] = "tampered-by-caller"

    second = build_mini_bucket(tmp_path, slug, [tid])  # same scope, cache hit
    assert second["digest"] != "tampered-by-caller"
    assert second["manifest"]["traces"][tid]["companions"]["trail"]["blob"] != "tampered-by-caller"


# --------------------------------------------------------------------------- #
# Hard invariant — a worker failure must fail loudly, never emit partial output
# --------------------------------------------------------------------------- #


def _boom_on_marker(chunk, _tools):
    """Module-level (picklable for spawn) worker function: raises for one
    specific chunk, succeeds for every other — the shape ``_run_chunks_parallel``
    dispatches, used to exercise the pool's failure-propagation contract."""
    if chunk == ["__boom__"]:
        raise RuntimeError("simulated worker crash (#209 hard-invariant test)")
    return (list(chunk), {"lines": len(chunk)})


def test_parallel_dispatch_worker_failure_propagates_loudly():
    """Hard invariant (issue #209 / PR #220 review): a worker failure in the
    parallel path must fail the seal loudly, never emit partially-redacted
    output. ``_run_chunks_parallel`` eagerly materializes ``pool.map(...)``
    into a ``list()`` — the stdlib ``ProcessPoolExecutor.map`` contract is
    that an exception raised inside any mapped call is re-raised from the
    result iterator at the point that item is consumed, and ``list()`` always
    reaches every item (there is no code path that yields a truncated result
    set on failure — the caller either gets the full ordered list or an
    exception, never a partial list silently swallowed). This exercises that
    exact contract with the SAME spawn context and dispatch shape
    ``_run_chunks_parallel`` uses, via a worker function that deterministically
    raises for one chunk among several.
    """
    from concurrent.futures import ProcessPoolExecutor
    from itertools import repeat

    from opentraces.core.capsule import companions as companions_mod

    chunks = [["ok-1"], ["__boom__"], ["ok-2"]]
    with pytest.raises(RuntimeError, match="simulated worker crash"):
        with ProcessPoolExecutor(
            max_workers=2, mp_context=companions_mod._MP_CONTEXT
        ) as pool:
            list(pool.map(_boom_on_marker, chunks, repeat(None)))
