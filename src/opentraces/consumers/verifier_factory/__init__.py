"""Skill Verifier Factory consumer.

Mines OpenTraces ``skill_invocation`` evidence into trace-grounded verifier
packages that SkillOpt / eval workflows can consume. Internal consumer boundary;
user-facing verb is ``opentraces workflow verifier-factory`` (dry-run by default).

Public surface:

* :func:`mine_verifier_candidates` -> ``opentraces.skill_verifier_candidates.v1``.
* :func:`emit_verifier_package` -> one verifier package (spec.yaml, fixtures,
  scorer.py, Dtrain/Dsel/Dtest rows, report) scored through the SkillOpt gate.
* :func:`run_factory` -> mine + emit the default three bucket-derived examples.
* :data:`ARCHETYPES` / :func:`get_archetype` -> the verifier archetype registry.
"""

from __future__ import annotations

from .archetypes import (
    ARCHETYPE_SCHEMA_VERSION,
    ARCHETYPES,
    ARCHETYPES_BY_SKILL,
    DEFAULT_EXAMPLES,
    GENERIC_ARCHETYPE_ID,
    ArchetypeSpecError,
    ContractElement,
    VerifierArchetype,
    archetype_from_dict,
    archetype_permissiveness_flags,
    archetype_to_dict,
    archetypes_for_skill,
    generic_archetype_for,
    get_archetype,
    resolve_archetype,
    validate_archetype_dict,
)
from .detectors import DetectorSpec, DetectorSpecError, detector_from_dict
from .rubric import (
    CalibrationPolicy,
    CalibrationReport,
    Criterion,
    EvidenceSpec,
    Rubric,
    RubricSpecError,
    Verdict,
    criterion_from_dict,
    rubric_from_archetype,
    rubric_from_dict,
    validate_rubric_dict,
    validate_verdict_dict,
)
from .calibration import (
    adversarial_probe,
    calibrate_rubric,
    emulate_human_labels,
    mann_whitney_auc,
    spearman,
    weak_label,
)
from .factory import (
    VerifierFactoryRequest,
    VerifierFactoryResult,
    run_factory,
)
from .authoring import (
    AuthoredArchetype,
    RubricScoreResult,
    VerifierResult,
    align_session,
    author_archetype,
    autoverify,
    autoverify_draft_rubric,
    draft_archetype,
    get_skill_examples,
    list_candidates,
    read_skill_definition,
    score_authored,
    score_rubric,
)
from .judge import (
    JudgeError,
    agent_verdict_map,
    build_judge_packet,
    post_verdict,
    read_emulated_labels,
    read_human_labels,
    read_verdicts,
    record_human_label,
)
from .evidence import resolve_evidence
from .mining import mine_verifier_candidates, propose_archetype_draft
from .packaging import VerifierPackageResult, emit_verifier_package
from .schema import (
    CANDIDATES_SCHEMA_VERSION,
    PACKAGE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    ROW_SCHEMA_VERSION,
    validate_candidates,
    validate_package,
    validate_report,
    validate_row,
)

__all__ = [
    "ARCHETYPES",
    "ARCHETYPES_BY_SKILL",
    "ARCHETYPE_SCHEMA_VERSION",
    "DEFAULT_EXAMPLES",
    "GENERIC_ARCHETYPE_ID",
    "ArchetypeSpecError",
    "ContractElement",
    "DetectorSpec",
    "DetectorSpecError",
    "VerifierArchetype",
    "archetype_from_dict",
    "archetype_permissiveness_flags",
    "archetype_to_dict",
    "archetypes_for_skill",
    "detector_from_dict",
    "generic_archetype_for",
    "get_archetype",
    "resolve_archetype",
    "validate_archetype_dict",
    "CalibrationPolicy",
    "CalibrationReport",
    "Criterion",
    "EvidenceSpec",
    "Rubric",
    "RubricSpecError",
    "Verdict",
    "criterion_from_dict",
    "rubric_from_archetype",
    "rubric_from_dict",
    "validate_rubric_dict",
    "validate_verdict_dict",
    "adversarial_probe",
    "calibrate_rubric",
    "emulate_human_labels",
    "mann_whitney_auc",
    "spearman",
    "weak_label",
    "VerifierFactoryRequest",
    "VerifierFactoryResult",
    "run_factory",
    "mine_verifier_candidates",
    "propose_archetype_draft",
    "AuthoredArchetype",
    "RubricScoreResult",
    "VerifierResult",
    "align_session",
    "author_archetype",
    "autoverify",
    "autoverify_draft_rubric",
    "draft_archetype",
    "get_skill_examples",
    "list_candidates",
    "read_skill_definition",
    "score_authored",
    "score_rubric",
    "JudgeError",
    "agent_verdict_map",
    "build_judge_packet",
    "post_verdict",
    "read_emulated_labels",
    "read_human_labels",
    "read_verdicts",
    "record_human_label",
    "resolve_evidence",
    "VerifierPackageResult",
    "emit_verifier_package",
    "CANDIDATES_SCHEMA_VERSION",
    "PACKAGE_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "ROW_SCHEMA_VERSION",
    "validate_candidates",
    "validate_package",
    "validate_report",
    "validate_row",
]
