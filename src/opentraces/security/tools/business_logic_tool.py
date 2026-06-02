"""Business-logic signal detector (plan 090).

Promotes the classifier's internal-infrastructure heuristics from presence-only
flags to REDACTABLE spans, so a usage-episode capsule scrubs internal hostnames,
collaboration-tool URLs, DB connection strings, and AWS account ids — not just
generic secrets/PII. Registered as the last detector and wired into the capsule
``REDACTION_FLOOR`` (so it runs over the whole envelope regardless of opt-in).
"""

from __future__ import annotations

import re
from typing import Any

from . import DetectorMixin, Finding, ToolInfo, cfg_block
from ..walker import FieldType

# Promoted verbatim from ``security/classifier.py`` (kept intentionally in sync).
# The classifier only FLAGS these (presence); here we emit spans so they redact.
_INTERNAL_HOSTNAME = re.compile(
    r"\b[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"\.(?:internal|corp|local)\b"
)
_AWS_ACCOUNT_ID = re.compile(r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):")
_DB_CONNECTION_STRING = re.compile(r"(?:jdbc:[a-z]+://|mongodb\+srv://)[^\s\"'`<>]{8,}")
_INTERNAL_URL = re.compile(
    r"https?://(?:"
    r"jira\.[a-zA-Z0-9.-]+"
    r"|confluence\.[a-zA-Z0-9.-]+"
    r"|[a-zA-Z0-9.-]+\.atlassian\.net"
    r"|[a-zA-Z0-9.-]+\.slack\.com/archives"
    r")"
)


class BusinessLogicDetector(DetectorMixin):
    """Redacts internal-infrastructure signals as spans."""

    name = "business_logic"
    display_name = "Business-logic signals"

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        return bool(getattr(block, "enabled", False)) if block else False

    def find(self, text: str, field_type: FieldType) -> list[Finding]:
        findings: list[Finding] = []
        for m in _INTERNAL_HOSTNAME.finditer(text):
            findings.append(Finding(
                tool=self.name, pattern="internal_hostname",
                matched_text=m.group(), start=m.start(), end=m.end(), severity="high",
            ))
        for m in _INTERNAL_URL.finditer(text):
            findings.append(Finding(
                tool=self.name, pattern="internal_url",
                matched_text=m.group(), start=m.start(), end=m.end(), severity="medium",
            ))
        for m in _DB_CONNECTION_STRING.finditer(text):
            findings.append(Finding(
                tool=self.name, pattern="db_connection_string",
                matched_text=m.group(), start=m.start(), end=m.end(), severity="high",
            ))
        for m in _AWS_ACCOUNT_ID.finditer(text):
            # Redact ONLY the 12-digit account id (capture group 1), not the
            # surrounding ``arn:aws:...:`` prefix.
            findings.append(Finding(
                tool=self.name, pattern="aws_account_id",
                matched_text=m.group(1), start=m.start(1), end=m.end(1), severity="high",
            ))
        return findings

    def describe(self, cfg: Any) -> ToolInfo:
        is_on = self.enabled(cfg)
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=is_on,
            state="enabled" if is_on else "disabled",
            detail="redacts internal hostnames, collab-tool URLs, DB strings, AWS account ids",
        )
