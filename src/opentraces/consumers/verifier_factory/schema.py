"""Schema constants + lightweight validators for the Skill Verifier Factory.

These are NEW artifact schemas emitted by the factory; they do not touch the core
``opentraces_schema`` package (TraceRecord / bucket / Trail / Context Tree write
contracts are untouched — additive-only). Each artifact carries an explicit
``schema_version`` and a small structural validator so tests can pin the contract
without a jsonschema dependency.

Frozen downstream-consumer contract (change requires a schema_version bump):

* ``opentraces.skill_verifier_candidates.v1`` , the mined candidate summary.
* ``opentraces.skill_verifier_package.v1`` , the per-package ``spec.yaml`` shape.
* ``opentraces.skill_verifier_rows.v1`` , a Dtrain/Dsel/Dtest verifier row.
* ``opentraces.skill_verifier_report.v1`` , the per-package verifier report.
"""

from __future__ import annotations

import json
from typing import Any

CANDIDATES_SCHEMA_VERSION = "opentraces.skill_verifier_candidates.v1"
PACKAGE_SCHEMA_VERSION = "opentraces.skill_verifier_package.v1"
ROW_SCHEMA_VERSION = "opentraces.skill_verifier_rows.v1"
REPORT_SCHEMA_VERSION = "opentraces.skill_verifier_report.v1"

# Rubric-centric verifier layer (additive; detailed design notes live outside the public package).
# The rubric is a strict additive superset of the archetype; a legacy archetype round-trips
# as a degenerate all-deterministic rubric.
RUBRIC_SCHEMA_VERSION = "opentraces.skill_verifier_rubric.v1"
VERDICT_SCHEMA_VERSION = "opentraces.skill_verifier_verdict.v1"
CALIBRATION_SCHEMA_VERSION = "opentraces.skill_verifier_calibration.v1"
CALIBRATION_POLICY_VERSION = "opentraces.skill_verifier_calibration_policy.v1"

#: Verifier and skill promotion are always manual / default-off (constraint).
APPROVAL_STATE = "manual_required_default_off"

CANDIDATES_REQUIRED_KEYS = (
    "schema_version",
    "generated_at",
    "project",
    "skills",
    "total_skill_invocation_units",
)
CANDIDATE_SKILL_REQUIRED_KEYS = (
    "skill_id",
    "usable_episodes",
    "agents",
    "sources",
    "archetypes",
    "leakage_safe_split",
    "source_refs",
)
ARCHETYPE_CANDIDATE_REQUIRED_KEYS = (
    "archetype_id",
    "title",
    "markers",
    "evidence_strength",
    "deficient_marker_frequency",
    "recommended",
    "creator_questions",
    "provenance",
)
PACKAGE_REQUIRED_KEYS = (
    "schema_version",
    "archetype_id",
    "skill_id",
    "generated_at",
    "semantic_target",
    "markers",
    "contract_elements",
    "graders",
    "creator_decisions",
    "split",
    "approval_state",
    "automatic_promotion",
    "source_refs",
)
ROW_REQUIRED_KEYS = (
    "schema_version",
    "row_id",
    "skill_id",
    "archetype_id",
    "split",
    "leakage_key",
    "reward",
    "deficient_markers_json",
    "source_trace_id",
    "source_unit_id",
)
REPORT_REQUIRED_KEYS = (
    "schema_version",
    "archetype_id",
    "skill_id",
    "baseline_digest",
    "candidate_digest",
    "dtrain_count",
    "dsel_count",
    "dtest_count",
    "dsel_before",
    "dsel_after",
    "dtest_score",
    "accepted",
    "approval_state",
    "automatic_promotion",
    "limitations_json",
    "source_refs",
)


class VerifierSchemaError(ValueError):
    """Raised when a factory artifact violates its declared schema."""


def _require(obj: Any, keys: tuple[str, ...], *, what: str) -> None:
    if not isinstance(obj, dict):
        raise VerifierSchemaError(f"{what}: expected an object, got {type(obj).__name__}")
    missing = [k for k in keys if k not in obj]
    if missing:
        raise VerifierSchemaError(f"{what}: missing required keys {missing}")


def validate_candidates(payload: Any) -> None:
    _require(payload, CANDIDATES_REQUIRED_KEYS, what="candidates")
    if payload.get("schema_version") != CANDIDATES_SCHEMA_VERSION:
        raise VerifierSchemaError("candidates: wrong schema_version")
    skills = payload.get("skills")
    if not isinstance(skills, list):
        raise VerifierSchemaError("candidates: skills must be a list")
    for entry in skills:
        _require(entry, CANDIDATE_SKILL_REQUIRED_KEYS, what="candidate skill")
        for arch in entry.get("archetypes", []):
            _require(arch, ARCHETYPE_CANDIDATE_REQUIRED_KEYS, what="archetype candidate")


def validate_package(payload: Any) -> None:
    _require(payload, PACKAGE_REQUIRED_KEYS, what="package spec")
    if payload.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise VerifierSchemaError("package spec: wrong schema_version")
    if payload.get("automatic_promotion") is not False:
        raise VerifierSchemaError("package spec: automatic_promotion must be False")
    graders = payload.get("graders")
    if not isinstance(graders, list) or not graders:
        raise VerifierSchemaError("package spec: graders must be a non-empty list")
    kinds = {g.get("kind") for g in graders if isinstance(g, dict)}
    if "deterministic" not in kinds:
        raise VerifierSchemaError("package spec: a deterministic grader is required for default CI")
    # Every non-deterministic (live) leg must be opt-in / default-off.
    for grader in graders:
        if not isinstance(grader, dict):
            raise VerifierSchemaError("package spec: grader must be an object")
        if grader.get("kind") != "deterministic" and grader.get("default_enabled") is not False:
            raise VerifierSchemaError(
                f"package spec: live grader {grader.get('id')!r} must be opt-in (default_enabled=False)"
            )


def validate_row(payload: Any) -> None:
    _require(payload, ROW_REQUIRED_KEYS, what="verifier row")
    if payload.get("schema_version") != ROW_SCHEMA_VERSION:
        raise VerifierSchemaError("verifier row: wrong schema_version")
    if payload.get("split") not in {"Dtrain", "Dsel", "Dtest"}:
        raise VerifierSchemaError("verifier row: split must be Dtrain|Dsel|Dtest")
    reward = payload.get("reward")
    if not isinstance(reward, (int, float)) or not (0.0 <= float(reward) <= 1.0):
        raise VerifierSchemaError("verifier row: reward must be a float in [0,1]")


def validate_report(payload: Any) -> None:
    _require(payload, REPORT_REQUIRED_KEYS, what="verifier report")
    if payload.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise VerifierSchemaError("verifier report: wrong schema_version")
    if payload.get("automatic_promotion") is not False:
        raise VerifierSchemaError("verifier report: automatic_promotion must be False")
    if payload.get("approval_state") != APPROVAL_STATE:
        raise VerifierSchemaError("verifier report: approval_state must be manual/default-off")


def dump_yaml(payload: Any) -> str:
    """Serialize a spec to YAML, falling back to JSON (a valid YAML subset).

    PyYAML is used when available so ``spec.yaml`` reads naturally; otherwise the
    canonical JSON encoding is emitted, which every YAML parser accepts. Either
    way the bytes round-trip back to the same object.
    """
    try:  # pragma: no cover - exercised whichever branch the env provides
        import yaml  # type: ignore

        return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False)
    except Exception:  # pragma: no cover - fallback path
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"
