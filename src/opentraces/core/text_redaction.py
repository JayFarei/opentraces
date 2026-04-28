"""Text redaction helpers for bounded local retrieval surfaces."""

from __future__ import annotations

import re

from ..security.secrets import redact_text, scan_text


_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b([A-Za-z_]*(?:api[_-]?key|password|passwd|secret|token)[A-Za-z_]*)\s*=\s*([^\s\"'`]+)",
    re.IGNORECASE,
)


def redact_index_text(text: object) -> str:
    """Redact secret-shaped strings before storing index previews."""

    value = str(text)
    value = redact_text(value, scan_text(value))
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
