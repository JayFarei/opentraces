"""Tolerant trace metadata resolver.

The attribution cache keys traces by whatever identifier the capture layer
handed it, which for Claude Code has historically been the ``session_id``
(e.g. ``b73af9c8-...``), even though the canonical trace_id is the
``trace_id`` field on the JSONL record. This module resolves a prefix
against BOTH fields — callers can pass either and get the same metadata.

Public API:

    TraceMeta(...)        — dataclass with short_name, task_description,
                             model, turn_count, timestamps.
    resolve_trace_meta(project_cwd, id_prefix) -> TraceMeta | None
    slugify_task(description, max_len=25) -> str | None

The JSONL layout (one trace per file, one record per line, schema 0.2.x)
is flat — ``trace_id``, ``session_id``, ``task.description``, ``agent``,
``timestamp_start/end``, ``steps`` — so we read only the first line of
each file. Results are cached via ``functools.lru_cache`` keyed on the
(project_dir_str, id_prefix) pair.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import get_project_dir


@dataclass(frozen=True)
class TraceMeta:
    trace_id: str
    session_id: str
    short_name: str | None
    task_description: str | None
    model: str | None
    timestamp_start: str | None
    timestamp_end: str | None
    turn_count: int


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

# Split on whitespace, punctuation, and hyphens so "multi-word-thing" and
# "word, other" both tokenize cleanly.
_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_STOP_WORDS = frozenset({
    # articles / determiners
    "a", "an", "the",
    # pronouns
    "i", "me", "my", "you", "your", "we", "us", "our", "they", "them",
    "their", "he", "she", "it", "its",
    # copulas / aux
    "is", "am", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "do", "does", "did",
    "will", "would", "shall", "should", "can", "could",
    "may", "might", "must",
    # prepositions
    "of", "to", "in", "on", "at", "by", "for", "with", "from", "as",
    "into", "about", "over", "under", "out", "off", "up", "down",
    # conjunctions / subordinators
    "and", "or", "but", "if", "so", "than", "that", "this", "these",
    "those", "because", "while", "when", "where", "what", "which",
    "who", "whom", "whose", "how", "why",
    # negation / modifiers
    "no", "not", "none",
    "now", "just", "only", "very", "really", "quite", "also", "too",
    "even", "then", "still", "yet", "already", "always", "ever",
    "never",
    # low-signal verbs / nouns
    "use", "using", "used", "way", "ways", "thing", "things",
    "make", "makes", "made",
    # contractions (stripped of apostrophes by splitter)
    "its", "lets", "weve", "youre", "id", "ill",
})


def slugify_task(description: str | None, max_len: int = 30) -> str | None:
    """Return a kebab-case slug of up to 3 meaningful tokens, or None.

    Algorithm (plan-043 follow-up patch):
    - Split on non-alphanumeric (whitespace, punctuation, hyphens).
    - Keep tokens where ``len(token) >= 3`` and token not in stopword set.
    - Take the first 3 surviving tokens, lowercase, join with ``-``.
    - Cap total length at ``max_len`` by dropping trailing tokens (never
      slice mid-token).
    - Returns None for empty, punctuation-only, or all-stopword input.
    """
    if not description:
        return None
    tokens_raw = _SPLIT_RE.split(description.lower())
    kept: list[str] = []
    for t in tokens_raw:
        if len(t) < 3:
            continue
        if t in _STOP_WORDS:
            continue
        kept.append(t)
        if len(kept) >= 3:
            break
    if not kept:
        return None
    # Cap length by trimming trailing tokens rather than mid-token slicing.
    while kept and len("-".join(kept)) > max_len:
        kept.pop()
    if not kept:
        return None
    slug = "-".join(kept)
    return slug or None


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------

def _strip_prefix(s: str) -> str:
    """Accept ``t:<id>`` or ``c:<sha>`` CLI-ish inputs and return the bare id."""
    if not s:
        return s
    if s.startswith(("t:", "c:", "T:", "C:")):
        return s[2:]
    return s


def _first_line(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            raw = f.readline()
    except OSError:
        return None
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _build_meta(record: dict) -> TraceMeta:
    tid = str(record.get("trace_id") or "")
    sid = str(record.get("session_id") or "")
    task = record.get("task") or {}
    desc = None
    if isinstance(task, dict):
        desc = task.get("description") or None
    slug = slugify_task(desc)
    agent = record.get("agent") or {}
    model = None
    if isinstance(agent, dict):
        model = agent.get("model") or None
        if isinstance(model, str) and "/" in model:
            # "anthropic/claude-opus-4-6" -> "claude-opus-4-6"
            model = model.split("/", 1)[1]
    steps = record.get("steps") or []
    turns = len(steps) if isinstance(steps, list) else 0
    return TraceMeta(
        trace_id=tid,
        session_id=sid,
        short_name=slug,
        task_description=desc,
        model=model,
        timestamp_start=record.get("timestamp_start"),
        timestamp_end=record.get("timestamp_end"),
        turn_count=turns,
    )


@lru_cache(maxsize=1024)
def _resolve_cached(project_root_str: str, id_prefix: str) -> TraceMeta | None:
    traces_dir = Path(project_root_str) / "traces"
    if not traces_dir.is_dir():
        return None
    p = _strip_prefix(id_prefix).strip()
    if not p:
        return None
    # Fast path: filename starts with prefix (filename is session_id for
    # Claude Code captures).
    candidates = sorted(traces_dir.glob(f"{p}*.jsonl"))
    for path in candidates:
        rec = _first_line(path)
        if rec is None:
            continue
        sid = str(rec.get("session_id") or "")
        tid = str(rec.get("trace_id") or "")
        if sid.startswith(p) or tid.startswith(p):
            return _build_meta(rec)
    # Slow path: full scan (trace_id != session_id case).
    for path in sorted(traces_dir.glob("*.jsonl")):
        rec = _first_line(path)
        if rec is None:
            continue
        sid = str(rec.get("session_id") or "")
        tid = str(rec.get("trace_id") or "")
        if sid.startswith(p) or tid.startswith(p):
            return _build_meta(rec)
    return None


def resolve_trace_meta(project_cwd: Path, id_prefix: str) -> TraceMeta | None:
    """Resolve a (session_id OR trace_id) prefix to TraceMeta, or None.

    ``project_cwd`` is the project working-tree root; we derive the
    ~/.opentraces/projects/<slug>/ path internally.
    """
    try:
        root = get_project_dir(Path(project_cwd).resolve())
    except Exception:
        return None
    return _resolve_cached(str(root), _strip_prefix(id_prefix))


def clear_cache() -> None:
    """Flush the resolver cache — for tests."""
    _resolve_cached.cache_clear()
