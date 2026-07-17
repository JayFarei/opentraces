"""Authoritative TraceMaterializationRef resolution for arena labels/origin.

Rebuilds the current materialization map from a project's registered source
repository and its Trail world-state. Extracted from the generic label
persistence module (``labels.py``) so that module no longer owns project
identity interpretation or Trail-projection construction (#329). Project
identity is interpreted by the repository-identity boundary
(:func:`opentraces.core.repo_identity.registered_source_repo`); this module
owns only the Trail-reconstruction concern and the fail-closed mapping onto
the label-integrity contract.
"""

from __future__ import annotations

from opentraces_schema import TraceRecord

from ..trace_slices import TraceMaterializationRef


def authoritative_trace_materialization_ref(
    project_slug: str,
    record: TraceRecord,
) -> TraceMaterializationRef:
    """Rebuild the current materialization map from registered Trail state.

    A canonical project registration makes its source repository authoritative.
    Record-only materialization is retained only for traces whose project has no
    registration, never as a fallback for a broken registered source.
    """

    from .. import repo_identity
    from .labels import LabelIntegrityError

    try:
        source_repo = repo_identity.registered_source_repo(project_slug)
    except repo_identity.ProjectRegistrationError as exc:
        raise LabelIntegrityError(str(exc)) from exc
    if source_repo is None:
        return TraceMaterializationRef.from_record(record)
    try:
        from ..trails import build_trail_query_projection_for_trace

        projection = build_trail_query_projection_for_trace(source_repo, record.trace_id)
        return TraceMaterializationRef.from_record(record, trail_projection=projection)
    except Exception as exc:
        raise LabelIntegrityError(
            "authoritative current Trace Map could not be rebuilt from Trail world-state"
        ) from exc


__all__ = ["authoritative_trace_materialization_ref"]
