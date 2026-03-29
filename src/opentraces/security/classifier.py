"""Tier 2 heuristic classifier.

Pattern-based flagging beyond regex: internal hostnames, AWS account IDs,
internal URLs, identifier density, and file path depth analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from opentraces_schema.models import TraceRecord

Severity = Literal["critical", "high", "medium", "low"]


@dataclass
class ClassifierFlag:
    """A single heuristic flag."""

    pattern_name: str
    matched_text: str
    reason: str
    severity: Severity


@dataclass
class ClassifierResult:
    """Aggregated classifier output."""

    flags: list[ClassifierFlag] = field(default_factory=list)
    risk_score: float = 0.0

    def merge(self, other: ClassifierResult) -> None:
        """Merge another result into this one."""
        self.flags.extend(other.flags)
        self.risk_score = min(1.0, max(self.risk_score, other.risk_score))


# ---------------------------------------------------------------------------
# Heuristic patterns
# ---------------------------------------------------------------------------

_INTERNAL_HOSTNAME = re.compile(
    r"\b[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"\.(?:internal|corp|local)\b"
)

_AWS_ACCOUNT_ID = re.compile(
    r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):"
)

_DB_CONNECTION_STRING = re.compile(
    r"(?:jdbc:[a-z]+://|mongodb\+srv://)[^\s\"'`<>]{8,}"
)

_INTERNAL_URL = re.compile(
    r"https?://(?:"
    r"jira\.[a-zA-Z0-9.-]+"
    r"|confluence\.[a-zA-Z0-9.-]+"
    r"|[a-zA-Z0-9.-]+\.atlassian\.net"
    r"|[a-zA-Z0-9.-]+\.slack\.com/archives"
    r")"
)

_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

_HEX_HASH = re.compile(r"\b[0-9a-fA-F]{32,64}\b")

_FILE_PATH = re.compile(r"(?:/[a-zA-Z0-9_.@-]+){3,}")


# ---------------------------------------------------------------------------
# Sensitivity thresholds
# ---------------------------------------------------------------------------

_SENSITIVITY_CONFIG = {
    "low": {
        "uuid_density_threshold": 0.02,
        "path_depth_threshold": 8,
        "min_severity": "high",
    },
    "medium": {
        "uuid_density_threshold": 0.01,
        "path_depth_threshold": 6,
        "min_severity": "medium",
    },
    "high": {
        "uuid_density_threshold": 0.005,
        "path_depth_threshold": 4,
        "min_severity": "low",
    },
}

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _severity_meets_min(severity: str, min_severity: str) -> bool:
    return _SEVERITY_ORDER.get(severity, 0) >= _SEVERITY_ORDER.get(min_severity, 0)


# ---------------------------------------------------------------------------
# Core classifier
# ---------------------------------------------------------------------------

def classify_content(
    text: str,
    sensitivity: str = "medium",
) -> ClassifierResult:
    """Classify text for heuristic risk signals.

    Args:
        text: The text to analyze.
        sensitivity: One of "low", "medium", "high".

    Returns:
        ClassifierResult with flags and risk score.
    """
    if not text:
        return ClassifierResult()

    config = _SENSITIVITY_CONFIG.get(sensitivity, _SENSITIVITY_CONFIG["medium"])
    min_sev = config["min_severity"]
    flags: list[ClassifierFlag] = []
    max_score = 0.0

    # Internal hostnames
    for m in _INTERNAL_HOSTNAME.finditer(text):
        sev: Severity = "high"
        if _severity_meets_min(sev, min_sev):
            flags.append(ClassifierFlag(
                pattern_name="internal_hostname",
                matched_text=m.group(),
                reason="Hostname with internal/corp/local TLD suggests internal infrastructure",
                severity=sev,
            ))
            max_score = max(max_score, 0.6)

    # AWS account IDs in ARNs
    for m in _AWS_ACCOUNT_ID.finditer(text):
        sev = "high"
        if _severity_meets_min(sev, min_sev):
            flags.append(ClassifierFlag(
                pattern_name="aws_account_id",
                matched_text=m.group(1),
                reason="AWS account ID in ARN pattern",
                severity=sev,
            ))
            max_score = max(max_score, 0.7)

    # Database connection strings (jdbc, mongodb+srv)
    for m in _DB_CONNECTION_STRING.finditer(text):
        sev = "high"
        if _severity_meets_min(sev, min_sev):
            flags.append(ClassifierFlag(
                pattern_name="db_connection_string",
                matched_text=m.group(),
                reason="Database connection string (jdbc/mongodb+srv)",
                severity=sev,
            ))
            max_score = max(max_score, 0.7)

    # Internal URLs
    for m in _INTERNAL_URL.finditer(text):
        sev = "medium"
        if _severity_meets_min(sev, min_sev):
            flags.append(ClassifierFlag(
                pattern_name="internal_url",
                matched_text=m.group(),
                reason="URL pointing to internal collaboration tool",
                severity=sev,
            ))
            max_score = max(max_score, 0.4)

    # UUID/hash density
    words = text.split()
    word_count = len(words) if words else 1
    uuid_count = len(_UUID_PATTERN.findall(text))
    hash_count = len(_HEX_HASH.findall(text))
    identifier_count = uuid_count + hash_count
    density = identifier_count / word_count

    if density >= config["uuid_density_threshold"] and identifier_count >= 3:
        sev = "medium"
        if _severity_meets_min(sev, min_sev):
            flags.append(ClassifierFlag(
                pattern_name="identifier_density",
                matched_text=f"{identifier_count} identifiers in {word_count} words",
                reason=f"High UUID/hash density ({density:.3f}) suggests internal system data",
                severity=sev,
            ))
            max_score = max(max_score, 0.3 + min(density * 10, 0.4))

    # File path depth
    for m in _FILE_PATH.finditer(text):
        path = m.group()
        depth = path.count("/") - 1  # segments after first /
        if depth >= config["path_depth_threshold"]:
            sev = "low"
            if _severity_meets_min(sev, min_sev):
                flags.append(ClassifierFlag(
                    pattern_name="deep_file_path",
                    matched_text=path,
                    reason=f"File path depth {depth} exceeds threshold, may reveal internal structure",
                    severity=sev,
                ))
                max_score = max(max_score, 0.2)

    return ClassifierResult(flags=flags, risk_score=min(1.0, max_score))


def classify_trace_record(
    record: TraceRecord,
    sensitivity: str = "medium",
) -> ClassifierResult:
    """Classify all text fields in a trace record.

    Args:
        record: The trace record to classify.
        sensitivity: One of "low", "medium", "high".

    Returns:
        Aggregated ClassifierResult.
    """
    result = ClassifierResult()

    # System prompts
    for prompt_text in record.system_prompts.values():
        result.merge(classify_content(prompt_text, sensitivity))

    # Task
    if record.task.description:
        result.merge(classify_content(record.task.description, sensitivity))

    # Steps
    for step in record.steps:
        if step.content:
            result.merge(classify_content(step.content, sensitivity))
        if step.reasoning_content:
            result.merge(classify_content(step.reasoning_content, sensitivity))

        for tc in step.tool_calls:
            for v in tc.input.values():
                if isinstance(v, str):
                    result.merge(classify_content(v, sensitivity))

        for obs in step.observations:
            if obs.content:
                result.merge(classify_content(obs.content, sensitivity))
            if obs.output_summary:
                result.merge(classify_content(obs.output_summary, sensitivity))

    # Outcome
    if record.outcome.description:
        result.merge(classify_content(record.outcome.description, sensitivity))
    if record.outcome.patch:
        result.merge(classify_content(record.outcome.patch, sensitivity))

    return result
