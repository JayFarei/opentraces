"""Plan-043 naming audit — forbid upstream-branded tokens in user-visible surfaces.

Rationale: the entity-diff binary we vendor is upstream-named ``sem`` from
Ataraxy Labs; the upstream stack uses tree-sitter under the hood. We
redistribute it as ``ot-entities`` and want none of those internal names
to leak into CLI help, error messages, file names, manifests, or other
surfaces a user could see.

This test greps:
  1. CLI help output (`./otd --help` and the four plan-043 subcommands)
  2. User-facing string literals in `src/opentraces/**/*.py`
  3. JSON/TOML manifests under `src/opentraces/`

Forbidden tokens (case-insensitive, word-boundary):
  - sem
  - tree-sitter
  - ataraxy

"Semantic" etc. are fine; we key on \\bsem\\b specifically.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "opentraces"
OTD = REPO_ROOT / "otd"

# Word-boundary regex. \bsem\b avoids matching "semantic", "Problem", etc.
FORBIDDEN = [
    re.compile(r"\bsem\b", re.IGNORECASE),
    re.compile(r"tree-sitter", re.IGNORECASE),
    re.compile(r"\bataraxy\b", re.IGNORECASE),
]

# Commands whose --help we audit. The plan-043 set plus top-level help.
HELP_COMMANDS = [
    ["--help"],
    ["setup", "--help"],
    ["doctor", "--help"],
    ["backfill", "--help"],
    ["setup", "entity-parser", "--help"],
]


def _find_hits(text: str) -> list[str]:
    hits: list[str] = []
    for rx in FORBIDDEN:
        for m in rx.finditer(text):
            # Show a little surrounding context.
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            hits.append(f"{rx.pattern!r}: …{text[start:end]!r}…")
    return hits


def test_cli_help_is_clean():
    if not OTD.is_file():
        pytest.skip("./otd not present")
    all_hits: list[str] = []
    for args in HELP_COMMANDS:
        try:
            r = subprocess.run(
                [str(OTD), *args], capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as e:
            pytest.fail(f"failed to run otd {' '.join(args)}: {e}")
        combined = (r.stdout or "") + "\n" + (r.stderr or "")
        for hit in _find_hits(combined):
            all_hits.append(f"otd {' '.join(args)} -> {hit}")
    assert not all_hits, (
        "forbidden tokens leaked into CLI help:\n  " + "\n  ".join(all_hits)
    )


# String literals we scan in .py sources. Heuristic regex — we look inside
# click.echo/print/human_echo/human_hint/click.style/rich.print string args,
# plus help= and short_help= kwargs, plus Click command/group names.
_STRING_LITERAL_CONTEXTS = [
    re.compile(r'(click\.echo|print|human_echo|human_hint|click\.style)\s*\(\s*(?P<s>[frub]*"(?:[^"\\]|\\.)*"|[frub]*\'(?:[^\'\\]|\\.)*\')', re.DOTALL),
    re.compile(r'(help|short_help|epilog)\s*=\s*(?P<s>[frub]*"(?:[^"\\]|\\.)*"|[frub]*\'(?:[^\'\\]|\\.)*\')', re.DOTALL),
    # Docstrings right under `def` / `class` / module top.
    re.compile(r'"""(?P<s>.*?)"""', re.DOTALL),
]


def _iter_user_facing_strings(py_text: str):
    for rx in _STRING_LITERAL_CONTEXTS:
        for m in rx.finditer(py_text):
            yield m.group("s")


def test_source_string_literals_are_clean():
    assert SRC_ROOT.is_dir(), f"missing {SRC_ROOT}"
    hits: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        # Skip the entities package README-ish comments; it documents the
        # upstream lineage by design (dev-facing, not user-facing). If
        # future changes ever bake those words into a click.echo/help=,
        # the regex still catches them.
        try:
            text = py.read_text()
        except OSError:
            continue
        for s in _iter_user_facing_strings(text):
            for rx in FORBIDDEN:
                if rx.search(s):
                    hits.append(f"{py.relative_to(REPO_ROOT)}: {rx.pattern!r} in {s[:120]!r}")
    assert not hits, (
        "forbidden tokens leaked into user-facing strings:\n  " + "\n  ".join(hits)
    )


def test_manifests_are_clean():
    """JSON/TOML manifests under src/opentraces/ should have no forbidden field names."""
    assert SRC_ROOT.is_dir()
    hits: list[str] = []
    for pattern in ("*.json", "*.toml"):
        for path in SRC_ROOT.rglob(pattern):
            try:
                text = path.read_text()
            except OSError:
                continue
            # Keys/values — just grep.
            for rx in FORBIDDEN:
                if rx.search(text):
                    hits.append(f"{path.relative_to(REPO_ROOT)}: {rx.pattern!r} appears in manifest")
    assert not hits, (
        "forbidden tokens leaked into manifests:\n  " + "\n  ".join(hits)
    )
