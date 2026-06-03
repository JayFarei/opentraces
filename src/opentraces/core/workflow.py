"""Shared workflow vocabulary for CLI, web, and TUI surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from .state import TraceStatus

DEFAULT_REVIEW_POLICY = "review"
DEFAULT_PUSH_POLICY = "manual"
DEFAULT_REMOTE_NAME = "opentraces"
DEFAULT_AGENT = "claude-code"

VISIBLE_STAGE_ORDER = ("inbox", "staged", "pushed", "rejected", "blocked")

OPENTRACES_ASCII = r"""
 ________  _________
|\   __  \|\___   ___\
\ \  \|\  \|___ \  \_|
 \ \  \\\  \   \ \  \
  \ \  \\\  \   \ \  \
   \ \_______\   \ \__\
    \|_______|    \|__|
""".strip("\n")


# One-line tagline printed under the banner in CLI surfaces.
OPENTRACES_TAGLINE = "Unlock trapped agent traces"


@dataclass(frozen=True)
class StagePresentation:
    key: str
    label: str
    description: str


STAGE_PRESENTATIONS = {
    "inbox": StagePresentation("inbox", "Inbox", "Needs review"),
    "staged": StagePresentation("staged", "Staged", "Ready to push"),
    "pushed": StagePresentation("pushed", "Pushed", "Published upstream"),
    "rejected": StagePresentation("rejected", "Rejected", "Kept local only"),
    "blocked": StagePresentation(
        "blocked", "Blocked", "Security finding — redact or reject before push"
    ),
}


def discover_supported_agents() -> tuple[str, ...]:
    builtin_agents = (DEFAULT_AGENT, "codex-cli", "pi")
    try:
        from ..capture import get_parsers

        parser_agents = tuple(get_parsers().keys())
    except Exception:
        parser_agents = ()

    if not parser_agents:
        parser_agents = builtin_agents

    preferred_order = {DEFAULT_AGENT: 0, "codex-cli": 1, "pi": 2}
    return tuple(sorted(
        {*builtin_agents, *parser_agents},
        key=lambda agent: (preferred_order.get(agent, 99), agent),
    ))


SUPPORTED_AGENTS = discover_supported_agents()


def normalize_review_policy(value: str | None) -> str:
    if value in {"review", "manual"}:
        return "review"
    if value in {"auto", "auto-ready", "auto_ready"}:
        return "auto"
    return DEFAULT_REVIEW_POLICY


def normalize_push_policy(value: str | None) -> str:
    if value in {"auto", "auto-push", "auto_push"}:
        return "auto-push"
    return DEFAULT_PUSH_POLICY


def normalize_agents(agents: list[str] | None) -> list[str]:
    aliases = {
        "claude": "claude-code",
        "codex": "codex-cli",
    }
    cleaned: list[str] = []
    for agent in agents or []:
        normalized = aliases.get(agent, agent)
        if normalized not in SUPPORTED_AGENTS or normalized in cleaned:
            continue
        cleaned.append(normalized)
    return cleaned or [DEFAULT_AGENT]


def legacy_mode_for_review_policy(review_policy: str) -> str:
    return "auto" if normalize_review_policy(review_policy) == "auto" else "review"


def review_policy_from_legacy_mode(mode: str | None) -> str:
    return "auto" if mode == "auto" else DEFAULT_REVIEW_POLICY


def resolve_visible_stage(status: TraceStatus | str | None) -> str:
    if isinstance(status, str):
        try:
            status = TraceStatus(status)
        except ValueError:
            status = None

    mapping = {
        TraceStatus.STAGED: "inbox",
        TraceStatus.PARSED: "inbox",
        TraceStatus.DISCOVERED: "inbox",
        TraceStatus.REVIEWING: "inbox",
        TraceStatus.APPROVED: "staged",
        TraceStatus.COMMITTED: "staged",
        TraceStatus.UPLOADING: "staged",
        TraceStatus.UPLOADED: "pushed",
        TraceStatus.REJECTED: "rejected",
        TraceStatus.FAILED: "inbox",
        TraceStatus.BLOCKED: "blocked",
    }
    return mapping.get(status, "inbox")


def decide_post_parse_status(
    result, *, review_policy: str
) -> tuple[TraceStatus, str | None]:
    """Decide what status a freshly-parsed trace should land in.

    Inputs
    ------
    result : ProcessedTrace
        The output of ``core.pipeline.process_trace``. Only ``needs_review``
        and ``trufflehog_blocked`` (or ``trufflehog_report.blocked``) are
        consulted, plus ``trufflehog_report.findings`` for the BLOCKED
        reason string. Duck-typed so the unit tests can pass a fake.
    review_policy : str
        ``"auto"`` or ``"review"``; anything else is treated as ``"review"``.

    Returns
    -------
    (status, block_reason)
        ``status`` is the ``TraceStatus`` to set. ``block_reason`` is a
        human-readable string when ``status == BLOCKED`` (consumed by
        ``StateManager.block_trace``), otherwise ``None``.

    Policy
    ------
    1. review_policy == "auto" AND not needs_review → COMMITTED
       (auto-promote to staging).
    2. otherwise → STAGED (waits for ``ot add``).

    TruffleHog findings no longer block here — the pipeline redacts the
    matched substrings in place (same mitigation as Tier 1 regex) and
    the per-finding detail is persisted on ``record.metadata.security``
    so the TUI can surface what was caught. Blocking was a poor fit for
    a deterministic scanner: we already know exactly what to mask.
    """
    needs_review = bool(getattr(result, "needs_review", True))
    policy = normalize_review_policy(review_policy)
    if policy == "auto" and not needs_review:
        return TraceStatus.COMMITTED, None
    return TraceStatus.STAGED, None


def stage_label(stage: str) -> str:
    return STAGE_PRESENTATIONS.get(stage, STAGE_PRESENTATIONS["inbox"]).label


def stage_description(stage: str) -> str:
    return STAGE_PRESENTATIONS.get(stage, STAGE_PRESENTATIONS["inbox"]).description
