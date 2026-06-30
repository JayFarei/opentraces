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


def short_trace_id(trace_id: str | None, length: int = 8) -> str:
    """Short display form of a ``trace_id``.

    Trace ids are opaque random UUIDs per the schema contract, so the
    first ``length`` characters give a human-friendly short handle that
    ``resolve_trace_id_prefix`` accepts directly. For legacy records
    carrying the deprecated ``<agent>_<session_uuid>`` form, we still
    strip the agent prefix so display stays meaningful during the
    migration window.
    """
    if not trace_id:
        return "?"
    core = trace_id.split("_", 1)[1] if "_" in trace_id else trace_id
    return core[:length]


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
    # Slow path: the fast-path prefix glob missed (the prefix matches an id that
    # is not the on-disk filename — e.g. queried by session_id while files are
    # named by trace_id). Resolve through the project's staging-state id index
    # first (a single state.json read, no directory scan), so the hot
    # present-trace path never scans the whole traces dir. Fall back to a
    # directory scan only for index-less stores (the index resolves a present
    # trace in a real store, so that hot path stays scan-free). Candidate paths
    # are read in sorted order to preserve the prior sorted-glob resolution.
    from .state import StateManager

    try:
        session = StateManager(traces_dir.parent / "state.json").get_session(p)
    except Exception:
        session = None
    if session is not None:
        candidate_paths = sorted(
            traces_dir / f"{gen.trace_id}.jsonl"
            for gen in session.generations
            if gen.trace_id
        )
    else:
        candidate_paths = sorted(traces_dir.glob("*.jsonl"))
    for path in candidate_paths:
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


# ---------------------------------------------------------------------------
# Short-prefix resolution for CLI args (``t:XX`` etc.)
# ---------------------------------------------------------------------------

class AmbiguousPrefixError(ValueError):
    """Raised when a trace-id prefix matches >1 stored trace."""

    def __init__(self, prefix: str, candidates: list[str]):
        self.prefix = prefix
        self.candidates = candidates
        super().__init__(
            f"ambiguous trace prefix {prefix!r}: "
            f"{len(candidates)} candidates: {', '.join(candidates[:6])}"
        )


def resolve_trace_id_prefix(project_cwd: Path, prefix: str) -> str | None:
    """Resolve a 2+ char prefix to a full ``trace_id`` (never a session_id).

    The user-facing identifier in opentraces is ``trace_id``. ``session_id``
    is an upstream-agent implementation detail that the CLI deliberately
    abstracts away. This resolver therefore:

    - Accepts a prefix that matches either ``trace_id`` or ``session_id``
      (people copy-paste both forms historically), but
    - ALWAYS returns the corresponding ``trace_id`` — when a prefix only
      matches a session_id, we look up the trace record and return its
      ``trace_id``. Callers that need the session_id read it off the
      ``TraceRecord``.

    Returns the full trace_id on a unique match, ``None`` when no stored
    trace has that prefix, and raises :class:`AmbiguousPrefixError` when
    the prefix matches multiple distinct trace_ids. Accepts the ``t:``
    prefix form and strips it transparently. Minimum prefix length is
    2 chars (after stripping the ``t:``).
    """
    p = _strip_prefix(prefix).strip().lower()
    if not p:
        return None
    if len(p) < 2:
        raise ValueError(
            f"trace-id prefix must be >=2 chars (got {prefix!r})"
        )
    try:
        root = get_project_dir(Path(project_cwd).resolve())
    except Exception:
        return None
    traces_dir = root / "traces"
    if not traces_dir.is_dir():
        return None
    # Always collect trace_ids — when only the session_id matches, map
    # through to the record's trace_id so the abstraction holds.
    matches: set[str] = set()

    def _consider(rec: dict) -> None:
        tid = str(rec.get("trace_id") or "")
        sid = str(rec.get("session_id") or "")
        if not tid:
            # No real trace_id in the file — fall back to the session_id
            # so we still have *some* identifier to round-trip. This only
            # happens for malformed / pre-fix legacy JSONLs.
            if sid and sid.lower().startswith(p):
                matches.add(sid)
            return
        if tid.lower().startswith(p):
            matches.add(tid)
        elif sid and sid.lower().startswith(p):
            matches.add(tid)  # NB: trace_id, not sid — abstraction boundary

    # Filename starts with prefix (cheap, bounded). A miss returns None rather
    # than falling through to a full-dir scan of every file in the traces dir.
    for path in traces_dir.glob(f"{p}*.jsonl"):
        rec = _first_line(path)
        if rec is None:
            continue
        _consider(rec)
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousPrefixError(prefix, sorted(matches))
    return next(iter(matches))
