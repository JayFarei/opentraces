"""Quality assessment framework for trace conformance scoring.

Public API:
    RubricItem, RubricReport -- scoring types
    CheckResult, CheckDef, PersonaDef -- extensible check/persona types
    score_trace -- backward-compatible convenience wrapper
    CONFORMANCE_PERSONA -- the structural conformance persona definition
    assess_trace, assess_batch, generate_report -- engine
    audit_schema_completeness -- schema audit
"""

from .types import (
    CheckDef,
    CheckResult,
    PersonaDef,
    RubricItem,
    RubricReport,
)
from .conformance import CONFORMANCE_PERSONA, score_trace
from .engine import (
    TraceAssessment,
    BatchAssessment,
    MultiProjectAssessment,
    PersonaScore,
    ProjectInfo,
    assess_trace,
    assess_batch,
    assess_multi_project,
    discover_projects,
    generate_report,
    generate_multi_project_report,
)
from .schema_audit import audit_schema_completeness, format_audit_report
from .gates import (
    DEFAULT_THRESHOLDS,
    GateResult,
    PRESERVATION_THRESHOLD,
    PersonaThreshold,
    check_gate,
)

__all__ = [
    "BatchAssessment",
    "CheckDef",
    "CheckResult",
    "CONFORMANCE_PERSONA",
    "DEFAULT_THRESHOLDS",
    "GateResult",
    "MultiProjectAssessment",
    "PRESERVATION_THRESHOLD",
    "PersonaDef",
    "PersonaScore",
    "PersonaThreshold",
    "ProjectInfo",
    "RubricItem",
    "RubricReport",
    "TraceAssessment",
    "assess_batch",
    "assess_multi_project",
    "assess_trace",
    "audit_schema_completeness",
    "check_gate",
    "discover_projects",
    "format_audit_report",
    "generate_multi_project_report",
    "generate_report",
    "score_trace",
]
