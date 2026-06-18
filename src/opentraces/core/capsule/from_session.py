"""Resolve a live agent ``session_id`` to a materialized bucket ``trace_id``.

Issue #98 Part B — ``capsule export --from-session <id>`` builds a capsule from
the CURRENT turn by materializing the in-flight session into the bucket and then
exporting it like any finalized trace. This module is the resolve step only; the
export proper is reused verbatim from :func:`export_capsule`.

Two source shapes, resolved per-agent (the asymmetry is real, not papered over):

* **Codex** writes a true opentraces sidecar at
  ``<project>/.opentraces/codex-cli/hooks/<session_id>.jsonl`` (see
  ``capture/codex_cli/hooks/_common.py::sidecar_path``). We ingest that path with
  the ``codex-cli`` parser. NOTE: a pure hook sidecar (``opentraces_hook`` lines
  only) carries no ``response_item`` envelopes, so the parser yields no steps and
  the session resolves as ``unparsed`` — that is the honest current-turn limit for
  Codex (the rollout, not the sidecar, carries the turns). The unit test seeds a
  rollout-shaped file at that path to prove the resolve→ingest→export wiring.
* **Claude Code** does NOT write an opentraces sidecar; its capture path ingests
  Claude's own session transcript under ``~/.claude/projects/<enc-cwd>/<id>.jsonl``
  (``capture/claude_code/parse.py``). We resolve the EXACT transcript inside THIS
  project's own ``encode_claude_path(project_dir)`` dir (never a glob across every
  project) and ingest it with the ``claude-code`` parser.

SECURITY: the ``session_id`` is treated as an opaque filename stem. It is
validated by :func:`is_safe_session_id` before any path is built — a separator,
``..``, or a glob metacharacter is rejected up front — and the Claude lookup is
pinned to the project's own encoded dir (with a resolve-under-root escape guard),
so a hostile id can neither traverse the filesystem nor pull another project's
transcript into this project's bucket.

The single load-bearing reuse is :func:`ingest_one_session`: it is idempotent,
enforces project-exclusion at the one choke point, and runs the security sanitize
pipeline — so a current-turn capsule is redacted identically to a finalized one.
``IngestResult.trace_id`` is ``str | None`` and is NOT always populated, so we
branch on ``result.action`` / ``result.error`` rather than blindly returning it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# Resolution outcomes. Anything other than ``resolved`` carries a distinct
# remediation in the CLI (the issue AC: a clear "not materialized; run X").
RESOLVED = "resolved"
NOT_FOUND = "not_found"
EXCLUDED = "excluded"
LOCKED = "locked"
UNPARSED = "unparsed"
INVALID_ID = "invalid_id"


@dataclass
class SessionResolution:
    """The result of resolving a ``--from-session`` id to a bucket trace."""

    status: str
    trace_id: str | None = None
    agent: str | None = None
    codex_sidecar: Path | None = None
    claude_glob: str | None = None
    detail: str | None = None


def _safe_sidecar_name(value: str) -> str:
    # Mirror capture/codex_cli/hooks/_common.py::safe_session_id so a resolver
    # lookup lands on the same file the hook wrote.
    out = "".join(
        ch if (ch.isalnum() or ch in {"_", ".", "-"}) else "_" for ch in str(value)
    ).strip("._")
    return out or "unknown"


def is_safe_session_id(session_id: str) -> bool:
    """A session id is treated as an OPAQUE filename stem, never a path.

    Reject anything that could traverse the filesystem or expand as a glob:
    a path separator, a bare ``.``/``..`` component, or any character outside
    ``[A-Za-z0-9_.-]`` (which also rejects the glob metacharacters ``* ? [ ]``).
    Equivalently, the id must be unchanged by the hook's own sanitizer — so the
    resolver can never look anywhere other than the exact file the hook wrote.
    """

    if not session_id or session_id in {".", ".."}:
        return False
    if "/" in session_id or "\\" in session_id:
        return False
    # The codex hook strips disallowed chars + leading/trailing ``._``; requiring
    # the id to be a fixed point of that sanitizer rejects ``*``, ``[``, ``]``,
    # ``?`` (mapped to ``_``) and any leading-dot / trailing-dot oddity.
    return _safe_sidecar_name(session_id) == session_id


def _codex_sidecar_path(project_dir: Path, session_id: str) -> Path:
    """The Codex opentraces hook sidecar path for one session in this project.

    Caller MUST have validated ``session_id`` with :func:`is_safe_session_id`
    first; we use it verbatim (no transform) so the lookup is the exact file.
    """

    return project_dir / ".opentraces" / "codex-cli" / "hooks" / f"{session_id}.jsonl"


def _claude_transcript_path(project_dir: Path, session_id: str) -> Path:
    """The EXACT Claude transcript path for this session IN THIS PROJECT.

    Claude stores sessions under ``~/.claude/projects/<encoded-cwd>/<id>.jsonl``
    where ``<encoded-cwd>`` is :func:`encode_claude_path` of the cwd. We resolve
    only inside this project's own encoded dir — never a glob across every
    project — so a session id can neither traverse out of the projects root nor
    pull in ANOTHER project's transcript into this project's bucket.
    """

    from ..repo_identity import encode_claude_path

    projects = (Path.home() / ".claude" / "projects").resolve()
    return projects / encode_claude_path(project_dir) / f"{session_id}.jsonl"


def _find_claude_transcript(session_id: str, project_dir: Path) -> Path | None:
    """Locate this project's Claude transcript for ``session_id``, safely.

    Confinement (defense in depth on top of :func:`is_safe_session_id`) that
    closes the WHOLE symlink class — file-symlink, dir-symlink, and any deeper
    symlink. ``Path.resolve()`` collapses every symlink in the path, so we then
    require the FIRST path segment of the resolved candidate (taken relative to
    the resolved ``~/.claude/projects`` root) to equal the LITERAL encoded name
    of the current project. We deliberately do NOT resolve the project dir as the
    confinement root: if the encoded project dir itself were a symlink to a
    sibling, resolving both sides would collapse into the sibling and the check
    would wrongly pass. Comparing the literal encoded segment rejects:

    * an ``<id>.jsonl`` symlink → a sibling's transcript (resolved parts[0] is the
      sibling's encoded name ≠ ours), and
    * a symlinked encoded DIR → a sibling encoded dir (resolved parts[0] is the
      sibling's encoded name ≠ ours).

    Returns the candidate only when it stays literally confined and exists.
    """

    from ..repo_identity import encode_claude_path

    projects = Path.home() / ".claude" / "projects"
    if not projects.exists():
        return None
    projects_root = projects.resolve()
    encoded = encode_claude_path(project_dir)
    candidate = projects_root / encoded / f"{session_id}.jsonl"
    try:
        rel = candidate.resolve().relative_to(projects_root)
    except (ValueError, OSError):
        return None
    # The resolved path's first segment under the projects root must be the
    # literal encoded name of THIS project — not a sibling reached via a symlink.
    if not rel.parts or rel.parts[0] != encoded:
        return None
    return candidate if candidate.is_file() else None


def _classify_ingest(result, *, agent: str, **paths) -> SessionResolution:
    """Map an :class:`IngestResult` onto a :class:`SessionResolution`.

    ``IngestResult.trace_id`` is ``str | None``; the excluded / lock skip paths
    leave it ``None`` with a distinct ``error``. Branch on action/error so a
    ``None`` trace_id never masquerades as a generic "trace not found".
    """

    action = getattr(result, "action", None)
    error = (getattr(result, "error", None) or "").strip()

    if action == "skipped":
        if error.startswith("project is excluded"):
            return SessionResolution(status=EXCLUDED, agent=agent, detail=error, **paths)
        if "lock" in error:
            return SessionResolution(status=LOCKED, agent=agent, detail=error, **paths)
        # below parse quality gate / missing source / no steps — the session
        # exists but has nothing materializable yet (the current-turn limit).
        return SessionResolution(status=UNPARSED, agent=agent, detail=error, **paths)

    if action == "error":
        return SessionResolution(status=UNPARSED, agent=agent, detail=error, **paths)

    # new / refreshed / new_generation / noop → use the trace_id when present.
    trace_id = getattr(result, "trace_id", None)
    if trace_id:
        return SessionResolution(
            status=RESOLVED, trace_id=str(trace_id), agent=agent, **paths
        )
    # Defensive: a success action with no trace_id should not happen, but never
    # surface it as a real capsule build.
    return SessionResolution(
        status=UNPARSED, agent=agent, detail=error or f"{action}: no trace id", **paths
    )


def resolve_session_to_trace(
    session_id: str,
    project_dir: Path,
    *,
    agent: str | None = None,
) -> SessionResolution:
    """Resolve ``session_id`` to a bucket ``trace_id`` by materializing the turn.

    ``agent`` is ``"codex"`` / ``"claude"`` to force a source, or ``None`` to
    auto-detect (Codex sidecar first, then a Claude transcript). Returns a
    :class:`SessionResolution` whose ``status`` keys the CLI's remediation.
    """

    from ..ingest import ingest_one_session

    project_dir = Path(project_dir).resolve()

    # SECURITY: the id is an opaque filename stem, never a path. Reject traversal
    # / glob metacharacters BEFORE building any path or touching the filesystem.
    if not is_safe_session_id(session_id):
        return SessionResolution(
            status=INVALID_ID, agent=agent,
            detail=f"{session_id!r} is not a valid session id",
        )

    codex_path = _codex_sidecar_path(project_dir, session_id)
    claude_path = _claude_transcript_path(project_dir, session_id)
    paths = {"codex_sidecar": codex_path, "claude_glob": str(claude_path)}

    want_codex = agent in (None, "codex")
    want_claude = agent in (None, "claude")

    if want_codex and codex_path.exists():
        result = ingest_one_session(
            codex_path, project_dir, parser_name="codex-cli"
        )
        return _classify_ingest(result, agent="codex", **paths)

    if want_claude:
        transcript = _find_claude_transcript(session_id, project_dir)
        if transcript is not None:
            result = ingest_one_session(
                transcript, project_dir, parser_name="claude-code"
            )
            return _classify_ingest(result, agent="claude", **paths)

    # Neither source materialized. If the user pinned an agent, only that source
    # was checked — the remediation names what was looked for.
    return SessionResolution(
        status=NOT_FOUND, agent=agent,
        codex_sidecar=codex_path, claude_glob=str(claude_path),
    )


__all__ = [
    "RESOLVED",
    "NOT_FOUND",
    "EXCLUDED",
    "LOCKED",
    "UNPARSED",
    "INVALID_ID",
    "SessionResolution",
    "is_safe_session_id",
    "resolve_session_to_trace",
]
