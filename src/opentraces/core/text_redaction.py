"""Text redaction helpers for bounded local retrieval surfaces."""

from __future__ import annotations

import re

from ..security import sanitize_text
from ..security.secrets import redact_text, scan_text


_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b([A-Za-z_]*(?:api[_-]?key|password|passwd|secret|token)[A-Za-z_]*)\s*=\s*([^\s\"'`]+)",
    re.IGNORECASE,
)


def redact_index_text(text: object) -> str:
    """Redact secret-shaped strings before storing index previews."""

    value = str(text)
    value, _ = sanitize_text(value, tools=["regex", "entropy"])
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


def redact_preview_text(text: object) -> str:
    """Fast redaction for bounded display previews.

    Query/discovery summaries run during cheap index refresh over many records,
    so they use regex detectors plus explicit assignment redaction and avoid the
    full entropy pass reserved for index/evidence text.
    """

    value = str(text)
    matches = scan_text(value, include_entropy=False)
    if matches:
        value = redact_text(value, matches)
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
