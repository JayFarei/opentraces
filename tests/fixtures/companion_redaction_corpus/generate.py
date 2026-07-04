"""Deterministic generator for the #209 (W1) committed companion-redaction corpus.

Produces ``v1/trail_raw.jsonl.gz`` — a raw (unredacted) trail-companion JSONL
body shaped like a real ``trail.jsonl.gz`` companion (one TrailEvent-ish dict
per line), used as the fixed input for the Part A byte-identity gate:
redacting this same fixture with the merge-base (``feat/seal-family``) code
and with the W1 work-branch code must produce byte-identical output and an
identical manifest.

Deliberately includes, per the #209 issue spec:
  - planted secrets (a generic ``sk-live-`` token + an AWS-shaped key) that
    the regex detector must catch,
  - home paths (``/Users/<name>/...`` and ``/home/<name>/...``) for the path
    anonymizer,
  - a high-entropy random-looking string for the entropy detector,
  - at least one multi-MB line (a single oversized ``tool_output`` field) —
    this is what exercises ``_chunk_lines_by_size``'s "one huge line becomes
    its own chunk" behavior and pushes the fixture's total size past
    ``_PARALLEL_THRESHOLD_BYTES`` so the default (parallel) path actually
    engages during the byte-identity comparison, not just the serial path.

Deterministic: every "random-looking" span is generated from a fixed seed, so
re-running this script reproduces the exact same committed bytes (gzip
``mtime=0`` as well, matching the substrate's own determinism contract).

Regenerate with:
    python tests/fixtures/companion_redaction_corpus/generate.py
"""

from __future__ import annotations

import gzip
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "v1"
OUT_PATH = OUT_DIR / "trail_raw.jsonl.gz"

SEED = 20260704
MULTI_MB_LINE_BYTES = 5 * 1024 * 1024  # > 1 MB, satisfies "at least one multi-MB line"
TOTAL_FILLER_LINES = 400  # padding so the corpus crosses the 4 MB parallel threshold

# Planted secrets — recognizable, deterministic, never real credentials.
PLANTED_SK_TOKEN = "sk-live-W1REDACTIONCORPUSAAAAAAAAAAAAAAAA"
PLANTED_AWS_KEY = "AKIAW1REDACTIONCORPUSFIX"
HOME_PATH_MAC = "/Users/octavia/projects/opentraces/secrets/creds.env"
HOME_PATH_LINUX = "/home/octavia/.config/opentraces/token.json"


def _high_entropy_token(rng: random.Random, length: int = 48) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    return "".join(rng.choice(alphabet) for _ in range(length))


def _filler_line(rng: random.Random, idx: int) -> dict:
    return {
        "event_type": "tool_call" if idx % 2 == 0 else "observation",
        "step": idx,
        "cwd": HOME_PATH_MAC if idx % 5 == 0 else "/repo/opentraces",
        "tool_name": "Bash" if idx % 3 else "Read",
        "content": f"routine line {idx} nothing sensitive here, just work item #{idx}",
        "note": _high_entropy_token(rng, 24) if idx % 11 == 0 else None,
    }


def build_lines() -> list[str]:
    rng = random.Random(SEED)
    lines: list[str] = []

    # (1) a planted generic API-key-shaped secret in free text.
    lines.append(json.dumps({
        "event_type": "tool_call",
        "step": 1,
        "cwd": "/repo/opentraces",
        "tool_name": "Bash",
        "content": f"deploying with token {PLANTED_SK_TOKEN} — do not commit",
    }))

    # (2) a planted AWS-shaped key.
    lines.append(json.dumps({
        "event_type": "observation",
        "step": 2,
        "cwd": "/repo/opentraces",
        "content": f"aws configure set aws_access_key_id {PLANTED_AWS_KEY}",
    }))

    # (3) a macOS home path.
    lines.append(json.dumps({
        "event_type": "tool_call",
        "step": 3,
        "cwd": HOME_PATH_MAC,
        "tool_name": "Read",
        "content": f"cat {HOME_PATH_MAC}",
    }))

    # (4) a Linux home path.
    lines.append(json.dumps({
        "event_type": "tool_call",
        "step": 4,
        "cwd": HOME_PATH_LINUX,
        "tool_name": "Read",
        "content": f"cat {HOME_PATH_LINUX}",
    }))

    # (5) a bare high-entropy string with no other structure.
    lines.append(json.dumps({
        "event_type": "observation",
        "step": 5,
        "content": _high_entropy_token(rng, 64),
    }))

    # (6) the multi-MB line — one oversized tool_output field. Mostly a
    # repeated (highly compressible) filler phrase so the COMMITTED gz stays
    # small, with one embedded high-entropy random chunk in the middle so the
    # entropy detector still has a genuine candidate to find inside a huge line.
    filler_phrase = "build step ok, no findings here; "
    reps = max(1, MULTI_MB_LINE_BYTES // len(filler_phrase))
    random_chunk = _high_entropy_token(rng, 256)
    huge_body = (filler_phrase * (reps // 2)) + random_chunk + (filler_phrase * (reps - reps // 2))
    lines.append(json.dumps({
        "event_type": "observation",
        "step": 6,
        "cwd": "/repo/opentraces",
        "tool_name": "Bash",
        "content": f"build log with an embedded secret {PLANTED_SK_TOKEN} somewhere inside\n{huge_body}",
    }))

    # (7..N) filler lines padding the corpus past the parallel-dispatch threshold.
    for idx in range(7, 7 + TOTAL_FILLER_LINES):
        obj = _filler_line(rng, idx)
        obj = {k: v for k, v in obj.items() if v is not None}
        lines.append(json.dumps(obj))

    # A blank line (companion redaction must skip, not crash on, blank lines).
    lines.append("")

    return lines


def main() -> None:
    lines = build_lines()
    body = "\n".join(lines) + "\n"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes gz, {len(body)} bytes raw)")


if __name__ == "__main__":
    main()
