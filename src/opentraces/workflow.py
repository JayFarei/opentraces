"""Shared workflow vocabulary for CLI, web, and TUI surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from .state import TraceStatus

DEFAULT_REVIEW_POLICY = "review"
DEFAULT_PUSH_POLICY = "manual"
DEFAULT_REMOTE_NAME = "opentraces"
DEFAULT_AGENT = "claude-code"

VISIBLE_STAGE_ORDER = ("inbox", "committed", "pushed", "rejected")

OPENTRACES_ASCII = r"""
  ___  _____
 / _ \|_   _|
| | | | | |
| |_| | | |
 \___/  |_|
 OPENTRACES
""".strip("\n")


@dataclass(frozen=True)
class StagePresentation:
    key: str
    label: str
    description: str


STAGE_PRESENTATIONS = {
    "inbox": StagePresentation("inbox", "Inbox", "Needs review"),
    "committed": StagePresentation("committed", "Committed", "Ready to push"),
    "pushed": StagePresentation("pushed", "Pushed", "Published upstream"),
    "rejected": StagePresentation("rejected", "Rejected", "Kept local only"),
}


def discover_supported_agents() -> tuple[str, ...]:
    try:
        from .parsers import get_parsers

        parser_agents = tuple(get_parsers().keys())
    except Exception:
        parser_agents = ()

    if not parser_agents:
        parser_agents = (DEFAULT_AGENT,)

    preferred_order = {DEFAULT_AGENT: 0}
    return tuple(sorted(set(parser_agents), key=lambda agent: (preferred_order.get(agent, 99), agent)))


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
    cleaned = [agent for agent in (agents or []) if agent in SUPPORTED_AGENTS]
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
        TraceStatus.APPROVED: "committed",
        TraceStatus.COMMITTED: "committed",
        TraceStatus.UPLOADING: "committed",
        TraceStatus.UPLOADED: "pushed",
        TraceStatus.REJECTED: "rejected",
        TraceStatus.FAILED: "inbox",
    }
    return mapping.get(status, "inbox")


def stage_label(stage: str) -> str:
    return STAGE_PRESENTATIONS.get(stage, STAGE_PRESENTATIONS["inbox"]).label


def stage_description(stage: str) -> str:
    return STAGE_PRESENTATIONS.get(stage, STAGE_PRESENTATIONS["inbox"]).description
