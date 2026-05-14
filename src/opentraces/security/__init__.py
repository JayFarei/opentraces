"""Security/privacy pipeline.

Public surface: ``sanitize_record`` / ``sanitize_text`` / ``sanitize_dict`` /
``list_tools``. Lower-level building blocks (``scan_text``,
``classify_trace_record``, ``anonymize_paths``, ...) are re-exported here for
advanced consumers.
"""

from .version import SECURITY_VERSION
from .anonymizer import anonymize_paths, hash_username
from .classifier import (
    ClassifierFlag,
    ClassifierResult,
    classify_content,
    classify_trace_record,
)
from .pipeline import (
    PipelineReport,
    list_tools,
    run_tools,
    sanitize_dict,
    sanitize_record,
    sanitize_text,
)
from .privacy import DEFAULT_PRIVACY_TIER, PRIVACY_TIERS, PrivacyTier
from .redactor import RedactingFilter, configure_logging
from .scanner import (
    FieldType,
    ScanResult,
    scan_content,
    scan_serialized,
)
from .secrets import (
    SecretMatch,
    redact_text,
    scan_text,
    shannon_entropy,
)
from .tools import (
    Detector,
    Finding,
    ToolContext,
    ToolInfo,
    ToolResult,
    Verdict,
)

__all__ = [
    # version
    "SECURITY_VERSION",
    # pipeline
    "PipelineReport",
    "list_tools",
    "run_tools",
    "sanitize_dict",
    "sanitize_record",
    "sanitize_text",
    # tool primitives
    "Detector",
    "Finding",
    "ToolContext",
    "ToolInfo",
    "ToolResult",
    "Verdict",
    # legacy / lower-level surface
    "DEFAULT_PRIVACY_TIER",
    "PRIVACY_TIERS",
    "PrivacyTier",
    "SecretMatch",
    "scan_text",
    "redact_text",
    "shannon_entropy",
    "anonymize_paths",
    "hash_username",
    "FieldType",
    "ScanResult",
    "scan_content",
    "scan_serialized",
    "ClassifierFlag",
    "ClassifierResult",
    "classify_content",
    "classify_trace_record",
    "RedactingFilter",
    "configure_logging",
]
