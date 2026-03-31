"""Security pipeline: regex scanning, anonymization, classification."""

from .version import SECURITY_VERSION
from .anonymizer import anonymize_paths, hash_username
from .classifier import (
    ClassifierFlag,
    ClassifierResult,
    classify_content,
    classify_trace_record,
)
from .redactor import RedactingFilter, configure_logging
from .scanner import (
    FieldType,
    ScanResult,
    apply_redactions,
    scan_content,
    scan_serialized,
    scan_trace_record,
    two_pass_scan,
)
from .secrets import (
    SecretMatch,
    redact_text,
    scan_text,
    shannon_entropy,
)

__all__ = [
    # version
    "SECURITY_VERSION",
    # secrets
    "SecretMatch",
    "scan_text",
    "redact_text",
    "shannon_entropy",
    # anonymizer
    "anonymize_paths",
    "hash_username",
    # scanner
    "FieldType",
    "ScanResult",
    "apply_redactions",
    "scan_content",
    "scan_serialized",
    "scan_trace_record",
    "two_pass_scan",
    # classifier
    "ClassifierFlag",
    "ClassifierResult",
    "classify_content",
    "classify_trace_record",
    # redactor
    "RedactingFilter",
    "configure_logging",
]
