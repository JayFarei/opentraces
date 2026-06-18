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
  (``capture/claude_code/parse.py``). We glob that transcript and ingest it with
  the ``claude-code`` parser.

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


@dataclass
class SessionResolution:
    """The result of resolving a ``--from-session`` id to a bucket trace."""

    status: str
    trace_id: str | None = None
    agent: str | None = None
    codex_sidecar: Path | None = None
    claude_glob: str | None = None
    detail: str | None = None


def _codex_sidecar_path(project_dir: Path, session_id: str) -> Path:
    """The Codex opentraces hook sidecar path for one session in this project."""

    safe = _safe_sidecar_name(session_id)
    return project_dir / ".opentraces" / "codex-cli" / "hooks" / f"{safe}.jsonl"


def _safe_sidecar_name(value: str) -> str:
    # Mirror capture/codex_cli/hooks/_common.py::safe_session_id so a resolver
    # lookup lands on the same file the hook wrote.
    out = "".join(
        ch if (ch.isalnum() or ch in {"_", ".", "-"}) else "_" for ch in str(value)
    ).strip("._")
    return out or "unknown"


def _claude_glob(session_id: str) -> str:
    return f"~/.claude/projects/*/{session_id}.jsonl"


def _find_claude_transcript(session_id: str) -> Path | None:
    projects = Path.home() / ".claude" / "projects"
    if not projects.exists():
        return None
    matches = sorted(projects.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


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
    codex_path = _codex_sidecar_path(project_dir, session_id)
    claude_glob = _claude_glob(session_id)
    paths = {"codex_sidecar": codex_path, "claude_glob": claude_glob}

    want_codex = agent in (None, "codex")
    want_claude = agent in (None, "claude")

    if want_codex and codex_path.exists():
        result = ingest_one_session(
            codex_path, project_dir, parser_name="codex-cli"
        )
        return _classify_ingest(result, agent="codex", **paths)

    if want_claude:
        transcript = _find_claude_transcript(session_id)
        if transcript is not None:
            result = ingest_one_session(
                transcript, project_dir, parser_name="claude-code"
            )
            return _classify_ingest(result, agent="claude", **paths)

    # Neither source materialized. If the user pinned an agent, only that source
    # was checked — the remediation names what was looked for.
    return SessionResolution(
        status=NOT_FOUND, agent=agent, codex_sidecar=codex_path, claude_glob=claude_glob
    )


__all__ = [
    "RESOLVED",
    "NOT_FOUND",
    "EXCLUDED",
    "LOCKED",
    "UNPARSED",
    "SessionResolution",
    "resolve_session_to_trace",
]
